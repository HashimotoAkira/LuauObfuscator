# LuauObfuscator — VM module (parser + vm)

Tài liệu về parser và VM backend của LuauObfuscator.

## Cấu trúc

```
src/
├── parser/
│   ├── __init__.py      # export Lexer, Parser, parse(), nodes
│   ├── lexer.py          # tokenizer Lua/Luau (string/number/comment/directive...)
│   ├── nodes.py          # dinh nghia AST (~40 loai node)
│   └── parser.py         # recursive-descent + precedence climbing -> AST
└── vm/
    ├── __init__.py       # export tat ca API duoi day
    ├── opcode.py          # 32 opcode (16 RISC + 16 CISC), bit-packing 32-bit
    ├── environment.py     # constant pool + register allocator + resolve local/upvalue/global
    ├── compiler.py        # AST -> FunctionProto (bytecode + constant pool)
    └── runtime_template.py # FunctionProto -> file .lua hoan chinh, tu chay duoc

tests/                    # Test scripts cho parser, opcode, environment, compiler, runtime
test_sample.lua            # file Luau mau dung de test parser
```

## Dùng nhanh (end-to-end)

```python
from src.vm.runtime_template import compile_and_render

lua_output = compile_and_render(source_code_luau)
# lua_output la 1 chuoi .lua HOAN CHINH, tu chay duoc (hoac co the require())
with open("output.lua", "w", encoding="utf-8") as f:
    f.write(lua_output)
```

Từng bước riêng:

```python
from src.parser import parse
from src.vm.compiler import compile_chunk
from src.vm.runtime_template import render_runtime

ast = parse(source_code_luau)
# VM supports only a subset of parser AST nodes and operators.
main_idx, protos = compile_chunk(ast)
lua_output = render_runtime(main_idx, protos)
```

`compile_chunk` nhận AST từ `src/parser/nodes.py`, nhưng sẽ ném
`CompilerError` cho node/toán tử chưa hỗ trợ. AST parser hợp lệ không đồng
nghĩa VM hiện thực hóa được mọi cú pháp Luau.

## Tích hợp trong pipeline chính

VM là backend tùy chọn, bật bằng `--enable-vm` hoặc
`PipelineConfig(enable_vm=True)`. Backend parse và có thể bỏ comment/type
annotations, sau đó gọi `compile_chunk` và `render_runtime`. Sau đó name
mangling và string pooling chạy trên VM runtime đã sinh theo cấu hình. Các pass
global caching, MBA, opaque predicates, dead code và CFF được bỏ qua. VM mode là
backend thay thế, không phải bước cuối nối sau các pass đó. Thống kê pipeline
ghi rõ các pass đã bỏ qua.

```bash
python src/main.py script.luau --enable-vm
```

VM chưa hỗ trợ toàn bộ Luau hoặc mọi BloxFruit source; cú pháp ngoài subset
sẽ dừng với `CompilerError` và vị trí nguồn.

Runtime VM resolves globals from the executor environment returned by
`getgenv()` first, then falls back to the script environment. This keeps
executor APIs and Roblox globals available without replacing or emulating
either environment.

The VM passes the host `task` library through as a normal global. Calls such as
`task.spawn`, `task.defer`, `task.delay`, `task.wait`, and `task.cancel` use the
executor/Roblox scheduler; the VM does not create its own scheduler or threads.
Scheduled callbacks are ordinary VM closures and can capture locals.

Upvalue capture discovery is repeated until the register layout stabilizes.
Captured loop variables are boxed per iteration so closures keep the value
from the iteration that created them.

`tests/fixtures/bfp_runtime_smoke.luau` is a small BloxFruit-shaped runtime
fixture that uses Roblox-native `Vector2`, `Vector3`, `CFrame`, `Instance`,
`typeof`, and `task` directly. It covers vector arithmetic and methods,
multiple returns and argument expansion, captured callbacks in a loop,
BindableEvent signals, scheduler callbacks, table operations, `if` expressions,
and `print`. It creates and destroys only a local BindableEvent; it does not
access game services or modify a character. The automated test compiles this
fixture through the VM and checks generated Luau syntax, but native runtime
assertions must be executed in Roblox Studio or an executor:

```bash
python src/main.py tests/fixtures/bfp_runtime_smoke.luau -o dist/BFP_smoke_obf.luau --enable-vm
```

`tests/fixtures/bfp_math_regression.luau` isolates the Vector2/Vector3/CFrame
math patterns used by BloxFruit movement, combat, and aiming code.
`tests/test_lune_native_math.py` supplies actual datatype values from Lune's
`@lune/roblox` module, runs each fixture raw and through the VM, then requires
identical output. The math artifact is generated as part of the test:

```bash
python tests/test_lune_native_math.py
```

The runnable VM-only Lune artifact is `dist/BFP_math_vm_test.luau`. The
separate runtime smoke comparison uses deterministic test doubles only for
`Instance`/`BindableEvent` and `task`; it does not claim to simulate Roblox
engine scheduling. Vector2, Vector3, and CFrame are Lune's actual native
datatypes in both raw and VM runs.

## Giới hạn đã biết (compiler sẽ ném `CompilerError` rõ ràng, không bao giờ sinh bytecode sai)

| Tính năng | Trạng thái |
|---|---|
| Upvalue (closure lồng đọc/ghi biến ngoài, kể cả đệ quy `local function`) | Đã hỗ trợ bằng capture cell dùng chung, gồm biến numeric-for và generic-for theo từng vòng |
| Nhiều giá trị trả về, `{...}`, `select("#", ...)` | Đã hỗ trợ cho `return`, gán theo số biến cố định (kể cả call cuối sau các giá trị RHS khác), và vararg; call/method-call cuối trong danh sách đối số được mở rộng |
| `for ... in iterator(...)`, `for ... in table`, và danh sách iterator 2/3 giá trị | Đã hỗ trợ iterator triple, table iteration và method call |
| `%`, `^`, `//`, `#` (length) | Đã hỗ trợ qua runtime helper |
| Unary minus (bao gồm kiểu có `__unm`, như vector) | Đã hỗ trợ bằng phép phủ định một ngôi của Luau |
| `goto`/label | Chưa hỗ trợ — cần resolve 2-pass |
| `%=`, `^=`, `//=` | Đã hỗ trợ qua runtime helper |

## Test

```bash
python3 tests/test_lexer_parser.py   # 40 test
python3 tests/test_opcode.py         # 18 test
python3 tests/test_environment.py    # 25 test
python3 tests/test_compiler.py       # 57 test (kiem tra cau truc bytecode)
python3 tests/test_runtime_e2e.py    # Runtime tests qua Lua 5.4 hoac Luau CLI (hien co 65 test)
```

Runtime tests se duoc bo qua neu khong tim thay Lua 5.4 hoac Luau CLI.

## Giới hạn triển khai

- `environment.py` giới hạn constant pool **262656 hằng số/hàm** và thanh ghi
  **256/hàm** (`ConstantPoolOverflowError`, `RegisterOverflowError`).
- `GETGLOBAL`/`SETGLOBAL` dùng C=0 cho global với constant index thấp,
  C=1..509 cho capture cell, và C>=512 cho global với constant index mở rộng.
  `GETGLOBAL` dành riêng C=510/511 cho vararg/runtime helper. Compiler chạy hai
  lượt để xác định local cần box trước khi phát bytecode cuối. Các opcode
  truy cập constant khác mã hóa phần chỉ số cao trong operand còn lại, hoặc
  fallback sang `LOADK` + truy cập thanh ghi.
- Không nối trực tiếp các pass `basic/`/`advanced/` hiện có trước compiler VM:
  string pooling dùng toán tử chưa hỗ trợ và các pass control-flow sinh thêm
  cấu trúc AST có thể vượt khỏi subset. `--enable-vm` chủ động bỏ qua các pass
  đó.
- `TEST`, `RETURN` là 2 opcode mình **tự thêm vào** thay cho `GETTABLE_N`/
  `SETTABLE_N` trong bản phác thảo opcode ban đầu — lý do: ISA ban đầu
  **thiếu lệnh RETURN** (không hàm nào trả về được) và việc giả lập
  if/while/and/or không có TEST rất cồng kềnh. Nếu bạn đã lỡ dùng
  `GETTABLE_N`/`SETTABLE_N` ở đâu đó trong code của mình, cần đổi sang
  `GETTABLE_K`/`SETTABLE_K` (dùng được cho cả index số nguyên).
- Mọi chỉ số (thanh ghi, hằng số, chỉ số lệnh) trong runtime Lua sinh ra
  đều **0-based**, khớp thẳng với phía Python — không cộng/trừ 1 ở đâu.
