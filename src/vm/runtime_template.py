# -*- coding: utf-8 -*-
"""
runtime_template.py
=====================
Sinh ra 1 file Lua HOAN CHINH, TU CHAY DUOC: nhung bytecode da bien dich
(tu compiler.py) vao 1 khung VM viet bang Lua (dispatcher register-based,
32 opcode, cay nhi phan 5 tang), cong voi "bo giai nen buffer" - buoc
giai ma cac lenh 32-bit (dong goi boi opcode.encode_instruction) thanh 4
mang song song Inst_OP/Inst_A/Inst_B/Inst_C, chay 1 lan luc cold start.

Kien truc du lieu trong Lua sinh ra (xem them ghi chu trong ham build):
    - MOI chi so (thanh ghi R[], hang so K[], chi so lenh/pc, chi so proto)
      deu dung quy uoc 0-based, GIONG HET phia Python (opcode.py, compiler.py),
      de khong can "+1" o bat ky dau - giam toi da rui ro loi lech chi so.
    - Theo opcode.py: CHI truong C co the mang gia tri CO DAU (IMM/JMP);
      truong B LUON LUON khong dau (da xac nhan bang cach duyet toan bo
      OPCODE_INFO) - nen bang C_SIGNED (32 phan tu) la DU, khong can bang
      rieng cho B.
    - Dispatcher la 1 cay if/else nhi phan CAN BANG, duoc SINH TU DONG
      bang `_emit_dispatch_tree()` (khong go tay tung nhanh) de tranh
      nham lan khi liet ke 32 truong hop.
    - Zero-GC: R[] (thanh ghi) duoc table.create() DUY NHAT 1 LAN moi khi
      goi ham (khong phai moi lenh); vong lap dispatch KHONG tao table
      con nao ca (tru 1 duong fallback hiem gap cho CALL voi >4 tham so).

Gioi han ke thua tu compiler.py (xem ghi chu chi tiet o do): chua ho tro
multret/vararg, '%','^','#', goto/label - cac gioi han nay la o
TANG BIEN DICH (AST->bytecode), runtime nay chi THI HANH dung nhung gi
compiler.py da sinh ra, khong co gioi han THEM nao o tang runtime.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List

from .opcode import Opcode, OPCODE_INFO, OperandKind
from .compiler import FunctionProto


# ---------------------------------------------------------------------------
# Lua literal rendering (string / number / hang so)
# ---------------------------------------------------------------------------

def _lua_string_literal(s: str) -> str:
    """Chuyen 1 chuoi Python thanh literal Lua AN TOAN TUYET DOI: ma hoa
    tung byte (UTF-8) thanh ky tu ASCII in duoc hoac escape thap phan
    \\ddd (LUON 3 chu so, zero-pad) de tranh Lua "nuot" nham chu so tiep
    theo (Lua doc \\ddd tham lam toi 3 chu so).
    """
    data = s.encode("utf-8")
    out = ['"']
    for byte in data:
        if byte == 0x22:      # "
            out.append('\\"')
        elif byte == 0x5C:    # backslash
            out.append("\\\\")
        elif byte == 0x0A:    # newline
            out.append("\\n")
        elif byte == 0x0D:    # carriage return
            out.append("\\r")
        elif byte == 0x09:    # tab
            out.append("\\t")
        elif 0x20 <= byte < 0x7F:
            out.append(chr(byte))
        else:
            out.append(f"\\{byte:03d}")
    out.append('"')
    return "".join(out)


def _lua_number_literal(v) -> str:
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if v != v:                # NaN
            return "(0/0)"
        if v == float("inf"):
            return "(1/0)"
        if v == float("-inf"):
            return "(-1/0)"
        return repr(v)
    raise ValueError(f"Gia tri so khong hop le: {v!r}")


def _render_constant(v: Any) -> str:
    """Render 1 phan tu cua constant pool (nil/bool/number/string) thanh
    literal Lua. Day la dang "chua obfuscate" (plain) - buoc gop chuoi
    thanh 1 blob + XOR (utils/crypto.py) se thay the ham nay o ban nang
    cap sau, khong anh huong phan con lai cua runtime.
    """
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return _lua_number_literal(v)
    if isinstance(v, str):
        return _lua_string_literal(v)
    raise ValueError(f"Khong the nhung hang so kieu {type(v).__name__} vao runtime")


# ---------------------------------------------------------------------------
# Sinh cay dispatch nhi phan can bang (khong go tay, tranh nham lan)
# ---------------------------------------------------------------------------

def _emit_dispatch_tree(opcodes: List[int], case_lines: Dict[int, List[str]], depth: int) -> List[str]:
    """Sinh code Lua dang `if op < X then ... else ... end` nhi phan CAN
    BANG, phan biet moi gia tri trong `opcodes` (danh sach LIEN TUC, da
    sap xep tang dan). `case_lines[op]` la danh sach dong lenh (CHUA thut
    le) xu ly cho dung opcode do. Dung de quy, moi lan chia doi danh sach,
    nen do sau cay ~ log2(so luong opcode) - voi 16 hoac 32 opcode cho ra
    dung "cay nhi phan 5 tang" nhu README yeu cau.
    """
    pad = "  " * depth
    if len(opcodes) == 1:
        return [pad + line for line in case_lines[opcodes[0]]]

    mid = len(opcodes) // 2
    left, right = opcodes[:mid], opcodes[mid:]
    pivot = right[0]

    lines = [f"{pad}if op < {pivot} then"]
    lines += _emit_dispatch_tree(left, case_lines, depth + 1)
    lines.append(f"{pad}else")
    lines += _emit_dispatch_tree(right, case_lines, depth + 1)
    lines.append(f"{pad}end")
    return lines


# ---------------------------------------------------------------------------
# Dinh nghia xu ly cho tung opcode (noi dung "than" cua tung nhanh la)
# ---------------------------------------------------------------------------
# Quy uoc bien trong moi doan code duoi day (da co san trong than dispatch):
#   op, a, b, c  - cac truong da giai ma cua lenh hien tai
#   R            - mang thanh ghi (0-based) cua LAN GOI HAM hien tai
#   K            - mang hang so (0-based) cua PROTO hien tai
#   PC_REF       - "bien tham chieu" pc (xem ghi chu ve __PC trick ben duoi)
#   PROTOS, GLOBALS, table_create, table_unpack, make_closure - dung chung

def _build_case_lines() -> Dict[int, List[str]]:
    cases: Dict[int, List[str]] = {}

    cases[Opcode.MOVE] = ["R[a] = R[b]"]
    cases[Opcode.LOADK] = ["R[a] = K[b + c * 512]"]
    cases[Opcode.GETGLOBAL] = [
        "if c == 0 then R[a] = GLOBALS[K[b]]",
        "elseif c == 510 then R[a] = V",
        "elseif c == 511 then R[a] = VM_HELPERS[b]",
        "elseif c >= 512 then R[a] = GLOBALS[K[b + (c - 511) * 512]]",
        "else",
        "  local cell = U[c - 1]",
        "  if type(cell) ~= 'table' then error('VM invalid upvalue cell: proto=' .. proto.name .. ', pc=' .. (pc - 1) .. ', upvalue=' .. (c - 1) .. ', type=' .. type(cell), 2) end",
        "  R[a] = cell[1]",
        "end",
    ]
    cases[Opcode.SETGLOBAL] = [
        "if c == 0 then GLOBALS[K[b]] = R[a]",
        "elseif c >= 512 then GLOBALS[K[b + (c - 511) * 512]] = R[a]",
        "else",
        "  local cell = U[c - 1]",
        "  if type(cell) ~= 'table' then error('VM invalid upvalue cell: proto=' .. proto.name .. ', pc=' .. (pc - 1) .. ', upvalue=' .. (c - 1) .. ', type=' .. type(cell), 2) end",
        "  cell[1] = R[a]",
        "end",
    ]
    cases[Opcode.GETTABLE] = ["R[a] = R[b][R[c]]"]
    cases[Opcode.SETTABLE] = [
        "if R[b] == nil then error('VM nil table key: proto=' .. proto.name .. ', pc=' .. (pc - 1) .. ', table_type=' .. type(R[a]) .. ', value_type=' .. type(R[c]), 2) end",
        "R[a][R[b]] = R[c]",
    ]
    cases[Opcode.NEWTABLE] = ["R[a] = table_create(b)"]
    cases[Opcode.CLOSURE] = ["R[a] = make_closure(b + c * 512, R, U)"]
    cases[Opcode.ADD] = ["R[a] = R[b] + R[c]"]
    cases[Opcode.SUB] = ["R[a] = R[b] - R[c]"]
    cases[Opcode.MUL] = ["R[a] = R[b] * R[c]"]
    cases[Opcode.DIV] = ["R[a] = R[b] / R[c]"]
    cases[Opcode.EQ] = ["R[a] = (R[b] == R[c])"]
    cases[Opcode.LT] = ["R[a] = (R[b] < R[c])"]
    cases[Opcode.JMP] = ["pc = pc + c"]

    cases[Opcode.CALL] = [
        "local open_results = c >= 256 and c % 512 >= 256",
        "local has_varargs = c >= 512",
        "c = c % 256",
        "local has_multret = b >= 256",
        "if has_multret then b = b - 256 end",
        "local nargs = b - 1",
        "local fn = R[a]",
        "if fn == nil then error('VM nil call: proto=' .. proto.name .. ', proto_id=' .. proto_idx .. ', pc=' .. (pc - 1) .. ', register=' .. a .. ', nargs=' .. nargs, 2) end",
        "local results",
        "if has_varargs or has_multret then",
        "  local tail",
        "  if has_varargs then",
        "    tail = V",
        "  else",
        "    local packed = R[a+nargs+1]",
        "    if type(packed) ~= 'table' or packed.tag ~= MULTRET_TAG then error('VM invalid multret argument: proto=' .. proto.name .. ', proto_id=' .. proto_idx .. ', pc=' .. (pc - 1) .. ', register=' .. (a+nargs+1) .. ', value_type=' .. type(packed) .. ', tag=' .. tostring(type(packed) == 'table' and packed.tag or nil), 2) end",
        "    tail = packed.values",
        "  end",
        "  local total_args = nargs + tail.n",
        "  local args = table_create(total_args)",
        "  for i = 1, nargs do args[i] = R[a+i] end",
        "  for i = 1, tail.n do args[nargs+i] = tail[i] end",
        "  results = table_pack(fn(table_unpack(args, 1, total_args)))",
        "elseif nargs == 0 then results = table_pack(fn())",
        "elseif nargs == 1 then results = table_pack(fn(R[a+1]))",
        "elseif nargs == 2 then results = table_pack(fn(R[a+1], R[a+2]))",
        "elseif nargs == 3 then results = table_pack(fn(R[a+1], R[a+2], R[a+3]))",
        "elseif nargs == 4 then results = table_pack(fn(R[a+1], R[a+2], R[a+3], R[a+4]))",
        "else",
        "  local args = table_create(nargs)",
        "  for i = 1, nargs do args[i] = R[a+i] end",
        "  results = table_pack(fn(table_unpack(args, 1, nargs)))",
        "end",
        "if open_results then",
        "  R[a] = {tag = MULTRET_TAG, values = results, n = results.n}",
        "else",
        "  local result_count = c == 0 and 255 or c - 1",
        "  for i = 1, result_count do R[a + i - 1] = results[i] end",
        "end",
    ]

    cases[Opcode.ADDI] = ["R[a] = R[b] + c"]
    cases[Opcode.SUBI] = ["R[a] = R[b] - c"]
    cases[Opcode.MULI] = ["R[a] = R[b] * c"]
    cases[Opcode.GETTABLE_K] = [
        "R[a] = R[b % 256][K[c + math.floor(b / 256) * 1024]]"
    ]
    cases[Opcode.SETTABLE_K] = [
        "R[a][K[b + math.floor(c / 256) * 512]] = R[c % 256]"
    ]
    cases[Opcode.TEST] = [
        "local truthy = R[a] ~= nil and R[a] ~= false",
        "if truthy == (b ~= 0) then pc = pc + c end",
    ]
    cases[Opcode.RETURN] = [
        "local nret = b - 1",
        "if nret == 0 then",
        "  return",
        "elseif nret == 1 then",
        "  return R[a]",
        "else",
        "  return table_unpack(R, a, a + nret - 1)",
        "end",
    ]
    cases[Opcode.EQ_JMP] = ["if R[a] == R[b] then pc = pc + c end"]
    cases[Opcode.LT_JMP] = ["if R[a] < R[b] then pc = pc + c end"]
    cases[Opcode.LE_JMP] = ["if R[a] <= R[b] then pc = pc + c end"]
    cases[Opcode.NEQ_JMP] = ["if R[a] ~= R[b] then pc = pc + c end"]

    cases[Opcode.CALL_GLOBAL] = [
        "local constant_high = 0",
        "if c >= 512 then constant_high = 1; c = c - 512 end",
        "local nargs = c - 1",
        "local fn = GLOBALS[K[b + constant_high * 512]]",
        "local result",
        "if nargs == 0 then result = fn()",
        "elseif nargs == 1 then result = fn(R[a+1])",
        "elseif nargs == 2 then result = fn(R[a+1], R[a+2])",
        "elseif nargs == 3 then result = fn(R[a+1], R[a+2], R[a+3])",
        "elseif nargs == 4 then result = fn(R[a+1], R[a+2], R[a+3], R[a+4])",
        "else",
        "  local args = table_create(nargs)",
        "  for i = 1, nargs do args[i] = R[a+i] end",
        "  result = fn(table_unpack(args, 1, nargs))",
        "end",
        "R[a] = result",
    ]
    cases[Opcode.CALL_METHOD] = [
        "local constant_high = 0",
        "if c >= 512 then constant_high = 1; c = c - 512 end",
        "local nargs = c - 1",
        "local self_obj = R[a]",
        "local fn = self_obj[K[b + constant_high * 512]]",
        "local result",
        "if nargs == 0 then result = fn(self_obj)",
        "elseif nargs == 1 then result = fn(self_obj, R[a+1])",
        "elseif nargs == 2 then result = fn(self_obj, R[a+1], R[a+2])",
        "elseif nargs == 3 then result = fn(self_obj, R[a+1], R[a+2], R[a+3])",
        "else",
        "  local args = table_create(nargs + 1)",
        "  args[1] = self_obj",
        "  for i = 1, nargs do args[i+1] = R[a+i] end",
        "  result = fn(table_unpack(args, 1, nargs + 1))",
        "end",
        "R[a] = result",
    ]
    cases[Opcode.FORPREP] = [
        "local idx, limit, step = R[a], R[a+1], R[a+2]",
        "local cont",
        "if step > 0 then cont = idx <= limit else cont = idx >= limit end",
        "if not cont then pc = pc + c end",
    ]
    cases[Opcode.FORLOOP] = [
        "R[a] = R[a] + R[a+2]",
        "local idx, limit, step = R[a], R[a+1], R[a+2]",
        "local cont",
        "if step > 0 then cont = idx <= limit else cont = idx >= limit end",
        "if cont then pc = pc + c end",
    ]
    cases[Opcode.CONCAT] = ["R[a] = table_concat_range(R, b, c)"]

    assert set(cases.keys()) == set(Opcode), "Thieu xu ly cho 1 so opcode"
    return cases


# ---------------------------------------------------------------------------
# Render toan bo file runtime
# ---------------------------------------------------------------------------

_RUNTIME_HEADER = '''\
-- ============================================================================
-- LuauObfuscator VM Runtime (tu dong sinh boi runtime_template.py)
-- KHONG chinh sua tay file nay - day la OUTPUT cua pipeline bien dich.
-- ============================================================================

local table_create = table.create or function(n) return {} end
local table_unpack = table.unpack or unpack
local table_pack = table.pack or function(...) return {n = select("#", ...), ...} end
local MULTRET_TAG = {}
local SCRIPT_GLOBALS = (getfenv and getfenv(0)) or _G
local function get_executor_environment()
  local provider = SCRIPT_GLOBALS.getgenv or _G.getgenv
  if type(provider) ~= "function" then return nil end
  local ok, environment = pcall(provider)
  if ok and type(environment) == "table" then return environment end
  return nil
end
local EXECUTOR_GLOBALS = get_executor_environment()
local GLOBALS = SCRIPT_GLOBALS
if EXECUTOR_GLOBALS and EXECUTOR_GLOBALS ~= SCRIPT_GLOBALS then
  GLOBALS = setmetatable({}, {
    __index = function(_, key)
      local value = EXECUTOR_GLOBALS[key]
      if value ~= nil then return value end
      return SCRIPT_GLOBALS[key]
    end,
    __newindex = function(_, key, value)
      if EXECUTOR_GLOBALS[key] ~= nil then
        EXECUTOR_GLOBALS[key] = value
      else
        SCRIPT_GLOBALS[key] = value
      end
    end,
  })
end
local VM_HELPERS = {
  [0] = function(a, b) return a - math.floor(a / b) * b end,
  [1] = function(a, b) return a ^ b end,
  [2] = function(a, b) return math.floor(a / b) end,
  [3] = function(value) return #value end,
  [4] = function(value, state, control)
    if type(value) == "function" then return value, state, control end
    local meta = getmetatable(value)
    local iter = type(meta) == "table" and meta.__iter
    if iter then return iter(value) end
    if type(value) == "table" then return next, value, nil end
    error("VM generic-for value is not iterable", 2)
  end,
  [5] = function(value) return -value end,
}

-- Noi 1 day thanh ghi R[from..to] thanh 1 chuoi (dung cho opcode CONCAT).
-- Tach rieng thanh ham de dispatcher gon hon; khong tao table trung gian.
local function table_concat_range(R, from, to)
  if from == to then
    return R[from]
  end
  local s = R[from]
  for i = from + 1, to do
    s = s .. R[i]
  end
  return s
end
'''


def _render_c_signed_table() -> str:
    lines = ["local C_SIGNED = {"]
    row = []
    for op in Opcode:
        signed = OPCODE_INFO[op].c_kind in (OperandKind.IMM, OperandKind.JMP)
        row.append(f"[{int(op)}]={'true' if signed else 'false'}")
    # Chia thanh vai dong cho de doc (khong bat buoc, chi de debug thuan tien)
    for i in range(0, len(row), 8):
        lines.append("  " + ", ".join(row[i:i + 8]) + ",")
    lines.append("}")
    return "\n".join(lines)


def _render_protos(protos: List[FunctionProto]) -> str:
    lines = [f"local PROTO_COUNT = {len(protos)}", "local PROTOS = {}"]
    for idx, proto in enumerate(protos):
        packed = ", ".join(str(w) for w in proto.code) if proto.code else ""
        const_parts = []
        for i, c in enumerate(proto.constants):
            const_parts.append(f"[{i}]={_render_constant(c)}")
        k_literal = "{" + ", ".join(const_parts) + "}"
        upvalue_parts = []
        for i, (kind, index) in enumerate(proto.upvalues):
            upvalue_parts.append(
                f"[{i}]={{{_lua_string_literal(kind)}, {index}}}"
            )
        upvalues_literal = "{" + ", ".join(upvalue_parts) + "}"
        name_literal = _lua_string_literal(proto.name)
        lines.append(
            f"PROTOS[{idx}] = {{ packed = {{{packed}}}, k = {k_literal}, "
            f"upvalues = {upvalues_literal}, num_upvalues = {len(proto.upvalues)}, "
            f"num_params = {proto.num_params}, max_registers = {proto.max_registers}, "
            f"name = {name_literal} }}"
        )
    return "\n".join(lines)


_UNPACK_PROTO_FN = '''\
-- ===== "Bo giai nen buffer": giai ma lenh 32-bit -> 4 mang song song =====
-- Dinh dang bit (PHAI khop CHINH XAC voi src/vm/opcode.py):
--   word = (op << 27) | (a << 19) | (b << 10) | (c << 0)
--   OP: 5 bit (0..31)  A: 8 bit (0..255)  B: 9 bit (0..511)  C: 10 bit (0..1023)
-- Dung so hoc thuan tuy (%, //) thay vi bit32/toan tu bitwise, de chay
-- duoc tren MOI phien ban Lua (5.1..5.4) lan Luau ma khong can thu vien
-- rieng - buoc nay chi chay 1 LAN luc cold start nen khong anh huong
-- hieu nang vong lap nong.
local function unpack_proto(proto)
  local packed = proto.packed
  local n = #packed
  local OP, A, B, C = table_create(n), table_create(n), table_create(n), table_create(n)
  for i = 1, n do
    local word = packed[i]
    local c = word % 1024
    local rest1 = (word - c) // 1024
    local b = rest1 % 512
    local rest2 = (rest1 - b) // 512
    local a = rest2 % 256
    local op = (rest2 - a) // 256
    if C_SIGNED[op] then
      c = c - 512
    end
    local idx = i - 1
    OP[idx] = op
    A[idx] = a
    B[idx] = b
    C[idx] = c
  end
  proto.op, proto.a, proto.b, proto.c = OP, A, B, C
  proto.packed = nil
end

for i = 0, PROTO_COUNT - 1 do
  unpack_proto(PROTOS[i])
end
'''


def _render_dispatcher(case_lines: Dict[int, List[str]]) -> str:
    risc = list(range(0, 16))
    cisc = list(range(16, 32))
    tree_lines = ["    if op < 16 then"]
    tree_lines += _emit_dispatch_tree(risc, case_lines, depth=3)
    tree_lines.append("    else")
    tree_lines += _emit_dispatch_tree(cisc, case_lines, depth=3)
    tree_lines.append("    end")

    body = "\n".join(tree_lines)

    return f'''\
-- ===== Tao 1 closure (gia tri ham) tu 1 proto - CLOSURE opcode dung ham nay =====
local function make_closure(proto_idx, parent_registers, parent_upvalues)
  local proto = PROTOS[proto_idx]
  local OP, A, B, C, K = proto.op, proto.a, proto.b, proto.c, proto.k
  local num_params = proto.num_params
  local max_registers = proto.max_registers
  local U = table_create(proto.num_upvalues)
  for i = 0, proto.num_upvalues - 1 do
    local descriptor = proto.upvalues[i]
    if descriptor[1] == "local" then
      U[i] = parent_registers[descriptor[2]]
    else
      U[i] = parent_upvalues[descriptor[2]]
    end
  end

  return function(...)
    local R = table_create(max_registers)
    for i = 0, num_params - 1 do
      R[i] = select(i + 1, ...)
    end
    local V = table_pack(select(num_params + 1, ...))

    local pc = 0
    while true do
      local op = OP[pc]
      local a, b, c = A[pc], B[pc], C[pc]
      pc = pc + 1

{body}
    end
  end
end
'''


def render_runtime(main_proto_idx: int, protos: List[FunctionProto]) -> str:
    """Sinh 1 file Lua HOAN CHINH chua VM runtime + bytecode da nhung,
    TU CHAY (goi proto 'main' va `return` ket qua cua no, giong hanh vi
    1 Lua ModuleScript/chunk thong thuong khi duoc require/dofile).

    Tham so:
        main_proto_idx: chi so proto goc trong `protos` (tra ve boi
                         compiler.compile_chunk()).
        protos:          danh sach FunctionProto (tra ve boi compiler.compile_chunk()).

    Tra ve: 1 chuoi Lua source hoan chinh, co the ghi thang ra file .lua.
    """
    if not (0 <= main_proto_idx < len(protos)):
        raise ValueError(f"main_proto_idx={main_proto_idx} ngoai pham vi (0..{len(protos)-1})")

    case_lines = _build_case_lines()

    parts = [
        _RUNTIME_HEADER,
        _render_c_signed_table(),
        "",
        _render_protos(protos),
        "",
        _UNPACK_PROTO_FN,
        "",
        _render_dispatcher(case_lines),
        "",
        "-- ===== Diem vao: chay proto chinh, tra ve ket qua (neu co) =====",
        f"local __main = make_closure({main_proto_idx}, nil, nil)",
        "return __main()",
        "",
    ]
    return "\n".join(parts)


def compile_and_render(source: str) -> str:
    """Tien ich: parse + compile + render trong 1 buoc, dung cho test
    nhanh / demo end-to-end. (Import noi bo de tranh phu thuoc vong giua
    cac module khi `src.parser` import `src.vm` hoac nguoc lai trong
    tuong lai.)
    """
    from ..parser import parse
    from .compiler import compile_chunk

    ast = parse(source)
    main_idx, protos = compile_chunk(ast)
    return render_runtime(main_idx, protos)


__all__ = ["render_runtime", "compile_and_render"]
