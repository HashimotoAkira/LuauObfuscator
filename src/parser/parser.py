# -*- coding: utf-8 -*-
"""
parser.py
=========
Parser de quy (recursive descent) + precedence climbing cho bieu thuc,
chuyen danh sach Token (tu lexer.py) thanh AST (dinh nghia trong nodes.py).

Do bao phu:
    - Toan bo cu phap statement/expression chuan cua Lua 5.1.
    - Mo rong cua Luau: compound assignment (+= -= ..=), '//' floor div,
      'continue', type annotation (': Type'), type cast ('expr :: Type'),
      type alias ('type X = ...', 'export type X = ...'), generic function
      ('function f<T>(...)'), attribute bien local ('local x <const> = 1').

Chien luoc voi he thong Type cua Luau:
    Luau co 1 ngon ngu type rat phuc tap (union, intersection, generic,
    function type, table type long nhau, indexed access type...). Vi muc
    tieu cuoi cung la LOT BO toan bo annotation truoc khi obfuscate (xem
    basic/annotations.py), parser nay KHONG dung bo phan tich type day du
    - thay vao do dung `_parse_type_raw()` de "nuot" token theo co che can
    bang ngoac (best-effort) va luu lai duoi dang text tho trong TypeInfo.raw.
    Dieu nay du de: (1) khong lam vo cu phap khi gap type annotation that,
    va (2) cho phep tang sau xoa nguyen annotation neu can.

Loi parse duoc bao dang `ParseError` kem (line, col) de de debug.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from .lexer import Lexer, Token, TokenType, tokenize
from .nodes import (
    Block, Param, FunctionBody, TypeInfo, TableField,
    NilExpr, TrueExpr, FalseExpr, VarargExpr, NumberExpr, StringExpr,
    NameExpr, IndexExpr, IndexBracketExpr, CallExpr, MethodCallExpr,
    FunctionExpr, TableConstructorExpr, BinOpExpr, UnOpExpr, ParenExpr,
    TypeCastExpr, IfExpr, IfExprClause, Expr,
    LocalAssignStmt, AssignStmt, CompoundAssignStmt, CallStmt, DoStmt,
    WhileStmt, RepeatStmt, IfClause, IfStmt, NumericForStmt,
    GenericForStmt, FunctionDeclStmt, LocalFunctionStmt, ReturnStmt,
    BreakStmt, ContinueStmt, GotoStmt, LabelStmt, TypeAliasStmt, Stmt,
)


class ParseError(Exception):
    def __init__(self, message: str, line: int, col: int):
        super().__init__(f"[Parser] Loi dong {line}, cot {col}: {message}")
        self.message = message
        self.line = line
        self.col = col


# ---------------------------------------------------------------------------
# Bang do uu tien toan tu nhi phan (precedence climbing)
# Gia tri cao hon = lien ket chat hon (binding manh hon).
# right_assoc=True nghia la toan tu ket hop phai (vd: a..b..c = a..(b..c))
# ---------------------------------------------------------------------------

_BIN_PRECEDENCE = {
    "or": (1, False),
    "and": (2, False),
    "<": (3, False), ">": (3, False), "<=": (3, False),
    ">=": (3, False), "~=": (3, False), "==": (3, False),
    "..": (4, True),
    "+": (5, False), "-": (5, False),
    "*": (6, False), "/": (6, False), "//": (6, False), "%": (6, False),
    "^": (8, True),
}
_UNARY_PRECEDENCE = 7

# Cac tu khoa lam ket thuc 1 block (khong tieu thu, de caller xu ly)
_BLOCK_END_KEYWORDS = {"end", "else", "elseif", "until"}

# Toan tu gan rut gon (compound assignment) cua Luau -> toan tu goc tuong ung
_COMPOUND_OPS = {
    "+=": "+", "-=": "-", "*=": "*", "/=": "/",
    "//=": "//", "%=": "%", "^=": "^", "..=": "..",
}

# Cac dau mo/dong dung de can bang khi "nuot" type annotation / generic list
_TYPE_OPEN = {"(", "{", "[", "<"}
_TYPE_CLOSE = {")", "}", "]", ">"}
_TYPE_STOP_OPS_AT_DEPTH0 = {"=", ",", ";", "::"}
_TYPE_STOP_KEYWORDS = {"end", "then", "do", "in", "until", "else", "elseif"}

# Luoi an toan them: cac tu khoa chac chan mo dau 1 statement moi, khong
# bao gio hop le ben trong 1 type annotation. Dung de chan truong hop raw
# type scan "an" qua ca statement tiep theo (vd sau 1 `type X = ...` ma
# khong co dau cham dut ro rang).
_STATEMENT_START_KEYWORDS = {
    "local", "function", "if", "while", "for", "repeat",
    "return", "break", "continue",
}


class Parser:
    """Parser de quy cho Lua/Luau.

    Cach dung co ban:
        tokens = tokenize(source)
        ast = Parser(tokens).parse()
    hoac don gian hon, dung ham tien ich `parse(source)` o cuoi file.
    """

    def __init__(self, tokens: List[Token]):
        # Tach rieng cac DIRECTIVE (vd '--!strict') ra khoi dong token chinh
        # vi chung khong tham gia bat ky quy tac ngu phap nao; giu lai de
        # cac tang sau (annotations.py) co the tham khao neu can.
        self.directives: List[Token] = [t for t in tokens if t.type == TokenType.DIRECTIVE]
        self.tokens: List[Token] = [t for t in tokens if t.type != TokenType.DIRECTIVE]
        if not self.tokens or self.tokens[-1].type != TokenType.EOF:
            last = tokens[-1] if tokens else None
            eof_line = last.line if last else 1
            eof_col = last.col if last else 1
            self.tokens.append(Token(TokenType.EOF, "", eof_line, eof_col))
        self.pos = 0

    # ----------------------------------------------------------------
    # Tien ich di chuyen / kiem tra token
    # ----------------------------------------------------------------

    def _peek(self, offset: int = 0) -> Token:
        idx = self.pos + offset
        if idx >= len(self.tokens):
            return self.tokens[-1]  # EOF
        return self.tokens[idx]

    def _advance(self) -> Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def _check(self, ttype: TokenType, value: Optional[str] = None) -> bool:
        tok = self._peek()
        if tok.type != ttype:
            return False
        if value is not None and tok.value != value:
            return False
        return True

    def _check_op(self, op: str) -> bool:
        return self._check(TokenType.OP, op)

    def _check_kw(self, kw: str) -> bool:
        return self._check(TokenType.KEYWORD, kw)

    def _check_name(self, name: str) -> bool:
        return self._check(TokenType.NAME, name)

    def _expect(self, ttype: TokenType, msg: Optional[str] = None) -> Token:
        tok = self._peek()
        if tok.type != ttype:
            self._error(msg or f"Ky vong {ttype.name}, nhung gap {tok.type.name} ({tok.value!r})")
        return self._advance()

    def _expect_op(self, op: str) -> Token:
        tok = self._peek()
        if not (tok.type == TokenType.OP and tok.value == op):
            self._error(f"Ky vong '{op}', nhung gap {tok.value!r}")
        return self._advance()

    def _expect_kw(self, kw: str) -> Token:
        tok = self._peek()
        if not (tok.type == TokenType.KEYWORD and tok.value == kw):
            self._error(f"Ky vong tu khoa '{kw}', nhung gap {tok.value!r}")
        return self._advance()

    def _error(self, msg: str):
        tok = self._peek()
        raise ParseError(msg, tok.line, tok.col)

    def _is_block_end(self) -> bool:
        tok = self._peek()
        if tok.type == TokenType.EOF:
            return True
        return tok.type == TokenType.KEYWORD and tok.value in _BLOCK_END_KEYWORDS

    # ----------------------------------------------------------------
    # Entry point
    # ----------------------------------------------------------------

    def parse(self) -> Block:
        """Parse toan bo chuong trinh, tra ve Block goc (chunk)."""
        start = self._peek()
        block = self._parse_block()
        if not self._check(TokenType.EOF):
            self._error(f"Token thua, khong parse het: {self._peek().value!r}")
        block.line, block.col = start.line, start.col
        return block

    # ----------------------------------------------------------------
    # Block & statement
    # ----------------------------------------------------------------

    def _parse_block(self) -> Block:
        tok = self._peek()
        block = Block(line=tok.line, col=tok.col)
        while not self._is_block_end():
            if self._check_kw("return"):
                block.statements.append(self._parse_return())
                break  # 'return' luon la statement cuoi cung cua 1 block
            stmt = self._parse_statement()
            if stmt is not None:
                block.statements.append(stmt)
        return block

    def _parse_statement(self) -> Optional[Stmt]:
        tok = self._peek()

        if self._check_op(";"):
            self._advance()
            return None

        if self._check_op("::"):
            return self._parse_label()

        if tok.type == TokenType.KEYWORD:
            if tok.value == "if":
                return self._parse_if()
            if tok.value == "while":
                return self._parse_while()
            if tok.value == "do":
                return self._parse_do()
            if tok.value == "for":
                return self._parse_for()
            if tok.value == "repeat":
                return self._parse_repeat()
            if tok.value == "function":
                return self._parse_function_stmt()
            if tok.value == "local":
                return self._parse_local()
            if tok.value == "break":
                self._advance()
                return BreakStmt(line=tok.line, col=tok.col)
            if tok.value == "continue":
                self._advance()
                return ContinueStmt(line=tok.line, col=tok.col)

        if tok.type == TokenType.NAME:
            if tok.value == "goto":
                self._advance()
                name_tok = self._expect(TokenType.NAME, "Ky vong ten nhan sau 'goto'")
                return GotoStmt(label=name_tok.value, line=tok.line, col=tok.col)
            if tok.value == "export" and self._peek(1).type == TokenType.NAME and self._peek(1).value == "type":
                return self._parse_type_alias()
            if tok.value == "type" and self._looks_like_type_alias():
                return self._parse_type_alias()

        return self._parse_expr_statement()

    def _looks_like_type_alias(self) -> bool:
        """Kiem tra tu vi tri 'type' (chua tieu thu) xem day co phai mo dau
        statement `type Name = ...` hay `type Name<T> = ...` khong, de
        phan biet voi truong hop 'type' chi la 1 ten bien thuong (contextual
        keyword, khong reserved trong Luau).
        """
        return self._peek(1).type == TokenType.NAME

    # -- Cac statement co cau truc ro rang -------------------------------

    def _parse_label(self) -> LabelStmt:
        start = self._expect_op("::")
        name_tok = self._expect(TokenType.NAME, "Ky vong ten nhan trong '::name::'")
        self._expect_op("::")
        return LabelStmt(name=name_tok.value, line=start.line, col=start.col)

    def _parse_if(self) -> IfStmt:
        start = self._expect_kw("if")
        clauses: List[IfClause] = []

        cond = self.parse_expression()
        self._expect_kw("then")
        body = self._parse_block()
        clauses.append(IfClause(cond=cond, body=body, line=start.line, col=start.col))

        while self._check_kw("elseif"):
            etok = self._advance()
            c = self.parse_expression()
            self._expect_kw("then")
            b = self._parse_block()
            clauses.append(IfClause(cond=c, body=b, line=etok.line, col=etok.col))

        else_body: Optional[Block] = None
        if self._check_kw("else"):
            self._advance()
            else_body = self._parse_block()

        self._expect_kw("end")
        return IfStmt(clauses=clauses, else_body=else_body, line=start.line, col=start.col)

    def _parse_while(self) -> WhileStmt:
        start = self._expect_kw("while")
        cond = self.parse_expression()
        self._expect_kw("do")
        body = self._parse_block()
        self._expect_kw("end")
        return WhileStmt(cond=cond, body=body, line=start.line, col=start.col)

    def _parse_do(self) -> DoStmt:
        start = self._expect_kw("do")
        body = self._parse_block()
        self._expect_kw("end")
        return DoStmt(body=body, line=start.line, col=start.col)

    def _parse_repeat(self) -> RepeatStmt:
        start = self._expect_kw("repeat")
        body = self._parse_block()
        self._expect_kw("until")
        cond = self.parse_expression()
        return RepeatStmt(body=body, cond=cond, line=start.line, col=start.col)

    def _parse_for(self) -> Stmt:
        start = self._expect_kw("for")
        first_name = self._expect(TokenType.NAME, "Ky vong ten bien sau 'for'").value

        if self._check_op("="):
            # Numeric for: for i = start, stop[, step] do ... end
            self._advance()
            start_expr = self.parse_expression()
            self._expect_op(",")
            stop_expr = self.parse_expression()
            step_expr: Optional[Expr] = None
            if self._check_op(","):
                self._advance()
                step_expr = self.parse_expression()
            self._expect_kw("do")
            body = self._parse_block()
            self._expect_kw("end")
            return NumericForStmt(
                var=first_name, start=start_expr, stop=stop_expr,
                step=step_expr, body=body, line=start.line, col=start.col,
            )

        # Generic for: for k, v[, ...] in exprlist do ... end
        if self._check_op(":"):
            self._advance()
            self._parse_type_raw()  # type annotation cua bien vong lap, bo qua
        names = [first_name]
        while self._check_op(","):
            self._advance()
            nm = self._expect(TokenType.NAME).value
            if self._check_op(":"):
                self._advance()
                self._parse_type_raw()
            names.append(nm)
        self._expect_kw("in")
        exprs = self._parse_expr_list()
        self._expect_kw("do")
        body = self._parse_block()
        self._expect_kw("end")
        return GenericForStmt(names=names, exprs=exprs, body=body, line=start.line, col=start.col)

    def _parse_function_stmt(self) -> FunctionDeclStmt:
        start = self._expect_kw("function")
        name_path = [self._expect(TokenType.NAME, "Ky vong ten ham sau 'function'").value]
        is_method = False
        while self._check_op("."):
            self._advance()
            name_path.append(self._expect(TokenType.NAME).value)
        if self._check_op(":"):
            self._advance()
            name_path.append(self._expect(TokenType.NAME).value)
            is_method = True
        self._maybe_skip_angle_brackets()  # generic: function f<T>(...)
        body = self._parse_funcbody(is_method=is_method)
        return FunctionDeclStmt(name_path=name_path, is_method=is_method, body=body,
                                 line=start.line, col=start.col)

    def _parse_local(self) -> Stmt:
        start = self._expect_kw("local")

        if self._check_kw("function"):
            self._advance()
            name_tok = self._expect(TokenType.NAME, "Ky vong ten ham sau 'local function'")
            self._maybe_skip_angle_brackets()
            body = self._parse_funcbody(is_method=False)
            return LocalFunctionStmt(name=name_tok.value, body=body, line=start.line, col=start.col)

        names: List[str] = []
        type_infos: List[Optional[TypeInfo]] = []

        name_tok = self._expect(TokenType.NAME, "Ky vong ten bien sau 'local'")
        names.append(name_tok.value)
        type_infos.append(self._maybe_parse_type_annotation())
        self._maybe_skip_angle_brackets()  # attribute: local x <const> = 1

        while self._check_op(","):
            self._advance()
            nm = self._expect(TokenType.NAME).value
            names.append(nm)
            type_infos.append(self._maybe_parse_type_annotation())
            self._maybe_skip_angle_brackets()

        values: List[Expr] = []
        if self._check_op("="):
            self._advance()
            values = self._parse_expr_list()

        return LocalAssignStmt(names=names, type_infos=type_infos, values=values,
                                line=start.line, col=start.col)

    def _parse_return(self) -> ReturnStmt:
        start = self._expect_kw("return")
        values: List[Expr] = []
        if not self._is_block_end() and not self._check_op(";"):
            values = self._parse_expr_list()
        if self._check_op(";"):
            self._advance()
        return ReturnStmt(values=values, line=start.line, col=start.col)

    def _parse_type_alias(self) -> TypeAliasStmt:
        start = self._peek()
        is_exported = False
        if self._check_name("export"):
            self._advance()
            is_exported = True
        self._advance()  # tieu thu 'type' (NAME contextual, khong phai KEYWORD)
        name_tok = self._expect(TokenType.NAME, "Ky vong ten alias sau 'type'")
        self._maybe_skip_angle_brackets()  # generic: type Foo<T> = ...
        self._expect_op("=")
        raw = self._parse_type_raw()
        return TypeAliasStmt(name=name_tok.value, is_exported=is_exported,
                              raw=raw.strip(), line=start.line, col=start.col)

    # -- Statement dang expression: goi ham / gan gia tri ------------------

    def _parse_expr_statement(self) -> Stmt:
        start = self._peek()
        expr = self._parse_suffixed_expr()

        if self._check(TokenType.OP) and self._peek().value in _COMPOUND_OPS:
            op_tok = self._advance()
            value = self.parse_expression()
            return CompoundAssignStmt(target=expr, op=_COMPOUND_OPS[op_tok.value],
                                       value=value, line=start.line, col=start.col)

        if self._check_op(",") or self._check_op("="):
            targets = [expr]
            while self._check_op(","):
                self._advance()
                targets.append(self._parse_suffixed_expr())
            self._expect_op("=")
            values = self._parse_expr_list()
            return AssignStmt(targets=targets, values=values, line=start.line, col=start.col)

        if not isinstance(expr, (CallExpr, MethodCallExpr)):
            self._error(
                "Statement khong hop le: bieu thuc khong phai loi goi ham "
                "va cung khong phai phep gan (thieu dau '=')"
            )
        return CallStmt(call=expr, line=start.line, col=start.col)

    # ----------------------------------------------------------------
    # Expression: precedence climbing
    # ----------------------------------------------------------------

    def parse_expression(self) -> Expr:
        """Entry point parse 1 bieu thuc day du, bao gom hau to `:: Type`
        (type cast) cua Luau neu co, co the lap lai nhieu lan (hiem gap
        nhung hop le ve mat cu phap: `x :: any :: number`).
        """
        expr = self._parse_binexpr(0)
        while self._check_op("::"):
            self._advance()
            type_info = self._parse_type_info(is_cast=True)
            expr = TypeCastExpr(expr=expr, type_info=type_info,
                                 line=type_info.line, col=type_info.col)
        return expr

    def _parse_expr_list(self) -> List[Expr]:
        exprs = [self.parse_expression()]
        while self._check_op(","):
            self._advance()
            exprs.append(self.parse_expression())
        return exprs

    def _parse_binexpr(self, min_prec: int) -> Expr:
        left = self._parse_unary()
        while True:
            tok = self._peek()
            key = tok.value if tok.type in (TokenType.OP, TokenType.KEYWORD) else None
            prec_info = _BIN_PRECEDENCE.get(key) if key else None
            if prec_info is None:
                break
            prec, right_assoc = prec_info
            if prec < min_prec:
                break
            op_tok = self._advance()
            next_min = prec if right_assoc else prec + 1
            right = self._parse_binexpr(next_min)
            left = BinOpExpr(op=op_tok.value, left=left, right=right,
                              line=op_tok.line, col=op_tok.col)
        return left

    def _parse_unary(self) -> Expr:
        tok = self._peek()
        is_unary_op = (
            tok.is_keyword("not")
            or (tok.type == TokenType.OP and tok.value in ("#", "-"))
        )
        if is_unary_op:
            self._advance()
            operand = self._parse_binexpr(_UNARY_PRECEDENCE)
            return UnOpExpr(op=tok.value, operand=operand, line=tok.line, col=tok.col)
        return self._parse_simple_expr()

    def _parse_simple_expr(self) -> Expr:
        tok = self._peek()

        if tok.is_keyword("if"):
            return self._parse_if_expr()
        if tok.is_keyword("nil"):
            self._advance()
            return NilExpr(line=tok.line, col=tok.col)
        if tok.is_keyword("true"):
            self._advance()
            return TrueExpr(line=tok.line, col=tok.col)
        if tok.is_keyword("false"):
            self._advance()
            return FalseExpr(line=tok.line, col=tok.col)
        if tok.is_op("..."):
            self._advance()
            return VarargExpr(line=tok.line, col=tok.col)
        if tok.type == TokenType.NUMBER:
            self._advance()
            return NumberExpr(raw=tok.value, value=tok.literal, line=tok.line, col=tok.col)
        if tok.type == TokenType.STRING:
            self._advance()
            # Chuoi ngan luu ca dau nhay trong `value` (xem lexer._read_short_string);
            # chuoi dai thi `value` chi la noi dung (khong dau ngoac).
            is_short = tok.value.startswith("'") or tok.value.startswith('"')
            return StringExpr(value=tok.literal, raw=tok.value,
                               is_long_string=not is_short, line=tok.line, col=tok.col)
        if tok.is_keyword("function"):
            self._advance()
            self._maybe_skip_angle_brackets()  # generic: function<T>(...)
            body = self._parse_funcbody(is_method=False)
            return FunctionExpr(body=body, line=tok.line, col=tok.col)
        if tok.is_op("{"):
            return self._parse_table_constructor()

        return self._parse_suffixed_expr()

    def _parse_if_expr(self) -> IfExpr:
        start = self._expect_kw("if")
        clauses: List[IfExprClause] = []

        cond = self.parse_expression()
        self._expect_kw("then")
        value = self.parse_expression()
        clauses.append(IfExprClause(cond=cond, value=value, line=start.line, col=start.col))

        while self._check_kw("elseif"):
            clause_start = self._advance()
            cond = self.parse_expression()
            self._expect_kw("then")
            value = self.parse_expression()
            clauses.append(IfExprClause(cond=cond, value=value, line=clause_start.line, col=clause_start.col))

        self._expect_kw("else")
        else_value = self.parse_expression()
        return IfExpr(clauses=clauses, else_value=else_value, line=start.line, col=start.col)

    # -- prefixexp: Name | '(' expr ')' , roi noi chuoi suffix ---------

    def _parse_primary_expr(self) -> Expr:
        tok = self._peek()
        if tok.type == TokenType.NAME:
            self._advance()
            return NameExpr(name=tok.value, line=tok.line, col=tok.col)
        if tok.is_op("("):
            self._advance()
            inner = self.parse_expression()
            self._expect_op(")")
            return ParenExpr(expr=inner, line=tok.line, col=tok.col)
        self._error(
            f"Ky vong 1 bieu thuc (ten bien hoac '('), nhung gap {tok.value!r}"
        )
        return None  # khong bao gio toi day

    def _parse_suffixed_expr(self) -> Expr:
        expr = self._parse_primary_expr()
        while True:
            tok = self._peek()
            if tok.is_op("."):
                self._advance()
                field_tok = self._expect(TokenType.NAME, "Ky vong ten thuoc tinh sau '.'")
                expr = IndexExpr(obj=expr, field=field_tok.value, line=tok.line, col=tok.col)
            elif tok.is_op("["):
                self._advance()
                key = self.parse_expression()
                self._expect_op("]")
                expr = IndexBracketExpr(obj=expr, key=key, line=tok.line, col=tok.col)
            elif tok.is_op(":"):
                self._advance()
                method_tok = self._expect(TokenType.NAME, "Ky vong ten method sau ':'")
                args = self._parse_args()
                expr = MethodCallExpr(obj=expr, method=method_tok.value, args=args,
                                       line=tok.line, col=tok.col)
            elif tok.is_op("(") or tok.type == TokenType.STRING or tok.is_op("{"):
                args = self._parse_args()
                expr = CallExpr(func=expr, args=args, line=tok.line, col=tok.col)
            else:
                break
        return expr

    def _parse_args(self) -> List[Expr]:
        tok = self._peek()
        if tok.is_op("("):
            self._advance()
            if self._check_op(")"):
                self._advance()
                return []
            args = self._parse_expr_list()
            self._expect_op(")")
            return args
        if tok.type == TokenType.STRING:
            self._advance()
            is_short = tok.value.startswith("'") or tok.value.startswith('"')
            return [StringExpr(value=tok.literal, raw=tok.value,
                                is_long_string=not is_short, line=tok.line, col=tok.col)]
        if tok.is_op("{"):
            return [self._parse_table_constructor()]
        self._error("Ky vong danh sach tham so goi ham: '(', chuoi, hoac '{'")
        return []

    def _parse_table_constructor(self) -> TableConstructorExpr:
        start = self._expect_op("{")
        fields: List[TableField] = []
        while not self._check_op("}"):
            ftok = self._peek()
            if ftok.is_op("["):
                self._advance()
                key = self.parse_expression()
                self._expect_op("]")
                self._expect_op("=")
                value = self.parse_expression()
                fields.append(TableField(key=key, value=value, is_computed_key=True,
                                          line=ftok.line, col=ftok.col))
            elif ftok.type == TokenType.NAME and self._peek(1).is_op("="):
                name_tok = self._advance()
                self._advance()  # '='
                value = self.parse_expression()
                fields.append(TableField(key=name_tok.value, value=value, is_computed_key=False,
                                          line=ftok.line, col=ftok.col))
            else:
                value = self.parse_expression()
                fields.append(TableField(key=None, value=value, is_computed_key=False,
                                          line=ftok.line, col=ftok.col))

            if self._check_op(",") or self._check_op(";"):
                self._advance()
                continue
            break

        self._expect_op("}")
        return TableConstructorExpr(fields=fields, line=start.line, col=start.col)

    # ----------------------------------------------------------------
    # Function body / params
    # ----------------------------------------------------------------

    def _parse_funcbody(self, is_method: bool) -> FunctionBody:
        start = self._expect_op("(")
        params: List[Param] = []
        if is_method:
            params.append(Param(name="self", line=start.line, col=start.col))

        if not self._check_op(")"):
            while True:
                if self._check_op("..."):
                    vtok = self._advance()
                    type_info = self._maybe_parse_type_annotation()
                    params.append(Param(name="...", type_info=type_info, is_vararg=True,
                                         line=vtok.line, col=vtok.col))
                    break
                name_tok = self._expect(TokenType.NAME, "Ky vong ten tham so")
                type_info = self._maybe_parse_type_annotation()
                params.append(Param(name=name_tok.value, type_info=type_info,
                                     line=name_tok.line, col=name_tok.col))
                if self._check_op(","):
                    self._advance()
                    continue
                break

        self._expect_op(")")

        return_type: Optional[TypeInfo] = None
        if self._check_op(":"):
            self._advance()
            return_type = self._parse_type_info(is_cast=False)

        body = self._parse_block()
        self._expect_kw("end")
        return FunctionBody(params=params, is_vararg=any(p.is_vararg for p in params),
                             body=body, return_type=return_type, line=start.line, col=start.col)

    # ----------------------------------------------------------------
    # Type annotation (best-effort raw capture) & generics
    # ----------------------------------------------------------------

    def _maybe_parse_type_annotation(self) -> Optional[TypeInfo]:
        if self._check_op(":"):
            self._advance()
            return self._parse_type_info(is_cast=False)
        return None

    def _parse_type_info(self, is_cast: bool) -> TypeInfo:
        tok = self._peek()
        raw = self._parse_type_raw()
        return TypeInfo(raw=raw.strip(), is_cast=is_cast, line=tok.line, col=tok.col)

    def _parse_type_raw(self) -> str:
        """'Nuot' token de lay raw text cua 1 type annotation, theo kieu
        best-effort: KHONG xay dung cay cu phap rieng cho he thong type
        cua Luau (union, intersection, generic, function type, table
        type...), chi nham nhan dung RANH GIOI ket thuc cua type trong cac
        ngu canh hay gap (sau ten bien, sau tham so, sau ')' lam kieu tra
        ve, sau '::' de cast, hoac ve phai 1 khai bao `type X = ...`).

        Van de can giai: Lua/Luau khong bat buoc dau cham dung statement,
        nen voi 1 bare NAME (vd module path `Foo.Bar`), ta khong the chi
        dua vao loai token de biet khi nao type ket thuc - vi statement
        ke tiep cung co the bat dau bang 1 NAME (vd 1 loi goi ham). Giai
        phap: chi tiep tuc "nuot" qua 1 NAME/STRING/NUMBER/dau ')' '}' ']'
        '>' khi token KE TIEP la 1 toan tu noi type ro rang ('.', '<',
        '?', '->', '|', '&'); neu khong co dau hieu noi nhu vay, dung lai
        ngay (gia dinh phan con lai la statement moi).
        """
        parts: List[str] = []
        depth = 0

        def is_hard_stop(tok: Token) -> bool:
            if tok.type == TokenType.OP:
                if depth == 0 and tok.value in _TYPE_CLOSE:
                    return True
                if depth == 0 and tok.value in _TYPE_STOP_OPS_AT_DEPTH0:
                    return True
            if tok.type == TokenType.KEYWORD:
                if tok.value in _TYPE_STOP_KEYWORDS or tok.value in _STATEMENT_START_KEYWORDS:
                    return True
            return False

        def peek_is_continuation() -> bool:
            nxt = self._peek()
            return nxt.type == TokenType.OP and nxt.value in ("." , "<", "?", "->", "|", "&")

        expect_more = True
        while expect_more:
            tok = self._peek()
            if tok.type == TokenType.EOF:
                break
            if depth == 0 and is_hard_stop(tok):
                break

            parts.append(tok.value)
            self._advance()

            if tok.type == TokenType.OP and tok.value in _TYPE_OPEN:
                depth += 1
                expect_more = True
                continue
            if tok.type == TokenType.OP and tok.value in _TYPE_CLOSE:
                depth = max(0, depth - 1)
                expect_more = depth > 0 or peek_is_continuation()
                continue
            if depth > 0:
                # Con dang o trong 1 nhom ngoac (table type, function type,
                # generic...): cu tiep tuc nuot toi khi dong het nhom.
                expect_more = True
                continue

            # depth == 0, vua tieu thu xong 1 token "tan cung" (NAME/STRING/
            # NUMBER/keyword singleton nhu true-false-nil/toan tu '.' '->' '?' '|' '&')
            if tok.type == TokenType.OP and tok.value in (".", "->"):
                expect_more = True  # bat buoc phai co them (ten/type) theo sau
            elif tok.type == TokenType.OP and tok.value in ("|", "&"):
                expect_more = True  # bat buoc phai co them 1 type ve sau
            else:
                expect_more = peek_is_continuation()

        return " ".join(parts)

    def _maybe_skip_angle_brackets(self):
        """Bo qua (khong luu lai) 1 danh sach trong dau `< ... >` can bang,
        dung cho generic (`function f<T>`) va attribute bien local
        (`local x <const>`). An toan vi o cac vi tri goi ham nay, '<' chi
        co the mang nghia mo generic/attribute, khong phai toan tu so sanh.
        """
        if not self._check_op("<"):
            return
        depth = 0
        while True:
            tok = self._peek()
            if tok.type == TokenType.EOF:
                self._error("Danh sach generic/attribute '<...>' khong duoc dong")
            if tok.is_op("<"):
                depth += 1
            elif tok.is_op(">"):
                depth -= 1
                self._advance()
                if depth == 0:
                    return
                continue
            self._advance()


# ---------------------------------------------------------------------------
# Ham tien ich
# ---------------------------------------------------------------------------

def parse(source: str, filename: str = "<chunk>") -> Block:
    """Tokenize + parse 1 chuoi source Lua/Luau, tra ve AST goc (Block)."""
    tokens = tokenize(source, filename)
    return Parser(tokens).parse()


__all__ = ["Parser", "ParseError", "parse"]