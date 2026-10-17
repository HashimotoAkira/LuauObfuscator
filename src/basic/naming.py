# -*- coding: utf-8 -*-
"""
src.basic.naming
=================
Doi ten bien DUA TREN AST (khong dung Regex) + Global-to-Local caching.

Chay SAU `basic.annotations.strip_type_annotations()` (de khong con
TypeCastExpr/TypeAliasStmt gay nhieu khi duyet cay).

================================================================
1. Nhung gi BI doi ten
================================================================
Chi nhung BINDING thuc su la local trong pham vi (scope) hien tai:
    - `local a, b = ...`                        (LocalAssignStmt.names)
    - `local function f(...) ... end`           (LocalFunctionStmt.name)
    - Tham so ham (bao gom `self` tu dong them cho method)
    - Bien vong lap: `for i = ...`, `for k, v in ...`

Moi THAM CHIEU (NameExpr) toi 1 trong cac binding tren, o bat ky dau
trong pham vi nhin thay no, deu duoc doi ten dong bo theo dung binding
ma no tro toi (xu ly bang scope resolution, khong phai text-matching).

================================================================
2. Nhung gi TUYET DOI KHONG doi ten
================================================================
    - Global "tu do" (khong resolve duoc toi binding local nao): vd
      `print`, `math`, `game`, `workspace`, `task`, hoac bat ky ten nao
      nguoi dung gan vao bien toan cuc (`foo = 1` o top-level khi `foo`
      chua tung duoc `local`).
    - Thuoc tinh dang `.field` (IndexExpr.field), tru field tren table local
      co nguon khai bao xac dinh va khong thoat khoi pham vi code.
    - Ten method trong `obj:method()` neu receiver khong phai table local
      da duoc xac minh.
    - Key dang ten trong table constructor chi duoc doi neu table duoc
      xac dinh la table local khong thoat ra ngoai.
    - `name_path` cua `function a.b:c()` (FunctionDeclStmt.name_path) -
      day co the la global/table-field, parser chi luu dang List[str]
      nen KHONG the resolve scope mot cach an toan; de bao toan ngu
      nghia, module nay CHU DONG BO QUA, chi doi ten PHAN BEN TRONG than
      ham (params/body).
    - Metamethod (`__index`, `__newindex`, `__namecall`, ...) - duoc bao
      ve TU DONG vi chung luon xuat hien duoi dang field/key (2 muc tren),
      khong bao gio la 1 NameExpr doc lap.

================================================================
3. Scope Shadowing (tai su dung ten)
================================================================
Dung 1 "ngan xep" cac tap ten dang duoc dung (`NameAllocator`): khi vao
1 scope moi, cac ten da cap cho scope TO HON (dang active tren duong di
tu goc) van bi coi la "dang ban", nhung khi scope ket thuc (vd het 1
nhanh if, het than 1 vong for), cac ten no da dung se duoc THA RA va co
the duoc 1 scope anh em (sibling) khac tai su dung. Day chinh la ky
thuat "scope shadowing / register-coloring" ma cac minifier that (vd
Terser/UglifyJS) dung, giup so luong ten ngan (_I, _l, _1...) khac nhau
trong toan bo output o muc thap nhat co the.

================================================================
4. Global-to-Local caching
================================================================
Vd: `math.floor` dung >= `min_cache_uses` lan trong ca chunk se duoc cache
thanh `local _x = math.floor` chen o DAU chunk, roi moi lan dung sau do
duoc thay bang `_x(...)`. Ap dung cho ca dang 2 cap `Lib.member` (voi
`Lib` nam trong danh sach thu vien duoc biet - xem LIBRARY_WHITELIST) lan
1 so global goi truc tiep thuong dung (xem BARE_CACHE_WHITELIST).

Thuat toan 2-pha, AN TOAN voi truong hop nguoi dung tu dat ten trung
voi thu vien chuan (vd `local math = require(...)`): o Pha A, moi local
that su DA duoc doi ten (thanh `_I`, `_l`...) truoc khi Pha B/C chay, nen
bat ky `NameExpr` nao con giu nguyen text goc (`"math"`, `"print"`...)
chac chan la THAM CHIEU GLOBAL THAT, khong the nham voi local bi che.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import secrets
from typing import Dict, List, Optional, Set, Tuple

from ..parser.lexer import KEYWORDS
from ..parser.nodes import (
    AssignStmt,
    Block,
    BreakStmt,
    CallExpr,
    CallStmt,
    CompoundAssignStmt,
    ContinueStmt,
    DoStmt,
    Expr,
    FunctionBody,
    FunctionDeclStmt,
    FunctionExpr,
    IfExpr,
    GenericForStmt,
    GotoStmt,
    IfStmt,
    IndexBracketExpr,
    IndexExpr,
    LabelStmt,
    LocalAssignStmt,
    LocalFunctionStmt,
    MethodCallExpr,
    NameExpr,
    NumericForStmt,
    Param,
    ParenExpr,
    RepeatStmt,
    ReturnStmt,
    Stmt,
    TableConstructorExpr,
    StringExpr,
    UnOpExpr,
    BinOpExpr,
    WhileStmt,
)
from ..parser.traversal import transform_tree, walk_tree

# ---------------------------------------------------------------------------
# Hang so cau hinh
# ---------------------------------------------------------------------------

#: Ten KHONG BAO GIO duoc dung lam ten bien da doi (tranh trung voi global
#: pho bien ma code co the dang dung o noi khac trong cung pham vi nhin
#: thay, hoac Instance/enum pho bien cua Roblox). Co the mo rong qua tham
#: so `protected_globals` cua `mangle()`.
KNOWN_GLOBALS: Set[str] = {
    "print", "math", "game", "workspace", "task", "string", "table",
    "os", "io", "coroutine", "bit32", "utf8", "debug",
    "pairs", "ipairs", "next", "select", "unpack", "type", "typeof",
    "tostring", "tonumber", "pcall", "xpcall", "error", "assert",
    "require", "rawget", "rawset", "rawequal", "rawlen",
    "setmetatable", "getmetatable", "loadstring", "load",
    "collectgarbage", "newproxy", "warn",
    "Instance", "Vector3", "Vector2", "CFrame", "Color3", "UDim2", "UDim",
    "Enum", "Ray", "Region3", "BrickColor", "TweenInfo", "Random",
    "DateTime", "NumberSequence", "ColorSequence", "NumberRange",
    "_G", "_VERSION", "shared", "script", "wait", "spawn", "delay", "tick",
}

#: Cac "thu vien" ma truy cap dang `Lib.member` du dieu kien de cache
#: thanh 1 local alias (`local _x = Lib.member`). Chi ap dung khi `Lib`
#: resolve ra la GLOBAL (khong phai bien local trung ten).
LIBRARY_WHITELIST: Set[str] = {
    "math", "string", "table", "os", "bit32", "utf8", "coroutine",
    "task", "debug",
}

#: Cac global goi/dung TRUC TIEP (khong qua `.`) du dieu kien de cache.
BARE_CACHE_WHITELIST: Set[str] = {
    "print", "pairs", "ipairs", "next", "select", "unpack", "type",
    "typeof", "tostring", "tonumber", "pcall", "xpcall", "error",
    "assert", "rawget", "rawset", "rawequal", "rawlen",
    "setmetatable", "getmetatable", "wait", "spawn", "delay", "tick", "warn",
}

_ALPHABET = "lIi1"
_MIN_NAME_LENGTH = 8
_MAX_NAME_LENGTH = 10


# ---------------------------------------------------------------------------
# NameAllocator: cap ten ngan theo kieu "ngan xep scope" (ho tro shadowing)
# ---------------------------------------------------------------------------

class NameAllocator:
    """Cap ten local dai 8-10 ky tu tu alphabet de nham lan `l`, `I`, `i`, `1`.

    Khi 1 scope ket thuc (`pop_scope`), cac ten no da dung duoc THA RA va
    co the duoc 1 scope khac (khong long nhau voi no) tai su dung - day
    la co che "Scope Shadowing" readme yeu cau.
    """

    def __init__(self, protected: Set[str], prefix: str = "_"):
        self._protected = protected
        self._prefix = prefix
        self._stack: List[Set[str]] = []

    def push_scope(self) -> None:
        self._stack.append(set())

    def pop_scope(self) -> None:
        self._stack.pop()

    def _reserved(self) -> Set[str]:
        reserved: Set[str] = set()
        for frame in self._stack:
            reserved |= frame
        return reserved

    def alloc(self) -> str:
        if not self._stack:
            raise RuntimeError("NameAllocator.alloc() goi khi chua co scope nao active")
        reserved = self._reserved()
        while True:
            length = secrets.randbelow(_MAX_NAME_LENGTH - _MIN_NAME_LENGTH + 1)
            length += _MIN_NAME_LENGTH
            candidate = self._prefix + "".join(
                secrets.choice(_ALPHABET) for _ in range(length)
            )
            if candidate in reserved or candidate in self._protected or candidate in KEYWORDS:
                continue
            self._stack[-1].add(candidate)
            return candidate


# ---------------------------------------------------------------------------
# Scope: cay lien ket cha-con, tra cuu binding theo ten GOC -> ten da doi
# ---------------------------------------------------------------------------

class Scope:
    __slots__ = ("parent", "bindings", "table_bindings")

    def __init__(self, parent: Optional["Scope"] = None):
        self.parent = parent
        self.bindings: Dict[str, str] = {}
        self.table_bindings: Dict[str, Optional[TableInfo]] = {}

    def declare(
        self,
        original_name: str,
        mangled_name: str,
        table_info: Optional[TableInfo] = None,
    ) -> None:
        self.bindings[original_name] = mangled_name
        self.table_bindings[original_name] = table_info

    def resolve(self, original_name: str) -> Optional[str]:
        scope: Optional[Scope] = self
        while scope is not None:
            if original_name in scope.bindings:
                return scope.bindings[original_name]
            scope = scope.parent
        return None

    def resolve_table_binding(
        self, original_name: str
    ) -> Optional[Tuple["Scope", Optional["TableInfo"]]]:
        scope: Optional[Scope] = self
        while scope is not None:
            if original_name in scope.bindings:
                return scope, scope.table_bindings.get(original_name)
            scope = scope.parent
        return None


@dataclass
class TableInfo:
    field_names: Dict[str, str] = field(default_factory=dict)
    children: Dict[str, "TableInfo"] = field(default_factory=dict)
    edits: List[Tuple[object, str, object]] = field(default_factory=list)
    unsafe: bool = False

    def mark_unsafe(self, seen: Optional[Set[int]] = None) -> None:
        seen = seen or set()
        if id(self) in seen:
            return
        seen.add(id(self))
        self.unsafe = True
        for child in self.children.values():
            child.mark_unsafe(seen)


@dataclass
class ManglingResult:
    block: Block
    renamed_count: int
    #: map hien thi (debug/bao cao): "ten_goc" -> "ten_da_doi" cho local
    #: cuoi cung tung duoc doi (co the bi ghi de neu nhieu binding khac
    #: scope trung ten goc - CHI de tham khao, khong dung lai de resolve).
    cached_globals: Dict[str, str]


# ---------------------------------------------------------------------------
# Renamer
# ---------------------------------------------------------------------------

class Renamer:
    def __init__(
        self,
        protected_globals: Optional[Set[str]],
        min_cache_uses: int,
        prefix: str,
        mangle_table_fields: bool = True,
    ):
        protected = set(KNOWN_GLOBALS)
        if protected_globals:
            protected |= set(protected_globals)
        self.allocator = NameAllocator(protected=protected, prefix=prefix)
        self.min_cache_uses = min_cache_uses
        self.rename_count = 0
        self.cache_alias_map: Dict[str, str] = {}
        self.bare_counts: Counter = Counter()
        self.lib_counts: Counter = Counter()
        self.table_infos: List[TableInfo] = []
        self.mangle_table_fields = mangle_table_fields
        self._global_alias_prefix = prefix + "G"
        self._global_alias_counter = 0

    def _alloc_global_alias_name(self) -> str:
        """Cap ten cho alias Global-to-Local caching (Pha B).

        QUAN TRONG: KHONG dung chung `self.allocator` (NameAllocator dang
        dung co che "tha ten khi scope dong" cho Pha A) - vi cac alias nay
        se duoc THAY the vao BEN TRONG nhieu ham/scope khac nhau o Pha C,
        trong khi cac scope do co the da `pop_scope()` tu lau va "tha ra"
        nhung ten da cap cho tham so/bien local cua chung (de scope anh
        em tai su dung - dung muc dich o Pha A). Neu Pha B lo cap trung 1
        ten vua duoc tha ra, ten do se bi DUNG 2 NGHIA KHAC NHAU trong
        CUNG 1 scope (vd tham so `x` doi thanh `_b`, roi alias cho
        `math.floor` cung bi cap trung `_b` -> shadow sai, vo nghia
        chuong trinh). De tranh HOAN TOAN truong hop nay, dung 1 namespace
        rieng biet (`_G0, _G1, ...`) khong bao gio trung voi dang
        `_I`, `_l`, `_1`, ...` ma `NameAllocator` sinh ra.
        """
        name = f"{self._global_alias_prefix}{self._global_alias_counter}"
        self._global_alias_counter += 1
        return name

    # -- Scope helpers ----------------------------------------------------

    def _child_scope(self, parent: Scope) -> Scope:
        self.allocator.push_scope()
        return Scope(parent=parent)

    def _declare(
        self, scope: Scope, name: str, table_info: Optional[TableInfo] = None
    ) -> str:
        mangled = self.allocator.alloc()
        scope.declare(name, mangled, table_info)
        self.rename_count += 1
        self.cache_alias_map[name] = mangled
        return mangled

    def _table_info_for(self, expr: Optional[Expr], scope: Scope) -> Optional[TableInfo]:
        if not self.mangle_table_fields:
            return None
        while isinstance(expr, ParenExpr):
            expr = expr.expr
        if isinstance(expr, NameExpr):
            binding = scope.resolve_table_binding(expr.name)
            return binding[1] if binding is not None else None
        if isinstance(expr, IndexExpr):
            parent = self._table_info_for(expr.obj, scope)
            if parent is not None:
                return parent.children.get(expr.field)
        if isinstance(expr, IndexBracketExpr):
            parent = self._table_info_for(expr.obj, scope)
            if parent is not None and isinstance(expr.key, StringExpr):
                return parent.children.get(expr.key.value)
        return None

    def _new_table_info(
        self, expr: Optional[Expr], scope: Scope
    ) -> Optional[TableInfo]:
        if not self.mangle_table_fields:
            return None
        while isinstance(expr, ParenExpr):
            expr = expr.expr
        if isinstance(expr, TableConstructorExpr):
            info = TableInfo()
            self.table_infos.append(info)
            for table_field in expr.fields:
                key = table_field.key
                if isinstance(key, str) and not table_field.is_computed_key:
                    field_name = key
                elif isinstance(key, StringExpr):
                    field_name = key.value
                else:
                    info.mark_unsafe()
                    child_info = self._new_table_info(table_field.value, scope)
                    if child_info is not None:
                        child_info.mark_unsafe()
                    continue
                mangled_field = self._mangled_table_field(info, field_name)
                if isinstance(key, str):
                    self._record_field_edit(
                        info, table_field, "key", mangled_field
                    )
                else:
                    self._record_field_edit(
                        info, key, "value", mangled_field
                    )
                    self._record_field_edit(info, key, "raw", "")
                child_info = self._new_table_info(table_field.value, scope)
                if child_info is not None:
                    info.children[field_name] = child_info
            return info
        return self._table_info_for(expr, scope)

    def _mangled_table_field(self, info: TableInfo, field_name: str) -> str:
        mangled = info.field_names.get(field_name)
        if mangled is None:
            mangled = self.allocator.alloc()
            info.field_names[field_name] = mangled
        return mangled

    def _record_field_edit(
        self, info: TableInfo, node: object, attribute: str, replacement: object
    ) -> None:
        info.edits.append((node, attribute, replacement))

    def _contains_function_expr(self, expr: Expr) -> bool:
        found = False

        def inspect(node: object) -> None:
            nonlocal found
            if isinstance(node, FunctionExpr):
                found = True

        walk_tree(expr, inspect)
        return found

    def _table_info_for_target(self, expr: Expr, scope: Scope) -> Optional[TableInfo]:
        if isinstance(expr, IndexExpr):
            return self._table_info_for(expr.obj, scope)
        if isinstance(expr, IndexBracketExpr):
            return self._table_info_for(expr.obj, scope)
        return None

    def _rename_table_field_access(self, expr: IndexExpr, info: TableInfo) -> None:
        original = expr.field
        renamed = self._mangled_table_field(info, original)
        self._record_field_edit(info, expr, "field", renamed)

    def _apply_table_field_names(self) -> None:
        for info in self.table_infos:
            if not info.unsafe:
                for node, attribute, value in info.edits:
                    if attribute.startswith("name_path["):
                        index = int(attribute[10:-1])
                        node.name_path[index] = value
                    else:
                        setattr(node, attribute, value)

    # -- Entry point: 1 Block tao scope con MOI -----------------------------

    def visit_block_new_scope(self, block: Block, parent_scope: Scope) -> None:
        child = self._child_scope(parent_scope)
        self.visit_block_in_scope(block, child)
        self.allocator.pop_scope()

    def visit_block_in_scope(self, block: Block, scope: Scope) -> None:
        for stmt in block.statements:
            self.visit_stmt(stmt, scope)

    # -- Statement dispatch -------------------------------------------------

    def visit_stmt(self, stmt: Stmt, scope: Scope) -> None:
        handler = _STMT_DISPATCH.get(type(stmt))
        if handler is not None:
            handler(self, stmt, scope)
        # Cac statement khong co trong bang (BreakStmt, ContinueStmt,
        # GotoStmt, LabelStmt) khong chua expr/ten bien nao can xu ly.

    def _h_local_assign(self, stmt: LocalAssignStmt, scope: Scope) -> None:
        # Danh gia ve phai TRUOC (dung scope hien tai, CHUA co ten moi),
        # dung ngu nghia Lua: `local x = x` tham chieu `x` ben ngoai.
        table_infos = [self._new_table_info(value, scope) for value in stmt.values]
        stmt.values = [self.visit_expr(v, scope) for v in stmt.values]
        original_names = stmt.names
        stmt.names = [
            self._declare(
                scope,
                name,
                table_infos[index] if index < len(table_infos) else None,
            )
            for index, name in enumerate(original_names)
        ]

    def _h_assign(self, stmt: AssignStmt, scope: Scope) -> None:
        table_infos = [self._new_table_info(value, scope) for value in stmt.values]
        values_contain_functions = [
            self._contains_function_expr(value) for value in stmt.values
        ]
        original_targets = stmt.targets
        target_bindings = [
            (
                target.name if isinstance(target, NameExpr) else None,
                scope.resolve_table_binding(target.name)
                if isinstance(target, NameExpr)
                else None,
                self._table_info_for_target(target, scope)
                if isinstance(target, (IndexExpr, IndexBracketExpr))
                else None,
                target.field
                if isinstance(target, IndexExpr)
                else (
                    target.key.value
                    if isinstance(target, IndexBracketExpr)
                    and isinstance(target.key, StringExpr)
                    else None
                ),
            )
            for target in original_targets
        ]
        stmt.values = [self.visit_expr(v, scope) for v in stmt.values]
        stmt.targets = [self.visit_expr(t, scope) for t in stmt.targets]
        for index, (original_name, binding, parent_info, field_name) in enumerate(
            target_bindings
        ):
            table_info = table_infos[index] if index < len(table_infos) else None
            value_contains_function = (
                values_contain_functions[index]
                if index < len(values_contain_functions)
                else False
            )
            if original_name is not None:
                if binding is None:
                    if table_info is not None:
                        table_info.mark_unsafe()
                    continue
                owner, old_info = binding
                if old_info is not None and old_info is not table_info:
                    old_info.mark_unsafe()
                    if table_info is not None:
                        table_info.mark_unsafe()
                if value_contains_function:
                    if old_info is not None:
                        old_info.mark_unsafe()
                    if table_info is not None:
                        table_info.mark_unsafe()
                if owner is not scope:
                    if old_info is not None:
                        old_info.mark_unsafe()
                    if table_info is not None:
                        table_info.mark_unsafe()
                    owner.table_bindings[original_name] = None
                else:
                    owner.table_bindings[original_name] = table_info
            else:
                if parent_info is not None:
                    old_child = (
                        parent_info.children.get(field_name)
                        if field_name is not None
                        else None
                    )
                    if value_contains_function or (
                        old_child is not None and table_info is not old_child
                    ):
                        parent_info.mark_unsafe()
                        if old_child is not None:
                            old_child.mark_unsafe()
                        if table_info is not None:
                            table_info.mark_unsafe()
                    if field_name is None:
                        parent_info.mark_unsafe()
                        if table_info is not None:
                            table_info.mark_unsafe()
                    elif table_info is None:
                        parent_info.children.pop(field_name, None)
                    else:
                        parent_info.children[field_name] = table_info

    def _h_compound_assign(self, stmt: CompoundAssignStmt, scope: Scope) -> None:
        original_name = (
            stmt.target.name if isinstance(stmt.target, NameExpr) else None
        )
        binding = (
            scope.resolve_table_binding(original_name)
            if original_name is not None
            else None
        )
        stmt.value = self.visit_expr(stmt.value, scope)
        stmt.target = self.visit_expr(stmt.target, scope)
        if original_name is not None and binding is not None and binding[1] is not None:
            binding[1].mark_unsafe()
            binding[0].table_bindings[original_name] = None

    def _h_call_stmt(self, stmt: CallStmt, scope: Scope) -> None:
        stmt.call = self.visit_expr(stmt.call, scope)

    def _h_do(self, stmt: DoStmt, scope: Scope) -> None:
        self.visit_block_new_scope(stmt.body, scope)

    def _h_while(self, stmt: WhileStmt, scope: Scope) -> None:
        stmt.cond = self.visit_expr(stmt.cond, scope)
        self.visit_block_new_scope(stmt.body, scope)

    def _h_repeat(self, stmt: RepeatStmt, scope: Scope) -> None:
        # Ngu nghia Lua: bien local khai bao trong body VAN con hieu luc
        # khi danh gia `cond` -> dung CHUNG 1 scope cho ca body lan cond.
        child = self._child_scope(scope)
        self.visit_block_in_scope(stmt.body, child)
        stmt.cond = self.visit_expr(stmt.cond, child)
        self.allocator.pop_scope()

    def _h_if(self, stmt: IfStmt, scope: Scope) -> None:
        for clause in stmt.clauses:
            clause.cond = self.visit_expr(clause.cond, scope)
            self.visit_block_new_scope(clause.body, scope)
        if stmt.else_body is not None:
            self.visit_block_new_scope(stmt.else_body, scope)

    def _h_numeric_for(self, stmt: NumericForStmt, scope: Scope) -> None:
        stmt.start = self.visit_expr(stmt.start, scope)
        stmt.stop = self.visit_expr(stmt.stop, scope)
        if stmt.step is not None:
            stmt.step = self.visit_expr(stmt.step, scope)
        child = self._child_scope(scope)
        stmt.var = self._declare(child, stmt.var)
        self.visit_block_in_scope(stmt.body, child)
        self.allocator.pop_scope()

    def _h_generic_for(self, stmt: GenericForStmt, scope: Scope) -> None:
        stmt.exprs = [self.visit_expr(e, scope) for e in stmt.exprs]
        child = self._child_scope(scope)
        stmt.names = [self._declare(child, n) for n in stmt.names]
        self.visit_block_in_scope(stmt.body, child)
        self.allocator.pop_scope()

    def _h_function_decl(self, stmt: FunctionDeclStmt, scope: Scope) -> None:
        # Neu name_path[0] la 1 bien local dang ton tai trong scope, phai
        # doi ten no thanh mangled name tuong ung.
        if stmt.name_path:
            original_root = stmt.name_path[0]
            resolved_root = scope.resolve(original_root)
            if resolved_root is not None:
                stmt.name_path[0] = resolved_root
                binding = scope.resolve_table_binding(original_root)
                current_info = binding[1] if binding is not None else None
                receiver_info = current_info
                if current_info is not None:
                    for field_index in range(1, len(stmt.name_path) - 1):
                        current_info = current_info.children.get(stmt.name_path[field_index])
                        if current_info is None:
                            break
                    if current_info is not None and len(stmt.name_path) > 1:
                        original_field = stmt.name_path[-1]
                        renamed_field = self._mangled_table_field(current_info, original_field)
                        self._record_field_edit(
                            current_info,
                            stmt,
                            f"name_path[{len(stmt.name_path) - 1}]",
                            renamed_field,
                        )
                        receiver_info = current_info
                else:
                    receiver_info = None
            else:
                receiver_info = None
        else:
            receiver_info = None
        self._visit_function_body(
            stmt.body,
            scope,
            is_method=stmt.is_method,
            method_table_info=receiver_info if stmt.is_method else None,
        )

    def _h_local_function(self, stmt: LocalFunctionStmt, scope: Scope) -> None:
        # Khai bao ten TRUOC khi vao than ham, de ho tro de quy
        # (`local function fact(n) return fact(n-1) end`).
        stmt.name = self._declare(scope, stmt.name)
        self._visit_function_body(stmt.body, scope)

    def _h_return(self, stmt: ReturnStmt, scope: Scope) -> None:
        for value in stmt.values:
            table_info = self._new_table_info(value, scope)
            if table_info is not None:
                table_info.mark_unsafe()
        stmt.values = [self.visit_expr(v, scope) for v in stmt.values]

    # Bang dispatch statement (gan sau khi tat ca method da dinh nghia).

    # -- Function body (params + than ham dung CHUNG 1 scope) --------------

    def _visit_function_body(
        self,
        body: FunctionBody,
        parent_scope: Scope,
        is_method: bool = False,
        method_table_info: Optional[TableInfo] = None,
    ) -> None:
        child = self._child_scope(parent_scope)
        for idx, param in enumerate(body.params):
            if param.is_vararg:
                continue  # '...' khong phai 1 ten bien, khong doi ten
            if is_method and idx == 0 and param.name == "self":
                # 'self' la receiver ngam dinh cua cu phap method ':' trong Lua.
                # Bat buoc giu nguyen de Lua bind dung self tai runtime.
                child.declare("self", "self", method_table_info)
                continue
            param.name = self._declare(child, param.name)
        self.visit_block_in_scope(body.body, child)
        self.allocator.pop_scope()

    # -- Expression dispatch --------------------------------------------

    def visit_expr(self, expr: Optional[Expr], scope: Scope) -> Optional[Expr]:
        if expr is None:
            return None
        handler = _EXPR_DISPATCH.get(type(expr))
        if handler is not None:
            return handler(self, expr, scope)
        return expr  # Nil/True/False/Vararg/Number/String: khong co gi de doi

    def _v_name(self, expr: NameExpr, scope: Scope) -> Expr:
        resolved = scope.resolve(expr.name)
        if resolved is not None:
            expr.name = resolved
        else:
            # Tham chieu global THAT (khong resolve duoc toi local nao).
            if expr.name in BARE_CACHE_WHITELIST:
                self.bare_counts[expr.name] += 1
        return expr

    def _v_index(self, expr: IndexExpr, scope: Scope) -> Expr:
        if isinstance(expr.obj, NameExpr) and scope.resolve(expr.obj.name) is None:
            if expr.obj.name in LIBRARY_WHITELIST:
                self.lib_counts[(expr.obj.name, expr.field)] += 1
                return expr  # KHONG di sau vao obj (de nguyen "math")
        table_info = self._table_info_for(expr.obj, scope)
        expr.obj = self.visit_expr(expr.obj, scope)
        if table_info is not None:
            self._rename_table_field_access(expr, table_info)
        return expr

    def _v_index_bracket(self, expr: IndexBracketExpr, scope: Scope) -> Expr:
        table_info = self._table_info_for(expr.obj, scope)
        expr.obj = self.visit_expr(expr.obj, scope)
        if table_info is not None:
            if isinstance(expr.key, StringExpr):
                original = expr.key.value
                renamed = self._mangled_table_field(table_info, original)
                self._record_field_edit(
                    table_info, expr.key, "value", renamed
                )
                self._record_field_edit(table_info, expr.key, "raw", "")
            else:
                table_info.mark_unsafe()
        expr.key = self.visit_expr(expr.key, scope)
        return expr

    def _v_call(self, expr: CallExpr, scope: Scope) -> Expr:
        expr.func = self.visit_expr(expr.func, scope)
        for arg in expr.args:
            table_info = self._new_table_info(arg, scope)
            if table_info is not None:
                table_info.mark_unsafe()
        expr.args = [self.visit_expr(a, scope) for a in expr.args]
        return expr

    def _v_method_call(self, expr: MethodCallExpr, scope: Scope) -> Expr:
        table_info = self._table_info_for(expr.obj, scope)
        expr.obj = self.visit_expr(expr.obj, scope)
        if table_info is not None:
            renamed = self._mangled_table_field(table_info, expr.method)
            self._record_field_edit(
                table_info, expr, "method", renamed
            )
        for arg in expr.args:
            arg_info = self._new_table_info(arg, scope)
            if arg_info is not None:
                arg_info.mark_unsafe()
        expr.args = [self.visit_expr(a, scope) for a in expr.args]
        return expr

    def _v_function_expr(self, expr: FunctionExpr, scope: Scope) -> Expr:
        self._visit_function_body(expr.body, scope)
        return expr

    def _v_table_constructor(self, expr: TableConstructorExpr, scope: Scope) -> Expr:
        for f in expr.fields:
            if f.is_computed_key:
                f.key = self.visit_expr(f.key, scope)
            # key dang ten (khong computed) la DU LIEU, khong doi ten.
            f.value = self.visit_expr(f.value, scope)
        return expr

    def _v_binop(self, expr: BinOpExpr, scope: Scope) -> Expr:
        expr.left = self.visit_expr(expr.left, scope)
        expr.right = self.visit_expr(expr.right, scope)
        return expr

    def _v_unop(self, expr: UnOpExpr, scope: Scope) -> Expr:
        expr.operand = self.visit_expr(expr.operand, scope)
        return expr

    def _v_paren(self, expr: ParenExpr, scope: Scope) -> Expr:
        expr.expr = self.visit_expr(expr.expr, scope)
        return expr

    def _v_if_expr(self, expr: IfExpr, scope: Scope) -> Expr:
        for clause in expr.clauses:
            clause.cond = self.visit_expr(clause.cond, scope)
            clause.value = self.visit_expr(clause.value, scope)
        expr.else_value = self.visit_expr(expr.else_value, scope)
        return expr

    # -- Pha B + C: Global-to-Local caching --------------------------------

    def apply_global_caching(self, block: Block) -> None:
        chosen_bare = sorted(
            name for name, cnt in self.bare_counts.items() if cnt >= self.min_cache_uses
        )
        chosen_lib = sorted(
            key for key, cnt in self.lib_counts.items() if cnt >= self.min_cache_uses
        )
        if not chosen_bare and not chosen_lib:
            return

        bare_alias: Dict[str, str] = {
            name: self._alloc_global_alias_name() for name in chosen_bare
        }
        lib_alias: Dict[Tuple[str, str], str] = {
            key: self._alloc_global_alias_name() for key in chosen_lib
        }

        # QUAN TRONG: thay the THAM CHIEU truoc, roi moi chen statement
        # khai bao alias vao sau - tranh bug tu tham chieu (`local _a = _a`)
        # neu khong statement khai bao alias moi chen cung se bi quet lai.
        def replace(node):
            if isinstance(node, NameExpr) and node.name in bare_alias:
                node.name = bare_alias[node.name]
                return True, node
            if isinstance(node, IndexExpr) and isinstance(node.obj, NameExpr):
                key = (node.obj.name, node.field)
                if key in lib_alias:
                    return True, NameExpr(
                        name=lib_alias[key], line=node.line, col=node.col
                    )
            return False, None

        transform_tree(block, replace)

        new_stmts: List[Stmt] = []
        for name in chosen_bare:
            alias = bare_alias[name]
            new_stmts.append(
                LocalAssignStmt(names=[alias], type_infos=[None], values=[NameExpr(name=name)])
            )
        for (lib, field) in chosen_lib:
            alias = lib_alias[(lib, field)]
            new_stmts.append(
                LocalAssignStmt(
                    names=[alias],
                    type_infos=[None],
                    values=[IndexExpr(obj=NameExpr(name=lib), field=field)],
                )
            )
        block.statements[0:0] = new_stmts

        for name, alias in bare_alias.items():
            self.cache_alias_map[name] = alias
        for (lib, field), alias in lib_alias.items():
            self.cache_alias_map[f"{lib}.{field}"] = alias


_STMT_DISPATCH = {
    LocalAssignStmt: Renamer._h_local_assign,
    AssignStmt: Renamer._h_assign,
    CompoundAssignStmt: Renamer._h_compound_assign,
    CallStmt: Renamer._h_call_stmt,
    DoStmt: Renamer._h_do,
    WhileStmt: Renamer._h_while,
    RepeatStmt: Renamer._h_repeat,
    IfStmt: Renamer._h_if,
    NumericForStmt: Renamer._h_numeric_for,
    GenericForStmt: Renamer._h_generic_for,
    FunctionDeclStmt: Renamer._h_function_decl,
    LocalFunctionStmt: Renamer._h_local_function,
    ReturnStmt: Renamer._h_return,
    # BreakStmt, ContinueStmt, GotoStmt, LabelStmt: khong co gi de doi ten.
}

_EXPR_DISPATCH = {
    NameExpr: Renamer._v_name,
    IndexExpr: Renamer._v_index,
    IndexBracketExpr: Renamer._v_index_bracket,
    CallExpr: Renamer._v_call,
    MethodCallExpr: Renamer._v_method_call,
    FunctionExpr: Renamer._v_function_expr,
    TableConstructorExpr: Renamer._v_table_constructor,
    BinOpExpr: Renamer._v_binop,
    UnOpExpr: Renamer._v_unop,
    ParenExpr: Renamer._v_paren,
    IfExpr: Renamer._v_if_expr,
}


def mangle(
    block: Block,
    *,
    protected_globals: Optional[Set[str]] = None,
    enable_global_caching: bool = True,
    min_cache_uses: int = 2,
    prefix: str = "_",
    mangle_table_fields: bool = True,
) -> ManglingResult:
    """Doi ten toan bo bien local trong `block` (AST da qua
    `strip_type_annotations`) va (tuy chon) ap dung Global-to-Local
    caching. Mutate AST in-place; tra ve `ManglingResult` de bao cao.
    """
    renamer = Renamer(
        protected_globals,
        min_cache_uses if enable_global_caching else 0,
        prefix,
        mangle_table_fields=mangle_table_fields,
    )
    root_scope = Scope(parent=None)
    renamer.allocator.push_scope()

    renamer.visit_block_in_scope(block, root_scope)

    if enable_global_caching:
        renamer.apply_global_caching(block)

    renamer._apply_table_field_names()
    renamer.allocator.pop_scope()

    return ManglingResult(
        block=block,
        renamed_count=renamer.rename_count,
        cached_globals=renamer.cache_alias_map,
    )


__all__ = ["mangle", "ManglingResult", "NameAllocator", "Scope", "KNOWN_GLOBALS"]