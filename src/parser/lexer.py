# -*- coding: utf-8 -*-
r"""
lexer.py
========
Bo tach token (tokenizer) cho Lua 5.1 / Luau.

Ho tro:
    - Comment don dong `--...` va da dong `--[[ ... ]]` / `--[==[ ... ]==]`
    - Dong dac biet `--!strict`, `--!nonstrict`, `--!nolint` -> token DIRECTIVE
      (basic/annotations.py se dung token nay de loai bo)
    - Chuoi: 'single', "double" voi day du escape sequence (\n \t \\ \' \"
      \a \b \f \r \v \0 \ddd \xXX \u{XXXX} \z (xoa whitespace tiep theo))
    - Chuoi dai: [[ ... ]], [=[ ... ]=], [==[ ... ]==] ...
    - So: thap phan, so mu (1e10, 1.5e-3), hex (0x1F, 0x1p4), Luau cho phep
      dau gach duoi phan tach chu so (1_000_000) va hau to 'i'? (khong co -
      Luau khong co so phuc, bo qua)
    - Toan tu Luau mo rong: // (floor div), compound assignment += -= *= /=
      //= %= ^= ..=, '::' (type cast), '->' (function type, chi can nhan
      dien token de khong vo cu phap khi skip type), '?' (optional type)
    - Keyword chuan Lua + 'continue' (tien ich rieng cua Luau, duoc coi
      nhu 1 tu khoa "mem" - xem ghi chu trong parser.py)

KHONG lam:
    - Khong resolve scope/bien o day (do la viec cua parser + naming.py)
    - Khong danh gia truoc (fold) hang so, chi tach token tho
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional


# ---------------------------------------------------------------------------
# TokenType
# ---------------------------------------------------------------------------

class TokenType(Enum):
    NAME = auto()
    NUMBER = auto()
    STRING = auto()
    KEYWORD = auto()
    OP = auto()          # toan tu / dau cau: + - * / ( ) { } [ ] , ; . .. ...
    DIRECTIVE = auto()    # dong dang '--!strict' o dau file
    EOF = auto()


KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for",
    "function", "if", "in", "local", "nil", "not", "or", "repeat",
    "return", "then", "true", "until", "while",
    # Tien ich rieng cua Luau (khong phai keyword "cung" trong moi ngu
    # canh nhung lexer gan nhan KEYWORD de parser xu ly thuan tien hon;
    # neu dung lam ten bien thong thuong trong code hop le cua Luau that
    # thi can xu ly rieng - o day uu tien do don gian cho obfuscator).
    "continue",
}

# Cac toan tu nhieu ky tu, sap xep DAI -> NGAN de khop tham lam dung.
# Luu y: "//=" PHAI dung truoc "//" (va "/="), neu khong "//" se bi khop
# nham truoc khi kip thay day la phep gan rut gon floor-division cua Luau.
MULTI_CHAR_OPERATORS = [
    "...", "..=",
    "//=",
    "::", "..", "==", "~=", "<=", ">=", "//", "->",
    "+=", "-=", "*=", "/=", "%=", "^=",
]

SINGLE_CHAR_OPERATORS = set("+-*/%^#<>=(){}[];:,.~?&|")

ESCAPE_MAP = {
    "a": "\a", "b": "\b", "f": "\f", "n": "\n", "r": "\r",
    "t": "\t", "v": "\v", "\\": "\\", '"': '"', "'": "'", "\n": "\n",
}


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"[Lexer] Loi dong {line}, cot {col}: {message}")
        self.message = message
        self.line = line
        self.col = col


@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    col: int
    # Chi dung cho STRING / NUMBER: gia tri da "giai ma" (vd string bo
    # escape, number da parse ra float/int). Voi cac loai khac trung voi
    # `value`.
    literal: Optional[object] = None

    def __repr__(self) -> str:
        v = self.value if len(self.value) <= 25 else self.value[:22] + "..."
        return f"Token({self.type.name}, {v!r}, {self.line}:{self.col})"

    def is_keyword(self, kw: str) -> bool:
        return self.type == TokenType.KEYWORD and self.value == kw

    def is_op(self, op: str) -> bool:
        return self.type == TokenType.OP and self.value == op


class Lexer:
    """Chuyen source code (str) thanh danh sach Token.

    Cach dung:
        lexer = Lexer(source_code)
        tokens = lexer.tokenize()   # List[Token], ket thuc bang token EOF
    """

    def __init__(self, source: str, filename: str = "<chunk>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.length = len(source)

    # -- Helper di chuyen con tro --------------------------------------

    def _peek(self, offset: int = 0) -> str:
        i = self.pos + offset
        return self.source[i] if i < self.length else ""

    def _advance(self) -> str:
        ch = self.source[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def _match(self, s: str) -> bool:
        if self.source.startswith(s, self.pos):
            for _ in s:
                self._advance()
            return True
        return False

    def _error(self, msg: str):
        raise LexError(msg, self.line, self.col)

    # -- Entry point ------------------------------------------------------

    def tokenize(self) -> List[Token]:
        tokens: List[Token] = []
        # Shebang o dong dau tien: '#!/usr/bin/env lua' (hiem nhung an toan
        # neu bo qua giong nhu whitespace).
        if self.source.startswith("#"):
            while self.pos < self.length and self._peek() != "\n":
                self._advance()

        while True:
            self._skip_whitespace_and_comments(tokens)
            if self.pos >= self.length:
                tokens.append(Token(TokenType.EOF, "", self.line, self.col))
                break
            tok = self._read_token()
            if tok is not None:
                tokens.append(tok)
        return tokens

    # -- Whitespace / comment ---------------------------------------------

    def _skip_whitespace_and_comments(self, tokens: List[Token]):
        while self.pos < self.length:
            ch = self._peek()
            if ch in " \t\r\n":
                self._advance()
                continue
            if ch == "-" and self._peek(1) == "-":
                self._read_comment(tokens)
                continue
            break

    def _read_comment(self, tokens: List[Token]):
        start_line, start_col = self.line, self.col
        self._advance()  # '-'
        self._advance()  # '-'

        # Directive dac biet cua Luau: --!strict / --!nonstrict / --!nolint
        if self._peek() == "!":
            bang_pos = self.pos
            self._advance()  # '!'
            text_start = self.pos
            while self.pos < self.length and self._peek() != "\n":
                self._advance()
            directive_text = self.source[text_start:self.pos].strip()
            tokens.append(Token(
                TokenType.DIRECTIVE, "!" + directive_text,
                start_line, start_col,
            ))
            return

        # Long comment --[[ ... ]] hoac --[=[ ... ]=] ...
        if self._peek() == "[":
            level = self._try_read_long_bracket_level()
            if level is not None:
                self._read_long_bracket_body(level)  # bo noi dung, khong giu token
                return

        # Comment 1 dong binh thuong
        while self.pos < self.length and self._peek() != "\n":
            self._advance()

    def _try_read_long_bracket_level(self) -> Optional[int]:
        """Neu vi tri hien tai khop `[=*[` thi tieu thu no va tra ve so
        dau '=' (level). Neu khong khop, KHONG di chuyen con tro, tra None.
        """
        save_pos, save_line, save_col = self.pos, self.line, self.col
        if self._peek() != "[":
            return None
        i = self.pos + 1
        level = 0
        while i < self.length and self.source[i] == "=":
            level += 1
            i += 1
        if i < self.length and self.source[i] == "[":
            # Hop le: tieu thu toi vi tri sau dau '[' thu hai
            while self.pos <= i:
                self._advance()
            return level
        self.pos, self.line, self.col = save_pos, save_line, save_col
        return None

    def _read_long_bracket_body(self, level: int) -> str:
        """Doc noi dung cho toi khi gap `]=*]` khop level, tra ve noi dung
        (khong bao gom dau mo/dong). Gia dinh dau mo da duoc tieu thu.
        """
        # Theo chuan Lua: neu ky tu dau tien sau dau mo la newline, bo qua no
        if self._peek() == "\r":
            self._advance()
        if self._peek() == "\n":
            self._advance()

        start = self.pos
        closing = "]" + ("=" * level) + "]"
        idx = self.source.find(closing, self.pos)
        if idx == -1:
            self._error("Chuoi/comment dai khong duoc dong (thieu ']...]')")
        content = self.source[start:idx]
        # Di chuyen con tro (cap nhat line/col) toi sau phan dong
        while self.pos < idx + len(closing):
            self._advance()
        return content

    # -- Token chinh --------------------------------------------------------

    def _read_token(self) -> Optional[Token]:
        start_line, start_col = self.line, self.col
        ch = self._peek()

        if ch.isalpha() or ch == "_":
            return self._read_name(start_line, start_col)

        if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
            return self._read_number(start_line, start_col)

        if ch in ("'", '"'):
            return self._read_short_string(start_line, start_col)

        if ch == "[" and self._peek(1) in ("[", "="):
            level = self._try_read_long_bracket_level()
            if level is not None:
                content = self._read_long_bracket_body(level)
                return Token(TokenType.STRING, content, start_line, start_col,
                             literal=content)
            # khong phai long-string hop le -> roi xuong xu ly nhu '[' thuong

        return self._read_operator(start_line, start_col)

    # -- Identifier / keyword -------------------------------------------

    def _read_name(self, line: int, col: int) -> Token:
        start = self.pos
        while self.pos < self.length and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        text = self.source[start:self.pos]
        ttype = TokenType.KEYWORD if text in KEYWORDS else TokenType.NAME
        return Token(ttype, text, line, col)

    # -- So -----------------------------------------------------------------

    def _read_number(self, line: int, col: int) -> Token:
        start = self.pos

        if self._peek() == "0" and self._peek(1) in ("x", "X"):
            self._advance()
            self._advance()
            self._consume_hex_digits()
            if self._peek() == ".":
                self._advance()
                self._consume_hex_digits()
            if self._peek() in ("p", "P"):
                self._advance()
                if self._peek() in ("+", "-"):
                    self._advance()
                self._consume_digits()
        elif self._peek() == "0" and self._peek(1) in ("b", "B"):
            # So nhi phan - tien ich rieng cua Luau
            self._advance()
            self._advance()
            while self._peek() in ("0", "1", "_"):
                self._advance()
        else:
            self._consume_digits()
            if self._peek() == ".":
                self._advance()
                self._consume_digits()
            if self._peek() in ("e", "E"):
                self._advance()
                if self._peek() in ("+", "-"):
                    self._advance()
                self._consume_digits()

        raw = self.source[start:self.pos]
        value = self._parse_number_literal(raw)
        return Token(TokenType.NUMBER, raw, line, col, literal=value)

    def _consume_digits(self):
        while self._peek().isdigit() or self._peek() == "_":
            self._advance()

    def _consume_hex_digits(self):
        hexchars = set("0123456789abcdefABCDEF_")
        while self._peek() in hexchars:
            self._advance()

    @staticmethod
    def _parse_number_literal(raw: str):
        cleaned = raw.replace("_", "")
        try:
            if cleaned.lower().startswith("0x"):
                if "." in cleaned or "p" in cleaned.lower():
                    return float.fromhex(cleaned)
                return int(cleaned, 16)
            if cleaned.lower().startswith("0b"):
                return int(cleaned, 2)
            if "." in cleaned or "e" in cleaned.lower():
                return float(cleaned)
            return int(cleaned)
        except ValueError:
            # Khong the parse (truong hop hiem) -> giu nguyen raw, de tang
            # sau xu ly / bao loi ro rang hon neu can.
            return raw

    # -- Chuoi ngan -----------------------------------------------------

    def _read_short_string(self, line: int, col: int) -> Token:
        quote = self._advance()  # ' hoac "
        raw_chars = [quote]
        value_chars: List[str] = []

        while True:
            if self.pos >= self.length:
                self._error("Chuoi khong duoc dong (thieu dau nhay dong)")
            ch = self._peek()
            if ch == "\n":
                self._error("Chuoi khong duoc dong truoc khi xuong dong")
            if ch == quote:
                raw_chars.append(self._advance())
                break
            if ch == "\\":
                raw_chars.append(self._advance())  # '\\'
                esc_raw, esc_val = self._read_escape_sequence()
                raw_chars.append(esc_raw)
                value_chars.append(esc_val)
                continue
            raw_chars.append(ch)
            value_chars.append(ch)
            self._advance()

        raw = "".join(raw_chars)
        value = "".join(value_chars)
        return Token(TokenType.STRING, raw, line, col, literal=value)

    def _read_escape_sequence(self) -> "tuple[str, str]":
        """Doc 1 escape sequence SAU dau '\\' (dau '\\' da duoc tieu thu).
        Tra ve (raw_text_sau_backslash, gia_tri_da_giai_ma).
        """
        ch = self._peek()

        if ch in ESCAPE_MAP:
            self._advance()
            return ch, ESCAPE_MAP[ch]

        if ch.isdigit():
            start = self.pos
            digits = ""
            for _ in range(3):
                if self._peek().isdigit():
                    digits += self._advance()
                else:
                    break
            code = int(digits)
            if code > 255:
                self._error(f"Ma escape thap phan vuot gioi han: \\{digits}")
            return self.source[start:self.pos], chr(code)

        if ch == "x":
            self._advance()
            start = self.pos
            hexdigits = ""
            for _ in range(2):
                c = self._peek()
                if c in "0123456789abcdefABCDEF":
                    hexdigits += self._advance()
                else:
                    break
            if len(hexdigits) != 2:
                self._error("Escape \\x can dung 2 chu so hex")
            return "x" + self.source[start:self.pos], chr(int(hexdigits, 16))

        if ch == "u":
            # \u{XXXX} (Unicode codepoint, Luau ho tro)
            self._advance()
            if self._peek() != "{":
                self._error("Escape \\u can theo sau boi '{'")
            self._advance()
            start = self.pos
            while self._peek() != "}":
                if self.pos >= self.length:
                    self._error("Escape \\u{...} khong duoc dong")
                self._advance()
            hexpart = self.source[start:self.pos]
            self._advance()  # '}'
            return "u{" + hexpart + "}", chr(int(hexpart, 16))

        if ch == "z":
            # \z : bo qua moi whitespace (bao gom xuong dong) ngay sau no
            self._advance()
            start = self.pos
            while self._peek() in " \t\r\n":
                self._advance()
            return "z" + self.source[start:self.pos], ""

        if ch == "\r":
            self._advance()
            if self._peek() == "\n":
                self._advance()
            return "\r\n", "\n"

        self._error(f"Escape sequence khong hop le: \\{ch!r}")
        return "", ""  # khong bao gio toi day, chi de thoa man type-checker

    # -- Toan tu / dau cau -----------------------------------------------

    def _read_operator(self, line: int, col: int) -> Token:
        for op in MULTI_CHAR_OPERATORS:
            if self.source.startswith(op, self.pos):
                for _ in op:
                    self._advance()
                return Token(TokenType.OP, op, line, col)

        ch = self._peek()
        if ch in SINGLE_CHAR_OPERATORS:
            self._advance()
            return Token(TokenType.OP, ch, line, col)

        self._error(f"Ky tu khong hop le: {ch!r} (ma {ord(ch) if ch else -1})")
        return None  # khong bao gio toi day


def tokenize(source: str, filename: str = "<chunk>") -> List[Token]:
    """Ham tien ich: tokenize nhanh 1 chuoi source."""
    return Lexer(source, filename).tokenize()


__all__ = ["TokenType", "Token", "Lexer", "LexError", "tokenize", "KEYWORDS"]