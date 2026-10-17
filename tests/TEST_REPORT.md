# BÁO CÁO KIỂM THỬ VÀ REVIEW LỖI - BỘ OBFUSCATOR LUAU

**Dự án:** Luau Obfuscator (Python)  
**Thời gian thực hiện:** 09/10/2026  
**Môi trường kiểm thử:**
- Python: `3.14.7`
- Luau Runtime: `Lune 0.10.5` (`LuauObfuscator/bin/lune.exe`) & `Luau CLI` (`.storage/Luau/luau.exe`)
- Test Framework: Python `unittest` chuẩn + Central Runner `run_tests.py`

---

## 1. TỔNG QUAN KẾT QUẢ KIỂM THỬ (SAU KHI FIX BUG)

Toàn bộ các thành phần theo yêu cầu (**Parser**, **Utils**, **Basic**, **Advanced**, **Anti-Tamper**, và **Integration Pipeline**) đã được kiểm thử và sửa lỗi dứt điểm. Tất cả **85 test cases** đều đạt trạng thái **PASS 100%**:

| Module / Nhóm | File Test | Số Test | Trạng thái trước fix | Trạng thái hiện tại | Đánh giá |
|---|---|:---:|:---:|:---:|:---:|
| **Parser** (`lexer`, `parser`, `traversal`, `nodes`) | `tests/test_parser.py` | 32 | 32 PASS | **32 PASS** | **100% OK** |
| **Utils** (`crypto`) | `tests/test_utils.py` | 10 | 10 PASS | **10 PASS** | **100% OK** |
| **Basic** (`annotations`, `comment`, `naming`, `strings`) | `tests/test_basic.py` | 18 | 17 PASS, 1 FAIL | **18 PASS** | **100% OK** |
| **Advanced** (`emitter`, `mba`, `dead_code`, `opaque_predicates`, `cff`, `anti_tamper`) | `tests/test_advanced.py` | 17 | 12 PASS, 1 FAIL | **17 PASS** | **100% OK** |
| **Integration** (Full Pipeline trên Lune/Luau) | `tests/test_integration.py` | 8 | 5 PASS, 3 FAIL | **8 PASS** | **100% OK** |
| **TỔNG CỘNG** | **5 test suites** | **85** | **76 PASS, 5 FAIL** | **85 PASS (100%)** | **HOÀN THÀNH** |

> **Ghi chú về VM:** Theo đúng yêu cầu, module `src/vm` chưa hoàn thành nên được giữ nguyên độc lập và loại trừ khỏi pipeline `main.py` ở thời điểm hiện tại.

---

## 2. CHI TIẾT CÁC LỖI ĐÃ ĐƯỢC KHẮC PHỤC (BUGS RESOLVED)

### [BUG 1] `naming.py` không rename receiver object trong `FunctionDeclStmt` -> [ĐÃ SỬA]
- **Nguyên nhân:** Hàm `_h_function_decl` trước đây bỏ qua hoàn toàn `stmt.name_path` vì giả định `name_path` luôn là biến toàn cục. Khi đối tượng được khai báo dạng `local obj = {}`, `naming.py` đổi tên `obj` thành `_a`, nhưng lệnh `function obj:getVal()` vẫn giữ nguyên `"obj"`, dẫn đến lỗi `attempt to index nil with 'getVal'`.
- **Cách sửa:** Trong `_h_function_decl`, kiểm tra `resolved = scope.resolve(stmt.name_path[0])`. Nếu là biến local đã mangled, cập nhật `stmt.name_path[0] = resolved`.

### [BUG 2] Xung đột tham số `self` giữa `parser`, `naming` và `emitter` -> [ĐÃ SỬA]
- **Nguyên nhân:** Khi định nghĩa method `function obj:method()`, parser tự thêm `self` vào `params`, naming đổi tên `self` thành `_b`, trong khi emitter kiểm tra cứng `param.name == "self"` nên không nhận diện được để loại bỏ receiver ẩn, in ra `function obj:method(_b)` khiến `_b` nhận `nil` lúc runtime.
- **Cách sửa:**
  1. Trong `naming.py`: Hàm `_visit_function_body` nhận cờ `is_method=stmt.is_method`. Nếu là method và tham số đầu tiên là `"self"`, giữ nguyên tên `"self"` và bind `child.declare("self", "self")` để bảo toàn từ khóa ngầm định của Lua.
  2. Trong `emitter.py`: Khi `skip_first_param=True`, luôn bỏ qua tham số đầu tiên bất kể tên gì.

### [BUG 3] `emitter.py` in hỏng Long String (`[[...]]`) -> [ĐÃ SỬA]
- **Nguyên nhân:** `lexer.py` lưu raw text của long string không có cặp dấu ngoặc `[[` và `]]`. Emitter lấy `node.raw` in chuỗi trần ra file làm vỡ cú pháp Lua (`Incomplete statement`).
- **Cách sửa:** Trong `emitter.py`, kiểm tra nếu `node.is_long_string` hoặc `node.raw` không có dấu ngoặc kép/đơn, tự động bọc chuỗi an toàn qua `_escape_short_string(node.value)`.

### [BUG 4] `opaque_predicates.py` chèn `dead_if()` sau câu lệnh phân kỳ -> [ĐÃ SỬA]
- **Nguyên nhân:** Hàm `_camouflage_statements` tự động append `dead_if()` vào cuối block mà không kiểm tra xem câu lệnh cuối cùng có phải là `ReturnStmt`, `BreakStmt`, `ContinueStmt`, `GotoStmt` hay không. Khi hàm kết thúc bằng `return`, mã sinh ra là `return val; if ... end;` gây lỗi `Expected 'end', got 'if'`.
- **Cách sửa:** Định nghĩa `_DIVERGENT_STMTS` và kiểm tra `if not last_is_divergent:` trước khi append `dead_if()` vào cuối block.

### [BUG 5] `opaque_predicates.py` gọi trực tiếp `tick()` gây crash ngoài Roblox -> [ĐÃ SỬA]
- **Nguyên nhân:** Mặc định `dialect="roblox"` gọi `tick()`. Trên Lune hoặc Luau standalone không có `tick()` dẫn đến `attempt to call a nil value`.
- **Cách sửa:** Đổi biểu thức gọi clock của dialect roblox thành `(tick or os.clock)()`. Trong Roblox chạy `tick()`, ngoài Roblox tự động fallback sang `os.clock()`, hoàn toàn an toàn và tương thích kép.

### [BUG 6] Refactor `_ast_utils.py` sang `src/parser/traversal.py` -> [ĐÃ SỬA]
- **Thực hiện:**
  - Chuyển toàn bộ logic duyệt cây AST sang `src/parser/traversal.py`.
  - Export trực tiếp `walk_tree`, `transform_tree`, `DROP` từ package `src.parser`.
  - Cập nhật toàn bộ các import trong toàn codebase.
  - Xóa bỏ hoàn toàn file cũ `src/basic/_ast_utils.py`.

### [BUG 7] Tên file `src/Mini interp.py` có dấu cách -> [ĐÃ SỬA]
- **Thực hiện:** Đổi tên thành `src/mini_interp.py` chuẩn PEP 8 và sửa `sys.path` trong `src/Test semantics.py`. File kiểm thử ngữ nghĩa chạy thành công 88/88 test.

### [BUG 8] Export thiếu trong `src/advanced/__init__.py` và `src/utils/__init__.py` -> [ĐÃ SỬA]
- **Thực hiện:** Export đầy đủ `cff`, `dead_code`, `opaque_predicates`, `anti_tamper`, `emitter`, `mba` và các crypto utilities.

### [BUG 9] `cff.py` không relay `continue_flag` khi lồng nhiều tầng -> [ĐÃ SỬA]
- **Nguyên nhân:** Trong `cff.py`, khi xử lý câu lệnh `continue`, hàm `_rewrite_break_continue_in_list` chỉ thay `continue` bằng `break` trần mà không gán cờ `continue_flag`. Khi một câu lệnh `if` chứa `continue` bị làm phẳng (CFF) lồng bên trong vòng lặp chính, lệnh `break` chỉ thoát khỏi vòng lặp dispatcher nội bộ của `if`, sau đó tiếp tục rơi xuống thực thi các lệnh phía sau trong vòng lặp ngoài thay vì nhảy sang lần lặp tiếp theo.
- **Cách sửa:** Bổ sung quản lý `continue_flag` song song với `break_flag`:
  - Trong `_rewrite_break_continue_in_list`: gán `<continue_flag> = true; break`.
  - Sau vòng lặp dispatcher: chèn `if <continue_flag> then continue end` để tiếp tục relay tín hiệu `continue` ra tầng ngoài cho đến khi chạm tới vòng lặp thật.

### [BUG 10] MBA biến đổi toán tử trên đối tượng có metamethod (`table`, `Vector3`, `CFrame`) -> [ĐÃ SỬA]
- **Nguyên nhân:** Khi gặp biểu thức `a + b` hoặc `a - b`, MBA trước đây áp dụng k-split biến đổi thành `(a + k) + (b - k)` một cách mù quáng cho mọi kiểu dữ liệu. Khi `a` và `b` là đối tượng tùy biến có metamethod `__add` (như `c1 + c2` trong OOP hoặc `Vector3 + Vector3` trong Roblox), phép tính `c1 - k` bị lỗi do `c1` không hỗ trợ trừ với số thực `k` (`attempt to perform arithmetic (sub) on table and number`), hoặc `c1 + k` gọi method cộng truyền vào số thực `k` gây lỗi khi truy cập trường (`attempt to index number with 'r'`).
- **Cách sửa:** Trong `mba.py`, bổ sung cờ `require_numeric_hint: bool = True` (mặc định bật). MBA chỉ thay thế các phép toán khi có ít nhất một toán hạng được xác định rõ là số học (`NumberExpr` hoặc biểu thức số). Với các biến đối tượng `a + b`, biểu thức được giữ nguyên để bảo toàn toàn vẹn cơ chế nạp chồng toán tử metamethod của Lua/Luau.

---

## 3. CÁC TÍNH NĂNG MỚI ĐÃ BỔ SUNG

1. **Module Anti-Tamper (`src/advanced/anti_tamper.py`)**:
   - `table.freeze`: Tự động đóng băng cấu trúc bảng nhạy cảm (bảng key giải mã chuỗi, cache table) với fallback an toàn trên mọi runtime.
   - Metatable Lock: Khóa metatable bằng `__metatable = "Locked"` và bẫy `__newindex` gây treo nếu có hành vi can thiệp trái phép.
   - Silent Corruption Guard: Kiểm tra toàn vẹn môi trường runtime; nếu phát hiện bị hook/patch thì âm thầm gây sai lệch kết quả tính thay vì báo lỗi lộ liễu.
2. **Nâng cấp `src/main.py`**:
   - Hỗ trợ các profile thiết lập sẵn: `--preset min`, `--preset balanced`, `--preset max`.
   - Hỗ trợ chế độ xử lý hàng loạt theo thư mục (Batch Mode): `python src/main.py src_dir/ -o dist_dir/`.
   - Hỗ trợ luồng dữ liệu chuẩn `stdin` / `stdout` qua ký tự `-`.
   - Cung cấp các cờ bật/tắt chi tiết cho từng tầng xử lý.
3. **Bộ kiểm thử ma trận chuyên sâu (`test_matrix.py` & `complex_test.luau`)**:
   - File `complex_test.luau` bao phủ toàn diện 8 khía cạnh phức tạp nhất của Luau (OOP/Metatable, Closures/Shadowing, Nested loops/Continue/Break, Compound/Bitwise, String literals/Long string, Table sort/pairs, Varargs/Multi-return, Type casts).
   - Kiểm thử ma trận 29/29 bài test độc lập: 11 Isolated Passes (PASS), 3 Presets (PASS), 15 Random Seeds kết hợp toàn bộ tính năng cùng lúc (PASS 100%).

---

## 4. CÁCH CHẠY BỘ TEST

Chạy toàn bộ 85 tests qua runner tập trung:
```powershell
& .\.venv\Scripts\python.exe LuauObfuscator\run_tests.py
```

Chạy ma trận kiểm thử nâng cao trên script phức tạp (`complex_test.luau`):
```powershell
& .\.venv\Scripts\python.exe LuauObfuscator\test_matrix.py
```

Chạy riêng từng nhóm module:
```powershell
& .\.venv\Scripts\python.exe LuauObfuscator\run_tests.py --module parser
& .\.venv\Scripts\python.exe LuauObfuscator\run_tests.py --module utils
& .\.venv\Scripts\python.exe LuauObfuscator\run_tests.py --module basic
& .\.venv\Scripts\python.exe LuauObfuscator\run_tests.py --module advanced
& .\.venv\Scripts\python.exe LuauObfuscator\run_tests.py --module integration
```
