# -*- coding: utf-8 -*-
"""
tests/test_runtime_e2e.py
===========================
Test "dau cuoi" (end-to-end) THAT SU: dich 1 doan Luau -> render runtime
Lua -> CHAY BANG LUA INTERPRETER THAT (lua5.4, qua subprocess) -> so sanh
stdout thuc te voi ket qua mong doi. Day la lop test MANH NHAT trong du
an (manh hon test cau truc bytecode o test_compiler.py) vi no xac nhan
toan bo pipeline (lexer->parser->compiler->runtime) cho ra KET QUA DUNG
tren 1 Lua interpreter thuc su, khong chi "bytecode trong co ve dung".

Yeu cau: binary `lua5.4` phai co trong PATH (cai qua apt: `apt-get install
-y lua5.4`). Neu khong co, cac test se SKIP (bao ro ly do) thay vi FAIL,
de khong chan CI/moi truong khong co Lua.

Chay:
    python3 tests/test_runtime_e2e.py
"""

import sys
import os
import subprocess
import shutil
import tempfile
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.vm.runtime_template import compile_and_render

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = os.path.join(ROOT_DIR, "tests", "fixtures")
LOCAL_LUAU = os.path.normpath(os.path.join(ROOT_DIR, "..", ".storage", "Luau", "luau.exe"))
LOCAL_LUAU_COMPILE = os.path.normpath(
    os.path.join(ROOT_DIR, "..", ".storage", "Luau", "luau-compile.exe")
)
LUA_BIN = shutil.which("lua5.4") or shutil.which("luau")
if LUA_BIN is None and os.path.isfile(LOCAL_LUAU):
    LUA_BIN = LOCAL_LUAU


def _run_lua_code(lua_code: str) -> str:
    with tempfile.NamedTemporaryFile(mode="w", suffix=".lua", delete=False) as f:
        f.write(lua_code)
        path = f.name
    try:
        result = subprocess.run(
            [LUA_BIN, path], capture_output=True, text=True, timeout=10
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"Lua thoat voi loi (code {result.returncode}):\n"
                f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}\n"
                f"--- generated lua ---\n{lua_code}"
            )
        return result.stdout.strip()
    finally:
        os.unlink(path)


def _run_lua(source: str) -> str:
    """Dich + render + chay 1 doan Luau, tra ve stdout (da strip)."""
    return _run_lua_code(compile_and_render(source))


def _expect_output(src: str, expected: str, msg: str = ""):
    actual = _run_lua(src)
    assert actual == expected, (
        f"{msg}\nNguon:\n{src}\nMong doi: {expected!r}\nThuc te:  {actual!r}"
    )


# ---------------------------------------------------------------------------
# Arithmetic / so sanh
# ---------------------------------------------------------------------------

def test_basic_arithmetic():
    _expect_output("print(1 + 2)", "3")
    _expect_output("print(10 - 3)", "7")
    _expect_output("print(4 * 5)", "20")
    _expect_output("print(10 / 4)", "2.5")


def test_addi_subi_muli_immediate_path():
    _expect_output("local x = 10\nx = x + 5\nprint(x)", "15")
    _expect_output("local x = 10\nx = x - 5\nprint(x)", "5")
    _expect_output("local x = 10\nx = x * 3\nprint(x)", "30")


def test_unary_minus():
    _expect_output("local x = 5\nprint(-x)", "-5")


def test_vector_like_arithmetic_preserves_operator_metamethods():
    src = """
    local vectorMt = {}
    vectorMt.__add = function(a, b)
        return setmetatable({x = a.x + b.x}, vectorMt)
    end
    vectorMt.__sub = function(a, b)
        return setmetatable({x = a.x - b.x}, vectorMt)
    end
    vectorMt.__mul = function(a, b)
        if type(a) == "number" then a, b = b, a end
        return setmetatable({x = a.x * b}, vectorMt)
    end
    vectorMt.__div = function(a, b)
        return setmetatable({x = a.x / b}, vectorMt)
    end
    vectorMt.__unm = function(value)
        return setmetatable({x = -value.x}, vectorMt)
    end
    local a = setmetatable({x = 7}, vectorMt)
    local b = setmetatable({x = 2}, vectorMt)
    local result = -((a + b - b) * 2 / 2)
    print(result.x)
    """
    _expect_output(src, "-7")


def test_comparisons_as_values():
    _expect_output("print(1 < 2)", "true")
    _expect_output("print(2 < 1)", "false")
    _expect_output("print(1 == 1)", "true")
    _expect_output("print(1 ~= 1)", "false")
    _expect_output("print(3 <= 3)", "true")
    _expect_output("print(3 >= 4)", "false")
    _expect_output("print(5 > 2)", "true")


def test_not_operator():
    _expect_output("print(not true)", "false")
    _expect_output("print(not false)", "true")
    _expect_output("print(not nil)", "true")


def test_and_or_short_circuit_value():
    _expect_output("print(1 and 2)", "2")
    _expect_output("print(false and 2)", "false")
    _expect_output("print(nil or 5)", "5")
    _expect_output("print(3 or 5)", "3")
    _expect_output('print((1<2) and (3<4) or false)', "true")
    _expect_output('print((1<2) and (3>4) or false)', "false")


def test_string_concat_chain():
    _expect_output('print("a" .. "b" .. "c")', "abc")
    _expect_output('local x,y=1,2\nprint("x=" .. x .. " y=" .. y)', "x=1 y=2")


# ---------------------------------------------------------------------------
# Bien / gan / hoan vi (chong regression cho bug tung phat hien)
# ---------------------------------------------------------------------------

def test_local_and_global():
    _expect_output("local x = 42\nprint(x)", "42")
    _expect_output("y = 7\nprint(y)", "7")


def test_swap_two_locals_runtime():
    _expect_output(
        "local a, b = 1, 2\na, b = b, a\nprint(a, b)",
        "2\t1",
        "Hoan vi a,b=b,a phai cho a=2,b=1 (chong regression bug da sua)",
    )


def test_swap_table_elements_runtime():
    _expect_output(
        "local t = {10, 20, 30}\nlocal i, j = 1, 3\nt[i], t[j] = t[j], t[i]\n"
        "print(t[1], t[2], t[3])",
        "30\t20\t10",
        "Hoan vi phan tu mang (dung trong moi thuat toan sap xep)",
    )


def test_local_self_reference_runtime():
    # Cache global thanh local - pattern cuc ky pho bien trong Luau that
    _expect_output(
        'local pr = print\nprint = nil\npr("ok")',
        "ok",
    )


def test_local_redeclare_shadow_runtime():
    _expect_output("local x = 1\nlocal x = x + 1\nprint(x)", "2")


def test_compound_assign():
    _expect_output("local x = 1\nx += 10\nprint(x)", "11")
    _expect_output("local x = 10\nx -= 3\nprint(x)", "7")
    _expect_output('local s = "a"\ns ..= "b"\nprint(s)', "ab")


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------

def test_if_elseif_else():
    src = """
    local function classify(n)
        if n < 0 then
            return "neg"
        elseif n == 0 then
            return "zero"
        else
            return "pos"
        end
    end
    print(classify(-5), classify(0), classify(5))
    """
    _expect_output(src, "neg\tzero\tpos")


def test_if_expression_values_and_nested_expression_contexts():
    src = """
    local function classify(n)
        return if n < 0 then "negative" elseif n == 0 then "zero" else "positive"
    end
    local enabled = true
    local value = 2 + (if enabled then 10 else 20)
    print(classify(-1), classify(0), classify(1), value)
    """
    _expect_output(src, "negative\tzero\tpositive\t12")


def test_if_without_else_fallthrough_runtime():
    # Chong regression cho bug 'nhay ra ngoai bytecode' da phat hien va sua
    src = """
    local function f(x)
        if x then
            return 1
        end
    end
    print(f(true), (f(false)))
    """
    _expect_output(src, "1\tnil")


def test_while_loop():
    _expect_output(
        "local i = 0\nwhile i < 5 do i = i + 1 end\nprint(i)", "5"
    )


def test_repeat_until():
    _expect_output(
        "local i = 0\nrepeat i = i + 1 until i >= 5\nprint(i)", "5"
    )


def test_repeat_scope_sees_body_locals_in_until():
    # Ngu nghia dac biet cua repeat: bien local trong than van thay duoc
    # trong dieu kien until (khac while).
    _expect_output(
        "local n = 0\nrepeat local done = (n >= 3) n = n + 1 until done\nprint(n)",
        "4",
    )


def test_numeric_for_basic():
    _expect_output(
        "local sum = 0\nfor i = 1, 10 do sum = sum + i end\nprint(sum)", "55"
    )


def test_captured_numeric_for_variable_gets_per_iteration_cell():
    src = """
    local callbacks = {}
    for index = 1, 3 do
        callbacks[index] = function() return index end
        index *= 10
    end
    print(callbacks[1](), callbacks[2](), callbacks[3]())
    """
    _expect_output(src, "10\t20\t30")


def test_numeric_for_negative_step():
    # Dung bien dem rieng de kiem tra day gia tri vong lap va buoc am.
    _expect_output(
        "local t = {}\nlocal n = 0\nfor i = 5, 1, -1 do n = n + 1 t[n] = i end\n"
        'print(table.concat(t, ","))',
        "5,4,3,2,1",
    )


def test_numeric_for_zero_iterations():
    _expect_output(
        "local count = 0\nfor i = 10, 1 do count = count + 1 end\nprint(count)", "0"
    )


def test_break_in_loop():
    _expect_output(
        "local x = 0\nfor i = 1, 100 do x = i if i == 5 then break end end\nprint(x)",
        "5",
    )


def test_continue_in_loop():
    _expect_output(
        "local sum = 0\nfor i = 1, 5 do if i == 3 then continue end sum = sum + i end\nprint(sum)",
        "12",  # 1+2+4+5 = 12 (bo qua 3)
    )


def test_nested_loops_with_break():
    src = """
    local found = nil
    for i = 1, 3 do
        for j = 1, 3 do
            if i == 2 and j == 2 then
                found = i * 10 + j
                break
            end
        end
    end
    print(found)
    """
    _expect_output(src, "22")


# ---------------------------------------------------------------------------
# Bang / index
# ---------------------------------------------------------------------------

def test_table_constructor_and_index():
    _expect_output(
        'local t = {10, 20, name = "x"}\nprint(t[1], t[2], t.name)',
        "10\t20\tx",
    )


def test_table_computed_key():
    _expect_output(
        'local k = "dynamic"\nlocal t = {[k] = 99}\nprint(t.dynamic)', "99"
    )


def test_nested_table():
    _expect_output(
        "local t = {a = {b = {c = 123}}}\nprint(t.a.b.c)", "123"
    )


def test_table_mutation():
    _expect_output(
        "local t = {}\nt.x = 1\nt.x = t.x + 1\nprint(t.x)", "2"
    )


# ---------------------------------------------------------------------------
# Ham / goi ham / method / closure
# ---------------------------------------------------------------------------

def test_local_function_call():
    _expect_output(
        "local function add(a, b) return a + b end\nprint(add(3, 4))", "7"
    )

def test_multiple_return_values_and_fixed_assignment():
    src = """
    local function values()
        return 11, nil, 33
    end
    local a, b, c, d = values()
    print(a, b == nil, c, d == nil)
    """
    _expect_output(src, "11\ttrue\t33\ttrue")


def test_multiple_return_values_from_nested_calls():
    src = """
    local function values()
        return 4, 9
    end
    local function forward()
        local a, b = values()
        return a + 1, b + 1
    end
    local x, y = forward()
    print(x, y)
    """
    _expect_output(src, "5\t10")


def test_return_values_survive_later_expression_temporaries():
    src = """
    local function values(a, b)
        local y = b ^ 2
        return a + 10, y, (a * 7 - b * 3 + (a + b))
    end
    local x, y, z = values(14, 5)
    print(x, y, z)
    """
    _expect_output(src, "24\t25\t102")


def test_global_function_call_fast_path():
    _expect_output('print("hello")', "hello")


def test_executor_globals_fall_back_to_script_globals():
    actual = _run_lua_code(
        "local executorEnvironment = { executorOnly = 'executor' }\n"
        "getgenv = function() return executorEnvironment end\n"
        + compile_and_render(
            'executorOnly = executorOnly .. "-updated"\n'
            'print(executorOnly, type(print))'
        )
    )
    assert actual == "executor-updated\tfunction"


def test_task_scheduler_apis_receive_vm_closures_and_arguments():
    actual = _run_lua_code(
        "task = {\n"
        "  spawn = function(callback, ...)\n"
        "    local thread = coroutine.create(callback)\n"
        "    local ok, err = coroutine.resume(thread, ...)\n"
        "    if not ok then error(err) end\n"
        "    return thread\n"
        "  end,\n"
        "  defer = function(callback, ...) callback(...) end,\n"
        "  delay = function(_, callback, ...) callback(...) end,\n"
        "  wait = function(duration) return coroutine.yield(duration) end,\n"
        "  resume = coroutine.resume,\n"
        "}\n"
        + compile_and_render(
            """
            local total = 0
            local thread = task.spawn(function(value)
                total += value
                local elapsed = task.wait(0.25)
                total += elapsed
                return total
            end, 2)
            task.defer(function() total += 3 end)
            task.delay(0, function() total += 4 end)
            local resumed, result = task.resume(thread, 6)
            print(total, resumed, result)
            """
        )
    )
    assert actual == "15\ttrue\t15"


def test_function_no_args_no_return():
    _expect_output(
        "local function f() end\nf()\nprint(\"done\")", "done"
    )


def test_nested_function_calls():
    src = """
    local function double(x) return x * 2 end
    local function quad(x) return double(double(x)) end
    print(quad(5))
    """
    _expect_output(src, "20")

def test_upvalue_shared_mutable_capture():
    src = """
    local function makeCounter()
        local count = 0
        return function()
            count += 1
            return count
        end
    end
    local counter = makeCounter()
    print(counter(), counter(), counter())
    """
    _expect_output(src, "1\t2\t3")

def test_sibling_closures_share_capture_cell():
    src = """
    local read
    local increment
    do
        local value = 0
        read = function() return value end
        increment = function() value += 1 end
    end
    increment()
    increment()
    print(read())
    """
    _expect_output(src, "2")


def test_captured_scope_register_reuse_keeps_old_cell():
    src = """
    local first
    do
        local value = 1
        first = function() return value end
    end
    local second
    do
        local value = 2
        second = function() return value end
    end
    print(first(), second())
    """
    _expect_output(src, "1\t2")


def test_transitive_upvalue_capture():
    src = """
    local function outer()
        local value = 10
        return function()
            return function()
                value += 1
                return value
            end
        end
    end
    local makeInner = outer()
    local inner = makeInner()
    print(inner(), inner())
    """
    _expect_output(src, "11\t12")


def test_captured_parameter_mutation():
    src = """
    local function makeCounter(value)
        return function()
            value += 1
            return value
        end
    end
    local counter = makeCounter(4)
    print(counter(), counter())
    """
    _expect_output(src, "5\t6")


def test_recursive_local_function():
    src = """
    local function fact(n)
        if n <= 1 then return 1 end
        return n * fact(n - 1)
    end
    print(fact(5))
    """
    _expect_output(src, "120")


def test_method_call_via_metatable():
    src = """
    local Obj = {}
    Obj.__index = Obj
    function Obj.new(v)
        return setmetatable({value = v}, Obj)
    end
    function Obj:getValue()
        return self.value
    end
    function Obj:addTo(n)
        self.value = self.value + n
        return self
    end
    local o = Obj.new(10)
    o:addTo(5)
    print(o:getValue())
    """
    _expect_output(src, "15")


def test_global_recursive_function():
    # Global function CO THE de quy (khong giong 'local function' bi gioi
    # han upvalue) vi GETGLOBAL khong phu thuoc parent scope.
    src = """
    function fact(n)
        if n <= 1 then
            return 1
        end
        return n * fact(n - 1)
    end
    print(fact(5))
    """
    _expect_output(src, "120")


def test_function_returning_table_used_immediately():
    src = """
    local function makePoint(x, y)
        return {x = x, y = y}
    end
    local p = makePoint(3, 4)
    print(p.x, p.y)
    """
    _expect_output(src, "3\t4")


def test_anonymous_function_as_value():
    src = """
    local add = function(a, b) return a + b end
    print(add(2, 3))
    """
    _expect_output(src, "5")


# ---------------------------------------------------------------------------
# Tong hop: 1 doan kha day du tinh nang, mo phong code game don gian
# ---------------------------------------------------------------------------

def test_integration_clamp_and_loop():
    src = """
    local function clamp(x, lo, hi)
        if x < lo then
            return lo
        elseif x > hi then
            return hi
        end
        return x
    end

    local total = 0
    for i = 1, 20 do
        total = total + clamp(i, 5, 15)
    end
    print(total)
    """
    # clamp(i,5,15) cho i=1..20: 5,5,5,5,6,7,8,9,10,11,12,13,14,15,15,15,15,15,15,15
    expected_total = sum(max(5, min(15, i)) for i in range(1, 21))
    _expect_output(src, str(expected_total))


def test_integration_inventory_like_pattern():
    src = """
    local inventory = {}
    local function addItem(name, qty)
        inventory[name] = (inventory[name] or 0) + qty
    end
    addItem("sword", 1)
    addItem("potion", 3)
    addItem("potion", 2)
    print(inventory.sword, inventory.potion)
    """
    _expect_output(src, "1\t5")


def test_main_pipeline_vm_backend():
    from src.main import ObfuscationPipeline, PipelineConfig

    source = """
    local function sum_to(limit: number): number
        local total = 0
        for i = 1, limit do
            total = total + i
        end
        return total
    end
    print(sum_to(5))
    """
    pipeline_result = ObfuscationPipeline(
        PipelineConfig(enable_vm=True, watermark=None, seed=7)
    ).process(source)

    assert pipeline_result.stats["vm_applied"] is True
    assert pipeline_result.stats["vm_proto_count"] == 2
    assert pipeline_result.stats["vm_instruction_count"] > 0
    assert "text_obfuscation_passes_skipped" in pipeline_result.stats
    assert _run_lua_code(pipeline_result.output_code) == "15"


def test_generic_for_pairs_runtime():
    _expect_output(
        "local values = {a = 2, b = 3}\n"
        "local sum = 0\n"
        "for key, value in pairs(values) do sum += value end\n"
        "print(sum)\n",
        "5",
    )


def test_generic_for_iterable_table_and_method_call():
    src = """
    local source = {
        values = {4, 5},
        getValues = function(self) return self.values end,
        iter = function(self)
            local index = 0
            return function()
                index += 1
                if index <= 2 then return index, index * 10 end
            end, self, nil
        end,
    }
    local sum = 0
    for key, value in source.values do sum += key + value end
    for key, value in source:getValues() do sum += key + value end
    for _, value in source:iter() do sum += value end
    print(sum)
    """
    _expect_output(src, "54")


def test_generic_for_captured_keys_are_boxed_before_use():
    src = """
    local methods = {
        GetChildren = function(self) return self.children end,
        FindFirstChild = function(self, name) return self[name] end,
    }
    local wrappers = {}
    for name, method in next, methods do
        wrappers[name] = function(self, ...)
            return method(self, ...)
        end
    end
    local instance = {children = {1, 2, 3}, value = 42}
    print(#wrappers.GetChildren(instance), wrappers.FindFirstChild(instance, "value"))
    """
    _expect_output(src, "3\t42")


def test_trailing_varargs_are_forwarded_to_calls():
    src = """
    local function forward(callback, ...)
        return callback(...)
    end
    local function sum(a, b, c)
        return a + b + c
    end
    print(forward(sum, 10, 20, 12))
    """
    _expect_output(src, "42")


def test_trailing_call_results_are_expanded_as_arguments():
    src = """
    local function combine(a, b, c)
        return a * 100 + b * 10 + c
    end
    local function addPrefix(prefix, a, b)
        return prefix + a + b
    end
    local function getValues(self)
        return 4, 2, 7
    end
    local source = {values = {4, 2, 7}, getValues = getValues}
    print(
        combine(table.unpack(source.values)),
        combine(source:getValues()),
        addPrefix(1, table.unpack({20, 21}))
    )
    """
    _expect_output(src, "427\t427\t42")


def test_nested_index_call_receiver_keeps_trailing_call_argument_contiguous():
    src = """
    local function target(self, a, b, c, d)
        return a + b + c + d
    end
    local function getRoot()
        return {child = {fn = target}}
    end
    local function tail()
        return 3, 4
    end
    print(getRoot().child:fn(1, 2, tail()))
    """
    _expect_output(src, "10")


def test_trailing_call_expands_after_fixed_assignment_values():
    src = """
    local function make()
        return 2, function(value)
            return value + 1
        end
    end
    local theme = {}
    local themes, motor, update = theme, make()
    print(themes == theme, motor, update(40))

    local factory = {
        make = function(self)
            return 3, function(value)
                return value + 2
            end
        end
    }
    local first, second, transform
    first, second, transform = 10, factory:make()
    print(first + second, transform(40))
    """
    _expect_output(src, "true\t2\t41\n13\t42")


def test_bfp_like_runtime_smoke_fixture():
    with open(os.path.join(FIXTURE_DIR, "bfp_runtime_smoke.luau"), encoding="utf-8") as fixture:
        source = fixture.read()
    generated = compile_and_render(source)
    assert "local MULTRET_TAG" in generated

    if not os.path.isfile(LOCAL_LUAU_COMPILE):
        return

    with tempfile.NamedTemporaryFile(mode="w", suffix=".luau", delete=False, encoding="utf-8") as output:
        output.write(generated)
        path = output.name
    try:
        result = subprocess.run(
            [LOCAL_LUAU_COMPILE, path],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, (
            f"BFP-like VM fixture failed Luau syntax compilation:\n{result.stderr}"
        )
    finally:
        os.unlink(path)


def test_nil_call_error_reports_proto_and_instruction():
    try:
        _run_lua("local missing = nil\nmissing()")
    except RuntimeError as exc:
        assert "VM nil call: proto=<main>, proto_id=0, pc=" in str(exc)
    else:
        raise AssertionError("Calling nil should include VM prototype/PC diagnostics")


def test_vararg_table_and_select_count_runtime():
    _expect_output(
        "local function count(...)\n"
        "  local args = {...}\n"
        "  return #args, select(\"#\", ...)\n"
        "end\n"
        "local a, b = count(\"x\", \"y\", \"z\")\n"
        "print(a, b)\n",
        "3\t3",
    )


def test_main_pipeline_vm_reports_unsupported_syntax():
    from src.main import ObfuscationPipeline, PipelineConfig
    from src.vm.compiler import CompilerError

    try:
        ObfuscationPipeline(PipelineConfig(enable_vm=True)).process("goto done\n::done::")
    except CompilerError as exc:
        assert "goto/label" in str(exc)
    else:
        raise AssertionError("VM mode must report unsupported goto")


def test_mod_pow_and_length_runtime_helpers():
    _expect_output(
        "local values = {2, 3, 4}\n"
        "print(17 % 5, 2 ^ 4, -7 // 3, #values)\n",
        "2\t16\t-3\t3",
    )


def test_large_constant_indices_across_vm_opcodes():
    low_assignments = "\n".join(
        f'sink = "constant-{index}"' for index in range(1024)
    )
    high_assignments = "\n".join(
        f'sink = "constant-{index}"' for index in range(1024, 2050)
    )
    source = (
        "local sink\n"
        + low_assignments
        + "\nlocal object = {}\nobject.directField = 5\n"
        + "local directValue = object.directField\n"
        + high_assignments
        + "\n"
        + "object.highMethod = function(self) return 9 end\n"
        + "highGlobalFunction = function() return 7 end\n"
        + "local readGlobal = highGlobalFunction\n"
        + "local readMethod = object.highMethod\n"
        + "print(directValue, highGlobalFunction(), readGlobal(), "
        + "readMethod(object), object:highMethod())\n"
    )
    _expect_output(source, "5\t7\t7\t9\t9")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    if LUA_BIN is None:
        print("BO QUA TOAN BO: khong tim thay Lua 5.4 hoac Luau CLI.")
        print("Cai dat Lua 5.4 hoac them luau CLI vao PATH de chay runtime tests.")
        sys.exit(0)

    print(f"Dung Lua interpreter: {LUA_BIN}")
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
