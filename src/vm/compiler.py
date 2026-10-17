# -*- coding: utf-8 -*-
"""
compiler.py
============
Dich AST (tu src/parser/nodes.py) thanh bytecode 32-bit (dinh dang trong
opcode.py), dung environment.py de quan ly constant pool + thanh ghi.

DAY LA BAN "v1": dich DUNG cho 1 tap con cua Lua/Luau du de chay cac doan
code game pho bien (bien local, if/while/repeat/numeric-for, bang, goi
ham/method, toan tu so hoc/logic co ban). Mot so tinh nang CHUA duoc ho
tro va se NEM CompilerError RO RANG thay vi sinh bytecode SAI:

    - Constant pool vuot qua gioi han ma hoa cua cac operand bytecode.
    - goto/label - can 1 buoc "resolve nhan" rieng (2-pass) de ho tro nhay
      toi/lui bat ky, chua lam o ban nay.

Cac gioi han tren deu la TODO co chu dich cho cac ban nang cap sau, KHONG
phai loi ngoai y muon - moi truong hop deu nem CompilerError voi thong
diep giai thich ro, khong bao gio am tham sinh bytecode sai ngu nghia.

Quy uoc thanh ghi (register convention) dung xuyen suot file nay:
    - Moi ham (FunctionProto) co "cua so thanh ghi" RIENG, danh so tu 0.
    - CALL/CALL_GLOBAL/CALL_METHOD: ham + tham so nam o day thanh ghi
      LIEN TUC bat dau tai thanh ghi cua ham; ket qua tra ve ghi DE len
      thanh ghi cua ham (giong quy uoc OP_CALL cua Lua that).
    - 1 ham con (closure) LUON duoc dich XONG truoc khi lenh CLOSURE cua
      ham cha duoc emit; proto duoc danh chi so truoc (dat cho) de ho
      tro tra cuu, tuong tu cach Lua that quan ly danh sach prototype.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from .opcode import (
    Opcode, encode_instruction, decode_instruction,
    C_SIGNED_BIAS,
)
from .environment import Environment

from ..parser.nodes import (
    Block, Stmt, Expr,
    NilExpr, TrueExpr, FalseExpr, VarargExpr, NumberExpr, StringExpr,
    NameExpr, IndexExpr, IndexBracketExpr, CallExpr, MethodCallExpr,
    FunctionExpr, TableConstructorExpr, BinOpExpr, UnOpExpr, IfExpr, ParenExpr,
    TypeCastExpr, FunctionBody,
    LocalAssignStmt, AssignStmt, CompoundAssignStmt, CallStmt, DoStmt,
    WhileStmt, RepeatStmt, IfStmt, NumericForStmt, GenericForStmt,
    FunctionDeclStmt, LocalFunctionStmt, ReturnStmt,
    BreakStmt, ContinueStmt, GotoStmt, LabelStmt, TypeAliasStmt,
)


class CompilerError(Exception):
    def __init__(self, message: str, line: int = 0, col: int = 0):
        loc = f" (dong {line}, cot {col})" if line else ""
        super().__init__(f"[Compiler]{loc} {message}")
        self.message = message
        self.line = line
        self.col = col


# Pham vi gia tri nguyen co the dung lam toan hang "immediate" cho
# ADDI/SUBI/MULI (truong C, 10-bit, signed excess-K).
_IMM_LO, _IMM_HI = -C_SIGNED_BIAS, C_SIGNED_BIAS - 1
_VM_HELPER_IDS = {
    "_mod": 0,
    "_pow": 1,
    "_idiv": 2,
    "_len": 3,
    "_iter": 4,
    "_neg": 5,
}


# ---------------------------------------------------------------------------
# FunctionProto - ket qua dich 1 ham
# ---------------------------------------------------------------------------

@dataclass
class FunctionProto:
    """Ket qua dich 1 function body thanh bytecode: ma lenh, bang hang
    so, va cac thong tin can de runtime cap phat khung thanh ghi.
    """
    name: str
    num_params: int
    is_vararg: bool
    code: List[int]
    constants: List[Any]
    max_registers: int
    upvalues: List[Tuple[str, int]] = field(default_factory=list)

    def disassemble(self) -> str:
        """In bytecode ra dang text de debug/test (dung opcode.disassemble)."""
        from .opcode import disassemble as _disasm
        lines = [f"; proto '{self.name}'  params={self.num_params} "
                 f"vararg={self.is_vararg} max_registers={self.max_registers}"]
        for i, word in enumerate(self.code):
            lines.append(f"{i:4d}  {_disasm(word)}")
        if self.constants:
            lines.append("; constants:")
            for i, c in enumerate(self.constants):
                lines.append(f"  K[{i}] = {c!r}")
        return "\n".join(lines)


@dataclass
class LoopContext:
    """Theo doi cac lenh JMP con cho patch cua 1 vong lap dang dich:
    break_patches  - nhay toi vi tri NGAY SAU vong lap khi vong lap ket thuc
    continue_patches - nhay toi diem "tiep tuc" cua vong lap (tuy loai
                        vong lap: dau dieu kien voi while, truoc FORLOOP
                        voi numeric-for, truoc dieu kien until voi repeat)
    """
    break_patches: List[int] = field(default_factory=list)
    continue_patches: List[int] = field(default_factory=list)


# ---------------------------------------------------------------------------
# _FuncCompiler - dich 1 function body thanh 1 FunctionProto
# ---------------------------------------------------------------------------

class _FuncCompiler:
    """Trang thai dich cho DUNG 1 ham dang duoc dich (1 FunctionProto)."""

    def __init__(
        self,
        env: Environment,
        compiler: "Compiler",
        name: str,
        proto_idx: int,
    ):
        self.env = env
        self.compiler = compiler
        self.name = name
        self.proto_idx = proto_idx
        self.captured_registers = compiler.captured_registers.get(proto_idx, set())
        self.code: List[int] = []
        self.loop_stack: List[LoopContext] = []

    # -- Emit / patch lenh --------------------------------------------------

    def emit(self, op: Opcode, a: int = 0, b: int = 0, c: int = 0) -> int:
        idx = len(self.code)
        self.code.append(encode_instruction(op, a, b, c))
        return idx

    def patch_jump(self, idx: int, target_pos: int) -> None:
        """Ghi lai truong C (offset nhay) cua lenh tai `idx` de PC (sau khi
        thuc thi lenh do, tuc da +1) cong voi offset nay ra dung `target_pos`.
        Dung CHUNG cho moi opcode co truong C kieu JMP (JMP, *_JMP, TEST,
        FORPREP, FORLOOP) vi encode_instruction/decode_instruction da tu
        biet truong nao can bias dua theo OPCODE_INFO.
        """
        d = decode_instruction(self.code[idx])
        offset = target_pos - (idx + 1)
        self.code[idx] = encode_instruction(d.op, d.a, d.b, offset)

    def patch_jumps(self, idxs: List[int], target_pos: int) -> None:
        for idx in idxs:
            self.patch_jump(idx, target_pos)

    # -- Tien ich thanh ghi / hang so ---------------------------------------

    def _load_const(self, dest_reg: int, value: Any) -> None:
        idx = self.env.constants.add(value)
        self.emit(Opcode.LOADK, dest_reg, idx % 512, idx // 512)

    def _emit_getglobal(self, dest_reg: int, constant_idx: int) -> None:
        high = constant_idx // 512
        self.emit(
            Opcode.GETGLOBAL,
            dest_reg,
            constant_idx % 512,
            511 + high if high else 0,
        )

    def _emit_setglobal(self, source_reg: int, constant_idx: int) -> None:
        high = constant_idx // 512
        self.emit(
            Opcode.SETGLOBAL,
            source_reg,
            constant_idx % 512,
            511 + high if high else 0,
        )

    def _emit_gettable_k(
        self,
        dest_reg: int,
        object_reg: int,
        constant_idx: int,
    ) -> None:
        if constant_idx < 2048:
            self.emit(
                Opcode.GETTABLE_K,
                dest_reg,
                object_reg + (constant_idx // 1024) * 256,
                constant_idx % 1024,
            )
            return

        base_mark = self.env.registers.mark()
        key_reg = self.env.registers.alloc_temp()
        self._load_const(key_reg, self.env.constants.get(constant_idx))
        self.emit(Opcode.GETTABLE, dest_reg, object_reg, key_reg)
        self.env.registers.release_to(base_mark)

    def _emit_settable_k(
        self,
        object_reg: int,
        constant_idx: int,
        value_reg: int,
    ) -> None:
        if constant_idx >= 2048:
            base_mark = self.env.registers.mark()
            key_reg = self.env.registers.alloc_temp()
            self._load_const(key_reg, self.env.constants.get(constant_idx))
            self.emit(Opcode.SETTABLE, object_reg, key_reg, value_reg)
            self.env.registers.release_to(base_mark)
            return

        self.emit(
            Opcode.SETTABLE_K,
            object_reg,
            constant_idx % 512,
            value_reg + (constant_idx // 512) * 256,
        )

    def _emit_vm_helper(self, dest_reg: int, helper_name: str) -> None:
        helper_id = _VM_HELPER_IDS[helper_name]
        self.emit(Opcode.GETGLOBAL, dest_reg, helper_id, 511)

    def _local_is_captured(self, register: int) -> bool:
        return register in self.captured_registers

    def _load_local(self, dest_reg: int, source_reg: int) -> None:
        if self._local_is_captured(source_reg):
            key_idx = self.env.constants.add(1)
            self._emit_gettable_k(dest_reg, source_reg, key_idx)
        elif dest_reg != source_reg:
            self.emit(Opcode.MOVE, dest_reg, source_reg)

    def _store_local(self, source_reg: int, dest_reg: int) -> None:
        if self._local_is_captured(dest_reg):
            key_idx = self.env.constants.add(1)
            self._emit_settable_k(dest_reg, key_idx, source_reg)
        elif source_reg != dest_reg:
            self.emit(Opcode.MOVE, dest_reg, source_reg)

    def _box_local(self, register: int) -> None:
        if not self._local_is_captured(register):
            return
        base_mark = self.env.registers.mark()
        value_reg = self.env.registers.alloc_temp()
        self.emit(Opcode.MOVE, value_reg, register)
        self.emit(Opcode.NEWTABLE, register, 0, 0)
        key_idx = self.env.constants.add(1)
        self._emit_settable_k(register, key_idx, value_reg)
        self.env.registers.release_to(base_mark)

    def _compile_runtime_helper(
        self,
        helper_name: str,
        arguments: List[Expr],
        dest_reg: int,
    ) -> None:
        base_mark = self.env.registers.mark()
        function_reg = self.env.registers.alloc_temp()
        self._emit_vm_helper(function_reg, helper_name)
        for argument in arguments:
            self._compile_into_fresh_reg(argument)
        self.emit(Opcode.CALL, function_reg, len(arguments) + 1, 2)
        if dest_reg != function_reg:
            self.emit(Opcode.MOVE, dest_reg, function_reg)
        self.env.registers.release_to(base_mark)

    def _is_vararg_count_call(self, expr: Expr) -> bool:
        return (
            isinstance(expr, CallExpr)
            and isinstance(expr.func, NameExpr)
            and expr.func.name == "select"
            and self.env.resolve_name("select").kind == "global"
            and len(expr.args) == 2
            and isinstance(expr.args[0], StringExpr)
            and expr.args[0].value == "#"
            and isinstance(expr.args[1], VarargExpr)
        )

    @staticmethod
    def _has_trailing_vararg(expr: CallExpr) -> bool:
        return bool(expr.args) and isinstance(expr.args[-1], VarargExpr)

    @staticmethod
    def _has_trailing_call(expr: CallExpr | MethodCallExpr) -> bool:
        return bool(expr.args) and isinstance(expr.args[-1], (CallExpr, MethodCallExpr))

    def _compile_open_call_argument(self, expr: Expr, dest_reg: int) -> None:
        if isinstance(expr, CallExpr):
            self._compile_call(expr, dest_reg, None)
        elif isinstance(expr, MethodCallExpr):
            self._compile_method_call(expr, dest_reg, None)
        else:
            raise CompilerError(
                f"Internal compiler error: expected call expression, got {type(expr).__name__}",
                expr.line,
                expr.col,
            )

    @staticmethod
    def _call_result_operand(want_results: Optional[int], tail_kind: Optional[str]) -> int:
        if want_results is None:
            result_count = 256
        else:
            if not 0 <= want_results <= 255:
                raise CompilerError(
                    "A function call can request at most 255 fixed results."
                )
            result_count = (want_results + 1) % 256
        if tail_kind == "vararg":
            return result_count + 512
        return result_count

    def _compile_vararg_count_into(self, dest_reg: int) -> None:
        self.emit(Opcode.GETGLOBAL, dest_reg, 0, 510)
        count_key = self.env.constants.add("n")
        self._emit_gettable_k(dest_reg, dest_reg, count_key)

    def _negate_boolean_in_place(self, reg: int) -> None:
        """Dao gia tri boolean DANG CO SAN trong `reg` (gia dinh reg CHAC
        CHAN la True/False, vd vua ra tu EQ/LT - KHONG phai 1 gia tri Lua
        "truthy" tong quat bat ky). Dung TEST (xem opcode.py) + LOADK.
        """
        idx_falsy = self.emit(Opcode.TEST, reg, 0, 0)  # falsy (=False) -> nhay toi SET_TRUE
        self._load_const(reg, False)                    # truthy (=True) -> ket qua la False
        idx_end = self.emit(Opcode.JMP, 0, 0, 0)
        set_true_pos = len(self.code)
        self.patch_jump(idx_falsy, set_true_pos)
        self._load_const(reg, True)
        end_pos = len(self.code)
        self.patch_jump(idx_end, end_pos)

    # ========================================================================
    # EXPRESSION
    # ========================================================================

    def _compile_into_fresh_reg(self, expr: Expr) -> int:
        """Cap phat 1 thanh ghi MOI (ngay sau thanh ghi vua cap phat gan
        nhat), dinh gia `expr` vao do, roi GIAI PHONG MOI thanh ghi tam
        sinh ra TRONG LUC dinh gia (chi giu lai thanh ghi ket qua).

        PHAI dung ham nay (thay vi tu `alloc_temp()` + `compile_expr_into`
        roi ke tiep alloc_temp() khac ngay) O BAT KY DAU can xay dung 1
        DAY THANH GHI LIEN TUC - tham so goi ham (CALL/CALL_GLOBAL/
        CALL_METHOD) hay toan hang CONCAT. Neu khong giai phong tam ngay,
        1 bieu thuc con phuc tap (vd mot hang so dung lam chi so bang, 1
        phep toan long nhau) se de lai thanh ghi tam CHUA giai phong, lam
        LECH vi tri cap phat cua PHAN TU KE TIEP trong day - pha vo tinh
        lien tuc ma cac opcode tren bat buoc phai co.

        Day KHONG phai gia thuyet ly thuyet: day CHINH LA nguyen nhan 1
        bug thuc su tung phat hien qua test end-to-end (chay bang Lua
        that) - `print(t[1], t[2], t.name)` cho ket qua SAI vi tham so
        thu 2 tro di bi lech thanh ghi, do buoc tinh chi so hang so `1`
        cho `t[1]` chiem mat 1 thanh ghi ma khong giai phong.
        """
        r = self.env.registers.alloc_temp()
        sub_mark = self.env.registers.mark()
        self.compile_expr_into(expr, r)
        self.env.registers.release_to(sub_mark)
        return r

    def compile_expr_to_any_reg(self, expr: Expr) -> int:
        """Dinh gia `expr`, tra ve 1 thanh ghi CHUA ket qua. Toi uu: neu
        expr la 1 bien local, tra thang ve thanh ghi cua no (khong MOVE
        thua). Nguoc lai, cap phat 1 thanh ghi TAM moi.
        """
        if isinstance(expr, NameExpr):
            res = self.env.resolve_name(expr.name)
            if res.kind == "local" and not self._local_is_captured(res.register):
                return res.register
        reg = self.env.registers.alloc_temp()
        self.compile_expr_into(expr, reg)
        return reg

    def compile_expr_into(self, expr: Expr, dest_reg: int) -> None:
        """Dinh gia `expr`, dam bao KET QUA CUOI CUNG nam trong `dest_reg`.

        QUY UOC QUAN TRONG cho nguoi goi ham nay: `dest_reg` PHAI da duoc
        cap phat TRUOC khi goi (vd qua alloc()/alloc_temp()), va MOI thanh
        ghi tam ham nay cap phat them trong luc dinh gia se nam SAU
        `dest_reg` trong thu tu cap phat - nho vay cac ham con co the an
        toan release_to() ve lai moc sau `dest_reg` ma khong lam mat no.
        """
        if isinstance(expr, NilExpr):
            self._load_const(dest_reg, None)
        elif isinstance(expr, TrueExpr):
            self._load_const(dest_reg, True)
        elif isinstance(expr, FalseExpr):
            self._load_const(dest_reg, False)
        elif isinstance(expr, NumberExpr):
            if isinstance(expr.value, str):
                raise CompilerError(f"So khong hop le: {expr.raw!r}", expr.line, expr.col)
            self._load_const(dest_reg, expr.value)
        elif isinstance(expr, StringExpr):
            self._load_const(dest_reg, expr.value)
        elif isinstance(expr, NameExpr):
            self._compile_name_into(expr, dest_reg)
        elif isinstance(expr, IndexExpr):
            base_mark = self.env.registers.mark()
            obj_reg = self.compile_expr_to_any_reg(expr.obj)
            kidx = self.env.name_constant(expr.field)
            self._emit_gettable_k(dest_reg, obj_reg, kidx)
            self.env.registers.release_to(base_mark)
        elif isinstance(expr, IndexBracketExpr):
            base_mark = self.env.registers.mark()
            obj_reg = self.compile_expr_to_any_reg(expr.obj)
            key_reg = self.compile_expr_to_any_reg(expr.key)
            self.emit(Opcode.GETTABLE, dest_reg, obj_reg, key_reg)
            self.env.registers.release_to(base_mark)
        elif isinstance(expr, BinOpExpr):
            self._compile_binop_into(expr, dest_reg)
        elif isinstance(expr, IfExpr):
            self._compile_if_expr_into(expr, dest_reg)
        elif isinstance(expr, UnOpExpr):
            self._compile_unop_into(expr, dest_reg)
        elif isinstance(expr, ParenExpr):
            # '(' expr ')' trong Lua ep 1 loi goi ham/vararg tra nhieu gia
            # tri xuong CON 1 - khop dung voi gia dinh "luon 1 gia tri" cua
            # compiler ban v1 nay, nen chi can dich bieu thuc ben trong.
            self.compile_expr_into(expr.expr, dest_reg)
        elif isinstance(expr, TypeCastExpr):
            # '::' Type chi co y nghia luc bien dich (kiem tra kieu cua
            # Luau) - khong sinh lenh runtime nao; annotations.py se lot
            # han ca node nay o buoc tien xu ly, day chi la du phong.
            self.compile_expr_into(expr.expr, dest_reg)
        elif isinstance(expr, CallExpr):
            if self._is_vararg_count_call(expr):
                self._compile_vararg_count_into(dest_reg)
            else:
                self._compile_call(expr, dest_reg, want_results=1)
        elif isinstance(expr, MethodCallExpr):
            self._compile_method_call(expr, dest_reg, want_results=1)
        elif isinstance(expr, TableConstructorExpr):
            self._compile_table_constructor(expr, dest_reg)
        elif isinstance(expr, FunctionExpr):
            self._compile_closure(expr.body, dest_reg, name="<anonymous>")
        elif isinstance(expr, VarargExpr):
            self.emit(Opcode.GETGLOBAL, dest_reg, 0, 510)
        else:
            raise CompilerError(f"Loai bieu thuc chua duoc ho tro: {type(expr).__name__}",
                                 expr.line, expr.col)

    def _compile_name_into(self, expr: NameExpr, dest_reg: int) -> None:
        res = self.env.resolve_name(expr.name)
        if res.kind == "local":
            self._load_local(dest_reg, res.register)
        elif res.kind == "global":
            kidx = self.env.name_constant(expr.name)
            self._emit_getglobal(dest_reg, kidx)
        else:
            self.emit(Opcode.GETGLOBAL, dest_reg, 0, res.register + 1)

    # -- Toan tu hai ngoi ----------------------------------------------------

    def _compile_binop_into(self, expr: BinOpExpr, dest_reg: int) -> None:
        op = expr.op
        if op in ("+", "-", "*", "/"):
            self._compile_arith_into(op, expr.left, expr.right, dest_reg)
        elif op == "..":
            self._compile_concat_into(expr, dest_reg)
        elif op in ("==", "~=", "<", ">", "<=", ">="):
            self._compile_relational_into(op, expr.left, expr.right, dest_reg)
        elif op == "and":
            self.compile_expr_into(expr.left, dest_reg)
            idx = self.emit(Opcode.TEST, dest_reg, 0, 0)  # falsy(left) -> nhay toi END, giu dest=left
            self.compile_expr_into(expr.right, dest_reg)
            self.patch_jump(idx, len(self.code))
        elif op == "or":
            self.compile_expr_into(expr.left, dest_reg)
            idx = self.emit(Opcode.TEST, dest_reg, 1, 0)  # truthy(left) -> nhay toi END, giu dest=left
            self.compile_expr_into(expr.right, dest_reg)
            self.patch_jump(idx, len(self.code))
        elif op in ("%", "^", "//"):
            self._compile_runtime_helper(
                {"%": "_mod", "^": "_pow", "//": "_idiv"}[op],
                [expr.left, expr.right],
                dest_reg,
            )
        else:
            raise CompilerError(
                f"Toan tu nhi phan chua duoc ho tro: '{op}' "
                f"(vd '%', '^' can mo rong ISA hoac dung ham helper runtime)",
                expr.line, expr.col,
            )

    def _compile_arith_into(self, op: str, left: Expr, right: Expr, dest_reg: int) -> None:
        base_mark = self.env.registers.mark()
        left_reg = self.compile_expr_to_any_reg(left)

        if op in ("+", "-", "*") and isinstance(right, NumberExpr) and isinstance(right.value, int) \
                and _IMM_LO <= right.value <= _IMM_HI:
            opc = {"+": Opcode.ADDI, "-": Opcode.SUBI, "*": Opcode.MULI}[op]
            self.emit(opc, dest_reg, left_reg, right.value)
            self.env.registers.release_to(base_mark)
            return

        right_reg = self.compile_expr_to_any_reg(right)
        opc = {"+": Opcode.ADD, "-": Opcode.SUB, "*": Opcode.MUL, "/": Opcode.DIV}[op]
        self.emit(opc, dest_reg, left_reg, right_reg)
        self.env.registers.release_to(base_mark)

    def _flatten_concat(self, expr: Expr) -> List[Expr]:
        """Gom 1 day '..' lien tiep (ket hop phai: a..(b..(c..d))) thanh
        danh sach theo thu tu trai->phai, de dich thanh 1 lenh CONCAT duy
        nhat tren day thanh ghi lien tuc (giong toi uu OP_CONCAT cua Lua).
        """
        if isinstance(expr, BinOpExpr) and expr.op == "..":
            return self._flatten_concat(expr.left) + self._flatten_concat(expr.right)
        return [expr]

    def _compile_concat_into(self, expr: BinOpExpr, dest_reg: int) -> None:
        parts = self._flatten_concat(expr)
        base_mark = self.env.registers.mark()
        regs = [self._compile_into_fresh_reg(part) for part in parts]
        # Cac thanh ghi tren LIEN TUC vi duoc cap phat tuan tu tu base_mark
        # VA moi thanh ghi tam con lai cua tung phan tu da duoc giai phong
        # ngay (xem _compile_into_fresh_reg).
        self.emit(Opcode.CONCAT, dest_reg, regs[0], regs[-1])
        self.env.registers.release_to(base_mark)

    def _compile_relational_into(self, op: str, left: Expr, right: Expr, dest_reg: int) -> None:
        base_mark = self.env.registers.mark()
        left_reg = self.compile_expr_to_any_reg(left)
        right_reg = self.compile_expr_to_any_reg(right)

        if op == "==":
            self.emit(Opcode.EQ, dest_reg, left_reg, right_reg)
        elif op == "~=":
            self.emit(Opcode.EQ, dest_reg, left_reg, right_reg)
            self._negate_boolean_in_place(dest_reg)
        elif op == "<":
            self.emit(Opcode.LT, dest_reg, left_reg, right_reg)
        elif op == ">":
            self.emit(Opcode.LT, dest_reg, right_reg, left_reg)
        elif op == "<=":
            self.emit(Opcode.LT, dest_reg, right_reg, left_reg)  # a<=b <=> not(b<a)
            self._negate_boolean_in_place(dest_reg)
        elif op == ">=":
            self.emit(Opcode.LT, dest_reg, left_reg, right_reg)  # a>=b <=> not(a<b)
            self._negate_boolean_in_place(dest_reg)
        self.env.registers.release_to(base_mark)

    # -- Toan tu mot ngoi ------------------------------------------------

    def _compile_unop_into(self, expr: UnOpExpr, dest_reg: int) -> None:
        if expr.op == "-":
            self._compile_runtime_helper("_neg", [expr.operand], dest_reg)
        elif expr.op == "not":
            self.compile_expr_into(expr.operand, dest_reg)
            idx_falsy = self.emit(Opcode.TEST, dest_reg, 0, 0)  # falsy -> nhay toi SET_TRUE
            self._load_const(dest_reg, False)                   # truthy -> 'not x' = false
            idx_end = self.emit(Opcode.JMP, 0, 0, 0)
            set_true_pos = len(self.code)
            self.patch_jump(idx_falsy, set_true_pos)
            self._load_const(dest_reg, True)                     # falsy -> 'not x' = true
            self.patch_jump(idx_end, len(self.code))
        elif expr.op == "#":
            self._compile_runtime_helper("_len", [expr.operand], dest_reg)
        else:
            raise CompilerError(f"Toan tu mot ngoi chua duoc ho tro: '{expr.op}'", expr.line, expr.col)

    # -- Goi ham / method --------------------------------------------------

    def _compile_call(
        self,
        expr: CallExpr,
        dest_reg: Optional[int],
        want_results: Optional[int],
    ) -> None:
        if self._is_vararg_count_call(expr) and want_results is not None:
            if dest_reg is not None:
                self._compile_vararg_count_into(dest_reg)
            return

        if isinstance(expr.func, NameExpr):
            res = self.env.resolve_name(expr.func.name)
            if (
                res.kind == "global"
                and want_results is not None
                and want_results <= 1
                and self.env.name_constant(expr.func.name) < 1024
                and not self._has_trailing_vararg(expr)
                and not self._has_trailing_call(expr)
            ):
                self._compile_call_global(expr, dest_reg)
                return

        base_mark = self.env.registers.mark()
        func_reg = self._compile_into_fresh_reg(expr.func)
        tail_kind = (
            "vararg" if self._has_trailing_vararg(expr)
            else "call" if self._has_trailing_call(expr)
            else None
        )
        call_args = expr.args[:-1] if tail_kind else expr.args
        for arg in call_args:
            self._compile_into_fresh_reg(arg)
        if tail_kind == "call":
            tail_reg = self.env.registers.alloc_temp()
            self._compile_open_call_argument(expr.args[-1], tail_reg)
        nargs = len(call_args)
        result_count = self._call_result_operand(want_results, tail_kind)
        argument_count = nargs + 1
        if tail_kind == "call":
            if argument_count > 255:
                raise CompilerError(
                    "A function call with an expanded trailing call supports at most 254 fixed arguments.",
                    expr.line,
                    expr.col,
                )
            argument_count += 256
        self.emit(Opcode.CALL, func_reg, argument_count, result_count)
        if dest_reg is not None:
            if want_results is None:
                if dest_reg != func_reg:
                    self.emit(Opcode.MOVE, dest_reg, func_reg)
            else:
                for result_offset in range(want_results):
                    if dest_reg + result_offset != func_reg + result_offset:
                        self.emit(
                            Opcode.MOVE,
                            dest_reg + result_offset,
                            func_reg + result_offset,
                        )
        self.env.registers.release_to(base_mark)

    def _compile_call_global(self, expr: CallExpr, dest_reg: Optional[int]) -> None:
        if not isinstance(expr.func, NameExpr):
            raise CompilerError(  # loi noi bo: ham goi sai, _compile_call da loc truoc do
                "Loi noi bo: _compile_call_global chi duoc goi khi expr.func la NameExpr",
                expr.line, expr.col,
            )
        kidx = self.env.name_constant(expr.func.name)
        base_mark = self.env.registers.mark()
        result_reg = self.env.registers.alloc_temp()
        for arg in expr.args:
            self._compile_into_fresh_reg(arg)
        nargs = len(expr.args)
        self.emit(
            Opcode.CALL_GLOBAL,
            result_reg,
            kidx % 512,
            nargs + 1 + (512 if kidx >= 512 else 0),
        )
        if dest_reg is not None and dest_reg != result_reg:
            self.emit(Opcode.MOVE, dest_reg, result_reg)
        self.env.registers.release_to(base_mark)

    def _compile_method_call(
        self,
        expr: MethodCallExpr,
        dest_reg: Optional[int],
        want_results: Optional[int],
    ) -> None:
        kidx = self.env.name_constant(expr.method)
        base_mark = self.env.registers.mark()
        tail_kind = (
            "vararg" if self._has_trailing_vararg(expr)
            else "call" if self._has_trailing_call(expr)
            else None
        )
        if kidx >= 1024 or want_results is None or want_results > 1 or tail_kind is not None:
            key_reg = None
            if kidx >= 2048:
                key_reg = self.env.registers.alloc_temp()
                self._load_const(key_reg, self.env.constants.get(kidx))
            func_reg = self.env.registers.alloc_temp()
            obj_reg = self.env.registers.alloc_temp()
            self.compile_expr_into(expr.obj, obj_reg)
            if key_reg is None:
                self._emit_gettable_k(func_reg, obj_reg, kidx)
            else:
                self.emit(Opcode.GETTABLE, func_reg, obj_reg, key_reg)
            call_args = expr.args[:-1] if tail_kind else expr.args
            for arg in call_args:
                self._compile_into_fresh_reg(arg)
            if tail_kind == "call":
                tail_reg = self.env.registers.alloc_temp()
                self._compile_open_call_argument(expr.args[-1], tail_reg)
            argument_count = len(call_args) + 2
            if tail_kind == "call":
                if argument_count > 255:
                    raise CompilerError(
                        "A method call with an expanded trailing call supports at most 253 explicit arguments.",
                        expr.line,
                        expr.col,
                    )
                argument_count += 256
            self.emit(
                Opcode.CALL,
                func_reg,
                argument_count,
                self._call_result_operand(want_results, tail_kind),
            )
            if dest_reg is not None:
                if want_results is None:
                    if dest_reg != func_reg:
                        self.emit(Opcode.MOVE, dest_reg, func_reg)
                else:
                    for result_offset in range(want_results):
                        if dest_reg + result_offset != func_reg + result_offset:
                            self.emit(
                                Opcode.MOVE,
                                dest_reg + result_offset,
                                func_reg + result_offset,
                            )
            self.env.registers.release_to(base_mark)
            return

        obj_reg = self._compile_into_fresh_reg(expr.obj)
        for arg in expr.args:
            self._compile_into_fresh_reg(arg)
        nargs = len(expr.args)
        self.emit(
            Opcode.CALL_METHOD,
            obj_reg,
            kidx % 512,
            nargs + 1 + (512 if kidx >= 512 else 0),
        )
        if dest_reg is not None and dest_reg != obj_reg:
            self.emit(Opcode.MOVE, dest_reg, obj_reg)
        self.env.registers.release_to(base_mark)

    # -- Table constructor -------------------------------------------------

    def _compile_table_constructor(self, expr: TableConstructorExpr, dest_reg: int) -> None:
        if (
            len(expr.fields) == 1
            and expr.fields[0].key is None
            and isinstance(expr.fields[0].value, VarargExpr)
        ):
            self.emit(Opcode.GETGLOBAL, dest_reg, 0, 510)
            return
        size_hint = min(len(expr.fields), 500)  # chi la goi y, khong can chinh xac tuyet doi
        self.emit(Opcode.NEWTABLE, dest_reg, size_hint, 0)

        pos_index = 1  # mang Lua bat dau tu chi so 1
        for f in expr.fields:
            base_mark = self.env.registers.mark()
            if f.key is None:
                val_reg = self.env.registers.alloc_temp()
                self.compile_expr_into(f.value, val_reg)
                kidx = self.env.constants.add(pos_index)
                self._emit_settable_k(dest_reg, kidx, val_reg)
                pos_index += 1
            elif not f.is_computed_key:
                val_reg = self.env.registers.alloc_temp()
                self.compile_expr_into(f.value, val_reg)
                kidx = self.env.constants.add(f.key)  # f.key la str (ten thuoc tinh)
                self._emit_settable_k(dest_reg, kidx, val_reg)
            else:
                key_reg = self.env.registers.alloc_temp()
                self.compile_expr_into(f.key, key_reg)
                val_reg = self.env.registers.alloc_temp()
                self.compile_expr_into(f.value, val_reg)
                self.emit(Opcode.SETTABLE, dest_reg, key_reg, val_reg)
            self.env.registers.release_to(base_mark)

    # -- Closure -------------------------------------------------------------

    def _compile_closure(self, func_body: FunctionBody, dest_reg: int, name: str) -> None:
        proto_idx = self.compiler.new_proto_slot()
        child_env = self.env.child()
        child_fc = _FuncCompiler(child_env, self.compiler, name, proto_idx)

        child_env.registers.push_scope()
        num_named_params = 0
        param_registers: List[int] = []
        for p in func_body.params:
            if p.is_vararg:
                continue  # vararg: chi khai bao, chua co du lieu (xem gioi han o dau file)
            param_registers.append(child_env.registers.alloc(p.name))
            num_named_params += 1
        for register in param_registers:
            child_fc._box_local(register)

        for s in func_body.body.statements:
            child_fc._compile_stmt(s)

        # LUON LUON them 1 RETURN ket thuc vo dieu kien, KHONG dua vao
        # kiem tra "lenh cuoi cung da emit co phai RETURN khong" - kiem
        # tra do SAI vi chi phan anh lenh cuoi cung VE MAT VAN BAN, khong
        # phan anh MOI DUONG THUC THI co thuc su ket thuc bang return hay
        # khong. Vi du 'if x then return 1 end' (khong co else/return sau
        # do): lenh cuoi cung duoc emit LA 1 RETURN (tu trong nhanh if),
        # nhung khi x=false, luong thuc thi NHAY QUA nhanh if va roi
        # XUONG DUOI no - tuc la can 1 RETURN o DAY, ngay sau if, chu
        # khong phai dua vao RETURN ben trong nhanh if (ma luong fall-
        # through khong he di qua). Neu bo qua, bytecode se nhay ra NGOAI
        # mang lenh khi gap truong hop fall-through nay (da xac nhan bang
        # test thuc te). Them RETURN du thua (truong hop ham da return o
        # MOI nhanh) chi la 1 lenh "chet" khong bao gio chay toi - vo hai.
        child_fc.emit(Opcode.RETURN, 0, 1, 0)

        child_env.registers.pop_scope()

        self.compiler.captured_registers[proto_idx] = set(child_env.captured_registers)
        proto = FunctionProto(
            name=name,
            num_params=num_named_params,
            is_vararg=func_body.is_vararg,
            upvalues=list(child_env.upvalues),
            code=child_fc.code,
            constants=child_env.constants.to_list(),
            max_registers=child_env.registers.max_registers_used,
        )
        self.compiler.protos[proto_idx] = proto
        self.emit(Opcode.CLOSURE, dest_reg, proto_idx % 512, proto_idx // 512)

    # ========================================================================
    # STATEMENT
    # ========================================================================

    def compile_body(self, block: Block) -> None:
        for s in block.statements:
            self._compile_stmt(s)

    def _compile_block_new_scope(self, block: Block) -> None:
        self.env.registers.push_scope()
        for s in block.statements:
            self._compile_stmt(s)
        self.env.registers.pop_scope()

    def _compile_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, LocalAssignStmt):
            self._compile_local_assign(stmt)
        elif isinstance(stmt, AssignStmt):
            self._compile_assign(stmt)
        elif isinstance(stmt, CompoundAssignStmt):
            self._compile_compound_assign(stmt)
        elif isinstance(stmt, CallStmt):
            base_mark = self.env.registers.mark()
            if isinstance(stmt.call, CallExpr):
                self._compile_call(stmt.call, dest_reg=None, want_results=0)
            else:
                self._compile_method_call(stmt.call, dest_reg=None, want_results=0)
            self.env.registers.release_to(base_mark)
        elif isinstance(stmt, DoStmt):
            self._compile_block_new_scope(stmt.body)
        elif isinstance(stmt, WhileStmt):
            self._compile_while(stmt)
        elif isinstance(stmt, RepeatStmt):
            self._compile_repeat(stmt)
        elif isinstance(stmt, IfStmt):
            self._compile_if(stmt)
        elif isinstance(stmt, NumericForStmt):
            self._compile_numeric_for(stmt)
        elif isinstance(stmt, GenericForStmt):
            self._compile_generic_for(stmt)
        elif isinstance(stmt, FunctionDeclStmt):
            self._compile_function_decl(stmt)
        elif isinstance(stmt, LocalFunctionStmt):
            self._compile_local_function(stmt)
        elif isinstance(stmt, ReturnStmt):
            self._compile_return(stmt)
        elif isinstance(stmt, BreakStmt):
            if not self.loop_stack:
                raise CompilerError("'break' o ngoai vong lap", stmt.line, stmt.col)
            idx = self.emit(Opcode.JMP, 0, 0, 0)
            self.loop_stack[-1].break_patches.append(idx)
        elif isinstance(stmt, ContinueStmt):
            if not self.loop_stack:
                raise CompilerError("'continue' o ngoai vong lap", stmt.line, stmt.col)
            idx = self.emit(Opcode.JMP, 0, 0, 0)
            self.loop_stack[-1].continue_patches.append(idx)
        elif isinstance(stmt, TypeAliasStmt):
            pass  # type alias chi ton tai luc bien dich, khong sinh lenh nao
        elif isinstance(stmt, (GotoStmt, LabelStmt)):
            raise CompilerError(
                "Chua ho tro goto/label - can 1 buoc resolve nhan rieng (2-pass) "
                "de xu ly nhay toi vi tri bat ky.",
                stmt.line, stmt.col,
            )
        else:
            raise CompilerError(f"Statement chua duoc ho tro: {type(stmt).__name__}", stmt.line, stmt.col)

    # -- local / assign ----------------------------------------------------

    def _compile_local_assign(self, stmt: LocalAssignStmt) -> None:
        """LUU Y QUAN TRONG VE NGU NGHIA: theo Lua, ten local MOI chi co
        hieu luc SAU KHI toan bo danh sach gia tri ben phai da duoc dinh
        gia xong - BEN TRONG cac bieu thuc gia tri do, moi tham chieu toi
        CHINH ten dang khai bao phai tro toi bien CU (outer scope/global),
        KHONG PHAI bien moi chua khoi tao. Vi du:
            local print = print        -- ve phai phai la GLOBAL print cu
            local a, b = b, a          -- hoan vi dung bang gia tri CU cua a,b

        Vi vay o day ta dinh gia TOAN BO gia tri vao thanh ghi TAM TRUOC,
        chi "dang ky" (bind) ten vao scope SAU KHI da dinh gia xong het -
        nho vay resolve_name() trong luc dinh gia se KHONG thay ten moi
        (chua duoc bind) nen tu dong tra ve dung bien cu. Thanh ghi tam
        duoc "thang chuc" thanh thanh ghi local chinh thuc qua bind(),
        KHONG can MOVE hay cap phat them - khong ton kem gi so voi cach cu.
        """
        n_names = len(stmt.names)
        value_regs = [self.env.registers.alloc_temp() for _ in range(n_names)]
        self._compile_assignment_values(stmt.values, value_regs)

        for i, name in enumerate(stmt.names):
            self.env.registers.bind(name, value_regs[i])
            self._box_local(value_regs[i])

    def _compile_assignment_values(
        self,
        values: List[Expr],
        target_regs: List[int],
    ) -> None:
        """Compile an expression list, expanding only its final call to fit targets."""
        target_count = len(target_regs)
        for index, value in enumerate(values):
            is_final_call = index == len(values) - 1 and isinstance(
                value, (CallExpr, MethodCallExpr)
            )
            if is_final_call:
                result_count = max(0, target_count - index)
                if result_count == 0:
                    if isinstance(value, CallExpr):
                        self._compile_call(value, None, 0)
                    else:
                        self._compile_method_call(value, None, 0)
                elif isinstance(value, CallExpr):
                    self._compile_call(value, target_regs[index], result_count)
                else:
                    self._compile_method_call(value, target_regs[index], result_count)
            elif index < target_count:
                self.compile_expr_into(value, target_regs[index])
            else:
                extra_mark = self.env.registers.mark()
                extra_reg = self.env.registers.alloc_temp()
                self.compile_expr_into(value, extra_reg)
                self.env.registers.release_to(extra_mark)

        if not values or not isinstance(values[-1], (CallExpr, MethodCallExpr)):
            for index in range(len(values), target_count):
                self._load_const(target_regs[index], None)

    def _assign_reg_to_target(self, target: Expr, value_reg: int) -> None:
        if isinstance(target, NameExpr):
            res = self.env.resolve_name(target.name)
            if res.kind == "local":
                self._store_local(value_reg, res.register)
            elif res.kind == "global":
                kidx = self.env.name_constant(target.name)
                self._emit_setglobal(value_reg, kidx)
            else:
                self.emit(Opcode.SETGLOBAL, value_reg, 0, res.register + 1)
        elif isinstance(target, IndexExpr):
            obj_reg = self.compile_expr_to_any_reg(target.obj)
            kidx = self.env.name_constant(target.field)
            self._emit_settable_k(obj_reg, kidx, value_reg)
        elif isinstance(target, IndexBracketExpr):
            obj_reg = self.compile_expr_to_any_reg(target.obj)
            key_reg = self.compile_expr_to_any_reg(target.key)
            self.emit(Opcode.SETTABLE, obj_reg, key_reg, value_reg)
        else:
            raise CompilerError(f"Muc tieu gan gia tri khong hop le: {type(target).__name__}",
                                 target.line, target.col)

    def _compile_assign_to_target(self, target: Expr, value_expr: Expr) -> None:
        if isinstance(target, NameExpr):
            res = self.env.resolve_name(target.name)
            if res.kind == "local":
                if self._local_is_captured(res.register):
                    value_reg = self.env.registers.alloc_temp()
                    self.compile_expr_into(value_expr, value_reg)
                    self._store_local(value_reg, res.register)
                    self.env.registers.release_to(value_reg)
                else:
                    self.compile_expr_into(value_expr, res.register)
                return
        value_reg = self.compile_expr_to_any_reg(value_expr)
        self._assign_reg_to_target(target, value_reg)

    def _compile_assign(self, stmt: AssignStmt) -> None:
        n_targets = len(stmt.targets)
        n_values = len(stmt.values)

        if n_targets == 1:
            # Truong hop pho bien nhat (1 muc tieu): khong co rui ro tham
            # chieu cheo giua nhieu muc tieu/gia tri, dung thang duong toi
            # uu (co the dich truc tiep vao thanh ghi local, tiet kiem 1
            # MOVE so voi duong tong quat ben duoi).
            value_expr = stmt.values[0] if n_values >= 1 else NilExpr(line=stmt.line, col=stmt.col)
            self._compile_assign_to_target(stmt.targets[0], value_expr)
            if n_values > 1:
                base_mark = self.env.registers.mark()
                for i in range(1, n_values):
                    tmp = self.env.registers.alloc_temp()
                    self.compile_expr_into(stmt.values[i], tmp)
                self.env.registers.release_to(base_mark)
            return

        # QUAN TRONG VE NGU NGHIA (giong LocalAssignStmt): PHAI dinh gia
        # TOAN BO danh sach gia tri TRUOC khi gan vao BAT KY muc tieu nao.
        # Neu gan tuan tu tung cap mot (gan muc tieu[0] roi moi dinh gia
        # muc tieu[1]), cac pattern hoan vi rat pho bien se SAI, vi du:
        #   a, b = b, a            -- can gia tri CU cua a va b
        #   t[i], t[j] = t[j], t[i] -- can gia tri CU cua t[i], t[j]
        # (neu gan a truoc, luc dinh gia gia tri cho b se doc nham a MOI).
        base_mark = self.env.registers.mark()
        value_regs = [self.env.registers.alloc_temp() for _ in range(n_targets)]
        self._compile_assignment_values(stmt.values, value_regs)

        for i, target in enumerate(stmt.targets):
            self._assign_reg_to_target(target, value_regs[i])

        self.env.registers.release_to(base_mark)

    def _compile_compound_assign(self, stmt: CompoundAssignStmt) -> None:
        target = stmt.target

        if isinstance(target, NameExpr):
            res = self.env.resolve_name(target.name)
            if res.kind == "local":
                if self._local_is_captured(res.register):
                    base_mark = self.env.registers.mark()
                    value_reg = self.env.registers.alloc_temp()
                    self._load_local(value_reg, res.register)
                    self._compile_binop_inplace(stmt.op, value_reg, stmt.value)
                    self._store_local(value_reg, res.register)
                    self.env.registers.release_to(base_mark)
                else:
                    self._compile_binop_inplace(stmt.op, res.register, stmt.value)
                return
            if res.kind == "global":
                base_mark = self.env.registers.mark()
                cur_reg = self.env.registers.alloc_temp()
                kidx = self.env.name_constant(target.name)
                self._emit_getglobal(cur_reg, kidx)
                self._compile_binop_inplace(stmt.op, cur_reg, stmt.value)
                self._emit_setglobal(cur_reg, kidx)
                self.env.registers.release_to(base_mark)
                return
            base_mark = self.env.registers.mark()
            value_reg = self.env.registers.alloc_temp()
            self.emit(Opcode.GETGLOBAL, value_reg, 0, res.register + 1)
            self._compile_binop_inplace(stmt.op, value_reg, stmt.value)
            self.emit(Opcode.SETGLOBAL, value_reg, 0, res.register + 1)
            self.env.registers.release_to(base_mark)
            return

        base_mark = self.env.registers.mark()
        if isinstance(target, IndexExpr):
            obj_reg = self.compile_expr_to_any_reg(target.obj)
            kidx = self.env.name_constant(target.field)
            cur_reg = self.env.registers.alloc_temp()
            self._emit_gettable_k(cur_reg, obj_reg, kidx)
            self._compile_binop_inplace(stmt.op, cur_reg, stmt.value)
            self._emit_settable_k(obj_reg, kidx, cur_reg)
        elif isinstance(target, IndexBracketExpr):
            obj_reg = self.compile_expr_to_any_reg(target.obj)
            key_reg = self.compile_expr_to_any_reg(target.key)
            cur_reg = self.env.registers.alloc_temp()
            self.emit(Opcode.GETTABLE, cur_reg, obj_reg, key_reg)
            self._compile_binop_inplace(stmt.op, cur_reg, stmt.value)
            self.emit(Opcode.SETTABLE, obj_reg, key_reg, cur_reg)
        else:
            raise CompilerError(f"Muc tieu gan gop khong hop le: {type(target).__name__}",
                                 stmt.line, stmt.col)
        self.env.registers.release_to(base_mark)

    def _compile_binop_inplace(self, op: str, cur_reg: int, value_expr: Expr) -> None:
        """cur_reg = cur_reg OP value_expr, ghi de KET QUA vao chinh cur_reg."""
        base_mark = self.env.registers.mark()
        if op == "..":
            left_tmp = self.env.registers.alloc_temp()
            self.emit(Opcode.MOVE, left_tmp, cur_reg)
            right_tmp = self.env.registers.alloc_temp()
            self.compile_expr_into(value_expr, right_tmp)
            self.emit(Opcode.CONCAT, cur_reg, left_tmp, right_tmp)
            self.env.registers.release_to(base_mark)
            return

        if op in ("%", "^", "//"):
            base_mark = self.env.registers.mark()
            helper_reg = self.env.registers.alloc_temp()
            helper_name = {"%": "_mod", "^": "_pow", "//": "_idiv"}[op]
            self._emit_vm_helper(helper_reg, helper_name)
            left_arg = self.env.registers.alloc_temp()
            self.emit(Opcode.MOVE, left_arg, cur_reg)
            right_arg = self.env.registers.alloc_temp()
            self.compile_expr_into(value_expr, right_arg)
            self.emit(Opcode.CALL, helper_reg, 3, 2)
            self.emit(Opcode.MOVE, cur_reg, helper_reg)
            self.env.registers.release_to(base_mark)
            return

        if op not in ("+", "-", "*", "/"):
            raise CompilerError(f"Toan tu gan gop chua duoc ho tro: '{op}'")

        if op in ("+", "-", "*") and isinstance(value_expr, NumberExpr) \
                and isinstance(value_expr.value, int) and _IMM_LO <= value_expr.value <= _IMM_HI:
            opc = {"+": Opcode.ADDI, "-": Opcode.SUBI, "*": Opcode.MULI}[op]
            self.emit(opc, cur_reg, cur_reg, value_expr.value)
            return

        base_mark = self.env.registers.mark()
        val_reg = self.compile_expr_to_any_reg(value_expr)
        opc = {"+": Opcode.ADD, "-": Opcode.SUB, "*": Opcode.MUL, "/": Opcode.DIV}[op]
        self.emit(opc, cur_reg, cur_reg, val_reg)
        self.env.registers.release_to(base_mark)

    # -- Dieu kien (dung chung cho if / while / repeat) ---------------------

    def _compile_cond_jump_if_false(self, cond: Expr) -> List[int]:
        """Dich 1 bieu thuc dieu kien; tra ve danh sach chi so lenh JMP
        con CHUA patch - khi PATCH toi dau, lenh se nhay toi do NEU dieu
        kien SAI (falsy). Toi uu rieng cho so sanh truc tiep (==,~=,<,>,
        <=,>=) bang cac opcode *_JMP (PHU DINH dieu kien, khoi can vat
        chat hoa gia tri boolean); truong hop con lai dinh gia vao 1
        thanh ghi roi dung TEST.
        """
        if isinstance(cond, BinOpExpr) and cond.op in ("==", "~=", "<", ">", "<=", ">="):
            base_mark = self.env.registers.mark()
            left = self.compile_expr_to_any_reg(cond.left)
            right = self.compile_expr_to_any_reg(cond.right)
            if cond.op == "==":
                idx = self.emit(Opcode.NEQ_JMP, left, right, 0)
            elif cond.op == "~=":
                idx = self.emit(Opcode.EQ_JMP, left, right, 0)
            elif cond.op == "<":
                idx = self.emit(Opcode.LE_JMP, right, left, 0)   # phu dinh cua < la <=(doi cho)
            elif cond.op == ">":
                idx = self.emit(Opcode.LE_JMP, left, right, 0)
            elif cond.op == "<=":
                idx = self.emit(Opcode.LT_JMP, right, left, 0)   # phu dinh cua <= la <(doi cho)
            else:  # '>='
                idx = self.emit(Opcode.LT_JMP, left, right, 0)
            self.env.registers.release_to(base_mark)
            return [idx]

        base_mark = self.env.registers.mark()
        reg = self.compile_expr_to_any_reg(cond)
        idx = self.emit(Opcode.TEST, reg, 0, 0)  # B=0: nhay khi falsy(R[A])
        self.env.registers.release_to(base_mark)
        return [idx]

    def _compile_if_expr_into(self, expr: IfExpr, dest_reg: int) -> None:
        end_patches: List[int] = []
        for clause in expr.clauses:
            false_patches = self._compile_cond_jump_if_false(clause.cond)
            self.compile_expr_into(clause.value, dest_reg)
            end_patches.append(self.emit(Opcode.JMP, 0, 0, 0))
            self.patch_jumps(false_patches, len(self.code))

        self.compile_expr_into(expr.else_value, dest_reg)
        self.patch_jumps(end_patches, len(self.code))

    # -- if / while / repeat / numeric-for -----------------------------

    def _compile_if(self, stmt: IfStmt) -> None:
        end_patches: List[int] = []
        n = len(stmt.clauses)
        for i, clause in enumerate(stmt.clauses):
            false_patches = self._compile_cond_jump_if_false(clause.cond)
            self._compile_block_new_scope(clause.body)
            is_last_branch = (i == n - 1) and (stmt.else_body is None)
            if not is_last_branch:
                end_patches.append(self.emit(Opcode.JMP, 0, 0, 0))
            self.patch_jumps(false_patches, len(self.code))
        if stmt.else_body is not None:
            self._compile_block_new_scope(stmt.else_body)
        self.patch_jumps(end_patches, len(self.code))

    def _compile_while(self, stmt: WhileStmt) -> None:
        cond_start = len(self.code)
        loop_ctx = LoopContext()
        self.loop_stack.append(loop_ctx)

        false_patches = self._compile_cond_jump_if_false(stmt.cond)
        self._compile_block_new_scope(stmt.body)

        self.patch_jumps(loop_ctx.continue_patches, cond_start)
        idx_back = self.emit(Opcode.JMP, 0, 0, 0)
        self.patch_jump(idx_back, cond_start)

        end_pos = len(self.code)
        self.patch_jumps(false_patches, end_pos)
        self.patch_jumps(loop_ctx.break_patches, end_pos)
        self.loop_stack.pop()

    def _compile_repeat(self, stmt: RepeatStmt) -> None:
        body_start = len(self.code)
        loop_ctx = LoopContext()
        self.loop_stack.append(loop_ctx)

        # Scope mo truoc than lap va CHI dong SAU khi dich xong dieu kien
        # 'until' - dung ngu nghia Lua (bien local trong than van thay duoc
        # trong dieu kien). Xem ghi chu trong nodes.RepeatStmt.
        self.env.registers.push_scope()
        for s in stmt.body.statements:
            self._compile_stmt(s)

        body_end = len(self.code)
        self.patch_jumps(loop_ctx.continue_patches, body_end)

        false_patches = self._compile_cond_jump_if_false(stmt.cond)  # dieu kien SAI -> lap lai
        self.env.registers.pop_scope()

        self.patch_jumps(false_patches, body_start)
        end_pos = len(self.code)
        self.patch_jumps(loop_ctx.break_patches, end_pos)
        self.loop_stack.pop()

    def _compile_numeric_for(self, stmt: NumericForStmt) -> None:
        self.env.registers.push_scope()

        idx_reg = self.env.registers.alloc_temp()
        self.compile_expr_into(stmt.start, idx_reg)
        limit_reg = self.env.registers.alloc_temp()
        self.compile_expr_into(stmt.stop, limit_reg)
        step_reg = self.env.registers.alloc_temp()
        if stmt.step is not None:
            self.compile_expr_into(stmt.step, step_reg)
        else:
            self._load_const(step_reg, 1)
        if not (limit_reg == idx_reg + 1 and step_reg == idx_reg + 2):
            # Bat buoc kiem tra THAT (khong dung 'assert' - co the bi strip
            # boi 'python -O', va vi pham dieu nay se sinh FORPREP/FORLOOP
            # sai ma KHONG he bao loi ro rang, dung dieu file nay co gang
            # tranh xuyen suot). Day la loi logic noi bo cua compiler (bug),
            # khong phai loi cu phap cua nguoi dung.
            raise CompilerError(
                "Loi noi bo: index/limit/step cua numeric-for khong nam o 3 "
                "thanh ghi LIEN TUC (yeu cau cua FORPREP/FORLOOP) - co the do "
                "mot bieu thuc con lam lech bo dem cap phat thanh ghi.",
                stmt.line, stmt.col,
            )

        loop_var_reg = self.env.registers.alloc_temp()
        self.env.registers.bind(stmt.var, loop_var_reg)

        forprep_idx = self.emit(Opcode.FORPREP, idx_reg, 0, 0)

        body_start = len(self.code)
        self.emit(Opcode.MOVE, loop_var_reg, idx_reg)
        self._box_local(loop_var_reg)
        loop_ctx = LoopContext()
        self.loop_stack.append(loop_ctx)
        self._compile_block_new_scope(stmt.body)

        forloop_pos = len(self.code)
        self.patch_jumps(loop_ctx.continue_patches, forloop_pos)
        self.emit(Opcode.FORLOOP, idx_reg, 0, 0)
        self.patch_jump(forloop_pos, body_start)

        end_pos = len(self.code)
        self.patch_jump(forprep_idx, end_pos)
        self.patch_jumps(loop_ctx.break_patches, end_pos)
        self.loop_stack.pop()

        self.env.registers.pop_scope()

    def _compile_generic_for(self, stmt: GenericForStmt) -> None:
        iterator_reg = self.env.registers.alloc_temp()
        state_reg = self.env.registers.alloc_temp()
        control_reg = self.env.registers.alloc_temp()

        if len(stmt.exprs) == 1 and isinstance(stmt.exprs[0], CallExpr):
            self._compile_call(stmt.exprs[0], iterator_reg, 3)
            self._normalize_generic_for_iterator(
                iterator_reg, state_reg, control_reg
            )
        elif len(stmt.exprs) == 1 and isinstance(stmt.exprs[0], MethodCallExpr):
            self._compile_generic_for_method(stmt.exprs[0], iterator_reg)
            self._normalize_generic_for_iterator(
                iterator_reg, state_reg, control_reg
            )
        elif len(stmt.exprs) == 1:
            self.compile_expr_into(stmt.exprs[0], iterator_reg)
            self._load_const(state_reg, None)
            self._load_const(control_reg, None)
            self._normalize_generic_for_iterator(
                iterator_reg, state_reg, control_reg
            )
        elif len(stmt.exprs) in (2, 3):
            iterator_reg = self.compile_expr_to_any_reg(stmt.exprs[0])
            state_reg = self.compile_expr_to_any_reg(stmt.exprs[1])
            if len(stmt.exprs) == 3:
                control_reg = self.compile_expr_to_any_reg(stmt.exprs[2])
            else:
                self._load_const(control_reg, None)
            self._normalize_generic_for_iterator(
                iterator_reg, state_reg, control_reg
            )
        else:
            raise CompilerError(
                "Generic-for VM chi ho tro iterator call, iterable don le, "
                "hoac 2-3 gia tri "
                "(iterator, state, control).",
                stmt.line, stmt.col,
            )

        self.env.registers.push_scope()
        loop_start = len(self.code)
        loop_ctx = LoopContext()
        self.loop_stack.append(loop_ctx)

        result_reg = self.env.registers.alloc_temp()
        self.env.registers.alloc_temp()
        for offset, name in enumerate(stmt.names[:2]):
            self.env.registers.bind(name, result_reg + offset)
        fn_reg = self.env.registers.alloc_temp()
        state_arg = self.env.registers.alloc_temp()
        control_arg = self.env.registers.alloc_temp()
        self.emit(Opcode.MOVE, fn_reg, iterator_reg)
        self.emit(Opcode.MOVE, state_arg, state_reg)
        self.emit(Opcode.MOVE, control_arg, control_reg)
        self.emit(Opcode.CALL, fn_reg, 3, 3)
        self.emit(Opcode.MOVE, result_reg, fn_reg)
        self.emit(Opcode.MOVE, result_reg + 1, fn_reg + 1)
        self.emit(Opcode.MOVE, control_reg, result_reg)

        nil_reg = self.env.registers.alloc_temp()
        self._load_const(nil_reg, None)
        is_nil_reg = self.env.registers.alloc_temp()
        self.emit(Opcode.EQ, is_nil_reg, result_reg, nil_reg)
        exit_jump = self.emit(Opcode.TEST, is_nil_reg, 1, 0)

        for offset, _ in enumerate(stmt.names[:2]):
            self._box_local(result_reg + offset)

        self._compile_block_new_scope(stmt.body)
        self.patch_jumps(loop_ctx.continue_patches, loop_start)
        back_jump = self.emit(Opcode.JMP, 0, 0, 0)
        self.patch_jump(back_jump, loop_start)

        end_pos = len(self.code)
        self.patch_jump(exit_jump, end_pos)
        self.patch_jumps(loop_ctx.break_patches, end_pos)
        self.loop_stack.pop()
        self.env.registers.pop_scope()

    def _normalize_generic_for_iterator(
        self,
        iterator_reg: int,
        state_reg: int,
        control_reg: int,
    ) -> None:
        base_mark = self.env.registers.mark()
        function_reg = self.env.registers.alloc_temp()
        self._emit_vm_helper(function_reg, "_iter")
        for source_reg in (iterator_reg, state_reg, control_reg):
            argument_reg = self.env.registers.alloc_temp()
            self.emit(Opcode.MOVE, argument_reg, source_reg)
        self.emit(Opcode.CALL, function_reg, 4, 4)
        for offset, dest_reg in enumerate((iterator_reg, state_reg, control_reg)):
            self.emit(Opcode.MOVE, dest_reg, function_reg + offset)
        self.env.registers.release_to(base_mark)

    def _compile_generic_for_method(
        self,
        expr: MethodCallExpr,
        result_reg: int,
    ) -> None:
        base_mark = self.env.registers.mark()
        method_key = self.env.name_constant(expr.method)
        key_reg = None
        if method_key >= 2048:
            key_reg = self.env.registers.alloc_temp()
            self._load_const(key_reg, self.env.constants.get(method_key))
        function_reg = self.env.registers.alloc_temp()
        object_reg = self.env.registers.alloc_temp()
        self.compile_expr_into(expr.obj, object_reg)
        if key_reg is None:
            self._emit_gettable_k(function_reg, object_reg, method_key)
        else:
            self.emit(Opcode.GETTABLE, function_reg, object_reg, key_reg)
        for argument in expr.args:
            self._compile_into_fresh_reg(argument)
        self.emit(Opcode.CALL, function_reg, len(expr.args) + 2, 4)
        for offset in range(3):
            self.emit(Opcode.MOVE, result_reg + offset, function_reg + offset)
        self.env.registers.release_to(base_mark)

    # -- function decl / return -----------------------------------------

    def _compile_function_decl(self, stmt: FunctionDeclStmt) -> None:
        base_mark = self.env.registers.mark()
        reg = self.env.registers.alloc_temp()
        self._compile_closure(stmt.body, reg, name=".".join(stmt.name_path))

        if len(stmt.name_path) == 1:
            target: Expr = NameExpr(name=stmt.name_path[0], line=stmt.line, col=stmt.col)
        else:
            obj: Expr = NameExpr(name=stmt.name_path[0], line=stmt.line, col=stmt.col)
            for part in stmt.name_path[1:-1]:
                obj = IndexExpr(obj=obj, field=part, line=stmt.line, col=stmt.col)
            target = IndexExpr(obj=obj, field=stmt.name_path[-1], line=stmt.line, col=stmt.col)

        self._assign_reg_to_target(target, reg)
        self.env.registers.release_to(base_mark)

    def _compile_local_function(self, stmt: LocalFunctionStmt) -> None:
        # Register the local before compiling its body so recursive closures
        # capture the same cell.
        reg = self.env.registers.alloc(stmt.name)
        self._box_local(reg)
        value_reg = reg
        if self._local_is_captured(reg):
            value_reg = self.env.registers.alloc_temp()
        self._compile_closure(stmt.body, value_reg, name=stmt.name)
        if value_reg != reg:
            self._store_local(value_reg, reg)
            self.env.registers.release_to(value_reg)

    def _compile_return(self, stmt: ReturnStmt) -> None:
        if not stmt.values:
            self.emit(Opcode.RETURN, 0, 1, 0)
            return
        base_mark = self.env.registers.mark()
        first_reg = self.env.registers.alloc_temp()
        for _ in range(len(stmt.values) - 1):
            self.env.registers.alloc_temp()
        for offset, value in enumerate(stmt.values):
            result_reg = first_reg + offset
            if isinstance(value, CallExpr) and offset == len(stmt.values) - 1:
                self._compile_call(value, result_reg, 1)
            else:
                self.compile_expr_into(value, result_reg)
        self.emit(Opcode.RETURN, first_reg, len(stmt.values) + 1, 0)
        self.env.registers.release_to(base_mark)


# ---------------------------------------------------------------------------
# Compiler - diem vao: quan ly danh sach FunctionProto toan cuc
# ---------------------------------------------------------------------------

class Compiler:
    """Dich 1 chunk (Block goc tra ve boi src.parser.parse()) thanh danh
    sach FunctionProto. CLOSURE opcode tham chieu ham con qua chi so
    trong `self.protos` (danh sach "phang" chung cho toan bo chunk, don
    gian hon so voi moi ham co bang proto rieng cua no).
    """

    def __init__(
        self,
        captured_registers: Optional[dict[int, set[int]]] = None,
        discover_captures: bool = False,
    ):
        self.protos: List[Optional[FunctionProto]] = []
        self.captured_registers = captured_registers or {}
        self.discover_captures = discover_captures

    def new_proto_slot(self) -> int:
        """Danh cho (dat placeholder None) 1 chi so proto, de ham cha co
        the tham chieu TOI no (qua CLOSURE) ngay ca khi ham con chua dich
        xong (se duoc dien vao sau khi dich xong than ham con).
        """
        idx = len(self.protos)
        self.protos.append(None)
        return idx

    def compile_chunk(self, block: Block) -> int:
        """Dich 1 chunk (vd toan bo 1 file .lua) thanh 1 FunctionProto
        goc (ham "main", vararg, 0 tham so). Tra ve CHI SO cua proto goc
        trong self.protos.
        """
        if not self.discover_captures and not self.protos:
            # First pass finds captured registers; the second emits cell accesses.
            discovery = Compiler(discover_captures=True)
            discovery._compile_chunk_once(block)
            captures = {
                proto_idx: set(registers)
                for proto_idx, registers in discovery.captured_registers.items()
            }
            for _ in range(16):
                compiled = Compiler(
                    captured_registers={
                        proto_idx: set(registers)
                        for proto_idx, registers in captures.items()
                    }
                )
                main_idx = compiled._compile_chunk_once(block)
                updated_captures = {
                    proto_idx: set(registers)
                    for proto_idx, registers in compiled.captured_registers.items()
                }
                if updated_captures == captures:
                    self.protos = compiled.protos
                    self.captured_registers = compiled.captured_registers
                    return main_idx
                captures = updated_captures

            raise CompilerError(
                "Capture discovery did not stabilize after 16 compile passes"
            )
        return self._compile_chunk_once(block)

    def _compile_chunk_once(self, block: Block) -> int:
        idx = self.new_proto_slot()
        env = Environment()
        fc = _FuncCompiler(env, self, name="<main>", proto_idx=idx)

        env.registers.push_scope()
        fc.compile_body(block)
        # Xem ghi chu chi tiet trong _compile_closure ve ly do LUON LUON
        # them RETURN o day thay vi chi kiem tra lenh cuoi cung da emit.
        fc.emit(Opcode.RETURN, 0, 1, 0)
        env.registers.pop_scope()

        proto = FunctionProto(
            name="<main>",
            num_params=0,
            is_vararg=True,
            upvalues=list(env.upvalues),
            code=fc.code,
            constants=env.constants.to_list(),
            max_registers=env.registers.max_registers_used,
        )
        self.protos[idx] = proto
        self.captured_registers[idx] = set(env.captured_registers)
        return idx


def compile_chunk(block: Block) -> Tuple[int, List[FunctionProto]]:
    """Tien ich: dich 1 AST chunk, tra ve (chi_so_proto_goc, danh_sach_proto)."""
    compiler = Compiler()
    main_idx = compiler.compile_chunk(block)
    # Tai thoi diem nay moi proto phai da duoc dien day du (khong con None)
    protos: List[FunctionProto] = []
    for i, p in enumerate(compiler.protos):
        if p is None:
            raise CompilerError(f"Proto noi bo #{i} chua duoc dich xong (loi logic compiler)")
        protos.append(p)
    return main_idx, protos


__all__ = ["Compiler", "CompilerError", "FunctionProto", "compile_chunk"]
