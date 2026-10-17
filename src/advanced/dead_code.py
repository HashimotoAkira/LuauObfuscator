# -*- coding: utf-8 -*-
"""
dead_code.py
============
Bom rac logic: chen cac statement "vo hai" (khong thay doi ket qua chuong
trinh, khong bao gio gay loi runtime) xen giua statement that, de:
    - Danh lua decompiler/AI doc nguoc: lam phinh to kich thuoc, lam loang
      cac statement that trong 1 bien bien rac, de bep Context Window khi
      mot AI co gang tom tat/doc hieu toan bo ham.
    - KHONG lam thay doi hanh vi: moi bien rac deu la ten moi hoan toan
      (khong trung/shadow bien that), moi nhanh rac deu KHONG BAO GIO duoc
      thuc thi (dieu kien hang so luon False duoi nhieu lop ngu trang khac
      nhau) hoac neu CO thuc thi (khai bao bien rac) thi chi la phep toan
      so hoc/logic tren hang so - khong goi ham, khong truy cap bang/global
      nao -> tuyet doi khong the nem loi (vd index nil).
    - Chi phi runtime gan nhu bang 0: nhanh `if` rac khong bao gio vao than
      (CPU bo qua ngay sau phep so sanh), con khai bao bien rac chi la 1-2
      phep toan so hoc tren so nguyen nho -> phu hop rang buoc 60 FPS.

3 loai rac duoc sinh (tron ngau nhien theo trong so):
    1. Khai bao `local` rac voi gia tri tinh tu bieu thuc so hoc/logic
       long nhau tren hang so (co thuc thi, re tien).
    2. Nhanh `if <dieu kien hang False duoi nhieu dang>` chua vai dong
       khai bao rac (KHONG BAO GIO thuc thi).
    3. Khai bao ham rac (`local function _xx() ... end`) nhung khong bao
       gio goi (chi ton chi phi tao closure 1 lan, khong phai hot path).

An toan ngu phap: module nay KHONG BAO GIO chen rac ngay sau 1 statement
phan ky (return/break/goto/continue) trong cung 1 block, vi Lua bat buoc
cac statement do phai la statement CUOI CUNG cua block chua no - chen gi
them sau no se la loi cu phap "unreachable code" / vo hieu ngay lap tuc.

Module nay KHONG lam viec voi opaque predicate "chat" ve mat toan hoc (vd
bam bang thuc MBA) - do la viec cua `advanced/opaque_predicates.py` va
`advanced/mba.py` rieng; dead_code.py chi can du "trong vo hop le + khong
qua trivial de mot script regex don gian loc sach" la dat yeu cau ve khoi
luong noi dung can AI/decompiler phai doc qua.
"""

from __future__ import annotations

import random
from typing import List, Optional

from ..parser.nodes import (
    Block, Stmt, Expr,
    LocalAssignStmt, IfStmt, IfClause,
    NumberExpr, TrueExpr, FalseExpr, NameExpr,
    BinOpExpr, UnOpExpr, FunctionExpr, FunctionBody,
    ReturnStmt, BreakStmt, ContinueStmt, GotoStmt,
    WhileStmt, RepeatStmt, DoStmt, NumericForStmt, GenericForStmt,
    FunctionDeclStmt, LocalFunctionStmt,
)

# Statement sau day KHONG duoc chen rac ngay SAU no trong cung 1 block -
# chung phai la statement CUOI CUNG cua block chua no theo ngu phap Lua.
_DIVERGENT_STMTS = (ReturnStmt, BreakStmt, ContinueStmt, GotoStmt)

_JUNK_PREFIX = "_jx"  # danh dau noi bo (se bi xoa/doi lai o buoc minify/naming that)

# Chi +,-,* duoc dung de GHEP 2 bieu thuc SO con (xem _num_expr) - TUYET
# DOI khong tron voi gia tri boolean, vi Lua/Luau nem loi runtime that su
# ("attempt to perform arithmetic on a boolean value") khi +,-,*,% gap mot
# toan hang khong phai so. day la ly do _num_expr/_bool_expr duoc tach
# RIENG BIET thay vi dung chung 1 ham de quy tron lan nhu truoc.
_NUM_OPS = ["+", "-", "*"]
_CMP_OPS = ["==", "~=", "<", ">", "<=", ">="]  # chi so sanh SO voi SO


def _rand_name(rng: random.Random, tag: str) -> str:
    return f"{_JUNK_PREFIX}{tag}_{rng.randrange(0x1000, 0xFFFF):x}"


def _num_literal(rng: random.Random) -> Expr:
    v = rng.randint(-9999, 9999)
    return NumberExpr(raw=str(v), value=v)


def _num_expr(rng: random.Random, depth: int = 0) -> Expr:
    """Luon luon tra ve 1 bieu thuc the loai SO (khong bao gio la boolean
    o bat ky nhanh con nao) - an toan tuyet doi lam toan hang cho +,-,*,%.
    `%` duoc xu ly rieng: so chia LUON la 1 hang so sinh TRUC TIEP (khong
    qua de quy) va DAM BAO khac 0, vi Lua 5.3+/Luau nem loi runtime that
    su ("attempt to perform 'n%%0'") khi modulo cho 0 giua 2 so nguyen."""
    if depth >= 2 or rng.random() < 0.55:
        return _num_literal(rng)

    if rng.random() < 0.15:
        left = _num_expr(rng, depth + 1)
        divisor = rng.choice([d for d in range(-13, 14) if d != 0])
        return BinOpExpr(op="%", left=left, right=NumberExpr(raw=str(divisor), value=divisor))

    op = rng.choice(_NUM_OPS)
    left = _num_expr(rng, depth + 1)
    right = _num_expr(rng, depth + 1)
    return BinOpExpr(op=op, left=left, right=right)


def _bool_expr(rng: random.Random, depth: int = 0) -> Expr:
    """Luon luon tra ve 1 bieu thuc an toan ve kieu cho ngu canh boolean:
    hang true/false, hoac SO SANH 2 SO (==,~=,<,>,<=,>= giua 2 so luon hop
    le trong Lua, khong bao gio nem loi 'attempt to compare ...'), noi voi
    nhau bang and/or (and/or trong Lua nhan bat ky kieu gia tri nao lam
    toan hang ma khong bao gio loi, nen an toan de de quy)."""
    if depth >= 2 or rng.random() < 0.5:
        if rng.random() < 0.5:
            return TrueExpr() if rng.random() < 0.5 else FalseExpr()
        op = rng.choice(_CMP_OPS)
        return BinOpExpr(op=op, left=_num_expr(rng, 2), right=_num_expr(rng, 2))

    op = rng.choice(["and", "or"])
    left = _bool_expr(rng, depth + 1)
    right = _bool_expr(rng, depth + 1)
    return BinOpExpr(op=op, left=left, right=right)


def _junk_const_expr(rng: random.Random, depth: int = 0) -> Expr:
    """Sinh 1 bieu thuc rac TONG QUAT (so hoac boolean), luon well-typed
    trong Lua - tuyet doi khong the nem loi runtime (khong tron so voi
    boolean trong +,-,*,%; khong chia/modulo cho 0; khong NameExpr/Call
    nen cung khong co 'attempt to call/index a nil value')."""
    return _num_expr(rng, depth) if rng.random() < 0.6 else _bool_expr(rng, depth)


def _junk_local_decl(rng: random.Random, tag: str) -> LocalAssignStmt:
    name = _rand_name(rng, tag)
    return LocalAssignStmt(names=[name], type_infos=[None], values=[_junk_const_expr(rng)])


def _always_false_cond(rng: random.Random) -> Expr:
    """Mot dieu kien LUON LUON False, nguy trang duoi nhieu dang khac
    nhau de khong de bi 1 regex/AI nhan dien tat ca o cung 1 mau."""
    n1, n2 = rng.randint(1, 999), rng.randint(1, 999)
    if n1 == n2:
        n2 += 1
    choices = [
        BinOpExpr(op="==", left=NumberExpr(raw=str(n1), value=n1), right=NumberExpr(raw=str(n2), value=n2)),
        BinOpExpr(op="<", left=NumberExpr(raw=str(n2), value=n2), right=NumberExpr(raw=str(n2), value=n2)),
        BinOpExpr(op="and", left=FalseExpr(), right=_junk_const_expr(rng)),
        UnOpExpr(op="not", operand=TrueExpr()),
        BinOpExpr(op="~=", left=NumberExpr(raw=str(n1), value=n1), right=NumberExpr(raw=str(n1), value=n1)),
    ]
    return rng.choice(choices)


def _junk_dead_branch(rng: random.Random, tag: str, filler: int = 2) -> IfStmt:
    body = [_junk_local_decl(rng, f"{tag}b{i}") for i in range(filler)]
    return IfStmt(
        clauses=[IfClause(cond=_always_false_cond(rng), body=Block(statements=body))],
        else_body=None,
    )


def _junk_dead_function(rng: random.Random, tag: str) -> LocalAssignStmt:
    """Khai bao ham rac, khong bao gio duoc goi o bat ky dau. Than ham chi
    chua bien/so hang noi bo, khong tham chieu ra ngoai (khong upvalue that)
    -> khong the anh huong gi den chuong trinh that du co vo tinh bi goi."""
    name = _rand_name(rng, f"fn{tag}")
    inner = [_junk_local_decl(rng, f"{tag}i{i}") for i in range(rng.randint(1, 3))]
    fb = FunctionBody(params=[], is_vararg=False, body=Block(statements=inner), return_type=None)
    return LocalAssignStmt(names=[name], type_infos=[None], values=[FunctionExpr(body=fb)])


_GENERATORS = (_junk_local_decl, _junk_dead_branch, _junk_dead_function)
_WEIGHTS = (0.5, 0.35, 0.15)


def _make_junk(rng: random.Random, tag: str) -> Stmt:
    gen = rng.choices(_GENERATORS, weights=_WEIGHTS, k=1)[0]
    return gen(rng, tag)


def inject(
    block: Block,
    *,
    density: float = 0.3,
    seed: Optional[int] = None,
    max_injections: Optional[int] = None,
    deep: bool = False,
) -> Block:
    """Chen statement rac xen giua cac statement that cua `block`.

    density:         xac suat chen 1 cum rac TRUOC moi statement that (0..1).
    max_injections:   tran tong so lan chen (None = khong gioi han).
    deep:             True -> de quy chen rac vao ca than cac cau truc con
                       (if/while/for/repeat/do/function) truoc khi xu ly
                       block hien tai.
    """
    rng = random.Random(seed)
    stmts = list(block.statements)

    if deep:
        stmts = [_inject_sub_bodies(s, rng, density, max_injections) for s in stmts]

    out: List[Stmt] = []
    count = 0
    for s in stmts:
        if (max_injections is None or count < max_injections) and rng.random() < density:
            out.append(_make_junk(rng, str(count)))
            count += 1
        out.append(s)
        # Khong chen rac SAU 1 statement phan ky (return/break/goto/
        # continue): statement do bat buoc la statement cuoi cung cua
        # block chua no theo ngu phap Lua, nen ve mat cau truc list nay no
        # cung se la phan tu cuoi - khong con "statement ke tiep" nao de
        # chen truoc nua, vong for tu ket thuc dung dan.

    return Block(statements=out, line=block.line, col=block.col)


def _inject_sub_bodies(stmt: Stmt, rng: random.Random, density: float, max_injections: Optional[int]) -> Stmt:
    seed2 = rng.randrange(1 << 30)
    if isinstance(stmt, IfStmt):
        for clause in stmt.clauses:
            clause.body = inject(clause.body, density=density, seed=seed2, max_injections=max_injections, deep=True)
            seed2 = rng.randrange(1 << 30)
        if stmt.else_body is not None:
            stmt.else_body = inject(stmt.else_body, density=density, seed=seed2, max_injections=max_injections, deep=True)
    elif isinstance(stmt, (WhileStmt, RepeatStmt, DoStmt, NumericForStmt, GenericForStmt)):
        stmt.body = inject(stmt.body, density=density, seed=seed2, max_injections=max_injections, deep=True)
    elif isinstance(stmt, (FunctionDeclStmt, LocalFunctionStmt)):
        if stmt.body is not None:
            stmt.body.body = inject(stmt.body.body, density=density, seed=seed2, max_injections=max_injections, deep=True)
    return stmt


__all__ = ["inject"]