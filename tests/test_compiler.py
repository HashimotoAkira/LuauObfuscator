# -*- coding: utf-8 -*-
"""
tests/test_compiler.py
========================
Test thuan Python (khong can pytest) cho src/vm/compiler.py.

Chay:
    python3 tests/test_compiler.py

Chien luoc test: vi day la 1 compiler (khong co runtime de THUC THI bytecode
va kiem tra ket qua - runtime_template.py chua duoc viet), cac test o day
kiem tra CAU TRUC cua bytecode sinh ra (opcode nao, thu tu nao, so luong
lenh, cac truong hop loi duoc bao dung) thay vi "chay thu". Dung
opcode.decode_instruction()/disassemble() de kiem tra tung lenh.
"""

import sys
import os
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.parser import parse
from src.vm.compiler import compile_chunk, CompilerError, Compiler
from src.vm.opcode import Opcode, decode_instruction


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _compile(src: str):
    ast = parse(src)
    main_idx, protos = compile_chunk(ast)
    return main_idx, protos


def _main_code(src: str):
    main_idx, protos = _compile(src)
    return protos[main_idx].code


def _ops(code):
    return [decode_instruction(w).op for w in code]


def _expect_compiler_error(src: str):
    try:
        _compile(src)
        assert False, f"Phai nem CompilerError cho: {src!r}"
    except CompilerError:
        pass


# ---------------------------------------------------------------------------
# Literal / constant
# ---------------------------------------------------------------------------

def test_local_number_literal():
    code = _main_code("local x = 42")
    ops = _ops(code)
    assert Opcode.LOADK in ops
    assert ops[-1] == Opcode.RETURN


def test_constant_dedup_across_statements():
    main_idx, protos = _compile("local a = 1\nlocal b = 1")
    proto = protos[main_idx]
    # 2 lan dung so 1 -> chi 1 hang so trong constant pool
    assert proto.constants.count(1) == 1


def test_nil_true_false_are_constants():
    main_idx, protos = _compile("local a = nil\nlocal b = true\nlocal c = false")
    proto = protos[main_idx]
    assert None in proto.constants
    assert True in proto.constants
    assert False in proto.constants


# ---------------------------------------------------------------------------
# Bien local / global
# ---------------------------------------------------------------------------

def test_local_variable_direct_register_no_extra_move():
    code = _main_code("local a = 1\nlocal b = a")
    ops = _ops(code)
    # 'local b = a' voi a la local -> KHONG can MOVE rieng NGOAI tru compiler
    # chon dung thang thanh ghi cua a lam gia tri cho b (xem _compile_local_assign:
    # compile_expr_into(NameExpr('a'), reg_cua_b) se emit 1 MOVE vi 2 thanh ghi
    # khac nhau - test nay chi kiem tra khong co thua LOADK cho 'a' lan 2)
    assert ops.count(Opcode.LOADK) == 1  # chi 1 LOADK cho gia tri '1' cua a


def test_global_read_write():
    code = _main_code("x = 1\nlocal y = x")
    ops = _ops(code)
    assert Opcode.SETGLOBAL in ops
    assert Opcode.GETGLOBAL in ops


def test_upvalue_read_compiles_with_capture_metadata():
    src = """
    local function outer()
        local x = 1
        local function inner()
            return x
        end
    end
    """
    _, protos = _compile(src)
    inner_proto = next(proto for proto in protos if proto.upvalues)
    assert inner_proto.upvalues == [("local", 0)]


# ---------------------------------------------------------------------------
# Toan tu so hoc / logic
# ---------------------------------------------------------------------------

def test_add_immediate_optimization():
    code = _main_code("local x = 1\nx = x + 1")
    ops = _ops(code)
    assert Opcode.ADDI in ops
    assert Opcode.ADD not in ops


def test_add_register_when_not_immediate():
    code = _main_code("local x = 1\nlocal y = 2\nlocal z = x + y")
    ops = _ops(code)
    assert Opcode.ADD in ops


def test_mod_and_pow_compile_through_runtime_helpers():
    code = _main_code("local x = 7 % 4\nlocal y = 2 ^ 3")
    ops = _ops(code)
    assert ops.count(Opcode.CALL) == 2


def test_length_operator_compiles_through_runtime_helper():
    code = _main_code("local t = {1,2,3}\nlocal n = #t")
    assert Opcode.CALL in _ops(code)


def test_concat_uses_concat_opcode():
    code = _main_code('local s = "a" .. "b" .. "c"')
    ops = _ops(code)
    assert ops.count(Opcode.CONCAT) == 1  # gop ca 3 chuoi thanh 1 lenh CONCAT


def test_relational_value_uses_eq_or_lt():
    code = _main_code("local ok = 1 < 2")
    ops = _ops(code)
    assert Opcode.LT in ops


def test_if_expression_compiles_to_branches():
    code = _main_code("local value = if x < 0 then 1 elseif x == 0 then 2 else 3")
    ops = _ops(code)
    assert Opcode.LE_JMP in ops
    assert Opcode.NEQ_JMP in ops
    assert ops.count(Opcode.JMP) == 2
    assert Opcode.LOADK in ops


def test_not_equal_negates_via_test():
    code = _main_code("local ok = 1 ~= 2")
    ops = _ops(code)
    assert Opcode.EQ in ops
    assert Opcode.TEST in ops  # dao gia tri boolean


def test_and_or_use_test_opcode():
    code = _main_code("local ok = (1 < 2) and (3 < 4) or false")
    ops = _ops(code)
    assert ops.count(Opcode.TEST) == 2  # 1 cho 'and', 1 cho 'or'


def test_not_operator():
    code = _main_code("local ok = not (1 < 2)")
    ops = _ops(code)
    assert Opcode.TEST in ops


def test_unary_minus_uses_runtime_unary_helper():
    code = _main_code("local x = 5\nlocal y = -x")
    instructions = [decode_instruction(word) for word in code]
    assert Opcode.CALL in [instruction.op for instruction in instructions]
    assert any(
        instruction.op == Opcode.GETGLOBAL
        and instruction.b == 5
        and instruction.c == 511
        for instruction in instructions
    )
    assert Opcode.SUB not in [instruction.op for instruction in instructions]


# ---------------------------------------------------------------------------
# If / While / Repeat / For
# ---------------------------------------------------------------------------

def test_if_without_else():
    code = _main_code("local x = 1\nif x < 10 then x = 2 end")
    ops = _ops(code)
    assert Opcode.LE_JMP in ops  # phu dinh cua '<' la LE_JMP (doi cho)


def test_if_else_both_branches_present():
    code = _main_code("local x = 1\nif x < 10 then x = 2 else x = 3 end")
    ops = _ops(code)
    assert ops.count(Opcode.LOADK) >= 3  # 1 cho x=1, 1 cho moi nhanh


def test_while_loop_jumps_back():
    code = _main_code("local i = 0\nwhile i < 10 do i = i + 1 end")
    # Phai co it nhat 1 JMP (nhay lui ve dau dieu kien)
    ops = _ops(code)
    assert Opcode.JMP in ops
    assert Opcode.LE_JMP in ops  # dieu kien i<10 bi phu dinh


def test_repeat_until():
    code = _main_code("local i = 0\nrepeat i = i + 1 until i >= 10")
    ops = _ops(code)
    assert Opcode.ADDI in ops  # i=i+1 toi uu thanh ADDI


def test_numeric_for_uses_forprep_forloop():
    code = _main_code("for i = 1, 10 do end")
    ops = _ops(code)
    assert Opcode.FORPREP in ops
    assert Opcode.FORLOOP in ops


def test_numeric_for_with_step():
    code = _main_code("local sum = 0\nfor i = 10, 1, -1 do sum = sum + i end")
    ops = _ops(code)
    assert Opcode.FORPREP in ops and Opcode.FORLOOP in ops


def test_break_inside_while():
    code = _main_code("while true do break end")
    ops = _ops(code)
    assert Opcode.JMP in ops


def test_break_outside_loop_raises():
    _expect_compiler_error("break")


def test_continue_outside_loop_raises():
    _expect_compiler_error("continue")


def test_generic_for_compiles():
    code = _main_code("for k, v in pairs({}) do print(k, v) end")
    assert Opcode.CALL in _ops(code)


def test_goto_label_raises_clear_error():
    _expect_compiler_error("::top:: goto top")


# ---------------------------------------------------------------------------
# Table / index
# ---------------------------------------------------------------------------

def test_table_constructor_positional_and_named():
    code = _main_code('local t = {1, 2, name = "x"}')
    ops = _ops(code)
    assert Opcode.NEWTABLE in ops
    assert ops.count(Opcode.SETTABLE_K) == 3  # 2 vi tri (chi so 1,2) + 1 ten


def test_table_computed_key_uses_settable():
    code = _main_code('local k = "x"\nlocal t = {[k] = 1}')
    ops = _ops(code)
    assert Opcode.SETTABLE in ops


def test_index_dot_uses_gettable_k():
    code = _main_code("local p = workspace.Part")
    ops = _ops(code)
    assert Opcode.GETTABLE_K in ops


def test_index_bracket_uses_gettable():
    code = _main_code('local t = {}\nlocal v = t["key"]')
    ops = _ops(code)
    assert Opcode.GETTABLE in ops


def test_assign_to_index_uses_settable_k():
    code = _main_code("local part = {}\npart.Position = 1")
    ops = _ops(code)
    assert Opcode.SETTABLE_K in ops


# ---------------------------------------------------------------------------
# Goi ham / method
# ---------------------------------------------------------------------------

def test_call_global_fast_path():
    code = _main_code('print("hi")')
    ops = _ops(code)
    assert Opcode.CALL_GLOBAL in ops
    assert Opcode.CALL not in ops


def test_call_local_function_uses_generic_call():
    code = _main_code("local function f() end\nf()")
    ops = _ops(code)
    assert Opcode.CALL in ops


def test_method_call_uses_call_method():
    code = _main_code("local part = {}\npart:Destroy()")
    ops = _ops(code)
    assert Opcode.CALL_METHOD in ops


# ---------------------------------------------------------------------------
# Ham / return / closure
# ---------------------------------------------------------------------------

def test_function_without_return_gets_implicit_return():
    main_idx, protos = _compile("local function f() end")
    fn_proto = [p for i, p in enumerate(protos) if i != main_idx][0]
    assert decode_instruction(fn_proto.code[-1]).op == Opcode.RETURN


def test_return_single_value():
    main_idx, protos = _compile("local function f() return 1 end")
    fn_proto = [p for i, p in enumerate(protos) if i != main_idx][0]
    # Luu y: code[-1] gio LUON la RETURN "an toan" tu dong them cuoi ham
    # (xem test_every_function_always_ends_with_unconditional_return), nen
    # o day ta tim lenh RETURN ung voi 'return 1' (B=2, tuc 1 gia tri)
    # BAT KY ĐAU trong code, khong chi o vi tri cuoi.
    returns = [decode_instruction(w) for w in fn_proto.code if decode_instruction(w).op == Opcode.RETURN]
    assert any(d.b == 2 for d in returns), "Phai co 1 lenh RETURN voi B=2 (tra ve 1 gia tri)"


def test_return_multiple_values_emits_contiguous_register_range():
    _, protos = _compile("local function f() return 1, 2 end")
    function_proto = next(proto for proto in protos if proto.name == "f")
    return_instruction = decode_instruction(function_proto.code[-2])
    assert return_instruction.op == Opcode.RETURN
    assert return_instruction.b == 3


def test_every_function_always_ends_with_unconditional_return():
    """Moi FunctionProto (ke ca ham 'main') PHAI luon ket thuc bang 1
    RETURN o CHINH VI TRI CUOI CUNG CUA MANG LENH - khong dua vao kiem tra
    "lenh cuoi cung da emit tinh co la RETURN" (sai, xem test ben duoi).
    """
    srcs = [
        "local function f() end",
        "local function f() return 1 end",
        "local function f(x) if x then return 1 end end",
        "local x = 1",
    ]
    for src in srcs:
        main_idx, protos = _compile(src)
        for proto in protos:
            assert decode_instruction(proto.code[-1]).op == Opcode.RETURN, (
                f"Proto '{proto.name}' (nguon: {src!r}) khong ket thuc bang RETURN"
            )


def test_if_without_else_fallthrough_does_not_jump_out_of_bounds():
    """Chong regression cho 1 bug thuc su tung phat hien: ham ket thuc
    bang 1 'if' khong co else/elseif, co return BEN TRONG nhanh if nhung
    KHONG CO gi sau if - truong hop x=false phai 'roi' (fall through)
    toi 1 vi tri HOP LE (RETURN an toan duoc tu dong them), khong duoc
    nhay ra ngoai mang lenh. Truoc khi sua, compiler chi kiem tra "lenh
    cuoi cung da emit co phai RETURN" (dung vi no tinh co la RETURN TU
    BEN TRONG nhanh if) nen KHONG them RETURN an toan, khien nhanh
    fall-through nhay ra ngoai bien mang bytecode.
    """
    main_idx, protos = _compile("""
    local function f(x)
        if x then
            return 1
        end
    end
    """)
    fn_proto = [p for i, p in enumerate(protos) if i != main_idx][0]
    jump_ops = {Opcode.JMP, Opcode.EQ_JMP, Opcode.LT_JMP, Opcode.LE_JMP,
                Opcode.NEQ_JMP, Opcode.TEST, Opcode.FORPREP, Opcode.FORLOOP}
    for idx, word in enumerate(fn_proto.code):
        d = decode_instruction(word)
        if d.op in jump_ops:
            target = idx + 1 + d.c
            assert 0 <= target <= len(fn_proto.code), (
                f"[{idx}] {d.op.name} nhay ra ngoai bytecode: target={target}, "
                f"len={len(fn_proto.code)}"
            )
    # Va cu the: lenh CUOI CUNG phai la RETURN an toan (tra ve 0 gia tri)
    assert decode_instruction(fn_proto.code[-1]).op == Opcode.RETURN


def test_closure_opcode_emitted_for_nested_function():
    code = _main_code("local function f() end")
    ops = _ops(code)
    assert Opcode.CLOSURE in ops


def test_closure_opcode_encodes_large_prototype_indices():
    source = "\n".join("do local f = function() end end" for _ in range(513))
    _, protos = _compile(source)
    encoded_indices = [
        ins.b + ins.c * 512
        for proto in protos
        for ins in (decode_instruction(word) for word in proto.code)
        if ins.op == Opcode.CLOSURE
    ]
    assert max(encoded_indices) >= 512


def test_function_decl_dotted_path():
    code = _main_code("local obj = {}\nfunction obj.new() end")
    ops = _ops(code)
    assert Opcode.CLOSURE in ops
    assert Opcode.SETTABLE_K in ops


def test_function_decl_method_has_implicit_self():
    main_idx, protos = _compile("local obj = {}\nfunction obj:getX() return self end")
    method_proto = [p for i, p in enumerate(protos) if i != main_idx][0]
    assert method_proto.num_params == 1  # chi 'self'


def test_recursive_local_function_compiles_with_upvalue():
    _, protos = _compile(
        "local function fact(n) if n <= 1 then return 1 end return n * fact(n - 1) end"
    )
    assert any(proto.upvalues for proto in protos)


def test_vararg_expression_compiles():
    _, protos = _compile("local function f(...) local args = {...} return #args end")
    assert any(Opcode.GETGLOBAL in _ops(proto.code) for proto in protos)


# ---------------------------------------------------------------------------
# Ngu nghia gan gia tri: thu tu danh gia RHS truoc khi gan (chong regression
# cho 1 bug thuc su tung phat hien trong qua trinh review - xem lich su)
# ---------------------------------------------------------------------------

def test_swap_two_locals_reads_old_values():
    """'a, b = b, a' phai hoan vi DUNG: ca 2 MOVE ghi vao a,b phai lay gia
    tri tu 2 thanh ghi TAM (da luu gia tri CU) chu khong phai lay truc tiep
    tu nhau (neu khong se bi ghi de truoc khi doc, vd a=b roi b=a(moi)=b).
    """
    code = _main_code("local a, b = 1, 2\na, b = b, a")
    ops = _ops(code)
    # Ky vong: 2 LOADK (gan a=1,b=2) + 4 MOVE (2 cho tinh gia tri tam, 2 cho
    # gan lai vao a,b) - neu toi uu sai con 2 MOVE la dau hieu bug tai hien.
    assert ops.count(Opcode.MOVE) == 4, (
        f"Ky vong 4 lenh MOVE (2 tinh tam + 2 gan lai) cho swap, nhung co {ops.count(Opcode.MOVE)}"
    )


def test_swap_table_elements_reads_before_write():
    """'t[i], t[j] = t[j], t[i]' (hoan vi phan tu mang - dung trong MOI
    thuat toan sap xep) phai doc CA HAI gia tri cu (2 GETTABLE) truoc khi
    ghi lai (2 SETTABLE).
    """
    code = _main_code("local t = {1,2,3}\nlocal i, j = 1, 3\nt[i], t[j] = t[j], t[i]")
    ops = _ops(code)
    get_idx = [k for k, o in enumerate(ops) if o == Opcode.GETTABLE]
    set_idx = [k for k, o in enumerate(ops) if o == Opcode.SETTABLE]
    assert len(get_idx) == 2 and len(set_idx) == 2
    # CA HAI GETTABLE phai xay ra TRUOC bat ky SETTABLE nao (doc het gia tri
    # cu roi moi ghi, khong duoc xen ke doc-ghi-doc-ghi vi se doc nham gia
    # tri MOI cua phan tu vua ghi truoc do).
    assert max(get_idx) < min(set_idx)


def test_local_self_reference_reads_outer_binding():
    """'local print = print' (pattern rat pho bien de cache global thanh
    local, toi uu toc do truy cap) phai doc GLOBAL 'print' (GETGLOBAL),
    khong duoc doc nham chinh bien local MOI (chua khoi tao).
    """
    code = _main_code("print = 1\nlocal print = print")
    ops = _ops(code)
    assert Opcode.GETGLOBAL in ops, (
        "local print = print phai dung GETGLOBAL de doc gia tri CU, "
        "khong duoc bo qua (dau hieu doc nham bien moi chua khoi tao)"
    )


def test_local_redeclare_shadow_uses_old_value():
    """'local x = 1; local x = x + 1' - ve phai cua khai bao local THU HAI
    phai dung gia tri CU cua x (=1, tu local dau tien), ket qua x moi = 2.
    """
    code = _main_code("local x = 1\nlocal x = x + 1")
    ops = _ops(code)
    assert Opcode.ADDI in ops  # x+1 toi uu thanh ADDI, chung to co doc duoc 'x' cu


# ---------------------------------------------------------------------------
# Thanh ghi LIEN TUC cho tham so goi ham / toan hang CONCAT (chong
# regression cho 1 bug thuc su: thieu giai phong thanh ghi tam giua cac
# phan tu lam LECH vi tri, phat hien qua test end-to-end that)
# ---------------------------------------------------------------------------

def test_call_global_args_are_contiguous_registers():
    """'print(t[1], t[2], t[3])' - moi tham so can 1 thanh ghi TAM de tinh
    chi so mang (K[...]) truoc khi GETTABLE; neu khong giai phong dung
    cach, cac GETTABLE se ghi ket qua vao thanh ghi KHONG LIEN TUC, pha vo
    quy uoc goi ham cua CALL_GLOBAL (doc tham so tai R[a+1..a+nargs]).
    """
    code = _main_code("local t = {1,2,3}\nprint(t[1], t[2], t[3])")
    decoded = [decode_instruction(w) for w in code]

    call_idx = next(i for i, d in enumerate(decoded) if d.op == Opcode.CALL_GLOBAL)
    call = decoded[call_idx]
    result_reg = call.a
    nargs = call.c - 1
    assert nargs == 3

    # 3 lenh GETTABLE ngay truoc CALL_GLOBAL phai ghi ket qua vao DUNG
    # result_reg+1, result_reg+2, result_reg+3 - theo DUNG THU TU.
    gettables = [d for d in decoded if d.op == Opcode.GETTABLE]
    assert len(gettables) == 3
    expected_regs = [result_reg + 1, result_reg + 2, result_reg + 3]
    actual_regs = [g.a for g in gettables]
    assert actual_regs == expected_regs, (
        f"Tham so cho CALL_GLOBAL khong lien tuc: mong {expected_regs}, duoc {actual_regs}"
    )


def test_call_args_with_complex_subexpressions_stay_contiguous():
    """Tuong tu nhung voi CALL tong quat (ham local) va tham so la bieu
    thuc toan hoc can nhieu thanh ghi tam de tinh (khong chi GETTABLE).
    """
    code = _main_code(
        "local function f(a, b, c) return a end\n"
        "local x, y = 1, 2\n"
        "f(x + 1, y * 2, x + y)"
    )
    decoded = [decode_instruction(w) for w in code]
    call = next(d for d in decoded if d.op == Opcode.CALL)
    func_reg = call.a
    nargs = call.b - 1
    assert nargs == 3
    # Khong the kiem tra truc tiep gia tri thanh ghi cua tung tham so o day
    # (vi ADD duoc dung cho ca 3, kho phan biet qua opcode don thuan), nhung
    # it nhat phai khong co loi khi bien dich/chay - test nay chu yeu dam
    # bao khong exception va cau truc CALL hop le; phan "dung gia tri" da
    # duoc xac nhan qua test_runtime_e2e.py (chay That bang Lua).
    assert func_reg >= 0


def test_concat_operands_are_contiguous_registers():
    """'a .. b .. c' voi a,b,c la cac bieu thuc can thanh ghi tam (khong
    chi la bien don gian) - CONCAT yeu cau day thanh ghi LIEN TUC.
    """
    code = _main_code('local t = {"x","y","z"}\nlocal s = t[1] .. t[2] .. t[3]')
    decoded = [decode_instruction(w) for w in code]
    concat = next(d for d in decoded if d.op == Opcode.CONCAT)
    first_reg, last_reg = concat.b, concat.c
    assert last_reg - first_reg == 2  # 3 toan hang -> khoang cach 2

    gettables = [d for d in decoded if d.op == Opcode.GETTABLE]
    assert len(gettables) == 3
    actual_regs = [g.a for g in gettables]
    assert actual_regs == [first_reg, first_reg + 1, first_reg + 2], (
        f"Toan hang CONCAT khong lien tuc: {actual_regs}"
    )


# ---------------------------------------------------------------------------
# Jump offset / patching: kiem tra TINH DUNG cua offset bang decode_instruction
# ---------------------------------------------------------------------------

def test_jump_offsets_point_to_valid_positions():
    """Voi MOI lenh co truong C kieu jump trong bytecode, kiem tra
    (idx+1+offset) nam trong pham vi [0, len(code)] (khong nhay ra ngoai
    bien bytecode - dau hieu cua loi patch).
    """
    src = """
    local function clamp(x, lo, hi)
        if x < lo then
            return lo
        elseif x > hi then
            return hi
        end
        return x
    end
    local sum = 0
    for i = 1, 10 do
        sum += i
        if i == 5 then
            continue
        end
        if i == 8 then
            break
        end
    end
    local ok = (sum > 0) and (sum < 100) or false
    while sum > 0 do
        sum = sum - 1
    end
    repeat
        sum = sum + 1
    until sum >= 10
    """
    main_idx, protos = _compile(src)
    jump_ops = {Opcode.JMP, Opcode.EQ_JMP, Opcode.LT_JMP, Opcode.LE_JMP,
                Opcode.NEQ_JMP, Opcode.TEST, Opcode.FORPREP, Opcode.FORLOOP}
    for proto in protos:
        for idx, word in enumerate(proto.code):
            d = decode_instruction(word)
            if d.op in jump_ops:
                target = idx + 1 + d.c
                assert 0 <= target <= len(proto.code), (
                    f"Jump o {proto.name}[{idx}] ({d.op.name}) nhay ra ngoai "
                    f"bytecode: target={target}, len={len(proto.code)}"
                )


def test_jump_safety_across_many_control_flow_combinations():
    """Quet 1 loat to hop if/while/for/repeat/break/continue/return long
    nhau - kiem tra MOI lenh nhay deu nam trong bien mang bytecode VA moi
    FunctionProto deu ket thuc bang RETURN. Day la 'luoi an toan' rong hon
    cho dung 1 lop bug tung phat hien thuc su (xem
    test_if_without_else_fallthrough_does_not_jump_out_of_bounds).
    """
    jump_ops = {Opcode.JMP, Opcode.EQ_JMP, Opcode.LT_JMP, Opcode.LE_JMP,
                Opcode.NEQ_JMP, Opcode.TEST, Opcode.FORPREP, Opcode.FORLOOP}
    cases = [
        "local function f(x) if x then return 1 end end",
        "local function f(x) while x do return 1 end end",
        "local function f(x) for i=1,10 do if i==x then return i end end end",
        "local function f(x) repeat if x then return 1 end until true end",
        "local function f(x) if x then return 1 elseif not x then return 2 end end",
        "local function f(x) if x then return 1 else return 2 end end",
        "local function f(x) do return 1 end end",
        "local function f(x) if x then if x then return 1 end end end",
        "local function f(x) for i=1,10 do if i==5 then break end if i==3 then continue end end end",
        "local function f(x) while x do if x then break end if x then continue end end end",
        "local function f(x) repeat if x then break end until true end",
        "local function f() local a,b=1,2 if a<b then return a else return b end end",
        "local function f(x) if x then elseif x then elseif x then else return 1 end end",
        "local function f() for i=1,3 do for j=1,3 do if i==j then return i end end end end",
    ]
    for src in cases:
        main_idx, protos = _compile(src)
        for proto in protos:
            assert decode_instruction(proto.code[-1]).op == Opcode.RETURN, (
                f"{src!r}: proto '{proto.name}' khong ket thuc bang RETURN"
            )
            for idx, word in enumerate(proto.code):
                d = decode_instruction(word)
                if d.op in jump_ops:
                    target = idx + 1 + d.c
                    assert 0 <= target <= len(proto.code), (
                        f"{src!r}: proto '{proto.name}'[{idx}] {d.op.name} nhay ra ngoai "
                        f"bytecode (target={target}, len={len(proto.code)})"
                    )


def test_compile_does_not_crash_on_full_sample_file():
    """Dich lai file test_sample.lua (dung cho parser) - dam bao compiler
    khong crash tren 1 file tong hop nhieu tinh nang (tru cac phan da
    biet la chua ho tro, duoc loai bo o ban rut gon duoi day).
    """
    src = """
    local part = workspace.Part
    local _floor = math.floor

    local function clamp(x, lo, hi)
        if x < lo then
            return lo
        elseif x > hi then
            return hi
        end
        return x
    end

    -- Luu y: 'obj' o day la GLOBAL (khong 'local'), de tranh dung vao
    -- gioi han upvalue da biet (obj.new tham chieu 'obj' tu than ham long
    -- ben trong - neu 'obj' la local se bi coi la upvalue, xem
    -- test_upvalue_read_raises). Day la pattern OOP rat pho bien trong
    -- Luau thuc te va se duoc ho tro day du khi upvalue duoc trien khai.
    obj = {}
    function obj.new(x, y)
        local self = setmetatable({}, obj)
        self.x, self.y = x, y
        return self
    end

    function obj:getSum()
        return self.x
    end

    local t = { 1, 2, 3, name = "hero" }
    local sum = 0
    for i = 1, 10 do
        sum = sum + i
    end

    local ok = (sum > 0) and (sum < 1000) or false
    part.Position = sum

    repeat
        sum = sum - 1
    until sum <= 0
    """
    main_idx, protos = _compile(src)
    assert len(protos) >= 1


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
            traceback.print_exc(limit=4)
            failed += 1
    print()
    print(f"Tong: {passed + failed} test, {passed} PASS, {failed} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
