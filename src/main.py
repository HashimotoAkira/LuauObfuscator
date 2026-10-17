# -*- coding: utf-8 -*-
"""
src.main
========
Pipeline chinh cua Luau Obfuscator:
Ket noi toan bo cac tang:
    Source Code (Lua/Luau)
      -> 1. Strip Comments (comment.py)
      -> 2. Lexer & Parser (parser/)
      -> 3. Strip Type Annotations (annotations.py)
      -> 4. AST Variable Mangling & Global Caching (naming.py)
      -> 5. String Pooling & 32-bit XOR Blob (strings.py)
      -> 6. Mixed Boolean Arithmetic - MBA (mba.py)
      -> 7. Opaque Predicates (opaque_predicates.py)
      -> 8. Dead Code Injection (dead_code.py)
      -> 9. Control Flow Flattening - CFF (cff.py)
      -> 10. AST Code Emitter (emitter.py)
      -> 11. Final Assembly & Output (dist/)

VM mode is a separate backend for the currently supported AST subset. It
performs comment/type preprocessing, then compiles and renders through src.vm;
the text-obfuscation passes are intentionally bypassed.

Cach dung trong code Python:
    from src.main import ObfuscationPipeline, PipelineConfig

    pipeline = ObfuscationPipeline(PipelineConfig.from_preset("balanced"))
    result = pipeline.process("local x = 1 + 2; print(x)")
    print(result.output_code)

Cach dung qua Command Line:
    python -m src.main input.luau -o dist/output.luau
    python src/main.py test.luau --run
    python src/main.py test.luau --preset max --run
    python src/main.py src_dir/ -o dist_dir/
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import glob
import os
import subprocess
import sys
import time
from typing import Optional, Dict, Any, List, Tuple

# Dam bao import duoc cac module trong src khi chay truc tiep file
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.parser import parse, nodes as N
from src.basic import (
    strip_comments,
    strip_type_annotations,
    mangle,
    pool_strings,
    render_decoder_lua,
    ManglingResult,
    StringPoolResult,
)
from src.advanced import (
    emitter,
    mba,
    dead_code,
    opaque_predicates,
    cff,
)
from src.vm import compile_chunk, render_runtime


@dataclass
class PipelineConfig:
    """Cau hinh toan dien cho qua trinh Obfuscate."""
    # Basic passes
    strip_comments: bool = True
    strip_type_annotations: bool = True
    mangle_names: bool = True
    global_caching: bool = True
    min_cache_uses: int = 2
    mangle_prefix: str = "_"
    mangle_table_fields: bool = True

    # String pooling
    pool_strings: bool = True
    string_min_len: int = 0
    string_max_len: Optional[int] = None
    string_excluded_values: frozenset[str] = frozenset()
    string_decoder_name: str = "_S"

    # Advanced passes
    enable_mba: bool = True
    mba_probability: float = 0.6
    mba_max_substitutions: Optional[int] = None
    mba_allow_duplicate: bool = False

    enable_opaque_predicates: bool = True
    dialect: str = "roblox"  # "roblox" hoac "lua" (roblox co fallback os.clock)
    opaque_condition_guard_probability: float = 0.55
    opaque_dead_branch_probability: float = 0.10
    opaque_phantom_clause_probability: float = 0.20
    opaque_max_injections: int = 60

    enable_dead_code: bool = True
    dead_code_density: float = 0.25
    dead_code_max_injections: Optional[int] = None

    enable_cff: bool = True
    cff_deep: bool = True
    cff_min_len: int = 2

    # Emitter
    minify: bool = True
    watermark: Optional[str] = "-- [Obfuscated by LuauObfuscator]"

    # Random Seed
    seed: Optional[int] = None
    enable_vm: bool = False

    @classmethod
    def from_preset(cls, preset: str = "balanced", **overrides) -> "PipelineConfig":
        """Tao cau hinh tu cac preset dinh san: 'min', 'balanced', 'max'."""
        preset = preset.lower()
        if preset == "min":
            cfg = cls(
                strip_comments=True,
                strip_type_annotations=True,
                mangle_names=True,
                global_caching=False,
                pool_strings=False,
                enable_mba=False,
                enable_opaque_predicates=False,
                enable_dead_code=False,
                enable_cff=False,
                minify=True,
            )
        elif preset == "max":
            cfg = cls(
                strip_comments=True,
                strip_type_annotations=True,
                mangle_names=True,
                global_caching=True,
                min_cache_uses=1,
                pool_strings=True,
                string_min_len=0,
                enable_mba=True,
                mba_probability=0.85,
                enable_opaque_predicates=True,
                enable_dead_code=True,
                dead_code_density=0.35,
                enable_cff=True,
                cff_deep=True,
                cff_min_len=2,
                minify=True,
            )
        else:  # balanced
            cfg = cls()

        for k, v in overrides.items():
            if hasattr(cfg, k) and v is not None:
                setattr(cfg, k, v)
        return cfg


@dataclass
class PipelineResult:
    """Ket qua sau khi chay pipeline."""
    output_code: str
    original_size: int
    obfuscated_size: int
    elapsed_seconds: float
    stats: Dict[str, Any] = field(default_factory=dict)


class ObfuscationPipeline:
    """Pipeline dieu phoi toan bo qua trinh bien doi AST."""

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.config = config or PipelineConfig()

    def process(self, source_code: str) -> PipelineResult:
        start_time = time.time()
        orig_size = len(source_code.encode("utf-8"))
        stats: Dict[str, Any] = {}

        # -------------------------------------------------------------------
        # Buoc 1: Tien xu ly Source Text (Xoa Comment)
        # -------------------------------------------------------------------
        current_text = source_code
        if self.config.strip_comments:
            current_text = strip_comments(current_text)
            stats["comments_stripped"] = True

        # -------------------------------------------------------------------
        # Buoc 2: Lexing & Parsing thanh cay AST
        # -------------------------------------------------------------------
        ast = parse(current_text)
        stats["initial_statements"] = len(ast.statements)

        # -------------------------------------------------------------------
        # Buoc 3: Lot Type Annotations cua Luau
        # -------------------------------------------------------------------
        if self.config.strip_type_annotations:
            ast = strip_type_annotations(ast)
            stats["types_stripped"] = True

        if self.config.enable_vm:
            main_proto_idx, protos = compile_chunk(ast)
            emitted_code = render_runtime(main_proto_idx, protos)
            vm_ast = parse(emitted_code)

            if self.config.mangle_names:
                # Keep VM protocol table fields (e.g. multireturn tags) stable.
                mangle_res = mangle(
                    vm_ast,
                    enable_global_caching=False,
                    min_cache_uses=self.config.min_cache_uses,
                    prefix=self.config.mangle_prefix,
                    mangle_table_fields=False,
                )
                vm_ast = mangle_res.block
                stats["renamed_identifiers"] = mangle_res.renamed_count

            decoder_header = ""
            if self.config.pool_strings:
                pool_res = pool_strings(
                    vm_ast,
                    min_len=self.config.string_min_len,
                    max_len=self.config.string_max_len,
                    exclude_values=self.config.string_excluded_values,
                    decoder_name=self.config.string_decoder_name,
                )
                vm_ast = pool_res.block
                if pool_res.count:
                    decoder_header = render_decoder_lua(pool_res)
                stats["pooled_strings_count"] = pool_res.count
                stats["string_blob_bytes"] = pool_res.total_bytes

            if decoder_header:
                decoder_ast = parse(decoder_header)
                decoder_ast.statements.extend(vm_ast.statements)
                vm_ast = decoder_ast

            if self.config.mangle_names:
                generated_names_res = mangle(
                    vm_ast,
                    enable_global_caching=False,
                    min_cache_uses=self.config.min_cache_uses,
                    prefix=self.config.mangle_prefix,
                    mangle_table_fields=False,
                )
                vm_ast = generated_names_res.block
                stats["renamed_identifiers"] = generated_names_res.renamed_count

            emitted_code = emitter.emit(vm_ast, minify=self.config.minify)
            if self.config.watermark:
                emitted_code = f"{self.config.watermark}\n{emitted_code}"
            stats["vm_applied"] = True
            stats["vm_proto_count"] = len(protos)
            stats["vm_instruction_count"] = sum(len(proto.code) for proto in protos)
            stats["vm_max_registers"] = max((proto.max_registers for proto in protos), default=0)
            stats["text_obfuscation_passes_skipped"] = [
                "global caching",
                "MBA",
                "opaque predicates",
                "dead code injection",
                "CFF",
            ]

            obf_size = len(emitted_code.encode("utf-8"))
            return PipelineResult(
                output_code=emitted_code,
                original_size=orig_size,
                obfuscated_size=obf_size,
                elapsed_seconds=time.time() - start_time,
                stats=stats,
            )

        # -------------------------------------------------------------------
        # Buoc 4: AST Name Mangling & Global Caching
        # -------------------------------------------------------------------
        if self.config.mangle_names:
            mangle_res: ManglingResult = mangle(
                ast,
                enable_global_caching=self.config.global_caching,
                min_cache_uses=self.config.min_cache_uses,
                prefix=self.config.mangle_prefix,
                mangle_table_fields=self.config.mangle_table_fields,
            )
            ast = mangle_res.block
            stats["renamed_identifiers"] = mangle_res.renamed_count
            stats["cached_globals"] = list(mangle_res.cached_globals.keys())

        # -------------------------------------------------------------------
        # Buoc 5: String Pooling & 32-bit XOR Blob
        # -------------------------------------------------------------------
        decoder_header: str = ""
        if self.config.pool_strings:
            pool_res: StringPoolResult = pool_strings(
                ast,
                min_len=self.config.string_min_len,
                max_len=self.config.string_max_len,
                exclude_values=self.config.string_excluded_values,
                decoder_name=self.config.string_decoder_name,
            )
            ast = pool_res.block
            if pool_res.count > 0:
                decoder_header = render_decoder_lua(pool_res)
            stats["pooled_strings_count"] = pool_res.count
            stats["string_blob_bytes"] = pool_res.total_bytes

        # -------------------------------------------------------------------
        # Buoc 6: Mixed Boolean Arithmetic (MBA)
        # -------------------------------------------------------------------
        if self.config.enable_mba:
            mba_cfg = mba.MBAConfig(
                probability=self.config.mba_probability,
                max_substitutions=self.config.mba_max_substitutions,
                allow_duplicate_simple=self.config.mba_allow_duplicate,
            )
            ast = mba.obfuscate(ast, mba_cfg)
            stats["mba_applied"] = True

        # -------------------------------------------------------------------
        # Buoc 7: Opaque Predicates (Dieu kien gia & nhanh ma)
        # -------------------------------------------------------------------
        if self.config.enable_opaque_predicates:
            ast = opaque_predicates.obfuscate_opaque_predicates(
                ast,
                dialect=self.config.dialect,
                condition_guard_probability=self.config.opaque_condition_guard_probability,
                dead_branch_probability=self.config.opaque_dead_branch_probability,
                phantom_clause_probability=self.config.opaque_phantom_clause_probability,
                max_injections=self.config.opaque_max_injections,
                seed=self.config.seed,
            )
            stats["opaque_predicates_applied"] = True

        # -------------------------------------------------------------------
        # Buoc 8: Dead Code Injection (Bom rac logic)
        # -------------------------------------------------------------------
        if self.config.enable_dead_code:
            ast = dead_code.inject(
                ast,
                density=self.config.dead_code_density,
                max_injections=self.config.dead_code_max_injections,
                deep=True,
                seed=self.config.seed,
            )
            stats["dead_code_applied"] = True

        # -------------------------------------------------------------------
        # Buoc 9: Control Flow Flattening (CFF)
        # -------------------------------------------------------------------
        if self.config.enable_cff:
            if self.config.cff_deep:
                ast = cff.flatten_deep(ast, seed=self.config.seed, min_len=self.config.cff_min_len)
            else:
                ast = cff.flatten(ast, seed=self.config.seed, min_len=self.config.cff_min_len)
            stats["cff_applied"] = True

        # Include generated decoder locals in the AST so they are mangled with
        # the same scope and collision rules as payload and advanced-pass locals.
        if decoder_header:
            decoder_ast = parse(decoder_header)
            decoder_ast.statements.extend(ast.statements)
            ast = decoder_ast

        # Advanced passes inject locals after the initial name-mangling pass.
        # Rename those generated bindings too, without reprocessing table fields.
        if self.config.mangle_names:
            generated_names_res = mangle(
                ast,
                enable_global_caching=False,
                min_cache_uses=self.config.min_cache_uses,
                prefix=self.config.mangle_prefix,
                mangle_table_fields=False,
            )
            ast = generated_names_res.block
            stats["renamed_identifiers"] = generated_names_res.renamed_count

        # -------------------------------------------------------------------
        # Buoc 10: Code Generation (Emitter)
        # -------------------------------------------------------------------
        emitted_code = emitter.emit(ast, minify=self.config.minify)

        # -------------------------------------------------------------------
        # Buoc 11: Final Assembly (Watermark + Decoder + Payload)
        # -------------------------------------------------------------------
        final_parts: List[str] = []
        if self.config.watermark:
            final_parts.append(self.config.watermark)
        final_parts.append(emitted_code)

        final_output = "\n".join(final_parts)
        obf_size = len(final_output.encode("utf-8"))
        elapsed = time.time() - start_time

        return PipelineResult(
            output_code=final_output,
            original_size=orig_size,
            obfuscated_size=obf_size,
            elapsed_seconds=elapsed,
            stats=stats,
        )


def _find_runner_bin() -> Optional[Tuple[str, str]]:
    """Tim lune hoac luau binary de chay test."""
    lune_path = os.path.normpath(os.path.join(ROOT_DIR, "bin", "lune.exe"))
    if os.path.isfile(lune_path) and os.access(lune_path, os.X_OK):
        return ("lune", lune_path)
    luau_path = os.path.normpath(os.path.join(ROOT_DIR, "..", ".storage", "Luau", "luau.exe"))
    if os.path.isfile(luau_path) and os.access(luau_path, os.X_OK):
        return ("luau", luau_path)
    return None


def _process_single_file(
    input_path: str,
    output_path: Optional[str],
    cfg: PipelineConfig,
    run_test: bool = False,
) -> bool:
    """Xu ly 1 file rieng le."""
    if input_path == "-":
        source_code = sys.stdin.read()
        in_display = "<stdin>"
    else:
        in_display = os.path.abspath(input_path)
        if not os.path.isfile(in_display):
            print(f"[ERROR] Khong tim thay file dau vao: {in_display}")
            return False
        with open(in_display, "r", encoding="utf-8") as f:
            source_code = f.read()

    # Xac dinh output path
    if output_path is None or output_path == "":
        output_dir = os.path.join(ROOT_DIR, "dist")
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.basename(in_display) if in_display != "<stdin>" else "stdin.luau"
        name_part, ext = os.path.splitext(base_name)
        out_display = os.path.join(output_dir, f"{name_part}_obf{ext or '.luau'}")
    elif output_path == "-":
        out_display = "<stdout>"
    else:
        out_display = os.path.abspath(output_path)

    if out_display != "<stdout>":
        print("=" * 65)
        print("                LUAU OBFUSCATOR PIPELINE")
        print("=" * 65)
        print(f"Input:   {in_display}")
        print(f"Output:  {out_display}")
        print(f"Dialect: {cfg.dialect}")
        print("\nDang xu ly qua pipeline...")

    pipeline = ObfuscationPipeline(cfg)
    try:
        result = pipeline.process(source_code)
    except Exception as exc:
        print(f"[ERROR] Loi trong qua trinh obfuscate: {exc}")
        return False

    if out_display == "<stdout>":
        sys.stdout.write(result.output_code)
        return True

    # Ghi file ket qua
    os.makedirs(os.path.dirname(out_display), exist_ok=True)
    with open(out_display, "w", encoding="utf-8", newline="\n") as f:
        f.write(result.output_code)

    print("=" * 65)
    print("                 KET QUA OBFUSCATE")
    print("=" * 65)
    print(f"Kich thuoc goc:        {result.original_size:,} bytes")
    ratio = result.obfuscated_size / max(1, result.original_size)
    print(f"Kich thuoc obfuscated: {result.obfuscated_size:,} bytes ({ratio:.2f}x)")
    print(f"Thoi gian xu ly:       {result.elapsed_seconds * 1000:.2f} ms")
    print("Thong so cac buoc:")
    for k, v in result.stats.items():
        print(f"  - {k}: {v}")
    print(f"\nDa xuat file thanh cong: {out_display}")

    # Chay thu neu co flag --run
    if run_test:
        runner_info = _find_runner_bin()
        if not runner_info:
            print("\n[WARN] Khong tim thay binary lune.exe hoac luau.exe de chay thu.")
            return True

        runner_type, runner_bin = runner_info
        print("\n" + "=" * 65)
        print(f"           CHAY THU QUA RUNTIME ({runner_type.upper()})")
        print("=" * 65)
        cmd = [runner_bin, "run", out_display] if runner_type == "lune" else [runner_bin, out_display]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0:
                print("[KET QUA RUNTIME - THANH CONG (EXIT 0)]")
                if res.stdout:
                    print(res.stdout)
            else:
                print(f"[KET QUA RUNTIME - THAT BAI (EXIT {res.returncode})]")
                print("STDERR:")
                print(res.stderr)
        except Exception as exc:
            print(f"[ERROR] Loi khi thuc thi script: {exc}")

    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Luau Obfuscator - Bao mat script Lua/Luau (Python)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("input", help="Duong dan toi file script nguon (.lua/.luau), thu muc, hoac '-' de doc tu stdin")
    parser.add_argument("-o", "--output", help="Duong dan file dau ra (hoac thu muc neu input la thu muc; '-' de in ra stdout)")

    # Preset
    parser.add_argument("--preset", choices=["min", "balanced", "max"], default="balanced",
                        help="Che do lam roi mac dinh: min (nhe), balanced (can bang), max (toi da)")

    # Flags bat/tat tung pass
    parser.add_argument("--no-comments-strip", action="store_true", help="Khong xoa comment")
    parser.add_argument("--no-type-strip", action="store_true", help="Khong lot type annotation")
    parser.add_argument("--no-mangle", action="store_true", help="Khong doi ten bien")
    parser.add_argument("--no-global-cache", action="store_true", help="Khong ap dung global caching")
    parser.add_argument("--no-strings", action="store_true", help="Khong ma hoa string pool")
    parser.add_argument("--no-mba", action="store_true", help="Khong dung Mixed Boolean Arithmetic")
    parser.add_argument("--no-opaque", action="store_true", help="Khong dung Opaque Predicates")
    parser.add_argument("--no-dead-code", action="store_true", help="Khong bom Dead Code")
    parser.add_argument("--no-cff", action="store_true", help="Khong dung Control Flow Flattening")
    parser.add_argument(
        "--enable-vm",
        action="store_true",
        help="Dung VM backend (chi ho tro mot tap con Lua/Luau; bo qua cac pass obfuscation AST)",
    )

    # Options
    parser.add_argument("--dialect", choices=["roblox", "lua"], default="roblox", help="Dialect moi truong runtime")
    parser.add_argument("--seed", type=int, default=None, help="Seed ngau nhien co the tai lap")
    parser.add_argument("--run", action="store_true", help="Chay thu file ket qua bang lune/luau sau khi obfuscate")

    args = parser.parse_args()

    # Khoi tao config tu preset
    cfg = PipelineConfig.from_preset(args.preset)
    cfg.enable_vm = args.enable_vm
    cfg.dialect = args.dialect
    if args.seed is not None:
        cfg.seed = args.seed

    # Override boi flags cu the
    if args.no_comments_strip:
        cfg.strip_comments = False
    if args.no_type_strip:
        cfg.strip_type_annotations = False
    if args.no_mangle:
        cfg.mangle_names = False
    if args.no_global_cache:
        cfg.global_caching = False
    if args.no_strings:
        cfg.pool_strings = False
    if args.no_mba:
        cfg.enable_mba = False
    if args.no_opaque:
        cfg.enable_opaque_predicates = False
    if args.no_dead_code:
        cfg.enable_dead_code = False
    if args.no_cff:
        cfg.enable_cff = False
    # Kiem tra neu input la thu muc (batch processing)
    if os.path.isdir(args.input):
        input_dir = os.path.abspath(args.input)
        out_dir = os.path.abspath(args.output) if args.output else os.path.join(ROOT_DIR, "dist")
        os.makedirs(out_dir, exist_ok=True)
        files = glob.glob(os.path.join(input_dir, "**", "*.luau"), recursive=True) + \
                glob.glob(os.path.join(input_dir, "**", "*.lua"), recursive=True)
        if not files:
            print(f"[WARN] Khong tim thay file .lua/.luau nao trong thu muc: {input_dir}")
            sys.exit(0)
        print(f"[BATCH] Tim thay {len(files)} files trong {input_dir}. Bat dau xu ly...")
        success = 0
        for f in files:
            rel = os.path.relpath(f, input_dir)
            out_file = os.path.join(out_dir, rel)
            if _process_single_file(f, out_file, cfg, run_test=False):
                success += 1
        print(f"\n[BATCH HOAN THANH] {success}/{len(files)} files da duoc xu ly vao {out_dir}")
        return

    # Xu ly 1 file
    ok = _process_single_file(args.input, args.output, cfg, run_test=args.run)
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()