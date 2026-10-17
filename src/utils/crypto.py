# -*- coding: utf-8 -*-
r"""
src/utils/crypto.py
====================
Tien ich "crypto" (thuc ra la obfuscation-grade, KHONG phai crypto bao mat
that su — muc tieu la gay kho cho nguoi/AI doc nguoc chu khong phai chong
tham so hoc) dung chung cho cac tang basic/strings.py va vm/environment.py
sau nay:

  - Sinh key ngau nhien (32-bit) bang `secrets` (CSPRNG cua he dieu hanh,
    tot hon `random` thuong de key khong de doan/seed lai).
  - Ma hoa/giai ma XOR theo khoi N-byte (word) tuan hoan key — XOR tu
    nghich dao (encode va decode dung chung 1 ham).
  - Chuyen bytes bat ky (ke ca du lieu da XOR, co the chua byte NUL hay
    byte khong in duoc) thanh 1 chuoi literal Lua AN TOAN 100% bang cach
    escape MOI byte dang thap phan `\ddd` — khong bao gio dung sai dau
    nhay/backslash du du lieu la gi.
  - Sinh suffix ngau nhien ngan de dat ten cho cac local/ham injected
    (vd `_OBFa93f1_BLOB`) sao cho gan nhu khong the trung voi ten nguoi
    dung da dat trong source goc.

Khong co dependency ngoai chuan (stdlib only) de giu Nuitka build gon nhe.
"""

from __future__ import annotations

import secrets
from typing import Union

# Bang chu cai dung de sinh suffix ten bien (chi chu thuong + so, khong
# dau gach duoi o dau vi se tu ghep voi prefix "_OBF" o noi goi).
_SUFFIX_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"


def random_u32() -> int:
    """Sinh 1 so nguyen ngau nhien 32-bit [0, 2**32 - 1] dung lam key XOR.

    Dung `secrets.randbits` (CSPRNG he dieu hanh) thay vi `random` module
    thuong: khong the bi du doan tu seed/thoi gian, phu hop lam key dung
    de "khoa" chuoi trong output cho du muc tieu chi la gay kho doc chu
    khong phai bao mat du lieu nhay cam.
    """
    return secrets.randbits(32)


def random_key(bits: int = 32) -> int:
    """Sinh key ngau nhien `bits`-bit bat ky (mac dinh 32-bit).

    `bits` phai la boi so cua 8 (de khop voi don vi byte khi dung lam
    key XOR theo khoi N-byte trong `xor_cipher`).
    """
    if bits <= 0 or bits % 8 != 0:
        raise ValueError("bits phai la so nguyen duong va la boi so cua 8")
    return secrets.randbits(bits)


def xor_cipher(data: Union[bytes, bytearray], key: int, word_size: int = 4) -> bytes:
    """Ma hoa/giai ma XOR theo khoi `word_size` byte, key tuan hoan.

    Voi byte thu i (0-indexed) cua `data`:
        key_byte = (key >> (8 * (i % word_size))) & 0xFF
        out[i]   = data[i] XOR key_byte

    XOR la phep tu nghich dao (A xor K xor K == A) nen ham nay dung chung
    cho CA ma hoa lan giai ma — chi can goi lai voi cung `key` va
    `word_size` se tra ve du lieu goc. Day cung chinh la cong thuc ma
    runtime Lua (bit32.bxor + bit32.band/rshift) phai tai hien y het khi
    giai ma o phia Luau, nen giu cong thuc don gian/ro rang o day de de
    doi chieu khi viet phan VM/runtime sau nay.

    `word_size` mac dinh 4 (khop "ma hoa XOR theo khoi 32-bit (word)"
    trong dac ta), nhung ham nay nhan word_size bat ky >= 1 de linh hoat
    test/doi sau nay.
    """
    if word_size < 1:
        raise ValueError("word_size phai >= 1")
    key_bytes = [(key >> (8 * b)) & 0xFF for b in range(word_size)]
    out = bytearray(len(data))
    for i, b in enumerate(data):
        out[i] = b ^ key_bytes[i % word_size]
    return bytes(out)


# Giu alias ro nghia hon o noi goi (encode/decode doc code de hieu hon,
# du ben trong chi la cung 1 ham xor_cipher).
xor_encode = xor_cipher
xor_decode = xor_cipher


def lua_byte_string(data: Union[bytes, bytearray]) -> str:
    r"""Chuyen bytes bat ky thanh 1 chuoi literal Lua AN TOAN tuyet doi.

    Escape TUNG byte dang thap phan `\ddd` (vd byte 0x41 -> "\65"),
    boc trong dau nhay kep. Cach nay hoat dong dung voi MOI chuoi byte
    (ke ca byte NUL \0, byte khong in duoc, byte >= 128 sau khi XOR),
    vi khong dua vao viec "escape dung dau nhay/backslash" theo tung
    truong hop — moi byte deu duoc bieu dien tuong minh, khong con
    truong hop dac biet nao co the lam vo cu phap chuoi Lua dau ra.

    Vi du: lua_byte_string(b"A\n") -> '"\65\10"'
    """
    parts = [f"\\{b}" for b in data]
    return '"' + "".join(parts) + '"'


def random_identifier_suffix(length: int = 6) -> str:
    """Sinh chuoi ngau nhien `length` ky tu (chu thuong + so) dung lam
    hau to cho ten bien/ham duoc "chen" (inject) vao output, vd dung
    truoc khi lam ten day du nhu f"_OBF{suffix}_BLOB" o tang strings.py,
    de gan nhu khong the trung voi bat ky ten nao nguoi dung da dat
    trong source goc (tranh loi scope/shadow ngoai y muon).
    """
    if length <= 0:
        raise ValueError("length phai > 0")
    return "".join(secrets.choice(_SUFFIX_ALPHABET) for _ in range(length))