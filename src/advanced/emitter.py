# -*- coding: utf-8 -*-
"""
advanced/emitter.py
====================
In (render) cay AST (sau khi da bi bien doi boi naming.py, cff.py, mba.py,
opaque_predicates.py, dead_code.py...) tro lai thanh source code Lua/Luau
hop le, nen ve 1 dong duy nhat.

Thiet ke:
    - Day la buoc CUOI CUNG truoc khi dua payload vao vm/compiler.py (hoac
      xuat thang neu khong dung VM). No khong tu bien doi logic, chi lam
      nhiem vu "tai tao code tu AST" mot cach CHINH XAC ve mat cu phap.
    - Emitter hoat dong theo kieu "token stream": moi node sinh ra 1 danh
      sach token tho (str), sau do 1 ham join thong minh (`_needs_space`)
      moi quyet dinh co chen khoang trang giua 2 token hay khong. Cach lam
      nay dam bao:
        (a) Khong bao gio lam dinh 2 token chu/so lien tiep (vd 'local' +
            'x' phai co khoang trang, neu khong se thanh 1 identifier
            'localx' sai hoan toan).
        (b) Khong vo tinh tao ra token khac (vd '-' ganh '-' thanh '--' ->
            comment; '.' ganh '.' thanh '..'/'...'; ':' ganh ':' thanh
            '::'); nhung van giu output gon nhat co the trong cac truong
            hop con lai (vd ')' ganh '+' khong can khoang trang).
    - Do uu tien toan tu (PRECEDENCE) va tinh ket hop (RIGHT_ASSOC) DUOC
      SAO Y HET bang `_BIN_PRECEDENCE` trong `parser/parser.py` de dam bao
      round-trip: parse(emit(parse(src))) phai cho ra AST tuong duong,
      khong bao gio lam sai nghia do thieu/thua dau ngoac.
    - Tu dong chen ';' sau MOI statement (ke ca statement long nhau trong
      block con) - dung theo yeu cau "tu chen ; cuoi cau lenh de nen ve 1
      dong an toan" trong dac ta, vi no triet tieu tat ca cac loi an-tach
      kinh dien cua Lua (vd dong truoc ket thuc bang ham goi `(...)` se bi
      hieu nham la goi ham cua bieu thuc truoc do neu khong co ';').
    - TypeInfo / annotation KHONG bao gio duoc in ra (annotations.py co
      nhiem vu lot sach truoc do, nhung emitter cu the phong thu kep: neu
      lo con sot TypeCastExpr, no chi in bieu thuc ben trong, bo qua '::Type').

Entry point chinh:
    emit(chunk: Block, minify: bool = True) -> str
"""

from __future__ import annotations

from typing import List, Sequence

from ..parser.nodes import (
    Node, Block,
    Expr, NilExpr, TrueExpr, FalseExpr, VarargExpr, NumberExpr, StringExpr,
    NameExpr, IndexExpr, IndexBracketExpr, CallExpr, MethodCallExpr,
    FunctionExpr, TableConstructorExpr, BinOpExpr, UnOpExpr, ParenExpr,
    TypeCastExpr, IfExpr, FunctionBody,
    Stmt, LocalAssignStmt, AssignStmt, CompoundAssignStmt, CallStmt, DoStmt,
    WhileStmt, RepeatStmt, IfStmt, NumericForStmt, GenericForStmt,
    FunctionDeclStmt, LocalFunctionStmt, ReturnStmt, BreakStmt, ContinueStmt,
    GotoStmt, LabelStmt, TypeAliasStmt,
)


class EmitError(Exception):
    """Nem ra khi gap 1 node AST khong nhan dien duoc (vd node tuy bien
    tu mot tang bien doi khac them vao ma emitter chua ho tro)."""


# ---------------------------------------------------------------------------
# Bang do uu tien - PHAI khop 100% voi parser/parser.py::_BIN_PRECEDENCE
# ---------------------------------------------------------------------------

PRECEDENCE = {
    "or": 1, "and": 2,
    "<": 3, ">": 3, "<=": 3, ">=": 3, "~=": 3, "==": 3,
    "..": 4,
    "+": 5, "-": 5,
    "*": 6, "/": 6, "//": 6, "%": 6,
    "^": 8,
}
UNARY_PRECEDENCE = 7
RIGHT_ASSOC = {"..", "^"}

# Cac loai node co the dung truc tiep o vi tri "prefixexp" (obj cua .field/
# [key], func cua goi ham) ma KHONG can boc ngoac - dung theo dung ngu phap
# Lua: prefixexp ::= Name | prefixexp '[' exp ']' | prefixexp '.' Name
#                   | functioncall | '(' exp ')'
_PREFIX_ATOM_TYPES = (NameExpr, IndexExpr, IndexBracketExpr, CallExpr,
                      MethodCallExpr, ParenExpr)

_WORD_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
_RISKY_PAIRS = {"--", "..", "::", "==", "~=", "<=", ">=", "//"}


def _needs_space(a: str, b: str) -> bool:
    if not a or not b:
        return False
    la, fb = a[-1], b[0]
    if la in _WORD_CHARS and fb in _WORD_CHARS:
        return True
    # Chi NUMBER token moi bat dau bang chu so (NAME khong bao gio bat dau
    # bang digit - xem lexer.py::_read_name, chi kich hoat khi la chu cai
    # hoac '_'). Vi vay dung a[0] (chu khong phai la=a[-1]) de phan biet
    # "42" (number, can cach truoc '.') voi "bit32" (name ket thuc bang
    # digit nhung KHONG can cach truoc '.' - 'bit32.bor' hoan toan an toan).
    if a[0].isdigit() and fb == ".":
        return True
    if (la + fb) in _RISKY_PAIRS:
        return True
    return False


def _format_number(value) -> str:
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
        return repr(value)
    return str(value)


def _escape_short_string(value: str) -> str:
    out = ['"']
    for ch in value:
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\0":
            out.append("\\0")
        elif ord(ch) < 0x20:
            out.append("\\%d" % ord(ch))
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)


class Emitter:
    """Bien 1 AST (Block/Chunk) thanh danh sach token roi join lai thanh
    chuoi source Lua. Dung lai duoc (khong giu state giua cac lan goi
    `emit`, moi lan goi la mot lan render doc lap)."""

    def __init__(self) -> None:
        pass

    # -- Public API ---------------------------------------------------

    def emit(self, chunk: Block, *, minify: bool = True) -> str:
        tokens = self._tokens_block(chunk)
        # Bo dau ';' thua o cuoi cung (khong bat buoc nhung cho gon).
        if tokens and tokens[-1] == ";":
            tokens = tokens[:-1]
        if minify:
            return self._join(tokens)
        return self._join_debug(tokens)

    # -- Joiner ---------------------------------------------------------

    def _join(self, tokens: Sequence[str]) -> str:
        out: List[str] = []
        prev = ""
        for tok in tokens:
            if out and _needs_space(prev, tok):
                out.append(" ")
            out.append(tok)
            prev = tok
        return "".join(out)

    def _join_debug(self, tokens: Sequence[str]) -> str:
        """Che do khong-nen, chi de debug thu cong: moi ';' xuong dong,
        cac token con lai cach nhau 1 khoang trang."""
        out: List[str] = []
        for tok in tokens:
            out.append(tok)
            out.append("\n" if tok == ";" else " ")
        return "".join(out).strip()

    # -- Helper noi danh sach con bang dau phay ------------------------

    @staticmethod
    def _join_comma(items: Sequence[List[str]]) -> List[str]:
        toks: List[str] = []
        for i, item in enumerate(items):
            if i > 0:
                toks.append(",")
            toks.extend(item)
        return toks

    # -- Atom wrapper (ngu phap prefixexp) ------------------------------

    def _tokens_atom(self, node: Expr) -> List[str]:
        toks = self._tokens_expr(node, 0)
        if isinstance(node, _PREFIX_ATOM_TYPES):
            return toks
        return ["("] + toks + [")"]

    # -- Expression -------------------------------------------------------

    def _tokens_expr(self, node: Expr, parent_prec: int = 0) -> List[str]:
        if isinstance(node, NilExpr):
            return ["nil"]
        if isinstance(node, TrueExpr):
            return ["true"]
        if isinstance(node, FalseExpr):
            return ["false"]
        if isinstance(node, VarargExpr):
            return ["..."]
        if isinstance(node, NumberExpr):
            return [node.raw if node.raw else _format_number(node.value)]
        if isinstance(node, StringExpr):
            if node.is_long_string or not (node.raw and (node.raw.startswith('"') or node.raw.startswith("'"))):
                return [_escape_short_string(node.value)]
            return [node.raw]
        if isinstance(node, NameExpr):
            return [node.name]
        if isinstance(node, IndexExpr):
            return self._tokens_atom(node.obj) + [".", node.field]
        if isinstance(node, IndexBracketExpr):
            return (self._tokens_atom(node.obj) + ["["]
                    + self._tokens_expr(node.key, 0) + ["]"])
        if isinstance(node, CallExpr):
            toks = self._tokens_atom(node.func) + ["("]
            toks += self._join_comma([self._tokens_expr(a, 0) for a in node.args])
            toks.append(")")
            return toks
        if isinstance(node, MethodCallExpr):
            toks = self._tokens_atom(node.obj) + [":", node.method, "("]
            toks += self._join_comma([self._tokens_expr(a, 0) for a in node.args])
            toks.append(")")
            return toks
        if isinstance(node, FunctionExpr):
            return ["function"] + self._tokens_funcbody_rest(node.body)
        if isinstance(node, TableConstructorExpr):
            return self._tokens_table(node)
        if isinstance(node, ParenExpr):
            return ["("] + self._tokens_expr(node.expr, 0) + [")"]
        if isinstance(node, TypeCastExpr):
            # annotations.py le ra da lot sach '::Type'; phong thu kep o day
            # bang cach chi in bieu thuc ben trong, bo het phan ep kieu.
            return self._tokens_expr(node.expr, parent_prec)
        if isinstance(node, IfExpr):
            toks: List[str] = []
            for i, clause in enumerate(node.clauses):
                toks.append("if" if i == 0 else "elseif")
                toks += self._tokens_expr(clause.cond, 0)
                toks.append("then")
                toks += self._tokens_expr(clause.value, 0)
            toks.append("else")
            toks += self._tokens_expr(node.else_value, 0)
            if parent_prec > 0:
                return ["("] + toks + [")"]
            return toks
        if isinstance(node, UnOpExpr):
            op_tok = node.op
            operand_toks = self._tokens_expr(node.operand, UNARY_PRECEDENCE)
            toks = [op_tok] + operand_toks
            if UNARY_PRECEDENCE < parent_prec:
                return ["("] + toks + [")"]
            return toks
        if isinstance(node, BinOpExpr):
            prec = PRECEDENCE.get(node.op, 0)
            right_assoc = node.op in RIGHT_ASSOC
            left_req = prec + (1 if right_assoc else 0)
            right_req = prec + (0 if right_assoc else 1)
            left_toks = self._tokens_expr(node.left, left_req)
            right_toks = self._tokens_expr(node.right, right_req)
            toks = left_toks + [node.op] + right_toks
            if prec < parent_prec:
                return ["("] + toks + [")"]
            return toks
        raise EmitError(f"Khong nhan dien duoc expression node: {node!r}")

    # -- Table constructor ----------------------------------------------

    def _tokens_table(self, node: TableConstructorExpr) -> List[str]:
        toks = ["{"]
        for i, f in enumerate(node.fields):
            if i > 0:
                toks.append(",")
            if f.key is None:
                toks += self._tokens_expr(f.value, 0)
            elif f.is_computed_key:
                toks.append("[")
                toks += self._tokens_expr(f.key, 0)
                toks.append("]")
                toks.append("=")
                toks += self._tokens_expr(f.value, 0)
            else:
                toks.append(f.key)
                toks.append("=")
                toks += self._tokens_expr(f.value, 0)
        toks.append("}")
        return toks

    # -- Function body (phan sau tu khoa 'function') ----------------------

    def _tokens_funcbody_rest(self, fbody: FunctionBody, *,
                               skip_first_param: bool = False) -> List[str]:
        toks = ["("]
        src_params = fbody.params
        if skip_first_param and src_params and not src_params[0].is_vararg:
            # Khai bao dang 'function obj:method(...)' da tu dong them
            # tham so 'self' vao dau params[] luc parse (xem parser.py:
            # `_parse_funcbody(is_method=True)`). Khi in lai bang cu phap
            # ':' (sugar), KHONG duoc in lai param dau nay (du da bi mangled
            # thanh ten khac), neu khong se bi nhan doi param.
            src_params = src_params[1:]
        params = []
        for p in src_params:
            params.append(["..."] if p.is_vararg else [p.name])
        toks += self._join_comma(params)
        toks.append(")")
        toks += self._tokens_block(fbody.body)
        toks.append("end")
        return toks

    # -- Block / statements ----------------------------------------------

    def _tokens_block(self, block: Block) -> List[str]:
        toks: List[str] = []
        for stmt in block.statements:
            stoks = self._tokens_stmt(stmt)
            if not stoks:
                continue
            toks.extend(stoks)
            toks.append(";")
        return toks

    def _tokens_stmt(self, node: Stmt) -> List[str]:
        if isinstance(node, LocalAssignStmt):
            toks = ["local"]
            toks += self._join_comma([[n] for n in node.names])
            if node.values:
                toks.append("=")
                toks += self._join_comma([self._tokens_expr(v, 0) for v in node.values])
            return toks
        if isinstance(node, AssignStmt):
            toks = self._join_comma([self._tokens_expr(t, 0) for t in node.targets])
            toks.append("=")
            toks += self._join_comma([self._tokens_expr(v, 0) for v in node.values])
            return toks
        if isinstance(node, CompoundAssignStmt):
            toks = self._tokens_expr(node.target, 0)
            toks.append(node.op + "=")
            toks += self._tokens_expr(node.value, 0)
            return toks
        if isinstance(node, CallStmt):
            return self._tokens_expr(node.call, 0)
        if isinstance(node, DoStmt):
            return ["do"] + self._tokens_block(node.body) + ["end"]
        if isinstance(node, WhileStmt):
            return (["while"] + self._tokens_expr(node.cond, 0) + ["do"]
                    + self._tokens_block(node.body) + ["end"])
        if isinstance(node, RepeatStmt):
            return (["repeat"] + self._tokens_block(node.body) + ["until"]
                    + self._tokens_expr(node.cond, 0))
        if isinstance(node, IfStmt):
            toks: List[str] = []
            for i, clause in enumerate(node.clauses):
                toks.append("if" if i == 0 else "elseif")
                toks += self._tokens_expr(clause.cond, 0)
                toks.append("then")
                toks += self._tokens_block(clause.body)
            if node.else_body is not None:
                toks.append("else")
                toks += self._tokens_block(node.else_body)
            toks.append("end")
            return toks
        if isinstance(node, NumericForStmt):
            toks = ["for", node.var, "="] + self._tokens_expr(node.start, 0)
            toks.append(",")
            toks += self._tokens_expr(node.stop, 0)
            if node.step is not None:
                toks.append(",")
                toks += self._tokens_expr(node.step, 0)
            toks.append("do")
            toks += self._tokens_block(node.body)
            toks.append("end")
            return toks
        if isinstance(node, GenericForStmt):
            toks = ["for"]
            toks += self._join_comma([[n] for n in node.names])
            toks.append("in")
            toks += self._join_comma([self._tokens_expr(e, 0) for e in node.exprs])
            toks.append("do")
            toks += self._tokens_block(node.body)
            toks.append("end")
            return toks
        if isinstance(node, FunctionDeclStmt):
            name_toks = [node.name_path[0]] if node.name_path else []
            mid = node.name_path[1:-1] if len(node.name_path) > 1 else []
            for part in mid:
                name_toks += [".", part]
            if len(node.name_path) > 1:
                sep = ":" if node.is_method else "."
                name_toks += [sep, node.name_path[-1]]
            return (["function"] + name_toks
                    + self._tokens_funcbody_rest(node.body, skip_first_param=node.is_method))
        if isinstance(node, LocalFunctionStmt):
            return ["local", "function", node.name] + self._tokens_funcbody_rest(node.body)
        if isinstance(node, ReturnStmt):
            toks = ["return"]
            toks += self._join_comma([self._tokens_expr(v, 0) for v in node.values])
            return toks
        if isinstance(node, BreakStmt):
            return ["break"]
        if isinstance(node, ContinueStmt):
            return ["continue"]
        if isinstance(node, GotoStmt):
            return ["goto", node.label]
        if isinstance(node, LabelStmt):
            return ["::", node.name, "::"]
        if isinstance(node, TypeAliasStmt):
            # annotations.py se xoa statement nay hoan toan; phong thu kep:
            # emitter tu bo qua, khong in gi ca (coi nhu statement rong).
            return []
        raise EmitError(f"Khong nhan dien duoc statement node: {node!r}")


def emit(chunk: Block, *, minify: bool = True) -> str:
    """Ham tien ich: render 1 Block/Chunk AST thanh source Lua."""
    return Emitter().emit(chunk, minify=minify)


__all__ = ["Emitter", "EmitError", "emit", "PRECEDENCE", "UNARY_PRECEDENCE", "RIGHT_ASSOC"]


# ---------------------------------------------------------------------------
# Test nho (chay truc tiep: python -m src.advanced.emitter)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from ..parser import parse, ParseError

    SAMPLES = [
        "local a, b = 1, 2\nlocal c = a + b * (a - b) ^ 2\n"
        "if c > 10 then\n  print(c)\nelseif c < 0 then\n  print(-c)\nelse\n  print(0)\nend\n",

        "local function fib(n)\n  if n < 2 then return n end\n"
        "  return fib(n-1) + fib(n-2)\nend\nprint(fib(10))\n",

        "for i = 1, 10 do\n  local x = -i\n  print(x)\nend\n"
        "for k, v in pairs({1,2,3}) do print(k, v) end\n",

        "local t = { 1, 2, x = 3, [\"y\"] = 4, [z] = 5 }\n"
        "local s = 'he said \"hi\"\\n'\n"
        "local neg = - -5\n"
        "local cc = 'a' .. 'b' .. 'c'\n",

        "local obj = {}\nfunction obj:method(x)\n  return self.y + x\nend\n"
        "obj:method(1)\n::top::\ngoto top\n",
    ]

    ok = 0
    for i, src in enumerate(SAMPLES):
        ast = parse(src)
        out = emit(ast, minify=True)
        print(f"--- sample {i} ---")
        print(out)
        try:
            # Round-trip: output phai tu parse lai duoc khong loi.
            parse(out)
            ok += 1
            print("[OK] round-trip parse thanh cong")
        except ParseError as e:
            print(f"[FAIL] round-trip parse loi: {e}")
    print(f"\n{ok}/{len(SAMPLES)} mau round-trip thanh cong")

    # Kiem tra rieng: method declaration ':' khong duoc nhan doi 'self'.
    method_src = "local obj = {}\nfunction obj:method(x)\n  return self.y + x\nend\n"
    method_out = emit(parse(method_src), minify=True)
    assert "method(self" not in method_out, (
        f"BUG: 'self' bi in lai thua trong tham so method -> {method_out!r}"
    )
    assert "method(x)" in method_out, f"method params sai -> {method_out!r}"
    print(f"\n[OK] method ':' khong nhan doi self -> {method_out}")