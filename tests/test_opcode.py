# -*- coding: utf-8 -*-
"""
tests/test_opcode.py
=====================
Test thuan Python (khong can pytest) cho src/vm/opcode.py.

Chay:
    python3 tests/test_opcode.py
"""

import sys
import os
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.vm.opcode import (
    Opcode, OPCODE_INFO, RISC_OPCODES, CISC_OPCODES,
    encode_instruction, decode_instruction, disassemble,
    OpcodeEncodeError, OpcodeDecodeError,
    OP_MAX, A_MAX, B_MAX, C_MAX, C_SIGNED_BIAS, B_SIGNED_BIAS,
)


def test_opcode_count_and_values():
    assert len(list(Opcode)) == 32
    values = sorted(int(op) for op in Opcode)
    assert values == list(range(32)), values
    assert len(RISC_OPCODES) == 16 and len(CISC_OPCODES) == 16


def test_every_opcode_has_info():
    for op in Opcode:
        assert op in OPCODE_INFO, op


def test_roundtrip_reg_reg_reg():
    word = encode_instruction(Opcode.ADD, a=1, b=2, c=3)
    d = decode_instruction(word)
    assert d.op == Opcode.ADD
    assert (d.a, d.b, d.c) == (1, 2, 3)


def test_roundtrip_move():
    word = encode_instruction(Opcode.MOVE, a=10, b=20)
    d = decode_instruction(word)
    assert d.op == Opcode.MOVE
    assert d.a == 10 and d.b == 20 and d.c == 0


def test_roundtrip_loadk_const_index():
    word = encode_instruction(Opcode.LOADK, a=0, b=511)  # B_MAX
    d = decode_instruction(word)
    assert d.b == 511


def test_jmp_signed_positive_and_negative():
    fwd = encode_instruction(Opcode.JMP, c=100)
    back = encode_instruction(Opcode.JMP, c=-100)
    assert decode_instruction(fwd).c == 100
    assert decode_instruction(back).c == -100


def test_jmp_signed_boundaries():
    lo = encode_instruction(Opcode.JMP, c=-C_SIGNED_BIAS)
    hi = encode_instruction(Opcode.JMP, c=C_SIGNED_BIAS - 1)
    assert decode_instruction(lo).c == -C_SIGNED_BIAS
    assert decode_instruction(hi).c == C_SIGNED_BIAS - 1

    try:
        encode_instruction(Opcode.JMP, c=-C_SIGNED_BIAS - 1)
        assert False, "Phai nem OpcodeEncodeError khi tran duoi"
    except OpcodeEncodeError:
        pass

    try:
        encode_instruction(Opcode.JMP, c=C_SIGNED_BIAS)
        assert False, "Phai nem OpcodeEncodeError khi tran tren"
    except OpcodeEncodeError:
        pass


def test_addi_negative_immediate():
    word = encode_instruction(Opcode.ADDI, a=1, b=1, c=-5)
    d = decode_instruction(word)
    assert d.c == -5


def test_unused_field_must_be_zero():
    try:
        encode_instruction(Opcode.MOVE, a=1, b=2, c=5)  # C khong dung o MOVE
        assert False, "Phai nem loi vi C khong dung nhung != 0"
    except OpcodeEncodeError:
        pass


def test_register_field_out_of_range_raises():
    try:
        encode_instruction(Opcode.ADD, a=256, b=0, c=0)  # A_MAX=255
        assert False
    except OpcodeEncodeError:
        pass
    try:
        encode_instruction(Opcode.ADD, a=0, b=512, c=0)  # B_MAX=511
        assert False
    except OpcodeEncodeError:
        pass
    try:
        encode_instruction(Opcode.ADD, a=0, b=0, c=1024)  # C_MAX=1023
        assert False
    except OpcodeEncodeError:
        pass


def test_decode_unknown_opcode_raises():
    # Dung thu ca 32 opcode (0..31) -> moi gia tri deu hop le, nen test nay
    # chi co the kiem tra qua duong bien tren cua truong OP (5 bit = 0..31,
    # vua khop 32 opcode nen khong co "khoang trong" -> thay vao do test
    # 1 word gia (word > 32-bit) bi tu choi boi decode_instruction.
    try:
        decode_instruction(1 << 33)
        assert False
    except OpcodeDecodeError:
        pass


def test_call_uses_hint_not_signed():
    # CALL: B=nargs+1, C=nret+1 - deu la HINT (khong am), 0 la hop le (vd
    # goi ham 0 tham so -> B=1; khong can gia tri am)
    word = encode_instruction(Opcode.CALL, a=5, b=1, c=1)
    d = decode_instruction(word)
    assert d.b == 1 and d.c == 1


def test_all_opcodes_roundtrip_with_safe_operands():
    """Voi moi opcode, thu encode/decode voi 1 bo gia tri "an toan" (0 cho
    moi truong duoc dung, trong pham vi hop le) de dam bao khong opcode
    nao bi loi cau hinh OperandKind.
    """
    for op in Opcode:
        info = OPCODE_INFO[op]

        def safe_value(kind):
            from src.vm.opcode import OperandKind
            if kind == OperandKind.UNUSED:
                return 0
            if kind in (OperandKind.IMM, OperandKind.JMP):
                return 1  # gia tri signed nho, chac chan hop le
            return 1  # REG/CONST/HINT/PROTO: gia tri unsigned nho

        a = safe_value(info.a_kind)
        b = safe_value(info.b_kind)
        c = safe_value(info.c_kind)
        word = encode_instruction(op, a=a, b=b, c=c)
        d = decode_instruction(word)
        assert d.op == op, f"{op.name}: opcode khong khop sau roundtrip"


def test_disassemble_readable_output():
    word = encode_instruction(Opcode.ADD, a=1, b=2, c=3)
    text = disassemble(word)
    assert text.startswith("ADD"), text
    assert "R1" in text and "R2" in text and "R3" in text


def test_disassemble_jump_shows_sign():
    word = encode_instruction(Opcode.LT_JMP, a=0, b=1, c=-3)
    text = disassemble(word)
    assert "LT_JMP" in text
    assert "-3" in text


def test_disassemble_const_and_global():
    word = encode_instruction(Opcode.GETGLOBAL, a=0, b=7)
    text = disassemble(word)
    assert "K[7]" in text


def test_word_fits_in_32_bits_for_max_fields():
    word = encode_instruction(Opcode.CONCAT, a=A_MAX, b=1, c=1)
    assert 0 <= word <= 0xFFFFFFFF


def test_risc_cisc_classification_matches_value_ranges():
    for op in Opcode:
        if int(op) < 16:
            assert op in RISC_OPCODES and not OPCODE_INFO[op].is_cisc
        else:
            assert op in CISC_OPCODES and OPCODE_INFO[op].is_cisc


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    test_fns = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed, failed = 0, 0
    for name, fn in test_fns:
        try:
            fn()
            print(f"  OK   {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {name}: {exc}")
            traceback.print_exc(limit=3)
            failed += 1
    print()
    print(f"Tong: {passed + failed} test, {passed} PASS, {failed} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
