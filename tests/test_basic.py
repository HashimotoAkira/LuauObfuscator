import pathlib
import subprocess
import tempfile
import unittest

from src.basic.naming import NameAllocator
from src.main import ObfuscationPipeline, PipelineConfig


ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]
LUNE_EXE = ROOT_DIR / "bin" / "lune.exe"


class NamingAndStringPoolingTests(unittest.TestCase):
    def test_allocator_uses_8_to_10_confusable_characters(self):
        allocator = NameAllocator(protected=set())
        allocator.push_scope()
        names = [allocator.alloc() for _ in range(100)]

        self.assertEqual(len(set(names)), len(names))
        for name in names:
            self.assertTrue(name.startswith("_"))
            body = name[1:]
            self.assertGreaterEqual(len(body), 8)
            self.assertLessEqual(len(body), 10)
            self.assertTrue(set(body) <= set("lIi1"))

    @unittest.skipUnless(LUNE_EXE.is_file(), "Lune executable is unavailable")
    def test_default_pipeline_pools_short_and_empty_strings(self):
        result = ObfuscationPipeline(
            PipelineConfig(
                mangle_names=False,
                pool_strings=True,
                enable_mba=False,
                enable_opaque_predicates=False,
                enable_dead_code=False,
                enable_cff=False,
                watermark=None,
            )
        ).process('print("q", "")')

        self.assertEqual(PipelineConfig().string_min_len, 0)
        self.assertEqual(result.stats["pooled_strings_count"], 2)
        self.assertNotIn('"q"', result.output_code)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = pathlib.Path(temp_dir) / "standard_obfuscated.luau"
            output_path.write_text(result.output_code, encoding="utf-8")
            execution = subprocess.run(
                [str(LUNE_EXE), "run", str(output_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(execution.stdout.strip(), "q")

    @unittest.skipUnless(LUNE_EXE.is_file(), "Lune executable is unavailable")
    def test_string_decoder_locals_use_confusable_names(self):
        result = ObfuscationPipeline(
            PipelineConfig(
                enable_mba=False,
                enable_opaque_predicates=False,
                enable_dead_code=False,
                enable_cff=False,
                watermark=None,
            )
        ).process('print("q")')

        for generated_name in (
            "_SBLOB",
            "_SKEY",
            "_SKB",
            "_SBXOR",
            "_SCACHE",
            "local function _S(",
        ):
            self.assertNotIn(generated_name, result.output_code)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = pathlib.Path(temp_dir) / "decoder_names.luau"
            output_path.write_text(result.output_code, encoding="utf-8")
            execution = subprocess.run(
                [str(LUNE_EXE), "run", str(output_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(execution.stdout.strip(), "q")

    def test_configured_ciphertext_can_be_excluded_from_string_pool(self):
        result = ObfuscationPipeline(
            PipelineConfig(
                mangle_names=False,
                pool_strings=True,
                string_excluded_values=frozenset({"embedded-ciphertext"}),
                enable_mba=False,
                enable_opaque_predicates=False,
                enable_dead_code=False,
                enable_cff=False,
                watermark=None,
            )
        ).process('print("ordinary", "embedded-ciphertext")')

        self.assertEqual(result.stats["pooled_strings_count"], 1)
        self.assertIn('"embedded-ciphertext"', result.output_code)
        self.assertNotIn('"ordinary"', result.output_code)

    def test_private_table_fields_are_renamed_across_aliases_and_methods(self):
        source = (
            "local config = {speed = 2, nested = {enabled = true}}; "
            "local alias = config; "
            "function config:boost() return self.speed end; "
            "print(alias.speed + config:boost(), alias.nested.enabled)"
        )
        result = ObfuscationPipeline(
            PipelineConfig(
                pool_strings=False,
                global_caching=False,
                enable_mba=False,
                enable_opaque_predicates=False,
                enable_dead_code=False,
                enable_cff=False,
                watermark=None,
            )
        ).process(source)

        self.assertNotIn(".speed", result.output_code)
        self.assertNotIn(".nested", result.output_code)
        self.assertNotIn(".enabled", result.output_code)
        self.assertNotIn(":boost", result.output_code)
        self.assertIn("self.", result.output_code)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = pathlib.Path(temp_dir) / "private_table.luau"
            output_path.write_text(result.output_code, encoding="utf-8")
            execution = subprocess.run(
                [str(LUNE_EXE), "run", str(output_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(execution.stdout.strip(), "4 true")

    def test_shared_table_schema_fields_can_be_kept_stable(self):
        source = (
            'local Bus = {Pipelines = {__newindex = {}}, SchemaVersion = 1}; '
            'genv.__SAFE_HOOK_BUS__ = Bus; '
            'local HookBus = genv.__SAFE_HOOK_BUS__; '
            'print(HookBus.SchemaVersion, HookBus.Pipelines.__newindex)'
        )
        result = ObfuscationPipeline(
            PipelineConfig(
                pool_strings=False,
                global_caching=False,
                mangle_table_fields=False,
                enable_mba=False,
                enable_opaque_predicates=False,
                enable_dead_code=False,
                enable_cff=False,
                watermark=None,
            )
        ).process(source)

        self.assertIn(".SchemaVersion", result.output_code)
        self.assertIn(".Pipelines", result.output_code)
        self.assertIn(".__newindex", result.output_code)

    def test_untraceable_or_escaped_table_fields_are_preserved(self):
        sources = (
            (
                "local config = getgenv().Config; print(config.speed)",
                ".speed",
            ),
            (
                "local config = {speed = 2}; consume(config); print(config.speed)",
                ".speed",
            ),
            (
                "local config = {speed = 2}; print(config[getKey()])",
                "speed=2",
            ),
        )
        for source, expected in sources:
            with self.subTest(source=source):
                result = ObfuscationPipeline(
                    PipelineConfig(
                        pool_strings=False,
                        global_caching=False,
                        enable_mba=False,
                        enable_opaque_predicates=False,
                        enable_dead_code=False,
                        enable_cff=False,
                        watermark=None,
                    )
                ).process(source)
                self.assertIn(expected, result.output_code)

    @unittest.skipUnless(LUNE_EXE.is_file(), "Lune executable is unavailable")
    def test_table_fields_fail_closed_for_closures_and_rebinding(self):
        cases = (
            (
                "local config; "
                "config = {speed = 2, read = function() return config.speed end}; "
                "print(config.read())",
                "2",
            ),
            (
                "local config = {speed = 1}; "
                "local function read() return config.speed end; "
                "config = {speed = 3}; print(read())",
                "3",
            ),
            (
                "local config = {speed = 1}; "
                "if true then config = {speed = 4} end; "
                "print(config.speed)",
                "4",
            ),
        )
        for source, expected_output in cases:
            with self.subTest(source=source):
                result = ObfuscationPipeline(
                    PipelineConfig(
                        pool_strings=False,
                        global_caching=False,
                        enable_mba=False,
                        enable_opaque_predicates=False,
                        enable_dead_code=False,
                        enable_cff=False,
                        watermark=None,
                    )
                ).process(source)
                self.assertIn(".speed", result.output_code)

                with tempfile.TemporaryDirectory() as temp_dir:
                    output_path = pathlib.Path(temp_dir) / "safe_table_fields.luau"
                    output_path.write_text(result.output_code, encoding="utf-8")
                    execution = subprocess.run(
                        [str(LUNE_EXE), "run", str(output_path)],
                        capture_output=True,
                        text=True,
                        timeout=15,
                        check=False,
                    )

                self.assertEqual(execution.returncode, 0, execution.stderr)
                self.assertEqual(execution.stdout.strip(), expected_output)

    @unittest.skipUnless(LUNE_EXE.is_file(), "Lune executable is unavailable")
    def test_advanced_generated_locals_use_confusable_names(self):
        result = ObfuscationPipeline(
            PipelineConfig(
                pool_strings=False,
                global_caching=False,
                enable_mba=False,
                enable_opaque_predicates=True,
                opaque_condition_guard_probability=1.0,
                opaque_dead_branch_probability=0.0,
                opaque_phantom_clause_probability=0.0,
                opaque_max_injections=1,
                enable_dead_code=False,
                enable_cff=True,
                cff_min_len=1,
                seed=7,
                watermark=None,
            )
        ).process(
            "local total = 0; "
            "if true then total = total + 1 else total = total + 2 end; "
            "print(total)"
        )

        for generated_prefix in ("_st", "_bf", "_cf", "_op"):
            self.assertNotIn(generated_prefix, result.output_code)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = pathlib.Path(temp_dir) / "generated_names.luau"
            output_path.write_text(result.output_code, encoding="utf-8")
            execution = subprocess.run(
                [str(LUNE_EXE), "run", str(output_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(execution.stdout.strip(), "1")

    @unittest.skipUnless(LUNE_EXE.is_file(), "Lune executable is unavailable")
    def test_vm_pipeline_mangles_names_and_pools_runtime_strings(self):
        source = (
            'local originalMessage = "unique vm string"; '
            'local empty = ""; '
            'local success, value = pcall(function() return originalMessage end); '
            'assert(success and empty == ""); print(value)'
        )
        result = ObfuscationPipeline(
            PipelineConfig(enable_vm=True, watermark=None)
        ).process(source)

        self.assertNotIn("VM invalid multret argument", result.output_code)
        self.assertNotIn("unique vm string", result.output_code)
        self.assertNotIn("local originalMessage", result.output_code)
        self.assertGreater(result.stats["renamed_identifiers"], 0)
        self.assertGreater(result.stats["pooled_strings_count"], 0)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = pathlib.Path(temp_dir) / "vm_obfuscated.luau"
            output_path.write_text(result.output_code, encoding="utf-8")
            execution = subprocess.run(
                [str(LUNE_EXE), "run", str(output_path)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )

        self.assertEqual(execution.returncode, 0, execution.stderr)
        self.assertEqual(execution.stdout.strip(), "unique vm string")


if __name__ == "__main__":
    unittest.main()
