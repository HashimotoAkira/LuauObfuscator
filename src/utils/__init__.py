# -*- coding: utf-8 -*-
"""
src.utils
=========
Tien ich dung chung cho LuauObfuscator:
- crypto.py: Sinh ngau nhien, ma hoa XOR khoi 32-bit, an toan hoa chuoi Lua byte literal.
"""

from .crypto import (
    random_u32,
    random_key,
    xor_cipher,
    xor_encode,
    xor_decode,
    lua_byte_string,
    random_identifier_suffix,
)

__all__ = [
    "random_u32",
    "random_key",
    "xor_cipher",
    "xor_encode",
    "xor_decode",
    "lua_byte_string",
    "random_identifier_suffix",
]

