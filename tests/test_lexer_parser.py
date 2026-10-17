# -*- coding: utf-8 -*-
"""
tests/test_lexer_parser.py
===========================
Bo test nho, thuan Python (khong can pytest) cho src/parser/{lexer,parser}.py.

Chay:
    python3 tests/test_lexer_parser.py

Moi test la 1 ham bat dau bang 'test_'. Script tu dong goi het va bao cao.
"""

import sys
import os
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.parser.lexer import Lexer, TokenType, tokenize, LexError
from src.parser.parser import Parser, ParseError, parse
from src.parser import nodes as N


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _first_stmt(src: str):
    block = parse(src)
    assert len(block.statements) >= 1, f"Khong co statement nao: {src!r}"
    return block.statements[0]


def _single_expr(src: str):
    """Parse `local _x = <src>` va tra ve expr cua values[0]."""
    stmt = _first_stmt(f"local _x = {src}")
    assert isinstance(stmt, N.LocalAssignStmt)
    return stmt.values[0]


# ---------------------------------------------------------------------------
# LEXER TESTS
# ---------------------------------------------------------------------------

def test_lexer_basic_tokens():
    toks = tokenize("local x = 1 + 2")
    kinds = [(t.type, t.value) for t in toks if t.type != TokenType.EOF]
    assert kinds == [
        (TokenType.KEYWORD, "local"),
        (TokenType.NAME, "x"),
        (TokenType.OP, "="),
        (TokenType.NUMBER, "1"),
        (TokenType.OP, "+"),
        (TokenType.NUMBER, "2"),
    ], kinds


def test_lexer_numbers():
    cases = {
        "42": 42,
        "3.14": 3.14,
        "0x1F": 31,
        "1e3": 1000.0,
        "1_000_000": 1000000,
        "0b101": 5,
    }
    for raw, expected in cases.items():
        toks = tokenize(raw)
        assert toks[0].type == TokenType.NUMBER, raw
        assert toks[0].literal == expected, f"{raw}: got {toks[0].literal}, muon {expected}"


def test_lexer_short_string_escapes():
    toks = tokenize(r'"a\tb\nc\65\x41\z   d"')
    s = toks[0]
    assert s.type == TokenType.STRING
    # \t \n \65(=A) \x41(=A) \z (bo whitespace) -> "a\tb\ncAAd"
    assert s.literal == "a\tb\ncAAd", repr(s.literal)


def test_lexer_long_string():
    src = "local s = [[hello\nworld]]"
    toks = tokenize(src)
    str_tok = [t for t in toks if t.type == TokenType.STRING][0]
    assert str_tok.literal == "hello\nworld", repr(str_tok.literal)


def test_lexer_long_string_with_equals_level():
    src = "local s = [==[ a ]] still inside ]==]"
    toks = tokenize(src)
    str_tok = [t for t in toks if t.type == TokenType.STRING][0]
    assert str_tok.literal == " a ]] still inside ", repr(str_tok.literal)


def test_lexer_comments_are_skipped():
    toks = tokenize("local x = 1 -- day la comment\nlocal y = 2")
    values = [t.value for t in toks if t.type != TokenType.EOF]
    assert "comment" not in " ".join(values)
    assert values.count("local") == 2


def test_lexer_long_comment_is_skipped():
    toks = tokenize("local x --[[ block\ncomment ]] = 1")
    values = [t.value for t in toks if t.type != TokenType.EOF]
    assert values == ["local", "x", "=", "1"]


def test_lexer_directive():
    toks = tokenize("--!strict\nlocal x = 1")
    assert toks[0].type == TokenType.DIRECTIVE
    assert toks[0].value == "!strict"


def test_lexer_compound_and_floordiv_operators():
    toks = tokenize("a //= b .. = c")  # '.. =' khong hop le lien ke that
    # Test rieng tung truong hop ro rang hon:
    for src, expect_op in [
        ("a += 1", "+="), ("a -= 1", "-="), ("a *= 1", "*="),
        ("a /= 1", "/="), ("a //= 1", "//="), ("a %= 1", "%="),
        ("a ^= 1", "^="), ("a ..= 1", "..="), ("a // b", "//"),
    ]:
        toks2 = tokenize(src)
        ops = [t.value for t in toks2 if t.type == TokenType.OP]
        assert expect_op in ops, f"{src}: {ops}"


def test_lexer_unterminated_string_raises():
    try:
        tokenize('"khong dong day nhay')
        assert False, "Phai nem LexError"
    except LexError:
        pass


# ---------------------------------------------------------------------------
# PARSER TESTS - expression precedence
# ---------------------------------------------------------------------------

def test_precedence_mul_over_add():
    e = _single_expr("1 + 2 * 3")
    assert isinstance(e, N.BinOpExpr) and e.op == "+"
    assert isinstance(e.right, N.BinOpExpr) and e.right.op == "*"
    assert isinstance(e.left, N.NumberExpr)


def test_precedence_pow_right_assoc_and_tighter_than_unary():
    # -2^2 == -(2^2)
    e = _single_expr("-2^2")
    assert isinstance(e, N.UnOpExpr) and e.op == "-"
    inner = e.operand
    assert isinstance(inner, N.BinOpExpr) and inner.op == "^"


def test_precedence_concat_right_assoc():
    e = _single_expr('"a" .. "b" .. "c"')
    assert isinstance(e, N.BinOpExpr) and e.op == ".."
    assert isinstance(e.left, N.StringExpr)
    assert isinstance(e.right, N.BinOpExpr) and e.right.op == ".."


def test_precedence_and_or():
    e = _single_expr("1 == 1 and 2 == 2 or false")
    # or co precedence thap nhat -> node goc la 'or'
    assert isinstance(e, N.BinOpExpr) and e.op == "or"
    assert isinstance(e.left, N.BinOpExpr) and e.left.op == "and"


def test_paren_preserved():
    e = _single_expr("(1 + 2) * 3")
    assert isinstance(e, N.BinOpExpr) and e.op == "*"
    assert isinstance(e.left, N.ParenExpr)


# ---------------------------------------------------------------------------
# PARSER TESTS - statements
# ---------------------------------------------------------------------------

def test_local_assign_multi():
    stmt = _first_stmt("local a, b, c = 1, 2")
    assert isinstance(stmt, N.LocalAssignStmt)
    assert stmt.names == ["a", "b", "c"]
    assert len(stmt.values) == 2


def test_if_elseif_else():
    stmt = _first_stmt("""
        if a then
            return 1
        elseif b then
            return 2
        else
            return 3
        end
    """)
    assert isinstance(stmt, N.IfStmt)
    assert len(stmt.clauses) == 2
    assert stmt.else_body is not None


def test_numeric_for():
    stmt = _first_stmt("for i = 1, 10, 2 do end")
    assert isinstance(stmt, N.NumericForStmt)
    assert stmt.var == "i"
    assert stmt.step is not None


def test_generic_for():
    stmt = _first_stmt("for k, v in pairs(t) do end")
    assert isinstance(stmt, N.GenericForStmt)
    assert stmt.names == ["k", "v"]


def test_function_decl_method():
    stmt = _first_stmt("function obj:getSum() return self.x end")
    assert isinstance(stmt, N.FunctionDeclStmt)
    assert stmt.is_method is True
    assert stmt.body.params[0].name == "self"


def test_local_function_recursive_name_visible():
    stmt = _first_stmt("local function fact(n) return n end")
    assert isinstance(stmt, N.LocalFunctionStmt)
    assert stmt.name == "fact"


def test_call_statement_vs_assignment():
    call_stmt = _first_stmt("foo(1, 2)")
    assert isinstance(call_stmt, N.CallStmt)
    assert isinstance(call_stmt.call, N.CallExpr)

    assign_stmt = _first_stmt("foo = 1")
    assert isinstance(assign_stmt, N.AssignStmt)


def test_method_call_chain():
    stmt = _first_stmt("part:Destroy()")
    assert isinstance(stmt, N.CallStmt)
    assert isinstance(stmt.call, N.MethodCallExpr)
    assert stmt.call.method == "Destroy"


def test_compound_assignment():
    stmt = _first_stmt("count += 1")
    assert isinstance(stmt, N.CompoundAssignStmt)
    assert stmt.op == "+"


def test_compound_assignment_concat():
    stmt = _first_stmt('s ..= "x"')
    assert isinstance(stmt, N.CompoundAssignStmt)
    assert stmt.op == ".."


def test_table_constructor_mixed_fields():
    e = _single_expr('{ 1, 2, name = "hero", ["key" .. 1] = true }')
    assert isinstance(e, N.TableConstructorExpr)
    assert len(e.fields) == 4
    assert e.fields[0].key is None  # positional
    assert e.fields[2].key == "name"
    assert e.fields[3].is_computed_key is True


def test_dot_index_not_confused_with_method_call():
    e = _single_expr("part.Position")
    assert isinstance(e, N.IndexExpr)
    assert e.field == "Position"


def test_vararg_and_function_expr():
    e = _single_expr("function(...) return ... end")
    assert isinstance(e, N.FunctionExpr)
    assert e.body.is_vararg is True


def test_break_and_continue():
    block = parse("while true do break end while true do continue end")
    w1, w2 = block.statements
    assert isinstance(w1.body.statements[0], N.BreakStmt)
    assert isinstance(w2.body.statements[0], N.ContinueStmt)


def test_goto_and_label():
    block = parse("::top:: goto top")
    assert isinstance(block.statements[0], N.LabelStmt)
    assert isinstance(block.statements[1], N.GotoStmt)
    assert block.statements[1].label == "top"


# ---------------------------------------------------------------------------
# PARSER TESTS - Luau type annotations / cast / alias (best-effort)
# ---------------------------------------------------------------------------

def test_param_type_annotation_is_captured_and_ignored_for_grammar():
    stmt = _first_stmt("local function f(x: number, y: string): boolean return true end")
    assert isinstance(stmt, N.LocalFunctionStmt)
    params = stmt.body.params
    assert params[0].name == "x" and params[0].type_info.raw == "number"
    assert params[1].name == "y" and params[1].type_info.raw == "string"
    assert stmt.body.return_type.raw == "boolean"


def test_local_var_type_annotation():
    stmt = _first_stmt("local hp: number = 100")
    assert stmt.type_infos[0].raw == "number"


def test_type_cast_operator():
    e = _single_expr("x :: any")
    assert isinstance(e, N.TypeCastExpr)
    assert e.type_info.raw == "any"
    assert e.type_info.is_cast is True


def test_type_alias_statement_then_next_statement_is_separate():
    block = parse("type Foo = { x: number, y: number }\nlocal z = 1")
    assert isinstance(block.statements[0], N.TypeAliasStmt)
    assert block.statements[0].name == "Foo"
    assert isinstance(block.statements[1], N.LocalAssignStmt)


def test_export_type_alias():
    block = parse("export type Bar = string | number")
    stmt = block.statements[0]
    assert isinstance(stmt, N.TypeAliasStmt)
    assert stmt.is_exported is True
    assert stmt.name == "Bar"


def test_generic_function_declaration():
    stmt = _first_stmt("function identity<T>(x: T): T return x end")
    assert isinstance(stmt, N.FunctionDeclStmt)
    assert stmt.body.params[0].name == "x"


def test_local_attribute_const_is_skipped():
    stmt = _first_stmt("local x <const> = 1")
    assert isinstance(stmt, N.LocalAssignStmt)
    assert stmt.names == ["x"]
    assert stmt.values[0].value == 1


def test_directive_strict_does_not_break_parsing():
    block = parse("--!strict\nlocal x = 1")
    assert len(block.statements) == 1


# ---------------------------------------------------------------------------
# PARSER TESTS - loi cu phap duoc bao dung
# ---------------------------------------------------------------------------

def test_parse_error_missing_end():
    try:
        parse("if true then local x = 1")
        assert False, "Phai nem ParseError (thieu 'end')"
    except ParseError:
        pass


def test_parse_error_bad_statement():
    try:
        parse("1 + 1")  # bieu thuc don thuan khong phai statement hop le
        assert False, "Phai nem ParseError"
    except ParseError:
        pass


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    test_fns = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed, failed = 0, 0
    for name, fn in test_fns:
        try:
            fn()
            print(f"  OK   {name}")
            passed += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  FAIL {name}: {exc}")
            traceback.print_exc(limit=3)
            failed += 1
    print()
    print(f"Tong: {passed + failed} test, {passed} PASS, {failed} FAIL")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
