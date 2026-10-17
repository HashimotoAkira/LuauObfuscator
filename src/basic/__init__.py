# -*- coding: utf-8 -*-
"""
src.basic
=========
Tang 1 cua pipeline obfuscate: bien doi AST "tho" (tu `src.parser.parse`)
thanh AST da duoc lam sach + lam roi o muc co ban, SAN SANG cho tang
`advanced/` (CFF) va `vm/` xu ly tiep.

Thu tu goi KHUYEN NGHI (quan trong - cac buoc sau phu thuoc gia dinh
buoc truoc da chay xong):

    from src.parser import parse
    from src.basic import strip_type_annotations, mangle, pool_strings

    ast = parse(source)
    ast = strip_type_annotations(ast)      # 1. lot annotation/type-cast/alias
    result_names = mangle(ast)             # 2. doi ten bien + cache global
    result_strings = pool_strings(ast)     # 3. gom + ma hoa chuoi

    # `result_strings.block` la AST cuoi cung (cung doi tuong voi `ast`).
    # `result_strings.to_lua_snippet()` la doan Lua can chen o DAU output
    # (dinh nghia blob chuoi + ham giai ma) truoc khi emitter (tang
    # advanced/) in AST tro lai thanh source.

`comment.strip_comments()` la tien ich DOC LAP (hoat dong o muc source
text, khong phai AST) - xem docstring trong `comment.py` de biet ly do.
"""

from .annotations import strip_type_annotations
from .comment import strip_comments
from .naming import mangle, ManglingResult, KNOWN_GLOBALS
from .strings import pool_strings, render_decoder_lua, StringPoolResult, DEFAULT_DECODER_NAME

__all__ = [
    "strip_type_annotations",
    "strip_comments",
    "mangle",
    "ManglingResult",
    "KNOWN_GLOBALS",
    "pool_strings",
    "render_decoder_lua",
    "StringPoolResult",
    "DEFAULT_DECODER_NAME",
]