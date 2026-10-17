# -*- coding: utf-8 -*-
"""
src.parser.traversal
====================
Tien ich duyet va bien doi cay AST (dataclass) tong quat, dua tren reflection
(`dataclasses.fields`), dung chung cho toan bo parser, basic va advanced passes.

Nguyen tac:
    - Moi node AST (xem src/parser/nodes.py) deu la dataclass, cac field
      con cua no chi co the la: node khac (dataclass), list cac node,
      hoac gia tri nguyen thuy (str/int/float/bool/None).
    - Field `line`/`col` luon bi bo qua khi duyet (chi phuc vu debug,
      khong phai 1 phan cu phap).

Cung cap 2 ham:
    walk_tree(value, on_node)      - duyet READ-ONLY, goi on_node(node)
                                      cho MOI dataclass node gap duoc.
    transform_tree(value, visit)   - duyet CO THE BIEN DOI/THAY THE node,
                                      dung cho cac buoc rewrite AST.

`DROP` la sentinel dac biet: neu `visit()` tra ve `(True, DROP)`, phan tu
do se bi LOAI BO hoan toan khoi list cha (dung de xoa han 1 statement,
vd TypeAliasStmt trong annotations.py).
"""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from typing import Any, Callable, Tuple

_POS_FIELDS = ("line", "col")


class _DropSentinel:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - chi phuc vu debug
        return "<DROP>"


# Dung lam gia tri tra ve tu `visit()` trong transform_tree() de bao hieu
# "xoa han phan tu nay khoi list cha".
DROP = _DropSentinel()


def walk_tree(value: Any, on_node: Callable[[Any], None]) -> None:
    """Duyet read-only toan bo cay AST bat dau tu `value`.

    Goi `on_node(node)` cho MOI dataclass node gap duoc (pre-order: node
    cha duoc goi truoc con cua no). Khong mutate bat cu gi.
    """
    if isinstance(value, list):
        for item in value:
            walk_tree(item, on_node)
        return
    if is_dataclass(value):
        on_node(value)
        for f in fields(value):
            if f.name in _POS_FIELDS:
                continue
            walk_tree(getattr(value, f.name), on_node)
        return
    # gia tri nguyen thuy (str/int/float/bool/None) -> khong co gi de duyet


def transform_tree(value: Any, visit: Callable[[Any], Tuple[bool, Any]]) -> Any:
    """Duyet VA co the bien doi cay AST bat dau tu `value`.

    `visit(node)` duoc goi cho moi dataclass node (pre-order) va phai tra
    ve 1 tuple `(handled, result)`:
        - handled=False: khong lam gi dac biet, transform_tree() se tu
          dong de quy vao cac field con cua node (hanh vi mac dinh).
        - handled=True: `result` THAY THE hoan toan node hien tai (co the
          la 1 node khac, hoac sentinel `DROP` de xoa khoi list cha).
          Luu y: neu `result` can duoc duyet tiep (vd thay TypeCastExpr
          bang chinh bieu thuc con cua no, ma bieu thuc con do co the lai
          chua 1 TypeCastExpr khac long nhau), `visit()` tu chiu trach
          nhiem goi lai `transform_tree(result, visit)` truoc khi tra ve.

    Danh sach (list) field duoc xu ly rieng: ket qua la 1 list moi, bat ky
    phan tu nao bi danh dau `DROP` se bi loai hoan toan (khong giu `None`
    thay the).
    """
    if isinstance(value, list):
        out = []
        for item in value:
            new_item = transform_tree(item, visit)
            if new_item is DROP:
                continue
            out.append(new_item)
        return out

    if is_dataclass(value):
        handled, result = visit(value)
        if handled:
            return result
        for f in fields(value):
            if f.name in _POS_FIELDS:
                continue
            setattr(value, f.name, transform_tree(getattr(value, f.name), visit))
        return value

    return value


__all__ = ["walk_tree", "transform_tree", "DROP"]

