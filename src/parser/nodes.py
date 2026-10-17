# -*- coding: utf-8 -*-
"""
nodes.py
========
Dinh nghia cac class AST (Abstract Syntax Tree) dung cho Lua/Luau.

Quy uoc chung:
- Moi node ke thua tu `Node`, co `line` va `col` de phuc vu debug/error
  va de cac tang sau (naming, cff, emitter...) co the map nguoc ve code goc.
- Cac node duoc viet bang dataclass cho gon, __repr__ tu sinh de de debug.
- Node KHONG tu validate ngu nghia (vd: kiem tra bien da khai bao chua) -
  day la AST "cu" (syntax tree), viec resolve scope se do tang `basic/naming.py`
  dam nhiem o buoc sau.

Nhom node:
    1. Co so:        Node, Chunk/Block
    2. Statement:     *Stmt
    3. Expression:    *Expr
    4. Phu tro:       Param, TableField, FunctionBody, TypeInfo, Clause...
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union


# ---------------------------------------------------------------------------
# 1. Node co so
# ---------------------------------------------------------------------------

@dataclass
class Node:
    """Lop goc cho moi node AST."""
    line: int = field(default=0, repr=False, compare=False)
    col: int = field(default=0, repr=False, compare=False)


@dataclass
class Block(Node):
    """Mot khoi lenh: danh sach cac statement tuan tu.

    Dung lam than cua: chunk goc, than ham, than if/while/for/do...
    """
    statements: List["Stmt"] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Block({len(self.statements)} stmts)"


# Chunk = Block goc cua ca file nguon (alias de code goi ro nghia hon)
Chunk = Block


# ---------------------------------------------------------------------------
# 2. Phu tro: type annotation, parameter, table field, function body
# ---------------------------------------------------------------------------

@dataclass
class TypeInfo(Node):
    """Luu annotation kieu Luau dang 'tho' (raw token text), KHONG parse sau
    vao cay cu phap rieng vi he thong type cua Luau rat phuc tap (union,
    intersection, generic, function type, table type long nhau...).

    Vi tang `basic/annotations.py` se lot sach cac annotation nay o buoc
    tien xu ly, parser chi can:
        - Nhan dien dung vi tri type bat dau/ket thuc (de khong vo cu phap)
        - Giu lai text tho (raw) phong truong hop can debug / giu nguyen

    Thuoc tinh:
        raw: chuoi text goc cua type annotation (khong bao gom dau ':')
        is_cast: True neu day la '::' (type cast) thay vi ':' (type annotation)
    """
    raw: str = ""
    is_cast: bool = False

    def __repr__(self) -> str:
        marker = "::" if self.is_cast else ":"
        return f"TypeInfo({marker} {self.raw!r})"


@dataclass
class Param(Node):
    """Tham so trong khai bao ham: ten + type annotation (neu co)."""
    name: str = ""
    type_info: Optional[TypeInfo] = None
    is_vararg: bool = False  # True neu param nay la '...'

    def __repr__(self) -> str:
        if self.is_vararg:
            return "Param(...)"
        t = f": {self.type_info.raw}" if self.type_info else ""
        return f"Param({self.name}{t})"


@dataclass
class FunctionBody(Node):
    """Phan than dung chung cho function expr / function stmt / method.

    params:        danh sach Param (co the rong)
    is_vararg:      True neu than ham co '...' o cuoi danh sach param
    body:           Block chua cac statement ben trong ham
    return_type:    TypeInfo cho kieu tra ve (vd: 'function f(): number')
    """
    params: List[Param] = field(default_factory=list)
    is_vararg: bool = False
    body: Block = field(default_factory=Block)
    return_type: Optional[TypeInfo] = None

    def __repr__(self) -> str:
        return f"FunctionBody({len(self.params)} params, vararg={self.is_vararg})"


# ---- Table constructor fields ---------------------------------------------

@dataclass
class TableField(Node):
    """Mot phan tu trong table constructor `{ ... }`.

    Co 3 dang theo chuan Lua:
        1. Positional:  { expr, expr, ... }        -> key=None
        2. Named:       { name = expr, ... }        -> key=Name (string)
        3. Keyed/computed: { [expr] = expr, ... }    -> key=Expr bat ky
    """
    key: Optional[Union[str, "Expr"]] = None
    value: "Expr" = None  # type: ignore[assignment]
    is_computed_key: bool = False  # True neu key duoc viet dang [expr]

    def __repr__(self) -> str:
        if self.key is None:
            return f"TableField(pos, {self.value!r})"
        if self.is_computed_key:
            return f"TableField([{self.key!r}], {self.value!r})"
        return f"TableField({self.key}={self.value!r})"


# ---------------------------------------------------------------------------
# 3. Expression nodes
# ---------------------------------------------------------------------------

@dataclass
class Expr(Node):
    """Lop goc cho moi expression. Khong dung truc tiep."""
    pass


@dataclass
class NilExpr(Expr):
    def __repr__(self) -> str:
        return "Nil"


@dataclass
class TrueExpr(Expr):
    def __repr__(self) -> str:
        return "True"


@dataclass
class FalseExpr(Expr):
    def __repr__(self) -> str:
        return "False"


@dataclass
class VarargExpr(Expr):
    """Bieu thuc '...' (chi hop le ben trong ham vararg)."""
    def __repr__(self) -> str:
        return "Vararg(...)"


@dataclass
class NumberExpr(Expr):
    # `raw` giu nguyen van text goc (vd '0x1F', '1e10', '1_000') de khong
    # mat thong tin bieu dien khi emit lai code; `value` la gia tri float/int
    # da parse, dung cho cac phep toi uu hoa (constant folding) o tang sau.
    raw: str = ""
    value: Union[int, float] = 0

    def __repr__(self) -> str:
        return f"Number({self.raw})"


@dataclass
class StringExpr(Expr):
    # `value` la chuoi da giai ma escape (\n, \t, \xXX, \u{...}, \z, ...)
    # `raw` la token nguyen goc bao gom dau nhay/ngoac dai, phuc vu emitter
    # minify co the tai su dung neu khong can doi dang.
    value: str = ""
    raw: str = ""
    is_long_string: bool = False  # True neu viet dang [[ ... ]] / [=[ ]=]

    def __repr__(self) -> str:
        preview = self.value if len(self.value) <= 20 else self.value[:17] + "..."
        return f"String({preview!r})"


@dataclass
class NameExpr(Expr):
    """Tham chieu toi 1 identifier: bien local, upvalue, hoac global."""
    name: str = ""

    def __repr__(self) -> str:
        return f"Name({self.name})"


@dataclass
class IndexExpr(Expr):
    """Truy cap thuoc tinh dang `obj.field` (dot-index, chi la duong cu
    phap cua obj['field'] nhung giu rieng de de nhan dien & khong doi ten
    thuoc tinh engine nhu `.Position`, `.Name`...).
    """
    obj: Expr = None  # type: ignore[assignment]
    field: str = ""

    def __repr__(self) -> str:
        return f"Index({self.obj!r}.{self.field})"


@dataclass
class IndexBracketExpr(Expr):
    """Truy cap dang `obj[expr]` (bracket-index, key la 1 expression bat ky)."""
    obj: Expr = None  # type: ignore[assignment]
    key: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"IndexBracket({self.obj!r}[{self.key!r}])"


@dataclass
class CallExpr(Expr):
    """Goi ham thuong: `f(args)`."""
    func: Expr = None  # type: ignore[assignment]
    args: List[Expr] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Call({self.func!r}, {len(self.args)} args)"


@dataclass
class MethodCallExpr(Expr):
    """Goi method dang `obj:method(args)` (duong cu phap tu dong truyen self).

    Giu rieng voi CallExpr vi tang `naming.py` TUYET DOI KHONG duoc doi ten
    `method` (co the la __namecall / API engine nhu `:Destroy()`), trong khi
    `obj` van co the bi doi ten neu la bien local.
    """
    obj: Expr = None  # type: ignore[assignment]
    method: str = ""
    args: List[Expr] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"MethodCall({self.obj!r}:{self.method}, {len(self.args)} args)"


@dataclass
class FunctionExpr(Expr):
    """Ham dang anonymous expression: `function(...) ... end`."""
    body: FunctionBody = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"FunctionExpr({self.body!r})"


@dataclass
class TableConstructorExpr(Expr):
    """Bieu thuc table: `{ ... }`."""
    fields: List[TableField] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"TableConstructor({len(self.fields)} fields)"


@dataclass
class BinOpExpr(Expr):
    """Phep toan 2 ngoi: + - * / // % ^ .. == ~= < > <= >= and or."""
    op: str = ""
    left: Expr = None  # type: ignore[assignment]
    right: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"BinOp({self.left!r} {self.op} {self.right!r})"


@dataclass
class UnOpExpr(Expr):
    """Phep toan 1 ngoi: - (negate), not, # (length), ~ (Luau: khong co
    bitwise unary rieng, nhung giu cho tuong lai / dialect khac)."""
    op: str = ""
    operand: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"UnOp({self.op} {self.operand!r})"


@dataclass
class ParenExpr(Expr):
    """Bieu thuc trong ngoac `( expr )`.

    Can giu rieng (khong "bop" thanh expr ben trong) vi ngoac co y nghia
    ngu nghia trong Lua: ep 1 loi goi ham / vararg tra ve nhieu gia tri
    xuong con 1 gia tri duy nhat. Emitter sau nay phai in lai dau ngoac.
    """
    expr: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"Paren({self.expr!r})"


@dataclass
class TypeCastExpr(Expr):
    """Bieu thuc ep kieu Luau: `expr :: Type`.

    Chi giu lai o muc syntax (type duoc luu dang TypeInfo.raw tho), vi
    annotations.py se can node nay de lot bo hoan toan khi tien xu ly.
    """
    expr: Expr = None  # type: ignore[assignment]
    type_info: TypeInfo = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"TypeCast({self.expr!r} :: {self.type_info.raw})"


@dataclass
class IfExprClause(Node):
    """Một nhánh `if/elseif` trong biểu thức điều kiện Luau."""
    cond: Expr = None  # type: ignore[assignment]
    value: Expr = None  # type: ignore[assignment]


@dataclass
class IfExpr(Expr):
    """Biểu thức điều kiện Luau: `if cond then value else value`."""
    clauses: List[IfExprClause] = field(default_factory=list)
    else_value: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"IfExpr({len(self.clauses)} clauses)"


# ---------------------------------------------------------------------------
# 4. Statement nodes
# ---------------------------------------------------------------------------

@dataclass
class Stmt(Node):
    """Lop goc cho moi statement. Khong dung truc tiep."""
    pass


@dataclass
class LocalAssignStmt(Stmt):
    """`local a, b: number = expr1, expr2`

    names:      danh sach ten bien duoc khai bao
    type_infos: danh sach TypeInfo tuong ung (None neu khong co annotation),
                cung do dai voi `names`
    values:     danh sach expression ve phai (co the it hon so luong names,
                hoac rong neu chi khai bao khong gan gia tri)
    """
    names: List[str] = field(default_factory=list)
    type_infos: List[Optional[TypeInfo]] = field(default_factory=list)
    values: List[Expr] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"LocalAssign({self.names} = {self.values})"


@dataclass
class AssignStmt(Stmt):
    """Gan gia tri cho bien/thuoc tinh da ton tai: `a, b.c, d[e] = v1, v2`."""
    targets: List[Expr] = field(default_factory=list)  # NameExpr/IndexExpr/IndexBracketExpr
    values: List[Expr] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Assign({self.targets} = {self.values})"


@dataclass
class CompoundAssignStmt(Stmt):
    """Phep gan rut gon kieu Luau: `a += 1`, `b ..= "x"`, v.v.

    Chi co DUY NHAT 1 target va 1 value (khac voi AssignStmt co the da gia tri).
    op la toan tu goc, khong bao gom dau '='. Vd: '+', '-', '*', '/', '//',
    '%', '^', '..'.
    """
    target: Expr = None  # type: ignore[assignment]
    op: str = ""
    value: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"CompoundAssign({self.target!r} {self.op}= {self.value!r})"


@dataclass
class CallStmt(Stmt):
    """Statement chi la 1 loi goi ham/method dung mot minh: `f(x)` hoac `o:m()`."""
    call: Expr = None  # type: ignore[assignment]  # CallExpr hoac MethodCallExpr

    def __repr__(self) -> str:
        return f"CallStmt({self.call!r})"


@dataclass
class DoStmt(Stmt):
    """`do ... end` - khoi lenh co scope rieng, khong dieu kien."""
    body: Block = field(default_factory=Block)

    def __repr__(self) -> str:
        return f"Do({self.body!r})"


@dataclass
class WhileStmt(Stmt):
    """`while cond do ... end`."""
    cond: Expr = None  # type: ignore[assignment]
    body: Block = field(default_factory=Block)

    def __repr__(self) -> str:
        return f"While({self.cond!r})"


@dataclass
class RepeatStmt(Stmt):
    """`repeat ... until cond`.

    Luu y ngu nghia Lua: bien local khai bao trong `body` VAN con trong
    scope khi danh gia `cond` (khac voi while). Tang naming.py phai ton
    trong dieu nay khi mangling.
    """
    body: Block = field(default_factory=Block)
    cond: Expr = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"Repeat(until {self.cond!r})"


@dataclass
class IfClause(Node):
    """1 nhanh trong chuoi if/elseif (dung noi bo trong IfStmt)."""
    cond: Expr = None  # type: ignore[assignment]
    body: Block = field(default_factory=Block)


@dataclass
class IfStmt(Stmt):
    """`if c1 then b1 elseif c2 then b2 ... else bn end`.

    clauses:   danh sach IfClause cho if + cac elseif (theo thu tu)
    else_body: Block cho nhanh else (None neu khong co)
    """
    clauses: List[IfClause] = field(default_factory=list)
    else_body: Optional[Block] = None

    def __repr__(self) -> str:
        return f"If({len(self.clauses)} clauses, else={self.else_body is not None})"


@dataclass
class NumericForStmt(Stmt):
    """`for i = start, stop, step do ... end` (step co the None = mac dinh 1)."""
    var: str = ""
    start: Expr = None  # type: ignore[assignment]
    stop: Expr = None  # type: ignore[assignment]
    step: Optional[Expr] = None
    body: Block = field(default_factory=Block)

    def __repr__(self) -> str:
        return f"NumericFor({self.var}={self.start!r},{self.stop!r},{self.step!r})"


@dataclass
class GenericForStmt(Stmt):
    """`for k, v in iter_exprs do ... end` (vd: `pairs(t)`, `ipairs(t)`)."""
    names: List[str] = field(default_factory=list)
    exprs: List[Expr] = field(default_factory=list)
    body: Block = field(default_factory=Block)

    def __repr__(self) -> str:
        return f"GenericFor({self.names} in {self.exprs})"


@dataclass
class FunctionDeclStmt(Stmt):
    """Khai bao ham dang statement: `function name.a.b:c(...) ... end`.

    name_path:  danh sach cac phan ten noi boi dau cham, vd ['a', 'b']
                cho `function a.b:c()` thi name_path = ['a', 'b'], method='c'
    is_method:  True neu khai bao dang `:` (tu dong them tham so self)
    """
    name_path: List[str] = field(default_factory=list)
    is_method: bool = False
    body: FunctionBody = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        sep = ":" if self.is_method else "."
        path = ".".join(self.name_path[:-1])
        tail = self.name_path[-1] if self.name_path else ""
        full = f"{path}{sep}{tail}" if path else tail
        return f"FunctionDecl({full})"


@dataclass
class LocalFunctionStmt(Stmt):
    """`local function name(...) ... end`.

    Khac voi `local name = function...` o cho ten ham duoc dua vao scope
    TRUOC khi parse than ham, cho phep de quy goi chinh no.
    """
    name: str = ""
    body: FunctionBody = None  # type: ignore[assignment]

    def __repr__(self) -> str:
        return f"LocalFunction({self.name})"


@dataclass
class ReturnStmt(Stmt):
    """`return expr1, expr2, ...` (co the rong)."""
    values: List[Expr] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"Return({self.values})"


@dataclass
class BreakStmt(Stmt):
    def __repr__(self) -> str:
        return "Break"


@dataclass
class ContinueStmt(Stmt):
    """`continue` - tien ich Luau (khong co trong Lua 5.1 chuan).

    Ve mat parser day khong phai tu khoa dung o moi vi tri (chi hop le
    trong vong lap), nhung parser se chap nhan no nhu 1 identifier dac biet
    va emit ContinueStmt; viec kiem tra ngu canh (phai nam trong loop) de
    cho buoc phan tich ngu nghia sau lam, giu parser don gian & "permissive".
    """
    def __repr__(self) -> str:
        return "Continue"


@dataclass
class GotoStmt(Stmt):
    """`goto label`."""
    label: str = ""

    def __repr__(self) -> str:
        return f"Goto({self.label})"


@dataclass
class LabelStmt(Stmt):
    """`::label::`."""
    name: str = ""

    def __repr__(self) -> str:
        return f"Label({self.name})"


@dataclass
class TypeAliasStmt(Stmt):
    """`type Name = <type...>` hoac `export type Name<T> = <type...>`
    (khai bao alias kieu cua Luau). Luu raw text vi khong parse sau he
    thong type; node nay ton tai chu yeu de annotations.py co the xoa
    nguyen ca statement khi lam sach code truoc khi obfuscate.
    """
    name: str = ""
    is_exported: bool = False
    raw: str = ""

    def __repr__(self) -> str:
        pre = "export " if self.is_exported else ""
        return f"TypeAlias({pre}type {self.name} = ...)"


__all__ = [
    "Node", "Block", "Chunk",
    "TypeInfo", "Param", "FunctionBody", "TableField",
    "Expr", "NilExpr", "TrueExpr", "FalseExpr", "VarargExpr",
    "NumberExpr", "StringExpr", "NameExpr",
    "IndexExpr", "IndexBracketExpr", "CallExpr", "MethodCallExpr",
    "FunctionExpr", "TableConstructorExpr", "BinOpExpr", "UnOpExpr",
    "ParenExpr", "TypeCastExpr",
    "Stmt", "LocalAssignStmt", "AssignStmt", "CompoundAssignStmt",
    "CallStmt", "DoStmt", "WhileStmt", "RepeatStmt",
    "IfClause", "IfStmt", "NumericForStmt", "GenericForStmt",
    "FunctionDeclStmt", "LocalFunctionStmt", "ReturnStmt",
    "BreakStmt", "ContinueStmt", "GotoStmt", "LabelStmt", "TypeAliasStmt",
]