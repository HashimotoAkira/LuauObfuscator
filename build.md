# PROMPT: Xây dựng Luau Obfuscator (Python) — Bảo mật Script Game

Bạn là kỹ sư Python chuyên về trình biên dịch/parser. Hãy xây dựng một **Obfuscator Lua/Luau** hoàn chỉnh theo đúng đặc tả kỹ thuật dưới đây. Mục tiêu: script game sau khi obfuscate phải **chạy mượt 60 FPS**, **không lộ cấu trúc code cho người/AI đọc ngược**, và toàn bộ công cụ build phải **miễn phí 100%, không giới hạn tính năng**.

## 1. Kiến trúc tổng thể (Two-Tier Hybrid)

Pipeline xử lý theo 3 giai đoạn, biến `source.lua` → `output.lua`:

1. **Tiền xử lý & dọn dẹp**: lột type annotations Luau (`--!strict`, `:: any`, `: type`), tách hằng số chuỗi/số.
2. **Làm rối payload** (chạy ở runtime 60 FPS, nên phải rẻ CPU): AST name mangling, global-to-local caching, Control Flow Flattening (CFF) dạng cây nhị phân, variable hoisting.
3. **Backend VM tùy chọn**: biên dịch một subset AST thành bytecode 32-bit (32 opcode lai RISC/CISC), render VM runtime tự chạy, sau đó áp dụng name mangling và string pooling. VM mode chưa chạy CFF hoặc các pass advanced khác.

## 2. Cấu trúc thư mục bắt buộc

```
LuauObfuscator/
├── build/                       # Script build một lần
├── dist/                        # Output sau khi obfuscate
└── src/
    ├── parser/                  # lexer.py, parser.py, nodes.py
    ├── basic/                   # annotations.py, comment.py, naming.py, strings.py
    ├── advanced/                # cff.py, emitter.py, mba.py, opaque_predicates.py, dead_code.py
    ├── vm/                      # opcode.py, compiler.py, environment.py, runtime_template.py
    ├── utils/                   # crypto.py
    └── main.py                  # Pipeline obfuscation hoặc backend VM tùy chọn
```
```text
LuauObfuscator/
│
├── build/                          # Script build một lần
├── dist/                           # Chứa file Lua đầu ra sau khi Obfuscate
│
│
├── bin/
│   └── lune.exe                    # luau.exe CLI nhưng tương thích các thư viện roblox
│
│
└── src/
    │
    ├── parser/                     # Bộ phân tích cú pháp (Không có cái này là không làm được gì)
    │   ├── __init__.py
    │   ├── lexer.py                # Tách code thành Token
    │   ├── parser.py               # Tách Token thành AST
    │   └── nodes.py                # Định nghĩa các Class: FunctionNode, IfNode, BlockNode...
    │
    ├── basic/                      # Tầng 1: Dữ liệu cơ bản
    │   ├── __init__.py
    │   ├── annotations.py          # Lột sạch --!strict, :: any, : type
    │   ├── comment.py              # Xóa comment (để minify)
    │   ├── naming.py               # AST Variable Mangling (Đổi tên local, params, hoisting)
    │   └── strings.py              # Ghép Monolithic String Blob & mã hóa XOR
    │
    ├── advanced/                   # Tầng 2: Luồng nâng cao (Sửa 'advance' -> 'advanced')
    │   ├── __init__.py
    │   ├── cff.py                  # Sửa từ ControlFlowFlattening.py (B-Search CFF dùng dấu <)
    │   └── emitter.py              # [MỚI] In cây AST sau khi biến đổi ra lại code Lua nén 1 dòng
    │   └── mba.py                  # Mixed Boolean Arithmetic (Làm rối toán học & bitwise)
    │   └── opaque_predicates.py    # Điều kiện giả & Nhánh ma phối hợp CFF
    │   └── dead_code.py            # Bơm rác logic đè bẹp Context Window của AI
    │
    ├── vm/                         # Tầng 3: Máy ảo nạp (Chuyển ra ngoài hoặc để trong advanced đều được)
    │   ├── __init__.py
    │   ├── opcode.py               # Khai báo 32 Opcodes (16 RISC + 16 CISC)
    │   ├── compiler.py             # [MỚI] Dịch AST logic thành Bytecode 32-bit (Bit-packing)
    │   ├── environment.py          # Sửa lỗi chính tả 'enviroment' (Quản lý hằng số, bảng global)
    │   └── runtime_template.py     # [MỚI] Khung Lua chứa vòng lặp VM 5 tầng & bộ giải nén buffer
    │
    ├── utils/                      # [NÊN CÓ] Các hàm dùng chung
    │   ├── __init__.py
    │   └── crypto.py               # Hàm random key, XOR bitwise tiện ích
    │
    └── main.py                     # Pipeline chính: Gọi Parser -> Basic -> CFF -> VM -> Xuất file
```


## 3. Yêu cầu từng module

### 3.1 Basic (naming.py, strings.py, comment.py, annotations.py)
- Đổi tên biến **dựa trên AST**, không dùng Regex.
- Đổi tên: `local`, hàm `local`, tham số, biến vòng lặp, upvalues.
- Tên local ngắn dùng các ký tự dễ nhầm `I`, `l`, `1`.
- Field của table local được đổi đồng bộ ở khai báo và điểm truy cập chỉ khi
  nguồn table xác minh được; alias và table lồng được theo dõi. Table escape
  qua lời gọi/return, truy cập key động hoặc nguồn gán không rõ sẽ bị loại khỏi
  field mangling để bảo toàn API bên ngoài.
- **Không đổi tên**: global ngôn ngữ (`print`, `math`, `game`, `workspace`, `task`), thuộc tính engine (`part.Position`), metamethod (`__index`, `__newindex`, `__namecall`).
- Tên local dài 8–10 ký tự từ alphabet `l`, `I`, `i`, `1` (không tính tiền tố `_`); hỗ trợ Scope Shadowing và Global-to-Local caching.
- Khi bật string pooling, gộp mọi string literal (kể cả ngắn/rỗng) thành **1 blob byte duy nhất**, kèm mảng `{offset, length}`, mã hóa XOR theo khối 32-bit (word).
- Minify: không phá khoảng trắng giữa token chữ/số, xóa comment, tự chèn `;` cuối câu lệnh để nén về 1 dòng an toàn.

### 3.2 Advanced (cff.py, emitter.py)
- Variable hoisting: khai báo tập trung các biến local đã mangling trước vòng lặp CFF, bên trong block chỉ gán.
- Gom 3–7 lệnh tuần tự không rẽ nhánh thành 1 Basic Block (không băm từng dòng).
- `task.wait()` / `coroutine.yield()` bắt buộc nằm **cuối** block, không cắt giữa chừng.
- CFF dạng **cây tìm kiếm nhị phân dùng dấu `<`** (không dùng `if/elseif` phẳng) → độ phức tạp O(log₂N).

### 3.3 VM (opcode.py, compiler.py, runtime_template.py)
- Bật backend bằng `python src/main.py input.luau --enable-vm`.
- `build\build.cmd` mặc định bật VM; truyền `--standard` ở đối số thứ ba để
  chọn pipeline AST obfuscation thông thường. Input ngoài subset VM sẽ báo lỗi
  và không được fallback sang output thường.
- Sau khi render VM runtime, pipeline vẫn chạy name mangling và string pooling theo cấu hình; global caching và các pass advanced bị bỏ qua để tránh sinh node/toán tử mà compiler chưa hỗ trợ.
- Compiler báo `CompilerError` cho cú pháp ngoài subset hiện thực.
- 32 opcode = 16 RISC nền (`MOVE, LOADK, GETGLOBAL, SETGLOBAL, GETTABLE, SETTABLE, NEWTABLE, CLOSURE, ADD, SUB, MUL, DIV, EQ, LT, JMP, CALL`) + 16 CISC gộp (`ADDI, SUBI, GETTABLE_K, EQ_JMP, LT_JMP, CALL_GLOBAL`, ...).
- Bit-packing lệnh 32-bit: `OP(5 bit) | A(8 bit) | B(9 bit) | C(10 bit)`.
- Bộ nhớ Zero-GC: 4 mảng song song `Inst_OP, Inst_A, Inst_B, Inst_C` + mảng thanh ghi `R` (`table.create`), tuyệt đối không tạo table con `{op=..}`.
- Dispatcher: **inline trong vòng lặp `while`**, cây nhị phân 5 tầng `if op < X then`, không dùng bảng `handlers[op]()`.

### 3.4 Anti-Tamper (module độc lập, không thuộc pipeline)
- Giữ module `src/advanced/anti_tamper.py` để dùng riêng khi cần.
- Pipeline mặc định và các preset không chèn Anti-Tamper vào output.
- `table.freeze()` cho mọi config/table nhạy cảm.
- Khóa metatable: `__metatable = "Locked"`, `__newindex` gây treo vòng lặp vô tận.
- Không `error("Hack detected")` lộ liễu — khi phát hiện hook, **âm thầm làm sai dữ liệu tính toán**.
- Khóa giải mã chuỗi phụ thuộc thuộc tính runtime: `#workspace.Name + string.byte(game.Name, 1)` để offline decompiler không thể giải mã.

## 4. Quy ước code nguồn đầu vào (trước khi đưa vào obfuscator)

- Rule of Three: lặp ≥3 lần mới tách hàm riêng.
- Hàm dài 5–20 dòng là lý tưởng; vượt 30–50 dòng phải cắt nhỏ.
- Trong vòng lặp render 60 FPS: inline phép tính ngắn, tránh tạo Call Frame thừa.

## 5. Yêu cầu quy trình build công cụ Python (chỉ dùng miễn phí, không bản quyền)

- **Giai đoạn dev hàng ngày**: chạy bằng **PyPy3** thay CPython (`pypy src/main.py input.lua`) để có JIT, tốc độ AST/đệ quy/bitwise nhanh hơn CPython 3–7 lần, không tốn thời gian build.
- **Giai đoạn đóng gói bản phát hành** (ra `.exe` để bỏ vào thư mục Game): dùng **Nuitka bản Free** (không giới hạn hiệu năng/số lần build) theo quy trình 2 bước:
  1. Profiling: `python -m nuitka --pgo-python=src/main.py --pgo-args="test_input.lua" src/main.py`
  2. Build cuối với PGO + LTO: `python -m nuitka --standalone --onefile --lto=yes --pgo src/main.py -o Obfuscator_Turbo.exe`
- Chấp nhận thời gian build 10–15 phút để đổi lấy tốc độ máy thực thi nhanh hơn Python thường 2–4 lần.

## 6. Tiêu chí nghiệm thu (Definition of Done)

- [ ] File `.lua` sau obfuscate chạy đúng logic gốc 100%, không lỗi `nil`, không lỗi scope.
- [ ] Toàn bộ chuỗi trong output không đọc được bằng mắt thường (đã blob + XOR).
- [ ] Decompiler/AI không thể tái dựng lại cấu trúc điều khiển gốc (CFF cây nhị phân).
- [ ] VM chạy Zero-GC, không phát sinh rác bộ nhớ trong hot loop.
- [ ] Mọi thao tác chống can thiệp (freeze, metatable lock, silent-failure) hoạt động đúng khi bị monkey-patch.
- [ ] Build ra được `Obfuscator_Turbo.exe` bằng Nuitka Free + PGO + LTO, chạy độc lập không cần cài Python.

Hãy code lần lượt theo đúng thứ tự module ở mục 2–3, viết test nhỏ cho từng module trước khi ráp pipeline `main.py`.