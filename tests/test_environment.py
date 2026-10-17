# -*- coding: utf-8 -*-
"""
tests/test_environment.py
===========================
Test thuan Python (khong can pytest) cho src/vm/environment.py.

Chay:
    python3 tests/test_environment.py
"""

import sys
import os
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.vm.environment import (
    ConstantPool, RegisterAllocator, Environment,
    ConstantPoolOverflowError, RegisterOverflowError, RegisterAllocatorError,
    ConstantPoolError,
)
from src.vm.opcode import A_MAX, B_MAX


# ---------------------------------------------------------------------------
# ConstantPool
# ---------------------------------------------------------------------------

def test_constant_pool_dedup_same_value():
    pool = ConstantPool()
    i1 = pool.add("hello")
    i2 = pool.add("hello")
    assert i1 == i2
    assert len(pool) == 1


def test_constant_pool_distinguishes_types():
    pool = ConstantPool()
    i_true = pool.add(True)
    i_one = pool.add(1)
    i_one_float = pool.add(1.0)
    i_str_one = pool.add("1")
    i_nil = pool.add(None)
    # Tat ca phai la hang so KHAC NHAU du Python coi True==1==1.0
    idxs = {i_true, i_one, i_one_float, i_str_one, i_nil}
    assert len(idxs) == 5, idxs
    assert len(pool) == 5


def test_constant_pool_get_roundtrip():
    pool = ConstantPool()
    idx = pool.add("workspace")
    assert pool.get(idx) == "workspace"


def test_constant_pool_get_invalid_index_raises():
    pool = ConstantPool()
    pool.add("x")
    try:
        pool.get(99)
        assert False
    except ConstantPoolError:
        pass


def test_constant_pool_overflow():
    pool = ConstantPool(max_size=3)
    pool.add("a")
    pool.add("b")
    pool.add("c")
    try:
        pool.add("d")
        assert False, "Phai nem ConstantPoolOverflowError"
    except ConstantPoolOverflowError:
        pass
    # Them lai 1 hang so DA CO van duoc (khong tinh la "them moi")
    assert pool.add("a") == 0


def test_constant_pool_unsupported_type_raises():
    pool = ConstantPool()
    try:
        pool.add([1, 2, 3])  # list khong phai hang so hop le
        assert False
    except ConstantPoolError:
        pass


def test_constant_pool_to_list_order():
    pool = ConstantPool()
    pool.add("a")
    pool.add("b")
    pool.add("c")
    assert pool.to_list() == ["a", "b", "c"]


def test_constant_pool_default_cap_matches_b_max():
    pool = ConstantPool()
    for i in range(B_MAX + 1):
        pool.add(f"const_{i}")
    assert len(pool) == B_MAX + 1
    try:
        pool.add("one_too_many")
        assert False
    except ConstantPoolOverflowError:
        pass


# ---------------------------------------------------------------------------
# RegisterAllocator
# ---------------------------------------------------------------------------

def test_register_alloc_sequential():
    regs = RegisterAllocator()
    regs.push_scope()
    r0 = regs.alloc("a")
    r1 = regs.alloc("b")
    assert r0 == 0 and r1 == 1
    regs.pop_scope()


def test_register_resolve_name():
    regs = RegisterAllocator()
    regs.push_scope()
    r = regs.alloc("x")
    assert regs.resolve("x") == r
    assert regs.resolve("khong_ton_tai") is None
    regs.pop_scope()


def test_register_scope_shadowing():
    regs = RegisterAllocator()
    regs.push_scope()
    r_outer = regs.alloc("x")
    regs.push_scope()
    r_inner = regs.alloc("x")  # shadow bien 'x' ngoai
    assert regs.resolve("x") == r_inner
    assert r_inner != r_outer
    regs.pop_scope()
    assert regs.resolve("x") == r_outer  # ra khoi scope trong, thay lai bien ngoai
    regs.pop_scope()


def test_register_reuse_after_scope_pop():
    regs = RegisterAllocator()
    regs.push_scope()
    regs.alloc("a")  # R0
    regs.push_scope()
    r_inner = regs.alloc("b")  # R1
    regs.pop_scope()
    r_reused = regs.alloc("c")  # phai tai su dung lai R1 (da giai phong)
    assert r_reused == r_inner
    regs.pop_scope()


def test_register_mark_release():
    regs = RegisterAllocator()
    regs.push_scope()
    regs.alloc("a")  # R0
    m = regs.mark()
    regs.alloc_temp()  # R1 (tam)
    regs.alloc_temp()  # R2 (tam)
    assert regs.max_registers_used == 3
    regs.release_to(m)
    r_next = regs.alloc_temp()
    assert r_next == 1  # tai su dung lai tu moc mark
    regs.pop_scope()


def test_register_release_to_below_scope_mark_raises():
    regs = RegisterAllocator()
    regs.push_scope()
    regs.alloc("a")  # R0, thuoc scope nay (mark=0)
    try:
        regs.release_to(0)  # == mark cua scope -> hop le; thu < mark moi la loi
        regs.alloc("b")
        regs.release_to(-1)
        assert False
    except RegisterAllocatorError:
        pass
    finally:
        regs.pop_scope()


def test_register_pop_without_push_raises():
    regs = RegisterAllocator()
    try:
        regs.pop_scope()
        assert False
    except RegisterAllocatorError:
        pass


def test_register_alloc_name_without_scope_raises():
    regs = RegisterAllocator()
    try:
        regs.alloc("x")
        assert False
    except RegisterAllocatorError:
        pass


def test_register_overflow():
    regs = RegisterAllocator()
    regs.push_scope()
    try:
        for _ in range(A_MAX + 2):
            regs.alloc_temp()
        assert False, "Phai nem RegisterOverflowError"
    except RegisterOverflowError:
        pass
    finally:
        regs.pop_scope()


def test_register_max_used_tracks_peak_not_current():
    regs = RegisterAllocator()
    regs.push_scope()
    regs.alloc("a")
    regs.push_scope()
    regs.alloc("b")
    regs.alloc("c")
    peak = regs.max_registers_used
    regs.pop_scope()  # giai phong b, c
    assert regs.max_registers_used == peak  # peak khong giam
    regs.pop_scope()


# ---------------------------------------------------------------------------
# Environment (local / upvalue / global resolution)
# ---------------------------------------------------------------------------

def test_environment_resolve_local():
    env = Environment()
    env.registers.push_scope()
    env.registers.alloc("hp")
    res = env.resolve_name("hp")
    assert res.kind == "local"
    assert res.register == 0
    env.registers.pop_scope()


def test_environment_resolve_global_fallback():
    env = Environment()
    env.registers.push_scope()
    res = env.resolve_name("print")  # khong khai bao local -> global
    assert res.kind == "global"
    env.registers.pop_scope()


def test_environment_resolve_upvalue():
    outer = Environment()
    outer.registers.push_scope()
    outer.registers.alloc("counter")

    inner = outer.child()
    inner.registers.push_scope()
    res = inner.resolve_name("counter")
    assert res.kind == "upvalue"

    inner.registers.pop_scope()
    outer.registers.pop_scope()


def test_environment_resolve_upvalue_through_multiple_levels():
    level0 = Environment()
    level0.registers.push_scope()
    level0.registers.alloc("x")

    level1 = level0.child()
    level1.registers.push_scope()

    level2 = level1.child()
    level2.registers.push_scope()

    res = level2.resolve_name("x")
    assert res.kind == "upvalue"

    level2.registers.pop_scope()
    level1.registers.pop_scope()
    level0.registers.pop_scope()


def test_environment_local_shadows_upvalue():
    outer = Environment()
    outer.registers.push_scope()
    outer.registers.alloc("x")

    inner = outer.child()
    inner.registers.push_scope()
    inner.registers.alloc("x")  # ham con tu khai bao local 'x' rieng

    res = inner.resolve_name("x")
    assert res.kind == "local"  # uu tien local cua chinh no, khong phai upvalue

    inner.registers.pop_scope()
    outer.registers.pop_scope()


def test_environment_name_constant_dedup():
    env = Environment()
    i1 = env.name_constant("workspace")
    i2 = env.name_constant("workspace")
    assert i1 == i2
    assert len(env.constants) == 1


def test_environment_constants_independent_per_function():
    outer = Environment()
    outer.name_constant("foo")
    inner = outer.child()
    # constant pool cua ham con la RIENG, khong chia se voi ham cha
    assert len(inner.constants) == 0
    inner.name_constant("bar")
    assert len(outer.constants) == 1
    assert len(inner.constants) == 1


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
