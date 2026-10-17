# -*- coding: utf-8 -*-
"""
cff.py
======
Control Flow Flattening (CFF) o muc AST.

Y tuong: 1 Block tuan tu (danh sach statement chay thang tu tren xuong)
duoc bam thanh nhieu "basic block" (3-7 statement khong re nhanh / 1
statement neu la cau truc dieu khien), gan cho moi block 1 ma trang thai
(state id) NGAU NHIEN KHONG TUAN TU, roi boc lai trong 1 vong lap:

    local _st = <id block dau tien>
    while _st ~= EXIT do
        if _st < pivot1 then
            if _st < pivot2 then
                <block A>
            else
                <block B>
            end
        else
            ...
        end
    end

Cay dieu phoi la CAY TIM KIEM NHI PHAN CHI DUNG TOAN TU '<' (khong dung
if/elseif phang, khong dung bang handlers[state]()) -> do phuc tap doc
O(log2 N) thay vi O(N), va thu tu VAT LY cua cac block (sap theo id de
xay cay) KHONG con lien quan gi den thu tu LOGIC thuc thi (id duoc shuffle
ngau nhien) -> decompiler/AI doc code khong the suy nguoc luong chay goc
tu thu tu xuat hien cua block trong source.

Gioi han co y (giu scope module gon, du dung trong pipeline that):
    - Chi flatten 1 CAP Block (danh sach statement truc tiep cua no).
      Cac statement co than rieng (if/while/for/function/do/repeat) duoc
      coi la "opaque" - giu nguyen, KHONG tu dong de quy vao ben trong.
      Muon flatten nhieu tang, goi flatten() cho than cua chung TRUOC,
      roi moi flatten() cho block cha (deep-first), xem ham `flatten_deep`.
    - Local khai bao truc tiep trong block duoc "hoisted": chuyen thanh
      khai bao `local` tap trung truoc vong lap (gia tri = nil), ben trong
      cac block chi con la phep GAN thuong - bat buoc vi 1 bien local Lua
      se bi tao lai (mat gia tri) neu nam trong than vong lap `while`.
    - return/break/goto/continue luon la statement CUOI cua block chua no
      (dung ngu phap Lua) nen KHONG duoc noi them lenh gan trang thai ke
      tiep sau no - CFF nhan dien va bo qua buoc noi nay cho dung cu phap.

CANH BAO NGU NGHIA quan trong (ly do co phan "relay break/continue" o
duoi day): boc 1 block la than cua vong lap goc (vd than cua `for`/`while`)
vao trong 1 vong lap `while` TONG HOP MOI se khien `break`/`continue` nam
ben trong (du o bat ky do sau nao qua if/do, MIEN LA khong bi chan boi 1
vong lap con khac) bi **doi tuong dich chuyen nham** sang vong lap tong hop
do thay vi vong lap goc - vi Lua luon cho `break`/`continue` nham den vong
lap GAN NHAT ve mat tu vung. Neu khong xu ly, hau qua la:
    - `break` chi thoat vong lap dieu phoi (1 vong "tick" gia) thay vi thoat
      han vong lap goc -> SAI logic (lang le chay sai, rat kho phat hien).
    - `continue` roi vao vong lap dieu phoi ma KHONG duoc cap nhat lai bien
      trang thai -> dieu kien `while` van dung -> LAP VO HAN that su.
Ham `flatten()` vi vay LUON quet va "relay" moi break/continue tim thay
(xem `_rewrite_break_continue_in_list`) truoc khi dung chung de xay basic
block, bat ke block dang flatten co phai la than vong lap hay khong - vi
voi flatten_deep (de quy nhieu tang), 1 vong lap tong hop long hon CUNG co
the vo tinh "chen" giua break/continue va vong lap goc cua no.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from ..parser.nodes import (
    Block, Stmt, Expr,
    LocalAssignStmt, AssignStmt, CompoundAssignStmt, CallStmt,
    DoStmt, WhileStmt, RepeatStmt, IfStmt, IfClause, NumericForStmt,
    GenericForStmt, FunctionDeclStmt, LocalFunctionStmt, ReturnStmt,
    BreakStmt, ContinueStmt, GotoStmt, LabelStmt, TypeAliasStmt,
    NameExpr, NumberExpr, BinOpExpr, FunctionExpr, TrueExpr, FalseExpr,
)


class CFFError(Exception):
    pass


# Statement nao bi coi la "cau truc / re nhanh" -> luon tach rieng thanh
# 1 basic block cua chinh no, khong duoc gop chung voi statement ben canh.
_STRUCTURAL_STMTS = (
    IfStmt, WhileStmt, RepeatStmt, NumericForStmt, GenericForStmt,
    DoStmt, FunctionDeclStmt, LocalFunctionStmt, LabelStmt,
)

# Statement "phan ky" - chuyen quyen dieu khien ra khoi dong chay tuyen
# tinh binh thuong (khong duoc noi them lenh gan trang thai ke tiep sau no,
# va theo ngu phap Lua day BAT BUOC phai la statement cuoi cung cua block
# chua no).
_DIVERGENT_STMTS = (ReturnStmt, BreakStmt, ContinueStmt, GotoStmt)

# Cau truc KHONG tao scope vong lap moi (if/do) - can di xuyen qua khi tim
# break/continue thuoc ve vong lap ben ngoai (xem _rewrite_break_continue).
_TRANSPARENT_STMTS = (IfStmt, DoStmt)

# Cau truc TAO vong lap rieng - break/continue ben trong (truoc khi bi
# chinh vong lap do tu xu ly, neu co) thuoc ve NO, khong duoc dung lai
# o day.
_LOOP_STMTS = (WhileStmt, RepeatStmt, NumericForStmt, GenericForStmt)

_MIN_GROUP = 3
_MAX_GROUP = 7

# id trang thai danh rieng cho "thoat vong lap dieu phoi" - chon so am de
# khong bao gio trung voi id block (id block luon duong, xem _gen_state_ids).
_EXIT_STATE = -1


def _is_yield_call(stmt: Stmt) -> bool:
    """True neu stmt la loi goi task.wait(...)/wait(...)/coroutine.yield(...)
    - theo dac ta, loai nay BAT BUOC phai nam CUOI 1 basic block (khong
    duoc cat o giua), vi no co the lam ham tam dung/nhuong quyen thuc thi
    giua chung 1 nhom lenh dang gop."""
    if not isinstance(stmt, CallStmt):
        return False
    func = getattr(stmt.call, "func", None)
    if func is None:
        return False
    if isinstance(func, NameExpr) and func.name == "wait":
        return True
    obj = getattr(func, "obj", None)
    fld = getattr(func, "field", None)
    if obj is not None and fld is not None:
        base = getattr(obj, "name", None)
        if base in ("task", "coroutine") and fld in ("wait", "yield"):
            return True
    return False


# ---------------------------------------------------------------------------
# Relay break/continue (xem canh bao ngu nghia o dau file)
# ---------------------------------------------------------------------------

def _contains_break_and_continue(stmts: List[Stmt]) -> Tuple[bool, bool]:
    """Quet (khong de quy vao vong lap/ham con) xem co break hoac continue nao
    'thuoc ve' pham vi vong lap NGOAI block dang xet hay khong."""
    has_break = False
    has_cont = False
    for s in stmts:
        if isinstance(s, BreakStmt):
            has_break = True
        elif isinstance(s, ContinueStmt):
            has_cont = True
        elif isinstance(s, IfStmt):
            for c in s.clauses:
                b, ct = _contains_break_and_continue(c.body.statements)
                has_break = has_break or b
                has_cont = has_cont or ct
            if s.else_body is not None:
                b, ct = _contains_break_and_continue(s.else_body.statements)
                has_break = has_break or b
                has_cont = has_cont or ct
        elif isinstance(s, DoStmt):
            b, ct = _contains_break_and_continue(s.body.statements)
            has_break = has_break or b
            has_cont = has_cont or ct
        # KHONG di vao While/Repeat/NumericFor/GenericFor/FunctionDecl/
        # LocalFunction - do la pham vi vong lap/ham khac.
    return has_break, has_cont


def _rewrite_break_continue_in_list(
    stmts: List[Stmt],
    break_flag: Optional[str],
    continue_flag: Optional[str],
) -> List[Stmt]:
    """Tra ve list MOI: moi BreakStmt/ContinueStmt tim thay duoc thay bang
    chuoi lenh 'relay':
        break    ->  <break_flag> = true ; break   (thoat dispatcher va bao cho ngoai)
        continue ->  <continue_flag> = true ; break (thoat dispatcher va bao cho ngoai)
    """
    out: List[Stmt] = []
    for s in stmts:
        if isinstance(s, BreakStmt):
            if break_flag is not None:
                out.append(AssignStmt(targets=[NameExpr(name=break_flag)], values=[TrueExpr()], line=s.line, col=s.col))
            out.append(BreakStmt(line=s.line, col=s.col))
        elif isinstance(s, ContinueStmt):
            if continue_flag is not None:
                out.append(AssignStmt(targets=[NameExpr(name=continue_flag)], values=[TrueExpr()], line=s.line, col=s.col))
            out.append(BreakStmt(line=s.line, col=s.col))
        elif isinstance(s, IfStmt):
            for c in s.clauses:
                c.body = Block(
                    statements=_rewrite_break_continue_in_list(c.body.statements, break_flag, continue_flag),
                    line=c.body.line, col=c.body.col,
                )
            if s.else_body is not None:
                s.else_body = Block(
                    statements=_rewrite_break_continue_in_list(s.else_body.statements, break_flag, continue_flag),
                    line=s.else_body.line, col=s.else_body.col,
                )
            out.append(s)
        elif isinstance(s, DoStmt):
            s.body = Block(
                statements=_rewrite_break_continue_in_list(s.body.statements, break_flag, continue_flag),
                line=s.body.line, col=s.body.col,
            )
            out.append(s)
        else:
            out.append(s)
    return out


@dataclass
class _BasicBlock:
    id: int
    stmts: List[Stmt] = field(default_factory=list)
    divergent: bool = False  # True neu stmt cuoi (nguyen goc) la return/break/goto/continue


def _split_basic_blocks(stmts: List[Stmt]) -> List[List[Stmt]]:
    """Gom statement tuan tu thanh tung nhom 3-7 phan tu; cau truc dieu
    khien luon tach rieng; task.wait()/coroutine.yield() luon la cuoi nhom."""
    groups: List[List[Stmt]] = []
    buf: List[Stmt] = []

    def flush() -> None:
        nonlocal buf
        if buf:
            groups.append(buf)
            buf = []

    for s in stmts:
        if isinstance(s, _STRUCTURAL_STMTS):
            flush()
            groups.append([s])
            continue

        buf.append(s)

        if isinstance(s, _DIVERGENT_STMTS):
            # return/break/goto/continue (hoac cap 'break_flag=true; break'
            # sau khi da relay) phai la statement cuoi cung cua block chua
            # no theo ngu phap Lua -> ket thuc nhom ngay.
            flush()
            continue

        if _is_yield_call(s) or len(buf) >= _MAX_GROUP:
            flush()
    flush()

    # Gop cac nhom qua nho (< _MIN_GROUP) vao nhom lien truoc, tru khi:
    #   - nhom lien truoc la 1 statement cau truc (_STRUCTURAL_STMTS) -
    #     khong duoc "dinh" them statement thuong vao sau no.
    #   - nhom lien truoc ket thuc bang statement phan ky/yield (khong the
    #     noi them code "roi theo sau" ve mat ngu phap/ngu nghia).
    merged: List[List[Stmt]] = []
    for g in groups:
        is_struct = len(g) == 1 and isinstance(g[0], _STRUCTURAL_STMTS)
        can_merge_into_prev = (
            not is_struct
            and merged
            and not (len(merged[-1]) == 1 and isinstance(merged[-1][0], _STRUCTURAL_STMTS))
            and not isinstance(merged[-1][-1], _DIVERGENT_STMTS)
            and not _is_yield_call(merged[-1][-1])
            and len(merged[-1]) < _MIN_GROUP
        )
        if can_merge_into_prev:
            merged[-1].extend(g)
        else:
            merged.append(g)
    return merged


def _collect_hoisted_names(groups: List[List[Stmt]]) -> List[str]:
    """Danh sach ten bien `local` khai bao TRUC TIEP trong cac nhom (khong
    de quy vao than if/while/for) - can hoist ra ngoai vong lap dispatcher
    de gia tri song sot qua nhieu vong lap `while`."""
    names: List[str] = []
    for g in groups:
        for s in g:
            if isinstance(s, LocalAssignStmt):
                names.extend(s.names)
            elif isinstance(s, LocalFunctionStmt):
                names.append(s.name)
    return names


def _degrade_locals(stmt: Stmt) -> Optional[Stmt]:
    """Chuyen 1 statement khai bao `local` (vi bien da duoc hoist len dau
    vong lap) thanh phep GAN thuong tuong duong. Tra ve None neu statement
    khong con can thiet (vd `local x` khong co gia tri khoi tao -> da la
    nil san tu cau hoist, khong can gan lai)."""
    if isinstance(stmt, LocalAssignStmt):
        if not stmt.values:
            return None
        targets = [NameExpr(name=n, line=stmt.line, col=stmt.col) for n in stmt.names]
        return AssignStmt(targets=targets, values=list(stmt.values), line=stmt.line, col=stmt.col)
    if isinstance(stmt, LocalFunctionStmt):
        fn = FunctionExpr(body=stmt.body, line=stmt.line, col=stmt.col)
        target = NameExpr(name=stmt.name, line=stmt.line, col=stmt.col)
        return AssignStmt(targets=[target], values=[fn], line=stmt.line, col=stmt.col)
    return stmt


def _gen_state_ids(n: int, rng: random.Random) -> List[int]:
    """Sinh n id trang thai DUONG, PHAN BIET, KHONG TUAN TU (xao tron),
    de thu tu xuat hien cua block trong cay dieu phoi (sap theo id) khong
    con tuong ung voi thu tu thuc thi logic goc."""
    span = max(n * 9, 64)
    pool = list(range(10, 10 + span, 9))
    rng.shuffle(pool)
    if len(pool) < n:  # phong ho cuc hiem khi n rat lon
        pool = list(range(10, 10 + n * 2))
        rng.shuffle(pool)
    return pool[:n]


def _build_dispatch(state_var: str, sorted_blocks: List[_BasicBlock], lo: int, hi: int) -> Stmt:
    """De quy xay cay tim kiem nhi phan CHI DUNG '<' tren danh sach block
    da sap xep theo id (sorted_blocks[lo..hi])."""
    if lo == hi:
        blk = sorted_blocks[lo]
        return DoStmt(body=Block(statements=list(blk.stmts)))

    mid = (lo + hi) // 2
    pivot_id = sorted_blocks[mid + 1].id
    left = _build_dispatch(state_var, sorted_blocks, lo, mid)
    right = _build_dispatch(state_var, sorted_blocks, mid + 1, hi)

    cond = BinOpExpr(op="<", left=NameExpr(name=state_var), right=NumberExpr(raw=str(pivot_id), value=pivot_id))
    return IfStmt(
        clauses=[IfClause(cond=cond, body=Block(statements=[left]))],
        else_body=Block(statements=[right]),
    )


def flatten(
    block: Block,
    *,
    seed: Optional[int] = None,
    state_var: Optional[str] = None,
    min_len: int = 2,
) -> Block:
    """Flatten 1 CAP Block (khong de quy vao than cac statement con).

    Tra ve Block MOI; neu block qua don gian de flatten co loi (< 2 basic
    block sau khi gom nhom) thi tra nguyen block goc (khong lam gi, tranh
    sinh ra vong lap dispatcher thua cho 1 block duy nhat).
    """
    stmts = list(block.statements)
    if len(stmts) < 2:
        return block

    groups = _split_basic_blocks(stmts)
    if len(groups) < min_len:
        return block

    rng = random.Random(seed)

    # Tu day tro di CHAC CHAN se boc block trong 1 vong lap dispatcher moi
    # -> phai relay break/continue TRUOC khi tiep tuc gom nhom.
    has_break, has_cont = _contains_break_and_continue(stmts)
    break_flag: Optional[str] = None
    continue_flag: Optional[str] = None
    if has_break:
        break_flag = f"_bf{rng.randrange(0x100, 0xFFF):x}"
    if has_cont:
        continue_flag = f"_cf{rng.randrange(0x100, 0xFFF):x}"

    if has_break or has_cont:
        stmts = _rewrite_break_continue_in_list(stmts, break_flag, continue_flag)
        groups = _split_basic_blocks(stmts)

    n = len(groups)
    ids = _gen_state_ids(n, rng)
    sv = state_var or f"_st{rng.randrange(0x100, 0xFFF):x}"

    basic_blocks: List[_BasicBlock] = []
    for i, g in enumerate(groups):
        body: List[Stmt] = []
        for s in g:
            d = _degrade_locals(s)
            if d is not None:
                body.append(d)

        last_is_divergent = len(g) > 0 and isinstance(g[-1], _DIVERGENT_STMTS)
        if not last_is_divergent:
            next_id = ids[i + 1] if i + 1 < n else _EXIT_STATE
            body.append(
                AssignStmt(
                    targets=[NameExpr(name=sv)],
                    values=[NumberExpr(raw=str(next_id), value=next_id)],
                )
            )
        basic_blocks.append(_BasicBlock(id=ids[i], stmts=body, divergent=last_is_divergent))

    sorted_blocks = sorted(basic_blocks, key=lambda b: b.id)
    dispatch = _build_dispatch(sv, sorted_blocks, 0, len(sorted_blocks) - 1)

    hoisted = _collect_hoisted_names(groups)
    out_stmts: List[Stmt] = []
    if hoisted:
        out_stmts.append(LocalAssignStmt(names=hoisted, type_infos=[None] * len(hoisted), values=[]))

    if break_flag is not None:
        out_stmts.append(LocalAssignStmt(names=[break_flag], type_infos=[None], values=[FalseExpr()]))
    if continue_flag is not None:
        out_stmts.append(LocalAssignStmt(names=[continue_flag], type_infos=[None], values=[FalseExpr()]))

    out_stmts.append(LocalAssignStmt(names=[sv], type_infos=[None], values=[NumberExpr(raw=str(ids[0]), value=ids[0])]))

    loop_cond = BinOpExpr(op="~=", left=NameExpr(name=sv), right=NumberExpr(raw=str(_EXIT_STATE), value=_EXIT_STATE))
    out_stmts.append(WhileStmt(cond=loop_cond, body=Block(statements=[dispatch])))

    if break_flag is not None:
        out_stmts.append(
            IfStmt(
                clauses=[IfClause(cond=NameExpr(name=break_flag), body=Block(statements=[BreakStmt()]))],
                else_body=None,
            )
        )
    if continue_flag is not None:
        out_stmts.append(
            IfStmt(
                clauses=[IfClause(cond=NameExpr(name=continue_flag), body=Block(statements=[ContinueStmt()]))],
                else_body=None,
            )
        )

    return Block(statements=out_stmts, line=block.line, col=block.col)


# ---------------------------------------------------------------------------
# De quy toan AST (deep-first): flatten than cua moi cau truc con TRUOC,
# roi moi flatten chinh block hien tai - giup CFF ap dung cho toan bo ham
# chu khong chi lop ngoai cung.
# ---------------------------------------------------------------------------

def _flatten_sub_bodies(stmt: Stmt, *, rng: random.Random, min_len: int) -> None:
    if isinstance(stmt, IfStmt):
        for clause in stmt.clauses:
            clause.body = flatten_deep(clause.body, seed=rng.randrange(1 << 30), min_len=min_len)
        if stmt.else_body is not None:
            stmt.else_body = flatten_deep(stmt.else_body, seed=rng.randrange(1 << 30), min_len=min_len)
    elif isinstance(stmt, (WhileStmt, RepeatStmt, DoStmt, NumericForStmt, GenericForStmt)):
        stmt.body = flatten_deep(stmt.body, seed=rng.randrange(1 << 30), min_len=min_len)
    elif isinstance(stmt, (FunctionDeclStmt, LocalFunctionStmt)):
        if stmt.body is not None:
            stmt.body.body = flatten_deep(stmt.body.body, seed=rng.randrange(1 << 30), min_len=min_len)


def flatten_deep(block: Block, *, seed: Optional[int] = None, min_len: int = 2) -> Block:
    """Nhu `flatten()` nhung de quy vao than TAT CA cau truc con (if/while/
    for/repeat/do/function) truoc khi flatten chinh block hien tai, de CFF
    phu kin toan bo than ham thay vi chi lop statement ngoai cung."""
    rng = random.Random(seed)
    stmts = [s for s in block.statements]
    for s in stmts:
        _flatten_sub_bodies(s, rng=rng, min_len=min_len)

    if len(stmts) < min_len:
        return Block(statements=stmts, line=block.line, col=block.col)

    return flatten(Block(statements=stmts, line=block.line, col=block.col), seed=rng.randrange(1 << 30))


__all__ = ["flatten", "flatten_deep", "CFFError"]