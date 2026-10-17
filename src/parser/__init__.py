# -*- coding: utf-8 -*-
"""
src.parser
==========
Package phan tich cu phap Lua/Luau cho LuauObfuscator.

Pipeline: source code (str) --Lexer--> List[Token] --Parser--> AST (Block)

Vi du dung nhanh:

    from src.parser import parse

    ast = parse(open("input.lua", encoding="utf-8").read())
    print(ast)

Hoac dung tung buoc (vd de debug token):

    from src.parser import Lexer, Parser

    tokens = Lexer(source).tokenize()
    ast = Parser(tokens).parse()
"""

from .lexer import Lexer, Token, TokenType, LexError, tokenize, KEYWORDS
from .parser import Parser, ParseError, parse
from .traversal import walk_tree, transform_tree, DROP
from . import nodes

__all__ = [
    "Lexer", "Token", "TokenType", "LexError", "tokenize", "KEYWORDS",
    "Parser", "ParseError", "parse",
    "walk_tree", "transform_tree", "DROP",
    "nodes",
]