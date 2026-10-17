# -*- coding: utf-8 -*-
"""
src.basic.annotations
======================
Tang tien xu ly dau tien cua pipeline Basic: "lot sach" toan bo dau vet
he thong type cua Luau ra khoi AST, de cac tang sau (naming, strings,
cff, vm...) chi can lam viec voi 1 cay cu phap Lua "thuan" (khong co
type annotation / type cast / type alias), giup don gian hoa dang ke.

Cu the, module nay xu ly 3 truong hop:

    1. Tham so ham co annotation kieu:   `function f(x: number)`
       -> xoa `Param.type_info`, giu nguyen ten tham so `x`.

    2. Kieu tra ve cua ham:              `function f(): boolean`
       -> xoa `FunctionBody.return_type`.

    3. Khai bao local co annotation:     `local hp: number = 100`
       -> xoa tung phan tu trong `LocalAssignStmt.type_infos` (gan None).

    4. Bieu thuc ep kieu (type cast):    `x :: any`
       -> THAY THE hoan toan `TypeCastExpr` bang chinh bieu thuc con cua
          no (`x`), loai bo ca node lan annotation kieu di kem.

    5. Khai bao type alias:              `type Foo = {...}` /
                                           `export type Bar = string | number`
       -> XOA HAN statement nay khoi `Block.statements` (no khong co tac
          dung runtime, chi ton tai o muc type-checker).

Cac directive dang `--!strict` / `--!nonstrict` / `--!nolint` da duoc
Lexer tach rieng ra khoi dong token chinh tu truoc (xem `Parser.directives`
trong `src/parser/parser.py`) nen KHONG con xuat hien trong AST - khong
can xu ly them o day.

Ham public duy nhat: `strip_type_annotations(block) -> block` (mutate
in-place VA tra ve chinh block do, de tien chain nhieu buoc pipeline:
`block = strip_type_annotations(parse(source))`).
"""

from __future__ import annotations

from typing import Tuple

from ..parser.nodes import (
    Block,
    FunctionBody,
    LocalAssignStmt,
    Param,
    TypeAliasStmt,
    TypeCastExpr,
)
from ..parser.traversal import DROP, transform_tree


def _visit(node) -> Tuple[bool, object]:
    # --- Cac node bi XOA HAN -------------------------------------------
    if isinstance(node, TypeAliasStmt):
        return True, DROP

    # --- Cac node bi "boc vo", thay bang chinh ben trong no -------------
    if isinstance(node, TypeCastExpr):
        # `x :: SomeType` -> `x`. Tiep tuc duyet vao ben trong (truong hop
        # hiem gap: `(x :: any) :: string` long nhau) truoc khi tra ve.
        return True, transform_tree(node.expr, _visit)

    # --- Cac node bi mutate tai cho (xoa field type, giu nguyen node) ---
    if isinstance(node, Param):
        node.type_info = None
        # Khong return True o day: van can de transform_tree tiep tuc di
        # vao cac field con khac cua Param (hien tai Param khong co field
        # dataclass nao khac dang quan tam, nhung giu hanh vi mac dinh de
        # an toan/mo rong sau nay).
        return False, None

    if isinstance(node, FunctionBody):
        node.return_type = None
        return False, None

    if isinstance(node, LocalAssignStmt):
        node.type_infos = [None] * len(node.names)
        return False, None

    return False, None


def strip_type_annotations(block: Block) -> Block:
    """Lot sach moi annotation/type-cast/type-alias Luau khoi `block`.

    Mutate AST in-place va tra ve chinh `block` (cung 1 doi tuong) de
    tien goi theo kieu chain: `block = strip_type_annotations(parse(src))`.
    """
    return transform_tree(block, _visit)


__all__ = ["strip_type_annotations"]