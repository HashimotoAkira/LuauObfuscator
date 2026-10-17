# -*- coding: utf-8 -*-
"""
src.basic.comment
==================
Xoa comment o muc SOURCE TEXT (truoc khi dua vao Lexer/Parser).

Luu y quan trong: `src.parser.Lexer` da tu dong BO QUA moi comment khi
tokenize (xem `Lexer._skip_whitespace_and_comments`), nen AST sinh ra tu
`parse()` von di KHONG BAO GIO chua comment - ve mat pipeline chinh
(`Parser -> Basic -> CFF -> VM`), tang Basic khong can lam gi them o
buoc nay.

Module nay ton tai de phuc vu 2 truong hop con lai:
    1. Nguoi dung muon xem truoc / debug 1 file .lua da duoc "don dep"
       comment ma KHONG can chay toan bo lexer/parser (vd check nhanh
       code nhap vao truoc khi build).
    2. Lam buoc tien xu ly doc lap, an toan tuyet doi voi noi dung string
       literal (ca ngan `"..."`/`'...'` lan dai `[[...]]`/`[==[...]==]`)
       - vd 1 chuoi chua `--` o giua van phai duoc GIU NGUYEN, khong bi
         hieu nham la mo dau comment.

Quy tac xoa (bam sat `Lexer._read_comment` / `_try_read_long_bracket_level`):
    - `-- ...` toi cuoi dong       -> xoa, GIU LAI ky tu xuong dong.
    - `--[[ ... ]]` / `--[=[ ... ]=]` (nhieu dong) -> xoa toan bo, nhung
      THAY THE phan noi dung da xoa bang cac ky tu `\n` tuong ung de
      SO DONG (line number) cua phan code con lai KHONG bi lech - quan
      trong de bao loi lexer/parser phia sau (neu chay tiep) van chi
      dung dong.
    - Dong directive `--!strict` KHONG bi xem la comment thuong (giu
      nguyen, vi no mang y nghia rieng cho Luau/parser).
    - Noi dung trong chuoi ngan/dai KHONG bao gio bi dung cham toi.
"""

from __future__ import annotations


def strip_comments(source: str) -> str:
    """Tra ve ban sao cua `source` voi moi comment (ngan + dai) da bi xoa,
    GIU NGUYEN so dong va moi noi dung ben trong chuoi/long-string.

    Dong directive (`--!strict`, `--!nonstrict`, `--!nolint`, ...) duoc
    GIU NGUYEN vi no khong phai comment thuan tuy.
    """
    out = []
    i = 0
    n = len(source)

    while i < n:
        ch = source[i]

        # --- Chuoi ngan: bo qua nguyen ven, xu ly escape don gian -------
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            i += 1
            while i < n:
                c = source[i]
                out.append(c)
                i += 1
                if c == "\\" and i < n:
                    out.append(source[i])
                    i += 1
                    continue
                if c == quote or c == "\n":
                    break
            continue

        # --- Chuoi dai HOAC comment dai: [[ ... ]] / [=[ ... ]=] --------
        if ch == "[":
            level = _match_long_bracket_open(source, i)
            if level is not None:
                # Day la mo dau 1 long-string THUAN (khong phai comment,
                # vi khong co "--" truoc), giu nguyen toan bo noi dung.
                start = i
                i = _skip_long_bracket(source, i, level)
                out.append(source[start:i])
                continue

        # --- Comment -----------------------------------------------------
        if ch == "-" and i + 1 < n and source[i + 1] == "-":
            # Dong directive '--!...' -> giu nguyen, khong xoa.
            if i + 2 < n and source[i + 2] == "!":
                start = i
                while i < n and source[i] != "\n":
                    i += 1
                out.append(source[start:i])
                continue

            # Comment dai: --[[ ... ]] hoac --[=[ ... ]=]...
            level = _match_long_bracket_open(source, i + 2)
            if level is not None:
                body_start = i + 2
                end = _skip_long_bracket(source, body_start, level)
                removed = source[i:end]
                out.append("\n" * removed.count("\n"))
                i = end
                continue

            # Comment 1 dong binh thuong: xoa toi cuoi dong, giu lai '\n'.
            while i < n and source[i] != "\n":
                i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def _match_long_bracket_open(source: str, pos: int):
    """Neu `source[pos:]` bat dau bang `[=*[` thi tra ve so dau '=' (level),
    nguoc lai tra ve None. KHONG di chuyen con tro cua caller (ham nay
    thuan tuy "nhin truoc")."""
    n = len(source)
    if pos >= n or source[pos] != "[":
        return None
    j = pos + 1
    level = 0
    while j < n and source[j] == "=":
        level += 1
        j += 1
    if j < n and source[j] == "[":
        return level
    return None


def _skip_long_bracket(source: str, pos: int, level: int) -> int:
    """`pos` dang dung ngay tai dau `[` mo dau cua `[=*[` (level da biet).
    Tra ve vi tri NGAY SAU dau `]=*]` dong tuong ung (hoac cuoi source
    neu khong tim thay, giong hanh vi "best-effort" cua Lexer that)."""
    n = len(source)
    # Bo qua '[=*['
    i = pos + 1 + level + 1
    # Luat Lua: neu ky tu ngay sau dau mo la xuong dong, bo qua no (khong
    # tinh vao noi dung) - khong anh huong toi line-count vi ta khong dem
    # noi dung, chi can tim dung diem dong.
    if i < n and source[i] == "\n":
        i += 1
    closing = "]" + ("=" * level) + "]"
    idx = source.find(closing, i)
    if idx == -1:
        return n
    return idx + len(closing)


__all__ = ["strip_comments"]