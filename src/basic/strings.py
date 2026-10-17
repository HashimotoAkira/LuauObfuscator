# -*- coding: utf-8 -*-
"""
src.basic.strings
===================
Gom TOAN BO chuoi literal trong AST thanh 1 blob byte duy nhat, ma hoa
XOR theo khoi 32-bit (word), va thay moi `StringExpr` bang 1 loi goi ham
giai ma `decoder_name(offset, length)`.

Pipeline:
    1. Duyet AST (read-only), thu thap moi `StringExpr.value` khac nhau
       (dedupe: chuoi giong het nhau dung CHUNG 1 vi tri trong blob).
    2. Noi tat ca thanh 1 blob byte (UTF-8), pad them so 0 cho du boi so
       4 (yeu cau cua XOR theo khoi 32-bit).
    3. Ma hoa: XOR tung byte voi byte tuong ung (theo vi tri mod 4) cua
       1 khoa 32-bit ngau nhien.
    4. Thay the moi `StringExpr` trong AST bang `CallExpr` goi ham giai
       ma voi (offset, length) tuong ung.
    5. Sinh kem 1 doan Lua (`render_decoder_lua`) dinh nghia blob + ham
       giai ma, de chen vao DAU file output.

Ham giai ma Lua dung THUAT TOAN XOR-8-BIT THU CONG (vong lap tung bit),
KHONG phu thuoc thu vien `bit32` - dam bao chay duoc tren ca Lua 5.1
chuan lan Luau, tranh truong hop runtime nao do khong co san `bit32`.
"""

from __future__ import annotations

import random
import struct
from dataclasses import dataclass
from typing import AbstractSet, Dict, List, Optional, Tuple

from ..parser.nodes import Block, CallExpr, NameExpr, NumberExpr, StringExpr
from ..parser.traversal import transform_tree, walk_tree

DEFAULT_DECODER_NAME = "_S"


@dataclass
class StringPoolResult:
    block: Block
    blob: bytes            # da ma hoa XOR, do dai la boi so cua 4
    key: int                # khoa XOR 32-bit (0 .. 2^32-1)
    decoder_name: str
    count: int               # so chuoi KHAC NHAU da gom vao blob
    total_bytes: int         # do dai blob SAU khi pad (truoc ma hoa khong doi)

    def to_lua_snippet(self) -> str:
        return render_decoder_lua(self)


def pool_strings(
    block: Block,
    *,
    min_len: int = 0,
    max_len: Optional[int] = None,
    exclude_values: AbstractSet[str] = frozenset(),
    decoder_name: str = DEFAULT_DECODER_NAME,
    key: Optional[int] = None,
) -> StringPoolResult:
    """Gom `StringExpr` co do dai trong [`min_len`, `max_len`] trong `block` vao 1 blob
    ma hoa XOR 32-bit duy nhat; thay node tuong ung bang loi goi ham giai
    ma. Mutate AST in-place, tra ve `StringPoolResult` de sinh runtime.
    """
    if min_len < 0:
        raise ValueError("min_len phai >= 0")
    if max_len is not None and max_len < min_len:
        raise ValueError("max_len phai >= min_len")
    if key is None:
        key = random.getrandbits(32)
    key &= 0xFFFFFFFF

    offsets: Dict[str, Tuple[int, int]] = {}
    order: List[str] = []

    def collect(node) -> None:
        if (
            isinstance(node, StringExpr)
            and len(node.value) >= min_len
            and (max_len is None or len(node.value) <= max_len)
            and node.value not in exclude_values
        ):
            if node.value not in offsets:
                order.append(node.value)
                offsets[node.value] = (0, 0)  # placeholder, dien o buoc sau

    walk_tree(block, collect)

    blob_parts: List[bytes] = []
    cursor = 0
    for s in order:
        encoded = s.encode("utf-8")
        offsets[s] = (cursor, len(encoded))
        blob_parts.append(encoded)
        cursor += len(encoded)

    raw_blob = bytearray(b"".join(blob_parts))
    pad = (-len(raw_blob)) % 4
    raw_blob.extend(b"\x00" * pad)

    key_bytes = struct.pack(">I", key)
    encrypted = bytearray(raw_blob)
    for i in range(len(encrypted)):
        encrypted[i] ^= key_bytes[i % 4]

    def replace(node) -> Tuple[bool, object]:
        if isinstance(node, StringExpr) and node.value in offsets:
            off, length = offsets[node.value]
            if length == 0 and len(node.value) == 0:
                # Chuoi rong: van the hien dung (goi decode voi length=0
                # tra ve ""), khong can dac cach.
                pass
            call = CallExpr(
                func=NameExpr(name=decoder_name, line=node.line, col=node.col),
                args=[
                    NumberExpr(raw=str(off), value=off, line=node.line, col=node.col),
                    NumberExpr(raw=str(length), value=length, line=node.line, col=node.col),
                ],
                line=node.line,
                col=node.col,
            )
            return True, call
        return False, None

    new_block = transform_tree(block, replace)

    return StringPoolResult(
        block=new_block,
        blob=bytes(encrypted),
        key=key,
        decoder_name=decoder_name,
        count=len(order),
        total_bytes=len(raw_blob),
    )


def _lua_byte_string_literal(data: bytes) -> str:
    """Bieu dien `data` thanh 1 literal chuoi Lua an toan tuyet doi: moi
    byte duoc viet duoi dang `\\ddd` (3 chu so thap phan), khong phu
    thuoc encoding/ky tu dac biet nao cua terminal/editor."""
    return '"' + "".join(f"\\{b:03d}" for b in data) + '"'


def render_decoder_lua(result: StringPoolResult) -> str:
    """Sinh doan Lua dinh nghia blob + khoa + ham giai ma
    `decoder_name(offset, length) -> string`. Doan nay can duoc chen vao
    DAU file output, TRUOC bat ky cho nao string moi duoc goi den."""
    blob_literal = _lua_byte_string_literal(result.blob)
    name = result.decoder_name
    return (
        f"local _SBLOB = {blob_literal}\n"
        f"local _SKEY = {result.key}\n"
        f"local _SKB = {{\n"
        f"    math.floor(_SKEY / 16777216) % 256,\n"
        f"    math.floor(_SKEY / 65536) % 256,\n"
        f"    math.floor(_SKEY / 256) % 256,\n"
        f"    _SKEY % 256,\n"
        f"}}\n"
        f"local _SBXOR\n"
        f"if bit32 and bit32.bxor then\n"
        f"    _SBXOR = bit32.bxor\n"
        f"else\n"
        f"    _SBXOR = function(a, b)\n"
        f"        local r, bv = 0, 1\n"
        f"        for _ = 1, 8 do\n"
        f"            local ab, bb = a % 2, b % 2\n"
        f"            if ab ~= bb then r = r + bv end\n"
        f"            a, b = (a - ab) / 2, (b - bb) / 2\n"
        f"            bv = bv * 2\n"
        f"        end\n"
        f"        return r\n"
        f"    end\n"
        f"end\n"
        f"local _SCACHE = {{}}\n"
        f"local function {name}(o, l)\n"
        f"    local cached = _SCACHE[o]\n"
        f"    if cached and cached[l] ~= nil then return cached[l] end\n"
        f"    local out = {{}}\n"
        f"    for i = 0, l - 1 do\n"
        f"        local p = o + i\n"
        f"        out[i + 1] = string.char(_SBXOR(string.byte(_SBLOB, p + 1), _SKB[(p % 4) + 1]))\n"
        f"    end\n"
        f"    local value = table.concat(out)\n"
        f"    if not cached then cached = {{}}; _SCACHE[o] = cached end\n"
        f"    cached[l] = value\n"
        f"    return value\n"
        f"end\n"
    )


__all__ = ["pool_strings", "render_decoder_lua", "StringPoolResult", "DEFAULT_DECODER_NAME"]