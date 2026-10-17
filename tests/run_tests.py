# -*- coding: utf-8 -*-
"""
run_tests.py
============
Runner tap trung chay toan bo bo test cua LuauObfuscator (Parser, Utils, Basic, Advanced, Integration).
Ho tro ca chay truc tiep bang binary lune.exe va luau.exe.

Cach dung:
    python run_tests.py
    python run_tests.py --module parser
    python run_tests.py --module basic
    python run_tests.py --module advanced
    python run_tests.py --module utils
    python run_tests.py --module integration
"""

import argparse
import os
import sys
import time
import unittest

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from tests.helpers import LUNE_BIN, LUAU_BIN, EXEC_BIN


def print_banner():
    print("=" * 70)
    print("       LUAU OBFUSCATOR - TEST SUITE RUNNER")
    print("=" * 70)
    print(f"Python:    {sys.version.split()[0]} ({sys.executable})")
    print(f"Lune CLI:  {LUNE_BIN or 'Not found'}")
    print(f"Luau CLI:  {LUAU_BIN or 'Not found'}")
    print(f"Executor:  {'Lune (' + LUNE_BIN + ')' if EXEC_BIN == LUNE_BIN and LUNE_BIN else ('Luau (' + LUAU_BIN + ')' if LUAU_BIN else 'None (Skipping runtime exec tests)')}")
    print("=" * 70)
    print()


def build_suite(module_name: str = "all") -> unittest.TestSuite:
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    modules_map = {
        "parser": "tests.test_parser",
        "utils": "tests.test_utils",
        "basic": "tests.test_basic",
        "advanced": "tests.test_advanced",
        "integration": "tests.test_integration",
    }

    if module_name == "all":
        for mod in modules_map.values():
            suite.addTests(loader.loadTestsFromName(mod))
    elif module_name in modules_map:
        suite.addTests(loader.loadTestsFromName(modules_map[module_name]))
    else:
        print(f"[ERROR] Module '{module_name}' khong hop le. Chon tu: {list(modules_map.keys())}")
        sys.exit(1)

    return suite


class ColoredTextTestResult(unittest.TextTestResult):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.successes = []

    def addSuccess(self, test):
        super().addSuccess(test)
        self.successes.append(test)


def main():
    parser = argparse.ArgumentParser(description="Chay bo test cho LuauObfuscator")
    parser.add_argument(
        "--module",
        choices=["all", "parser", "utils", "basic", "advanced", "integration"],
        default="all",
        help="Chon phan muon kiem thu (mac dinh: all)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=True,
        help="In chi tiet tung test case",
    )
    args = parser.parse_args()

    print_banner()

    suite = build_suite(args.module)
    runner = unittest.TextTestRunner(
        verbosity=2 if args.verbose else 1,
        resultclass=ColoredTextTestResult,
    )

    start_time = time.time()
    result = runner.run(suite)
    elapsed = time.time() - start_time

    total = result.testsRun
    failures = len(result.failures)
    errors = len(result.errors)
    xfails = len(result.expectedFailures)
    skipped = len(result.skipped)
    passed = len(getattr(result, "successes", []))

    print("\n" + "=" * 70)
    print("                 TONG KET KET QUA TEST")
    print("=" * 70)
    print(f"Tong so tests chay:    {total}")
    print(f"PASSED:                {passed}")
    print(f"EXPECTED FAILURES:     {xfails} (Loi da ghi nhan vao TEST_REPORT.md)")
    print(f"SKIPPED:               {skipped}")
    print(f"FAILED (Unexpected):   {failures}")
    print(f"ERRORS (Unexpected):   {errors}")
    print(f"Thoi gian thuc thi:    {elapsed:.3f}s")
    print("=" * 70)

    if failures > 0 or errors > 0:
        print("\n===> KET QUA: CO LOI NGOAI DU KIEN CAN XU LY! <===")
        sys.exit(1)
    else:
        print("\n===> KET QUA: TAT CA TEST HOAN TAT THANH CONG! <===")
        if xfails > 0:
            print(f"(Chu y: Co {xfails} loi kien truc da ghi nhan, xem chi tiet tai TEST_REPORT.md)")
        sys.exit(0)


if __name__ == "__main__":
    main()

