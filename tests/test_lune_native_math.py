import os
import subprocess
import sys
import tempfile

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from src.vm.runtime_template import compile_and_render


FIXTURE_PATHS = (
    os.path.join(ROOT_DIR, "tests", "fixtures", "bfp_math_regression.luau"),
    os.path.join(ROOT_DIR, "tests", "fixtures", "bfp_runtime_smoke.luau"),
)
MATH_VM_ARTIFACT = os.path.join(ROOT_DIR, "dist", "BFP_math_vm_test.luau")
LUNE_BIN = os.path.join(ROOT_DIR, "bin", "lune.exe")
LUNE_NATIVE_TYPES = '''\
local roblox = require("@lune/roblox")
Vector2 = roblox.Vector2
Vector3 = roblox.Vector3
CFrame = roblox.CFrame
'''
DETERMINISTIC_ENGINE_STUBS = '''\
task = {
    spawn = function(callback, ...)
        return callback(...)
    end,
    defer = function(callback, ...)
        return callback(...)
    end,
    wait = function()
        return 0
    end,
}
Instance = {
    new = function(className)
        assert(className == "BindableEvent")
        local callbacks = {}
        local firedArgs
        local signal = {}
        function signal:Connect(callback)
            local connection = {Connected = true}
            callbacks[connection] = callback
            function connection:Disconnect()
                self.Connected = false
                callbacks[self] = nil
            end
            return connection
        end
        function signal:Fire(...)
            firedArgs = table.pack(...)
            for _, callback in pairs(callbacks) do
                callback(...)
            end
        end
        function signal:Wait()
            assert(firedArgs, "test signal waited before Fire")
            return table.unpack(firedArgs, 1, firedArgs.n)
        end
        return {
            Event = signal,
            Fire = function(_, ...)
                signal:Fire(...)
            end,
            Destroy = function() end,
        }
    end,
}
'''


def _run_lune(source: str) -> str:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".luau", encoding="utf-8", delete=False
    ) as source_file:
        source_file.write(source)
        source_path = source_file.name
    try:
        result = subprocess.run(
            [LUNE_BIN, "run", source_path],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise AssertionError(
                f"Lune exited with code {result.returncode}\n"
                f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result.stdout.strip()
    finally:
        os.unlink(source_path)


def _read_fixture(path: str) -> str:
    with open(path, encoding="utf-8") as fixture_file:
        return fixture_file.read()


def _assert_raw_and_vm_match(path: str, engine_stubs: str = "") -> tuple[str, str]:
    source = _read_fixture(path)
    prelude = LUNE_NATIVE_TYPES + engine_stubs
    native_output = _run_lune(prelude + source)
    vm_output = _run_lune(prelude + compile_and_render(source))

    assert native_output == vm_output, (
        f"Raw Lune result differs from VM result for {path}:\n"
        f"raw: {native_output}\n"
        f"vm:  {vm_output}"
    )
    assert "PASS" in native_output, native_output
    return source, native_output


def test_bfp_math_fixture_matches_native_lune_and_vm():
    source, output = _assert_raw_and_vm_match(FIXTURE_PATHS[0])
    with open(MATH_VM_ARTIFACT, "w", encoding="utf-8", newline="\n") as artifact:
        artifact.write(LUNE_NATIVE_TYPES + compile_and_render(source))
    print(f"bfp_math_regression.luau: {output}")


def test_runtime_smoke_fixture_matches_with_engine_stubs():
    _, output = _assert_raw_and_vm_match(
        FIXTURE_PATHS[1], DETERMINISTIC_ENGINE_STUBS
    )
    print(f"bfp_runtime_smoke.luau: {output}")


if __name__ == "__main__":
    if not os.path.isfile(LUNE_BIN):
        raise SystemExit(f"Lune executable not found: {LUNE_BIN}")
    test_bfp_math_fixture_matches_native_lune_and_vm()
    test_runtime_smoke_fixture_matches_with_engine_stubs()
