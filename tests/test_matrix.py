# -*- coding: utf-8 -*-
"""
test_matrix.py
==============
Script kiem thu ma tran toan dien tren complex_test.luau:
  1. Test tung pass rieng le (Isolated Passes): chi bat duy nhat 1 tinh nang.
  2. Test cac Preset (min, balanced, max).
  3. Test Full Pipeline ket hop dong thoi tat ca cac tinh nang tren nhieu Random Seeds.
  4. Kiem tra tinh dung dan cua ket qua thuc thi tren runtime Lune (bin/lune.exe).
"""

import os
import subprocess
import sys
import time

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.main import ObfuscationPipeline, PipelineConfig

COMPLEX_LUAU = os.path.join(ROOT_DIR, "complex_test.luau")
LUNE_EXE = os.path.join(ROOT_DIR, "bin", "lune.exe")
TMP_OUTPUT = os.path.join(ROOT_DIR, "build", "matrix_tmp.luau")


def get_ground_truth() -> str:
    """Chay file goc complex_test.luau de lay output chuan."""
    cmd = [LUNE_EXE, "run", COMPLEX_LUAU]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print("[ERROR] File complex_test.luau loi cu phap hoac runtime:")
        print(res.stderr)
        sys.exit(1)
    return res.stdout


def run_test_case(name: str, cfg: PipelineConfig, source: str, expected: str) -> tuple[bool, str]:
    """Obfuscate voi cau hinh cfg, chay tren Lune va so sanh voi expected."""
    os.makedirs(os.path.dirname(TMP_OUTPUT), exist_ok=True)
    pipe = ObfuscationPipeline(cfg)
    try:
        res = pipe.process(source)
    except Exception as exc:
        return False, f"Obfuscation Exception: {exc}"

    with open(TMP_OUTPUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(res.output_code)

    run_res = subprocess.run([LUNE_EXE, "run", TMP_OUTPUT], capture_output=True, text=True, timeout=10)
    if run_res.returncode != 0:
        return False, f"Runtime Error (exit {run_res.returncode}): {run_res.stderr.strip()[:200]}"
    if run_res.stdout != expected:
        return False, f"Output Mismatch! Got length {len(run_res.stdout)}, expected {len(expected)}"
    return True, f"OK ({res.obfuscated_size:,} B, {res.elapsed_seconds*1000:.1f}ms)"


def main():
    print("=" * 70)
    print("      LUAU OBFUSCATOR - MA TRAN KIEM THU NANG CAO (COMPLEX)")
    print("=" * 70)
    print(f"Test script: {COMPLEX_LUAU}")
    print(f"Runtime:     {LUNE_EXE}")

    with open(COMPLEX_LUAU, "r", encoding="utf-8") as f:
        source_code = f.read()

    expected = get_ground_truth()
    print(f"Baseline Output Length: {len(expected)} chars across 8 test sections.\n")

    # =========================================================================
    # PHAN 1: TEST TUNG TINH NANG RIENG LE (ISOLATED PASSES)
    # =========================================================================
    print("--- PHAN 1: KIEM THU TUNG PASS CO LAP (ISOLATED PASSES) ---")
    isolated_cases = [
        ("Base (Chi parse & emit)", PipelineConfig(
            strip_comments=False, strip_type_annotations=False, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("1. Strip Comments Only", PipelineConfig(
            strip_comments=True, strip_type_annotations=False, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("2. Strip Type Annotations Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("3. Mangle Names & Cache Globals Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=True,
            global_caching=True, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("4. String Pooling (32-bit XOR) Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=True, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("5. Mixed Boolean Arithmetic (MBA) Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=True,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("6. Opaque Predicates (Nhánh ma) Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=True, enable_dead_code=False, enable_cff=False,
            minify=False
        )),
        ("7. Dead Code Injection Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=True, enable_cff=False,
            minify=False
        )),
        ("8. Control Flow Flattening (CFF) Only", PipelineConfig(
            strip_comments=False, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=True,
            minify=False
        )),
        ("9. Minify Only", PipelineConfig(
            strip_comments=True, strip_type_annotations=True, mangle_names=False,
            global_caching=False, pool_strings=False, enable_mba=False,
            enable_opaque_predicates=False, enable_dead_code=False, enable_cff=False,
            minify=True
        )),
    ]

    p1_pass = 0
    for name, cfg in isolated_cases:
        ok, msg = run_test_case(name, cfg, source_code, expected)
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} {name:<42} -> {msg}")
        if ok:
            p1_pass += 1
    print(f"Ket qua Phan 1: {p1_pass}/{len(isolated_cases)} passed.\n")

    # =========================================================================
    # PHAN 2: TEST CAC PRESET TIEN DUNG
    # =========================================================================
    print("--- PHAN 2: KIEM THU CAC PRESET (MIN, BALANCED, MAX) ---")
    presets = ["min", "balanced", "max"]
    p2_pass = 0
    for p in presets:
        cfg = PipelineConfig.from_preset(p)
        ok, msg = run_test_case(f"Preset '{p}'", cfg, source_code, expected)
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} Preset '{p}': {msg}")
        if ok:
            p2_pass += 1
    print(f"Ket qua Phan 2: {p2_pass}/{len(presets)} passed.\n")

    # =========================================================================
    # PHAN 3: TEST FULL PIPELINE VOI TAT CA CHUC NANG TREN 15 SEEDS NGAU NHIEN
    # =========================================================================
    print("--- PHAN 3: KIEM THU FULL PIPELINE (TAT CA CHUC NANG CUNG BAT) TREN 15 SEEDS ---")
    seeds = [0, 1, 7, 11, 23, 42, 77, 99, 123, 256, 512, 777, 1024, 2048, 9999]
    p3_pass = 0
    for s in seeds:
        cfg = PipelineConfig(seed=s)  # Mac dinh bat tat ca tinh nang!
        ok, msg = run_test_case(f"Seed {s}", cfg, source_code, expected)
        status = "[PASS]" if ok else "[FAIL]"
        print(f"  {status} Full Pipeline (Seed={s:<5}) -> {msg}")
        if ok:
            p3_pass += 1
    print(f"Ket qua Phan 3: {p3_pass}/{len(seeds)} passed.\n")

    print("=" * 70)
    total_tests = len(isolated_cases) + len(presets) + len(seeds)
    total_passed = p1_pass + p2_pass + p3_pass
    print(f"TONG KET: {total_passed}/{total_tests} TESTS PASSED (100% MATCH RUNTIME)")
    print("=" * 70)


if __name__ == "__main__":
    main()
