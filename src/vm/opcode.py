# -*- coding: utf-8 -*-
"""
opcode.py
=========
Dinh nghia tap lenh (instruction set) cho VM register-based cua
LuauObfuscator: 32 opcode = 16 RISC (nen tang) + 16 CISC (lenh gop,
toi uu cho cac pattern thuong gap trong game script chay 60 FPS).

Dinh dang bit-packing cua 1 lenh (32-bit word, little “logical” layout,
KHONG lien quan endianness vi day la so nguyen Python, chi anh huong
khi serialize ra byte - xem utils/crypto.py + vm/runtime_template.py):

    bit:   31      27 26      19 18       10 9          0
           +--------+-----------+-----------+-----------+
           | OP (5) |   A (8)   |   B (9)   |   C (10)  |
           +--------+-----------+-----------+-----------+

    - OP (5 bit):  0-31, ma lenh (du cho dung 32 opcode)
    - A  (8 bit):  0-255, HAU HET la thanh ghi dich (R[A])
    - B  (9 bit):  0-511, thanh ghi / hang so / hint tuy lenh
    - C  (10 bit): 0-1023, thanh ghi / hang so / offset nhay tuy lenh

    Truong B va C co 2 "che do" doc:
        - UNSIGNED (REG, CONST, HINT, PROTO): doc truc tiep 0..max
        - SIGNED  (IMM, JMP): dung ky thuat "excess-K" (bias encoding):
          gia tri thuc = raw - BIAS, voi BIAS = 2^(so_bit - 1).
          Vi du truong C (10 bit, BIAS=512): raw=512 -> gia tri 0,
          raw=0 -> gia tri -512, raw=1023 -> gia tri 511.
          Nho vay JMP co the nhay lui (offset am) ma khong can bit dau rieng.

Bo nho Zero-GC (4 mang song song Inst_OP/Inst_A/Inst_B/Inst_C + mang
thanh ghi R) la viec cua `vm/compiler.py` va `vm/runtime_template.py` -
module nay CHI dinh nghia dinh dang lenh va ham encode/decode thuan tuy,
khong giu state, khong phu thuoc runtime.

API chinh:
    Opcode                  - IntEnum, 32 gia tri (0..31)
    OPCODE_INFO[opcode]     - metadata: ten, nhom RISC/CISC, y nghia A/B/C
    encode_instruction(...) - dong goi (op,a,b,c) -> so nguyen 32-bit
    decode_instruction(w)   - giai ma so nguyen 32-bit -> DecodedInstruction
    disassemble(word)       - chuoi text de debug, vd "ADD R1, R2, R3"
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Dict, NamedTuple, Tuple


class OpcodeError(Exception):
    """Loi chung khi encode/decode lenh (tran bit, opcode khong ton tai...)."""
    pass


class OpcodeEncodeError(OpcodeError):
    pass


class OpcodeDecodeError(OpcodeError):
    pass


# ---------------------------------------------------------------------------
# Dinh dang bit (field widths) - XEM docstring dau file de biet y nghia
# ---------------------------------------------------------------------------

OP_BITS = 5
A_BITS = 8
B_BITS = 9
C_BITS = 10
WORD_BITS = OP_BITS + A_BITS + B_BITS + C_BITS
assert WORD_BITS == 32, "Tong so bit cac truong phai dung 32 (lenh 32-bit)"

OP_MAX = (1 << OP_BITS) - 1   # 31
A_MAX = (1 << A_BITS) - 1     # 255
B_MAX = (1 << B_BITS) - 1     # 511
C_MAX = (1 << C_BITS) - 1     # 1023

# Vi tri dich bit (shift) cua tung truong trong word 32-bit, tinh tu LSB.
C_SHIFT = 0
B_SHIFT = C_BITS                      # 10
A_SHIFT = C_BITS + B_BITS             # 19
OP_SHIFT = C_BITS + B_BITS + A_BITS   # 27

# Bias (excess-K) dung cho cac truong doc o che do SIGNED.
B_SIGNED_BIAS = 1 << (B_BITS - 1)   # 256  (pham vi gia tri: -256..255)
C_SIGNED_BIAS = 1 << (C_BITS - 1)   # 512  (pham vi gia tri: -512..511)


# ---------------------------------------------------------------------------
# Tap lenh: 16 RISC (0-15) + 16 CISC (16-31)
# ---------------------------------------------------------------------------

class Opcode(IntEnum):
    # ---- RISC: lenh nen tang, 1 thao tac don gian moi lenh -------------
    MOVE = 0          # R[A] = R[B]
    LOADK = 1         # R[A] = K[B]                       (hang so tu constant pool)
    GETGLOBAL = 2     # R[A] = G[K[B]]; C>0 upvalue, C>=512 high constant index
    SETGLOBAL = 3     # G[K[B]] = R[A]; C>0 upvalue, C>=512 high constant index
    GETTABLE = 4      # R[A] = R[B][R[C]]
    SETTABLE = 5      # R[A][R[B]] = R[C]
    NEWTABLE = 6      # R[A] = table.create-like {}        (B=array hint, C=hash hint)
    CLOSURE = 7       # R[A] = closure(proto[B + C * 512])
    ADD = 8           # R[A] = R[B] + R[C]
    SUB = 9           # R[A] = R[B] - R[C]
    MUL = 10          # R[A] = R[B] * R[C]
    DIV = 11          # R[A] = R[B] / R[C]
    EQ = 12           # R[A] = (R[B] == R[C])
    LT = 13           # R[A] = (R[B] <  R[C])
    JMP = 14          # PC += signed(C)
    CALL = 15         # CALL; B low 8 bits=nargs+1, B bit 8=expand final call result;
                      # C low 8 bits=nret+1 (0 encodes 255), C bit 8=open results,
                      # C bit 9=expand V

    # ---- CISC: lenh gop, toi uu cho pattern lap lai nhieu --------------
    ADDI = 16         # R[A] = R[B] + imm(C)               (cong hang so nho: +1, +dt,...)
    SUBI = 17         # R[A] = R[B] - imm(C)
    MULI = 18         # R[A] = R[B] * imm(C)
    GETTABLE_K = 19   # R[A] = R[B][K[C]]                   (truy cap thuoc tinh ten co dinh;
                      #   K[C] co the la string (ten field) hoac number (chi so mang hang so),
                      #   nen opcode nay bao phu luon vai tro 1 "GETTABLE_N" rieng)
    SETTABLE_K = 20   # R[A][K[B]] = R[C]
    TEST = 21         # if truthy(R[A]) == (B ~= 0) then PC += signed(C)  (test+nhay gop 1 lenh;
                      #   runtime la Lua that nen co truthy tu nhien: chi can 'if R[A] then' -
                      #   xem runtime_template.py. Dung cho if/while/and/or/not.)
    RETURN = 22       # return R[A], R[A+1], ..., R[A+B-2] ; B=nret+1 (B=1 -> khong tra gia tri).
                      #   Lenh BAT BUOC de 1 ham co the ket thuc va tra gia tri ve noi goi.
    EQ_JMP = 23       # if R[A] == R[B] then PC += signed(C)   (gop EQ+JMP)
    LT_JMP = 24       # if R[A] <  R[B] then PC += signed(C)
    LE_JMP = 25       # if R[A] <= R[B] then PC += signed(C)
    NEQ_JMP = 26      # if R[A] ~= R[B] then PC += signed(C)
    CALL_GLOBAL = 27  # R[A] = G[K[B]](R[A+1]..) ; C=nargs+1  (gop GETGLOBAL+CALL)
    CALL_METHOD = 28  # R[A] = R[A]:K[B](R[A+1]..) ; C=nargs+1 (gop GETTABLE method+CALL; R[A]
                      #   vua la doi tuong, vua tu dong duoc truyen lam tham so 'self' dau tien,
                      #   giong OP_SELF+OP_CALL cua Lua goc gop lam 1 lenh)
    FORPREP = 29      # chuan bi numeric-for: R[A],R[A+1],R[A+2] = index,limit,step; C=signed jmp
    FORLOOP = 30      # tang index, kiem tra limit, nhay lui neu con dung; C=signed jmp
    CONCAT = 31       # R[A] = R[B] .. R[B+1] .. ... .. R[C]  (noi chuoi 1 day thanh ghi)


RISC_OPCODES = frozenset(op for op in Opcode if op.value < 16)
CISC_OPCODES = frozenset(op for op in Opcode if op.value >= 16)

assert len(list(Opcode)) == 32, "Phai co dung 32 opcode (5 bit)"
assert len(RISC_OPCODES) == 16 and len(CISC_OPCODES) == 16


# ---------------------------------------------------------------------------
# Metadata cho tung opcode: dung de encode/decode thong minh (tu dong bias
# cac truong signed) va de disassemble ra text de doc.
# ---------------------------------------------------------------------------

class OperandKind(IntEnum):
    """Y nghia/che do doc cua 1 truong toan hang (A, B hoac C)."""
    UNUSED = 0   # khong dung, luon encode = 0
    REG = 1      # chi so thanh ghi, unsigned, doc truc tiep
    CONST = 2    # chi so vao constant pool (K[]), unsigned, doc truc tiep
    PROTO = 3    # chi so vao bang function prototype (cho CLOSURE), unsigned
    HINT = 4     # so dem / size hint nho, KHONG am (vd so tham so, size table)
    IMM = 5      # hang so nguyen nho CO DAU (excess-K bias)
    JMP = 6      # do lech PC (program counter) CO DAU (excess-K bias)


@dataclass(frozen=True)
class OpcodeInfo:
    name: str
    is_cisc: bool
    a_kind: OperandKind
    b_kind: OperandKind
    c_kind: OperandKind
    doc: str


def _signed_kinds() -> frozenset:
    return frozenset({OperandKind.IMM, OperandKind.JMP})


# R=REG, K=CONST, P=PROTO, H=HINT, I=IMM, J=JMP, _=UNUSED (viet tat cho gon bang duoi)
_R, _K, _P, _H, _I, _J, _U = (
    OperandKind.REG, OperandKind.CONST, OperandKind.PROTO,
    OperandKind.HINT, OperandKind.IMM, OperandKind.JMP, OperandKind.UNUSED,
)

OPCODE_INFO: Dict[Opcode, OpcodeInfo] = {
    # ---------------------------- RISC ----------------------------------
    Opcode.MOVE: OpcodeInfo("MOVE", False, _R, _R, _U,
                             "R[A] = R[B]"),
    Opcode.LOADK: OpcodeInfo("LOADK", False, _R, _K, _H,
                              "R[A] = K[B + C * 512]"),
    Opcode.GETGLOBAL: OpcodeInfo("GETGLOBAL", False, _R, _K, _H,
                                  "C=0: global; C=1..509: upvalue; C=510/511: vararg/helper; C>=512: high global constant"),
    Opcode.SETGLOBAL: OpcodeInfo("SETGLOBAL", False, _R, _K, _H,
                                  "C=0: global; C=1..511: upvalue; C>=512: high global constant"),
    Opcode.GETTABLE: OpcodeInfo("GETTABLE", False, _R, _R, _R,
                                 "R[A] = R[B][R[C]]"),
    Opcode.SETTABLE: OpcodeInfo("SETTABLE", False, _R, _R, _R,
                                 "R[A][R[B]] = R[C]"),
    Opcode.NEWTABLE: OpcodeInfo("NEWTABLE", False, _R, _H, _H,
                                 "R[A] = {} (B=array size hint, C=hash size hint)"),
    Opcode.CLOSURE: OpcodeInfo("CLOSURE", False, _R, _P, _H,
                                "R[A] = closure(proto[B + C * 512])"),
    Opcode.ADD: OpcodeInfo("ADD", False, _R, _R, _R,
                            "R[A] = R[B] + R[C]"),
    Opcode.SUB: OpcodeInfo("SUB", False, _R, _R, _R,
                            "R[A] = R[B] - R[C]"),
    Opcode.MUL: OpcodeInfo("MUL", False, _R, _R, _R,
                            "R[A] = R[B] * R[C]"),
    Opcode.DIV: OpcodeInfo("DIV", False, _R, _R, _R,
                            "R[A] = R[B] / R[C]"),
    Opcode.EQ: OpcodeInfo("EQ", False, _R, _R, _R,
                           "R[A] = (R[B] == R[C])"),
    Opcode.LT: OpcodeInfo("LT", False, _R, _R, _R,
                           "R[A] = (R[B] < R[C])"),
    Opcode.JMP: OpcodeInfo("JMP", False, _U, _U, _J,
                            "PC += signed(C)"),
    Opcode.CALL: OpcodeInfo("CALL", False, _R, _H, _H,
                             "CALL; B bit 8 expands final call arg; C bit 8=open results, bit 9 expands V"),

    # ---------------------------- CISC ----------------------------------
    Opcode.ADDI: OpcodeInfo("ADDI", True, _R, _R, _I,
                             "R[A] = R[B] + imm(C)"),
    Opcode.SUBI: OpcodeInfo("SUBI", True, _R, _R, _I,
                             "R[A] = R[B] - imm(C)"),
    Opcode.MULI: OpcodeInfo("MULI", True, _R, _R, _I,
                             "R[A] = R[B] * imm(C)"),
    Opcode.GETTABLE_K: OpcodeInfo("GETTABLE_K", True, _R, _R, _K,
                                   "R[A] = R[B][K[C]]"),
    Opcode.SETTABLE_K: OpcodeInfo("SETTABLE_K", True, _R, _K, _H,
                                   "R[A][K[B + floor(C / 256) * 512]] = R[C % 256]"),
    Opcode.TEST: OpcodeInfo("TEST", True, _R, _H, _J,
                             "if truthy(R[A]) == (B~=0) then PC += signed(C)"),
    Opcode.RETURN: OpcodeInfo("RETURN", True, _R, _H, _U,
                               "return R[A..A+B-2]; B=nret+1 (B=1 nghia la khong tra gia tri nao)"),
    Opcode.EQ_JMP: OpcodeInfo("EQ_JMP", True, _R, _R, _J,
                               "if R[A] == R[B] then PC += signed(C)"),
    Opcode.LT_JMP: OpcodeInfo("LT_JMP", True, _R, _R, _J,
                               "if R[A] < R[B] then PC += signed(C)"),
    Opcode.LE_JMP: OpcodeInfo("LE_JMP", True, _R, _R, _J,
                               "if R[A] <= R[B] then PC += signed(C)"),
    Opcode.NEQ_JMP: OpcodeInfo("NEQ_JMP", True, _R, _R, _J,
                                "if R[A] ~= R[B] then PC += signed(C)"),
    Opcode.CALL_GLOBAL: OpcodeInfo("CALL_GLOBAL", True, _R, _K, _H,
                                    "R[A] = G[K[B]](R[A+1..]); C=nargs+1"),
    Opcode.CALL_METHOD: OpcodeInfo("CALL_METHOD", True, _R, _K, _H,
                                    "R[A] = R[A]:K[B](R[A+1..]); C=nargs+1 (tu dong truyen self)"),
    Opcode.FORPREP: OpcodeInfo("FORPREP", True, _R, _U, _J,
                                "R[A],R[A+1],R[A+2] = index,limit,step; nhay toi sau FORLOOP neu khong du vong"),
    Opcode.FORLOOP: OpcodeInfo("FORLOOP", True, _R, _U, _J,
                                "R[A] += R[A+2]; neu con trong [limit] thi PC += signed(C) (nhay lui dau than vong lap)"),
    Opcode.CONCAT: OpcodeInfo("CONCAT", True, _R, _R, _R,
                               "R[A] = R[B] .. R[B+1] .. ... .. R[C]"),
}

assert set(OPCODE_INFO.keys()) == set(Opcode), "Moi opcode phai co OpcodeInfo tuong ung"
for _op, _info in OPCODE_INFO.items():
    assert _info.is_cisc == (_op in CISC_OPCODES), f"Sai nhom RISC/CISC cho {_op.name}"


# ---------------------------------------------------------------------------
# Encode / decode
# ---------------------------------------------------------------------------

class DecodedInstruction(NamedTuple):
    """Ket qua giai ma 1 lenh. `a`, `b`, `c` da duoc "giai bias" (neu la
    truong SIGNED) de co the dung truc tiep nhu gia tri thuc; `raw_b`,
    `raw_c` giu nguyen gia tri bit-pattern tho (0..max) phong khi can debug
    o muc bit.
    """
    op: Opcode
    a: int
    b: int
    c: int
    raw_a: int
    raw_b: int
    raw_c: int


def _encode_field(value: int, kind: OperandKind, bits: int, bias: int, field_name: str, op_name: str) -> int:
    """Chuyen 1 gia tri logic (co the am neu la IMM/JMP) thanh bit-pattern
    unsigned de nhoi vao truong `bits` bit. Nem OpcodeEncodeError neu tran.
    """
    max_unsigned = (1 << bits) - 1
    if kind in _signed_kinds():
        lo, hi = -bias, bias - 1
        if not (lo <= value <= hi):
            raise OpcodeEncodeError(
                f"{op_name}: truong {field_name} (signed, {bits} bit) chi chua duoc "
                f"[{lo}, {hi}], nhung nhan gia tri {value}"
            )
        return value + bias
    else:
        if kind == OperandKind.UNUSED:
            if value != 0:
                raise OpcodeEncodeError(
                    f"{op_name}: truong {field_name} khong duoc dung (UNUSED) nhung "
                    f"nhan gia tri khac 0 ({value})"
                )
            return 0
        if not (0 <= value <= max_unsigned):
            raise OpcodeEncodeError(
                f"{op_name}: truong {field_name} (unsigned, {bits} bit) chi chua duoc "
                f"[0, {max_unsigned}], nhung nhan gia tri {value}"
            )
        return value


def encode_instruction(op: Opcode, a: int = 0, b: int = 0, c: int = 0) -> int:
    """Dong goi (op, a, b, c) thanh 1 so nguyen 32-bit (Python int).

    Gia tri a/b/c truyen vao la gia tri LOGIC (vd offset nhay co the am);
    ham nay tu tra cuu OPCODE_INFO de biet truong nao can bias encode
    (IMM/JMP) va truong nao doc truc tiep (REG/CONST/HINT/PROTO/UNUSED),
    tranh viec goi phai tu nho quy tac bias.
    """
    if not isinstance(op, Opcode):
        try:
            op = Opcode(op)
        except ValueError as exc:
            raise OpcodeEncodeError(f"Opcode khong hop le: {op!r}") from exc

    info = OPCODE_INFO[op]
    raw_a = _encode_field(a, info.a_kind, A_BITS, 1 << (A_BITS - 1), "A", info.name)
    raw_b = _encode_field(b, info.b_kind, B_BITS, B_SIGNED_BIAS, "B", info.name)
    raw_c = _encode_field(c, info.c_kind, C_BITS, C_SIGNED_BIAS, "C", info.name)

    word = (int(op) << OP_SHIFT) | (raw_a << A_SHIFT) | (raw_b << B_SHIFT) | (raw_c << C_SHIFT)
    return word


def decode_instruction(word: int) -> DecodedInstruction:
    """Giai ma 1 so nguyen 32-bit thanh DecodedInstruction. Tu dong "go
    bias" cho cac truong IMM/JMP dua theo OPCODE_INFO cua opcode tuong ung.
    """
    if not (0 <= word <= 0xFFFFFFFF):
        raise OpcodeDecodeError(f"Gia tri lenh vuot pham vi 32-bit: {word!r}")

    raw_op = (word >> OP_SHIFT) & OP_MAX
    raw_a = (word >> A_SHIFT) & A_MAX
    raw_b = (word >> B_SHIFT) & B_MAX
    raw_c = (word >> C_SHIFT) & C_MAX

    try:
        op = Opcode(raw_op)
    except ValueError as exc:
        raise OpcodeDecodeError(f"Ma opcode khong ton tai: {raw_op}") from exc

    info = OPCODE_INFO[op]
    a = raw_a - (1 << (A_BITS - 1)) if info.a_kind in _signed_kinds() else raw_a
    b = raw_b - B_SIGNED_BIAS if info.b_kind in _signed_kinds() else raw_b
    c = raw_c - C_SIGNED_BIAS if info.c_kind in _signed_kinds() else raw_c

    return DecodedInstruction(op=op, a=a, b=b, c=c, raw_a=raw_a, raw_b=raw_b, raw_c=raw_c)


# ---------------------------------------------------------------------------
# Disassembler (debug / test)
# ---------------------------------------------------------------------------

def _format_operand(kind: OperandKind, value: int) -> str:
    if kind == OperandKind.UNUSED:
        return "-"
    if kind == OperandKind.REG:
        return f"R{value}"
    if kind == OperandKind.CONST:
        return f"K[{value}]"
    if kind == OperandKind.PROTO:
        return f"P[{value}]"
    if kind == OperandKind.HINT:
        return str(value)
    if kind == OperandKind.IMM:
        return f"{value:+d}"
    if kind == OperandKind.JMP:
        return f"{value:+d}"
    return str(value)  # pragma: no cover - khong bao gio toi day


def disassemble(word: int) -> str:
    """Tra ve 1 dong text de debug, vd: 'ADD      R1, R2, R3' hoac
    'LT_JMP   R0, R1, +4'. Dung cho log/test, khong dung trong runtime.
    """
    d = decode_instruction(word)
    info = OPCODE_INFO[d.op]
    operands = []
    if info.a_kind != OperandKind.UNUSED:
        operands.append(_format_operand(info.a_kind, d.a))
    if info.b_kind != OperandKind.UNUSED:
        operands.append(_format_operand(info.b_kind, d.b))
    if info.c_kind != OperandKind.UNUSED:
        operands.append(_format_operand(info.c_kind, d.c))
    operand_str = ", ".join(operands)
    return f"{info.name:<12} {operand_str}".rstrip()


# ---------------------------------------------------------------------------
# Tien ich tra cuu nguoc (dung cho assembler/debug tool neu can)
# ---------------------------------------------------------------------------

NAME_TO_OPCODE: Dict[str, Opcode] = {info.name: op for op, info in OPCODE_INFO.items()}


__all__ = [
    "Opcode", "OperandKind", "OpcodeInfo", "OPCODE_INFO",
    "RISC_OPCODES", "CISC_OPCODES", "NAME_TO_OPCODE",
    "OP_BITS", "A_BITS", "B_BITS", "C_BITS", "WORD_BITS",
    "OP_MAX", "A_MAX", "B_MAX", "C_MAX",
    "B_SIGNED_BIAS", "C_SIGNED_BIAS",
    "DecodedInstruction",
    "encode_instruction", "decode_instruction", "disassemble",
    "OpcodeError", "OpcodeEncodeError", "OpcodeDecodeError",
]
