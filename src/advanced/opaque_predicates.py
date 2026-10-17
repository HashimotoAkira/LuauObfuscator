# -*- coding: utf-8 -*-
"""
src.advanced.opaque_predicates
===============================
Tang "Dieu kien gia & Nhanh ma phoi hop CFF" (muc 3.2 trong readme).

Module nay sinh ra cac bieu thuc boolean AST goi la "opaque predicate":
nhin qua thi phuc tap (goi ham runtime, toan hoc, bitwise...) nhung
GIA TRI THAT SU luon co the xac dinh truoc ve mat dai so (luon True,
hoac luon False), bat ke runtime tra ve gi. Dung de:

    1. "Boc" cac dieu kien that (if/while/repeat) bang mot lop nguy trang
       dai so (vd: `cond and <luon True>`) ma khong doi ngu nghia.
    2. Chen them cac "nhanh ma" (phantom branch) - elseif/if chet, dieu
       kien luon False, than chua code rac - vao giua cac nhanh that,
       de lam AI/decompiler kho phan biet nhanh nao la logic goc, dac
       biet khi phoi hop voi cay dispatch nhi phan cua CFF (cff.py).

Nguyen tac an toan (BAT BUOC doc truoc khi sua module nay):
    - Moi predicate luon-True/luon-False duoc boc trong 1 IIFE Lua:
          (function() local _e = <entropy>; return <bool-expr voi _e> end)()
      Ly do: cac ham entropy (tick()/os.clock()/math.random()) la ham co
      TAC DUNG PHU VE THOI GIAN (gia tri khac nhau moi lan goi). Neu dung
      lai CUNG MOT bieu thuc entropy o nhieu vi tri trong cay AST (thay vi
      dung lai GIA TRI da tinh), moi lan "emit" lai se phat sinh 1 LOI GOI
      THAT khac nhau -> dang thuc dai so (vd e - e == 0) co the SAI luc
      runtime. Boc trong IIFE + `local` dam bao entropy chi duoc danh gia
      DUNG 1 LAN moi khi predicate duoc goi, giu dung bat bien dai so.
    - Khong bao gio dung `true`/`false` lam goc (TrueExpr/FalseExpr) cho
      opaque predicate - qua lo lieu, AI doc ra ngay. Luon dua tren 1 gia
      tri runtime khong biet truoc (tick()/os.clock()) + 1 bat bien toan
      hoc luon dung voi MOI gia tri so thuc huu han.
    - Moi ham sinh code o day deu THUAN (khong tac dung phu that len chuong
      trinh goc) va khong bao gio can thiep vao bien/scope cua code that
      (ten bien tam luon duoc sinh moi, khong trung voi code nguoi dung).
    - `camouflage()` chi doc va bien doi CAC NODE DA TON TAI trong cay (cond,
      danh sach statements...); no khong tu de quy vao chinh cac node OPAQUE
      vua sinh ra (tranh de quy vo han / phinh to khong kiem soat) - xem
      `_InjectionBudget` de co chan cung.

API chinh (dung tu cff.py / main.py):

    from src.advanced.opaque_predicates import OpaquePredicateInjector

    inj = OpaquePredicateInjector(dialect="roblox", seed=1234)
    inj.camouflage(block)                       # xu ly toan bo 1 Block (de quy)
    inj.guard_condition(some_cond_expr)          # boc 1 dieu kien rieng le
    inj.camouflage_dispatch_condition(cmp_expr)  # danh rieng cho pivot CFF (`state < mid`)
    inj.true_expr(); inj.false_expr()            # lay predicate tho khi can
    inj.dead_if()                                # 1 IfStmt chet dung rieng

Hoac dung nhanh khong can giu instance:

    from src.advanced.opaque_predicates import obfuscate_opaque_predicates
    obfuscate_opaque_predicates(block)
"""

from __future__ import annotations

import dataclasses
import itertools
import random
from typing import Callable, List, Optional, Sequence

try:  # package that (src la top-level package, chay qua main.py/pipeline)
    from ..parser import nodes as N
except ImportError:  # chay doc lap file nay (python opaque_predicates.py) de test nhanh
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
    from src.parser import nodes as N


# ---------------------------------------------------------------------------
# Helper dung chung de dung AST node gon hon
# ---------------------------------------------------------------------------

def _num(value) -> N.NumberExpr:
    """Tao NumberExpr tu 1 so Python (int/float)."""
    return N.NumberExpr(raw=str(value), value=value)


def _name(identifier: str) -> N.NameExpr:
    return N.NameExpr(name=identifier)


def _string(value: str) -> N.StringExpr:
    return N.StringExpr(value=value, raw=repr(value).replace("'", '"'))


def _dotted(*parts: str) -> N.Expr:
    """Dung chuoi ten noi boi dau cham thanh IndexExpr long nhau: a.b.c"""
    expr: N.Expr = _name(parts[0])
    for p in parts[1:]:
        expr = N.IndexExpr(obj=expr, field=p)
    return expr


def _call(func: N.Expr, *args: N.Expr) -> N.CallExpr:
    return N.CallExpr(func=func, args=list(args))


def _paren(expr: N.Expr) -> N.Expr:
    """Boc ParenExpr - luon an toan (khong doi ngu nghia bieu thuc boolean/so
    hoc don gia tri), nhung tranh boc long nhau neu khong can thiet."""
    if isinstance(expr, (N.ParenExpr, N.NameExpr, N.NumberExpr, N.StringExpr,
                          N.CallExpr, N.IndexExpr, N.NilExpr, N.TrueExpr, N.FalseExpr)):
        return expr
    return N.ParenExpr(expr=expr)


# ---------------------------------------------------------------------------
# Injection budget - chan cung tong so node "rac" duoc chen, tranh phinh to
# khong kiem soat du xac suat ngau nhien co the lech may (xem docstring dau
# file, phan "de quy vo han / phinh to khong kiem soat").
# ---------------------------------------------------------------------------

class _InjectionBudget:
    def __init__(self, max_injections: int) -> None:
        self._remaining = max_injections

    def take(self) -> bool:
        if self._remaining <= 0:
            return False
        self._remaining -= 1
        return True


_DIVERGENT_STMTS = (N.ReturnStmt, N.BreakStmt, N.ContinueStmt, N.GotoStmt)


# ---------------------------------------------------------------------------
# OpaquePredicateInjector
# ---------------------------------------------------------------------------

class OpaquePredicateInjector:
    """Sinh predicate mo & chen nhanh ma vao cay AST Lua/Luau.

    Tham so:
        dialect:
            "roblox" -> dung `tick()` (co san trong moi script Roblox) lam
                        nguon entropy runtime.
            "lua"    -> dung `os.clock()` (chuan Lua/Luau ngoai Roblox).
        entropy_source:
            Callable tra ve 1 Expr moi (khong bi cache) dai dien cho 1 gia
            tri runtime kho doan truoc, KIEU SO, huu han. Neu truyen vao se
            ghi de len `dialect`. Dung khi muon dong bo entropy voi 1 bien
            da duoc seed san boi vm/environment.py thay vi goi dong ho that.
        use_bit32:
            Co dung them cac template dua tren `bit32.band` khong (mot so
            sandbox/runtime co the khong co thu vien bit32).
        guard_conditions:
            Co boc dieu kien that (if/while/repeat) bang opaque predicate
            khong.
        condition_guard_probability:
            Xac suat boc 1 dieu kien cu the (0..1). Khong boc 100% de code
            sinh ra khong co "dau van tay" qua deu dang (moi if deu bi boc
            y het nhau de lo pattern).
        dead_branch_probability:
            Xac suat chen 1 nhanh `if <luon False> then <rac> end` truoc
            moi statement trong 1 block.
        phantom_clause_probability:
            Xac suat chen them 1 elseif chet vao 1 IfStmt that da co san.
        max_injections:
            Chan cung tong so lan chen rac (dead_if + phantom clause) cho
            toan bo 1 lan goi camouflage(), de ket qua luon on dinh ve kich
            thuoc du seed ngau nhien the nao.
        seed:
            Seed cho bo sinh ngau nhien noi bo - truyen vao de ket qua
            deterministic khi viet test.
    """

    #: cac tien to ten bien tam - giu ngan & vo nghia, giong phong cach bien
    #: da bi mangling boi basic/naming.py (tranh noi bat giua code that).
    _NAME_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"

    def __init__(
        self,
        dialect: str = "roblox",
        entropy_source: Optional[Callable[[], N.Expr]] = None,
        use_bit32: bool = True,
        guard_conditions: bool = True,
        condition_guard_probability: float = 0.55,
        dead_branch_probability: float = 0.10,
        phantom_clause_probability: float = 0.20,
        max_injections: int = 60,
        seed: Optional[int] = None,
    ) -> None:
        if dialect not in ("roblox", "lua"):
            raise ValueError(f"dialect khong hop le: {dialect!r} (chi nhan 'roblox' hoac 'lua')")

        self.dialect = dialect
        self._entropy_source = entropy_source
        self.use_bit32 = use_bit32
        self.guard_conditions = guard_conditions
        self.condition_guard_probability = condition_guard_probability
        self.dead_branch_probability = dead_branch_probability
        self.phantom_clause_probability = phantom_clause_probability

        self._rng = random.Random(seed)
        self._name_counter = itertools.count(1)
        self._budget = _InjectionBudget(max_injections)

        self._true_templates: List[Callable[[], N.Expr]] = [
            self._tpl_true_square_nonneg,
            self._tpl_true_parity_complete,
            self._tpl_true_add_sub_identity,
            self._tpl_true_double_even,
            self._tpl_true_contradiction_negated,
            self._tpl_true_table_len_const,
            self._tpl_true_type_is_number,
            self._tpl_true_cross_reflexive,
            self._tpl_true_cross_sum_order,
            self._tpl_true_floor_identity,
        ]
        self._false_templates: List[Callable[[], N.Expr]] = [
            self._tpl_false_square_negative,
            self._tpl_false_parity_impossible,
            self._tpl_false_add_sub_broken,
            self._tpl_false_self_unequal,
            self._tpl_false_contradiction,
            self._tpl_false_table_len_wrong,
            self._tpl_false_type_not_number,
            self._tpl_false_cross_unequal,
            self._tpl_false_floor_broken,
        ]
        if self.use_bit32:
            self._true_templates.append(self._tpl_true_bitwise_parity)
            self._false_templates.append(self._tpl_false_bitwise_parity)

    # ------------------------------------------------------------------
    # Dat ten bien tam
    # ------------------------------------------------------------------

    def _fresh_name(self, hint: str = "o") -> str:
        """Sinh 1 ten local tam, ngan & vo nghia (giong style bien da
        mangling), khong bao gio trung voi code nguoi dung vi luon co
        counter noi bo tang dan + tien to rieng '_op'."""
        n = next(self._name_counter)
        suffix = "".join(self._rng.choice(self._NAME_ALPHABET) for _ in range(3))
        return f"_op{hint}{n}{suffix}"

    def _raw_runtime_value(self) -> N.Expr:
        if self._entropy_source is not None:
            return self._entropy_source()
        if self.dialect == "roblox":
            # An toan: trong Roblox dung tick(), ngoai Roblox tu fallback sang os.clock()
            # de khong bao gio bi 'attempt to call a nil value' tren Lune/Luau standalone.
            clock_fn = N.ParenExpr(expr=N.BinOpExpr(op="or", left=_name("tick"), right=_dotted("os", "clock")))
            return _call(clock_fn)
        return _call(_dotted("os", "clock"))

    def _entropy_int_expr(self, modulo: int = 8192) -> N.Expr:
        """`math.floor(<raw>() * 1000) % modulo` - luon la so nguyen (duoi
        dang float cua Lua) khong am, nam trong [0, modulo)."""
        raw = self._raw_runtime_value()
        scaled = N.BinOpExpr(op="*", left=raw, right=_num(1000))
        floored = _call(_dotted("math", "floor"), scaled)
        return N.BinOpExpr(op="%", left=floored, right=_num(modulo))

    # ------------------------------------------------------------------
    # Boc 1 doan logic can dung lai entropy nhieu lan vao 1 IIFE, dam bao
    # entropy CHI duoc danh gia DUNG 1 LAN (xem docstring dau file).
    # ------------------------------------------------------------------

    def _iife(self, local_names: Sequence[str], entropy_exprs: Sequence[N.Expr],
               return_expr: N.Expr) -> N.Expr:
        assert len(local_names) == len(entropy_exprs)
        local_stmt = N.LocalAssignStmt(
            names=list(local_names),
            type_infos=[None] * len(local_names),
            values=list(entropy_exprs),
        )
        body = N.Block(statements=[local_stmt, N.ReturnStmt(values=[return_expr])])
        fn = N.FunctionExpr(body=N.FunctionBody(params=[], is_vararg=False, body=body))
        return _call(N.ParenExpr(expr=fn))

    # ------------------------------------------------------------------
    # Template - LUON DUNG (True)
    # Moi template tu sinh ten bien tam rieng, khong can tham so ngoai.
    # ------------------------------------------------------------------

    def _tpl_true_square_nonneg(self) -> N.Expr:
        # e*e >= 0 voi moi so thuc e
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op=">=", left=N.BinOpExpr(op="*", left=_name(e), right=_name(e)),
                            right=_num(0))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_parity_complete(self) -> N.Expr:
        # voi e nguyen: e % 2 == 0 hoac e % 2 == 1 (khong co kha nang thu 3)
        e = self._fresh_name("e")
        m = N.BinOpExpr(op="%", left=_name(e), right=_num(2))
        cond = N.BinOpExpr(op="or",
                            left=N.BinOpExpr(op="==", left=m, right=_num(0)),
                            right=N.BinOpExpr(op="==", left=m, right=_num(1)))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_add_sub_identity(self) -> N.Expr:
        # (e + 1) - 1 == e
        e = self._fresh_name("e")
        lhs = N.BinOpExpr(op="-", left=N.ParenExpr(expr=N.BinOpExpr(op="+", left=_name(e), right=_num(1))),
                           right=_num(1))
        cond = N.BinOpExpr(op="==", left=lhs, right=_name(e))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_double_even(self) -> N.Expr:
        # (e * 2) % 2 == 0, luon dung voi moi e
        e = self._fresh_name("e")
        lhs = N.BinOpExpr(op="%", left=N.ParenExpr(expr=N.BinOpExpr(op="*", left=_name(e), right=_num(2))),
                           right=_num(2))
        cond = N.BinOpExpr(op="==", left=lhs, right=_num(0))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_contradiction_negated(self) -> N.Expr:
        # not (e < 0 and e > 0) -- khong the vua < 0 vua > 0 -> phu dinh luon dung
        e = self._fresh_name("e")
        inner = N.BinOpExpr(op="and",
                             left=N.BinOpExpr(op="<", left=_name(e), right=_num(0)),
                             right=N.BinOpExpr(op=">", left=_name(e), right=_num(0)))
        cond = N.UnOpExpr(op="not", operand=N.ParenExpr(expr=inner))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_table_len_const(self) -> N.Expr:
        # #{e, e, e} == 3 -- do dai table constructor 3 phan tu positional luon la 3
        e = self._fresh_name("e")
        tbl = N.TableConstructorExpr(fields=[
            N.TableField(key=None, value=_name(e), is_computed_key=False),
            N.TableField(key=None, value=_name(e), is_computed_key=False),
            N.TableField(key=None, value=_name(e), is_computed_key=False),
        ])
        cond = N.BinOpExpr(op="==", left=N.UnOpExpr(op="#", operand=tbl), right=_num(3))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_type_is_number(self) -> N.Expr:
        # type(e) == "number" -- e luon la so (xem _entropy_int_expr)
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="==", left=_call(_name("type"), _name(e)), right=_string("number"))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_cross_reflexive(self) -> N.Expr:
        # (e1 - e2) == (e1 - e2) -- phan xa, nhung dung 2 nguon entropy doc lap
        # de khong lo ngay la "x == x" don gian khi nhin luot qua.
        e1, e2 = self._fresh_name("e"), self._fresh_name("e")
        diff1 = N.ParenExpr(expr=N.BinOpExpr(op="-", left=_name(e1), right=_name(e2)))
        diff2 = N.ParenExpr(expr=N.BinOpExpr(op="-", left=_name(e1), right=_name(e2)))
        cond = N.BinOpExpr(op="==", left=diff1, right=diff2)
        return self._iife([e1, e2], [self._entropy_int_expr(), self._entropy_int_expr()], cond)

    def _tpl_true_cross_sum_order(self) -> N.Expr:
        # voi e1, e2 >= 0: (e1 + e2) >= e1 and (e1 + e2) >= e2
        e1, e2 = self._fresh_name("e"), self._fresh_name("e")
        total = N.ParenExpr(expr=N.BinOpExpr(op="+", left=_name(e1), right=_name(e2)))
        cond = N.BinOpExpr(op="and",
                            left=N.BinOpExpr(op=">=", left=total, right=_name(e1)),
                            right=N.BinOpExpr(op=">=", left=total, right=_name(e2)))
        return self._iife([e1, e2], [self._entropy_int_expr(), self._entropy_int_expr()], cond)

    def _tpl_true_floor_identity(self) -> N.Expr:
        # math.floor(e) == e -- e da la so nguyen (ket qua cua math.floor truoc do)
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="==", left=_call(_dotted("math", "floor"), _name(e)), right=_name(e))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_true_bitwise_parity(self) -> N.Expr:
        # bit32.band(e,1) == 0 hoac == 1 -- bit thap nhat chi co 2 kha nang
        e = self._fresh_name("e")
        band = _call(_dotted("bit32", "band"), _name(e), _num(1))
        cond = N.BinOpExpr(op="or",
                            left=N.BinOpExpr(op="==", left=band, right=_num(0)),
                            right=N.BinOpExpr(op="==", left=band, right=_num(1)))
        return self._iife([e], [self._entropy_int_expr()], cond)

    # ------------------------------------------------------------------
    # Template - LUON SAI (False) - phan lon la phu dinh truc tiep cua
    # cac bat bien True o tren.
    # ------------------------------------------------------------------

    def _tpl_false_square_negative(self) -> N.Expr:
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="<", left=N.BinOpExpr(op="*", left=_name(e), right=_name(e)), right=_num(0))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_parity_impossible(self) -> N.Expr:
        e = self._fresh_name("e")
        m = N.BinOpExpr(op="%", left=_name(e), right=_num(2))
        cond = N.BinOpExpr(op="and",
                            left=N.BinOpExpr(op="~=", left=m, right=_num(0)),
                            right=N.BinOpExpr(op="~=", left=m, right=_num(1)))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_add_sub_broken(self) -> N.Expr:
        e = self._fresh_name("e")
        lhs = N.BinOpExpr(op="-", left=N.ParenExpr(expr=N.BinOpExpr(op="+", left=_name(e), right=_num(1))),
                           right=_num(1))
        cond = N.BinOpExpr(op="~=", left=lhs, right=_name(e))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_self_unequal(self) -> N.Expr:
        # e ~= e -- chi dung voi NaN; entropy cua ta luon la so huu han nen luon sai
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="~=", left=_name(e), right=_name(e))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_contradiction(self) -> N.Expr:
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="and",
                            left=N.BinOpExpr(op="<", left=_name(e), right=_num(0)),
                            right=N.BinOpExpr(op=">", left=_name(e), right=_num(0)))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_table_len_wrong(self) -> N.Expr:
        e = self._fresh_name("e")
        tbl = N.TableConstructorExpr(fields=[
            N.TableField(key=None, value=_name(e), is_computed_key=False),
            N.TableField(key=None, value=_name(e), is_computed_key=False),
            N.TableField(key=None, value=_name(e), is_computed_key=False),
        ])
        cond = N.BinOpExpr(op="~=", left=N.UnOpExpr(op="#", operand=tbl), right=_num(3))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_type_not_number(self) -> N.Expr:
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="~=", left=_call(_name("type"), _name(e)), right=_string("number"))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_cross_unequal(self) -> N.Expr:
        e1, e2 = self._fresh_name("e"), self._fresh_name("e")
        diff1 = N.ParenExpr(expr=N.BinOpExpr(op="-", left=_name(e1), right=_name(e2)))
        diff2 = N.ParenExpr(expr=N.BinOpExpr(op="-", left=_name(e1), right=_name(e2)))
        cond = N.BinOpExpr(op="~=", left=diff1, right=diff2)
        return self._iife([e1, e2], [self._entropy_int_expr(), self._entropy_int_expr()], cond)

    def _tpl_false_floor_broken(self) -> N.Expr:
        e = self._fresh_name("e")
        cond = N.BinOpExpr(op="~=", left=_call(_dotted("math", "floor"), _name(e)), right=_name(e))
        return self._iife([e], [self._entropy_int_expr()], cond)

    def _tpl_false_bitwise_parity(self) -> N.Expr:
        e = self._fresh_name("e")
        band = _call(_dotted("bit32", "band"), _name(e), _num(1))
        cond = N.BinOpExpr(op="and",
                            left=N.BinOpExpr(op="~=", left=band, right=_num(0)),
                            right=N.BinOpExpr(op="~=", left=band, right=_num(1)))
        return self._iife([e], [self._entropy_int_expr()], cond)

    # ------------------------------------------------------------------
    # API cong khai - predicate tho
    # ------------------------------------------------------------------

    def true_expr(self) -> N.Expr:
        """Tra ve 1 Expr luon danh gia ra True luc runtime (khong bao gio
        la literal `true` tran - luon dua tren 1 bat bien + gia tri runtime
        khong doan truoc duoc)."""
        template = self._rng.choice(self._true_templates)
        return template()

    def false_expr(self) -> N.Expr:
        """Tra ve 1 Expr luon danh gia ra False luc runtime."""
        template = self._rng.choice(self._false_templates)
        return template()

    # ------------------------------------------------------------------
    # API cong khai - boc 1 dieu kien that
    # ------------------------------------------------------------------

    def guard_condition(self, cond: N.Expr) -> N.Expr:
        """Boc `cond` bang 1 opaque predicate, GIU NGUYEN gia tri logic
        cuoi cung cua `cond`. `cond` goc luon duoc giu lai y nguyen (chi
        boc ParenExpr quanh no khi can) - khong bi thay the hay rut gon,
        nen cay con ben trong van duoc cac buoc camouflage/CFF khac tiep
        tuc xu ly binh thuong."""
        variant = self._rng.choice(("and_true_left", "and_true_right", "or_false", "not_false_and"))
        wrapped_cond = _paren(cond)

        if variant == "and_true_left":
            return N.BinOpExpr(op="and", left=_paren(self.true_expr()), right=wrapped_cond)
        if variant == "and_true_right":
            return N.BinOpExpr(op="and", left=wrapped_cond, right=_paren(self.true_expr()))
        if variant == "or_false":
            return N.BinOpExpr(op="or", left=wrapped_cond, right=_paren(self.false_expr()))
        # not_false_and: not(<luon False>) and cond  ==  true and cond  == cond
        inner_not = N.UnOpExpr(op="not", operand=_paren(self.false_expr()))
        return N.BinOpExpr(op="and", left=_paren(inner_not), right=wrapped_cond)

    def camouflage_dispatch_condition(self, cmp_expr: N.Expr) -> N.Expr:
        """Bien the danh rieng cho pivot so sanh cua CFF nhi phan (vd
        `state < mid` trong cff.py). Ve ban chat giong `guard_condition`,
        nhung dat ten rieng de code goi (cff.py) the hien ro y dinh:
        lam mo gia tri pivot that su, tranh 1 ke tan cong doc thang duoc
        cay so sanh nhi phan de suy ra toan bo bang dispatch."""
        return self.guard_condition(cmp_expr)

    # ------------------------------------------------------------------
    # API cong khai - than rac / nhanh chet
    # ------------------------------------------------------------------

    def junk_statement(self) -> N.Stmt:
        """1 statement vo hai, khong tac dung phu ra ngoai scope cua no
        (chi gan vao 1 local MOI, ten khong trung ai) - dung lam "rac" lap
        day cac nhanh chet."""
        name = self._fresh_name("j")
        modulo = self._rng.choice((97, 251, 509, 1024, 4096))
        base = self._entropy_int_expr(modulo=modulo)
        op = self._rng.choice(("+", "-", "*"))
        rhs = N.BinOpExpr(op=op, left=base, right=_num(self._rng.randint(1, 97)))
        return N.LocalAssignStmt(names=[name], type_infos=[None], values=[rhs])

    def junk_block(self, min_stmts: int = 1, max_stmts: int = 3) -> N.Block:
        count = self._rng.randint(min_stmts, max_stmts)
        return N.Block(statements=[self.junk_statement() for _ in range(count)])

    def dead_if(self) -> N.IfStmt:
        """1 IfStmt hoan chinh, dieu kien luon False, than chua code rac.
        Khong bao gio thuc thi luc runtime, nhung ve mat tinh (static) thi
        trong y het 1 nhanh logic that."""
        clause = N.IfClause(cond=self.false_expr(), body=self.junk_block())
        return N.IfStmt(clauses=[clause], else_body=None)

    # ------------------------------------------------------------------
    # API cong khai - duyet & nguy trang ca cay
    # ------------------------------------------------------------------

    def camouflage(self, block: N.Block) -> N.Block:
        """Duyet de quy toan bo `block` (va moi Block long ben trong no:
        than if/while/repeat/for/do, than ham long, callback...), boc cac
        dieu kien that va chen nhanh ma. Bien doi TAI CHO (in-place) va
        tra ve chinh `block` da truyen vao (cung quy uoc voi
        strip_type_annotations/strip_comments ben basic/) de co the
        dung truc tiep trong pipeline kieu `block = injector.camouflage(block)`.
        """
        self._process_node(block)
        return block

    # -- noi bo: duyet 1 Node bat ky bang reflection dataclass fields ----

    def _process_node(self, node) -> None:
        if isinstance(node, N.Block):
            self._camouflage_statements(node)
            for stmt in node.statements:
                self._process_node(stmt)
            return
        if not isinstance(node, N.Node):
            return
        for f in dataclasses.fields(node):
            value = getattr(node, f.name)
            if isinstance(value, N.Node):
                self._process_node(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, N.Node):
                        self._process_node(item)

    def _camouflage_statements(self, block: N.Block) -> None:
        """Xu ly 1 lop (khong de quy sau) danh sach statements truc tiep
        cua 1 Block: chen dead_if() xen ke, boc dieu kien IfStmt/While/Repeat,
        chen phantom clause vao IfStmt da co san. De quy vao cac Block con
        (than if/while/ham long...) do `_process_node` dam nhiem sau do."""
        new_statements: List[N.Stmt] = []
        for stmt in block.statements:
            if self._budget.take() and self._rng.random() < self.dead_branch_probability:
                new_statements.append(self.dead_if())

            if self.guard_conditions and self._rng.random() < self.condition_guard_probability:
                if isinstance(stmt, N.IfStmt):
                    for clause in stmt.clauses:
                        clause.cond = self.guard_condition(clause.cond)
                elif isinstance(stmt, N.WhileStmt):
                    stmt.cond = self.guard_condition(stmt.cond)
                elif isinstance(stmt, N.RepeatStmt):
                    # Luu y ngu nghia Lua: `cond` cua repeat-until van thay
                    # duoc cac bien local khai bao trong `body`. Vi
                    # guard_condition chi boc THEM vao ben ngoai (khong doi
                    # cho `cond` goc duoc dat), scope nay van duoc giu nguyen.
                    stmt.cond = self.guard_condition(stmt.cond)

            if isinstance(stmt, N.IfStmt) and self._budget.take() \
                    and self._rng.random() < self.phantom_clause_probability:
                self._inject_phantom_clause(stmt)

            new_statements.append(stmt)

        last_is_divergent = bool(new_statements and isinstance(new_statements[-1], _DIVERGENT_STMTS))
        if not last_is_divergent and self._budget.take() and self._rng.random() < self.dead_branch_probability:
            new_statements.append(self.dead_if())

        block.statements = new_statements

    def _inject_phantom_clause(self, if_stmt: N.IfStmt) -> None:
        """Chen 1 elseif 'ma' (dieu kien luon False, than la rac) vao vi
        tri ngau nhien trong chuoi if/elseif da co san. An toan tuyet doi:
        vi dieu kien luon False va cac clause Lua danh gia tuan tu, chen o
        bat ky vi tri nao cung khong lam thay doi nhanh nao thuc su chay."""
        phantom = N.IfClause(cond=self.false_expr(), body=self.junk_block())
        pos = self._rng.randrange(len(if_stmt.clauses) + 1)
        if_stmt.clauses.insert(pos, phantom)


# ---------------------------------------------------------------------------
# Ham tien ich muc module - dung nhanh khong can tu quan ly instance
# ---------------------------------------------------------------------------

def obfuscate_opaque_predicates(block: N.Block, **kwargs) -> N.Block:
    """Tao 1 OpaquePredicateInjector dung 1 lan roi camouflage() `block`.
    `**kwargs` duoc chuyen thang vao constructor cua OpaquePredicateInjector
    (dialect, seed, cac xac suat...). Tra ve chinh `block` (bien doi in-place).
    """
    injector = OpaquePredicateInjector(**kwargs)
    return injector.camouflage(block)


__all__ = [
    "OpaquePredicateInjector",
    "obfuscate_opaque_predicates",
]


# ---------------------------------------------------------------------------
# Self-test noi bo (khong can pytest) - chi kiem tra CAU TRUC AST sinh ra
# dung quy uoc nodes.py, KHONG kiem tra ngu nghia Lua that su (muon vay
# phai co 1 Lua runtime, ngoai pham vi file Python nay). Chay:
#     python3 src/advanced/opaque_predicates.py
# ---------------------------------------------------------------------------

def _debug_render(node, indent: int = 0) -> str:
    """Bo render Lua CUC KY toi gian, CHI phuc vu doc thu trong self-test
    ben duoi (chua phai emitter.py chinh thuc cua du an - emitter that se
    nam o src/advanced/emitter.py va can xu ly day du minify/precedence)."""
    pad = "  " * indent
    if isinstance(node, N.Block):
        return "\n".join(_debug_render(s, indent) for s in node.statements)
    if isinstance(node, N.LocalAssignStmt):
        vals = ", ".join(_debug_render(v) for v in node.values)
        return f"{pad}local {', '.join(node.names)} = {vals}"
    if isinstance(node, N.ReturnStmt):
        return f"{pad}return {', '.join(_debug_render(v) for v in node.values)}"
    if isinstance(node, N.IfStmt):
        lines = []
        for i, c in enumerate(node.clauses):
            kw = "if" if i == 0 else "elseif"
            lines.append(f"{pad}{kw} {_debug_render(c.cond)} then")
            lines.append(_debug_render(c.body, indent + 1))
        if node.else_body is not None:
            lines.append(f"{pad}else")
            lines.append(_debug_render(node.else_body, indent + 1))
        lines.append(f"{pad}end")
        return "\n".join(lines)
    if isinstance(node, N.BinOpExpr):
        return f"{_debug_render(node.left)} {node.op} {_debug_render(node.right)}"
    if isinstance(node, N.UnOpExpr):
        return f"{node.op}{_debug_render(node.operand)}"
    if isinstance(node, N.ParenExpr):
        return f"({_debug_render(node.expr)})"
    if isinstance(node, N.CallExpr):
        return f"{_debug_render(node.func)}({', '.join(_debug_render(a) for a in node.args)})"
    if isinstance(node, N.IndexExpr):
        return f"{_debug_render(node.obj)}.{node.field}"
    if isinstance(node, N.FunctionExpr):
        fb = node.body
        return f"function() {_debug_render(fb.body, 0).strip()} end"
    if isinstance(node, N.TableConstructorExpr):
        return "{" + ", ".join(_debug_render(f.value) for f in node.fields) + "}"
    if isinstance(node, N.NameExpr):
        return node.name
    if isinstance(node, N.NumberExpr):
        return node.raw
    if isinstance(node, N.StringExpr):
        return node.raw
    return repr(node)


def _count_nodes(node) -> int:
    if isinstance(node, N.Block):
        return 1 + sum(_count_nodes(s) for s in node.statements)
    if not isinstance(node, N.Node):
        return 0
    total = 1
    for f in dataclasses.fields(node):
        value = getattr(node, f.name)
        if isinstance(value, N.Node):
            total += _count_nodes(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, N.Node):
                    total += _count_nodes(item)
    return total


def _find_all(node, node_type) -> List:
    found = []

    def _walk(n):
        if isinstance(n, node_type):
            found.append(n)
        if isinstance(n, N.Block):
            for s in n.statements:
                _walk(s)
            return
        if not isinstance(n, N.Node):
            return
        for f in dataclasses.fields(n):
            value = getattr(n, f.name)
            if isinstance(value, N.Node):
                _walk(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, N.Node):
                        _walk(item)

    _walk(node)
    return found


def test_true_expr_is_not_a_bare_literal():
    inj = OpaquePredicateInjector(seed=1)
    expr = inj.true_expr()
    assert not isinstance(expr, (N.TrueExpr, N.FalseExpr)), "khong duoc dung literal true/false tran"
    assert isinstance(expr, N.CallExpr), "predicate phai la 1 loi goi IIFE"


def test_false_expr_is_not_a_bare_literal():
    inj = OpaquePredicateInjector(seed=2)
    expr = inj.false_expr()
    assert not isinstance(expr, (N.TrueExpr, N.FalseExpr))
    assert isinstance(expr, N.CallExpr)


def test_entropy_only_evaluated_once_per_predicate():
    # Moi predicate phai chi co DUNG 1 lenh LocalAssignStmt khai bao entropy
    # (hoac 2 neu template dung 2 nguon doc lap), va KHONG duoc goi truc
    # tiep tick()/os.clock() ben ngoai pham vi local do (tuc la: moi loi goi
    # entropy nam trong values cua dung 1 LocalAssignStmt duy nhat o dau IIFE).
    inj = OpaquePredicateInjector(seed=3, dialect="roblox")
    for _ in range(30):
        expr = inj.true_expr()
        # Cay con: CallExpr(ParenExpr(FunctionExpr(body=Block([LocalAssignStmt, ReturnStmt]))))
        fn_expr = expr.func.expr  # type: ignore[attr-defined]
        assert isinstance(fn_expr, N.FunctionExpr)
        body_stmts = fn_expr.body.body.statements
        assert isinstance(body_stmts[0], N.LocalAssignStmt)
        assert isinstance(body_stmts[-1], N.ReturnStmt)
        # tick() chi duoc goi trong gia tri cua LocalAssignStmt dau tien,
        # khong xuat hien o noi nao khac trong return expr (dung lai bien).
        tick_calls_in_return = _find_all(body_stmts[-1], N.CallExpr)
        tick_like = [c for c in tick_calls_in_return
                     if isinstance(c.func, N.NameExpr) and c.func.name == "tick"]
        assert tick_like == [], "entropy khong duoc goi lai trong return - phai dung lai bien local"


def test_guard_condition_preserves_original_subtree():
    inj = OpaquePredicateInjector(seed=4)
    original_cond = N.BinOpExpr(op="==", left=_name("hp"), right=_num(0))
    guarded = inj.guard_condition(original_cond)
    names = [n.name for n in _find_all(guarded, N.NameExpr)]
    assert "hp" in names, "bieu thuc goc phai con nguyen ven ben trong predicate da boc"


def test_camouflage_wraps_if_condition_and_injects_statements():
    src_if = N.IfStmt(
        clauses=[N.IfClause(cond=N.BinOpExpr(op=">", left=_name("hp"), right=_num(0)),
                             body=N.Block(statements=[N.ReturnStmt(values=[_num(1)])]))],
        else_body=None,
    )
    block = N.Block(statements=[src_if])
    inj = OpaquePredicateInjector(seed=5, condition_guard_probability=1.0,
                                   dead_branch_probability=1.0, phantom_clause_probability=1.0,
                                   max_injections=100)
    before = len(block.statements)
    inj.camouflage(block)
    after = len(block.statements)
    assert after > before, "phai co dead_if() duoc chen them vao block"
    # IfStmt that van con trong cay (du da bi boc dieu kien / them phantom clause)
    if_stmts = _find_all(block, N.IfStmt)
    assert any(len(s.clauses) >= 1 for s in if_stmts)
    names_inside = [n.name for n in _find_all(block, N.NameExpr)]
    assert "hp" in names_inside, "dieu kien that (hp > 0) phai van con nguyen trong cay sau khi camouflage"


def test_camouflage_recurses_into_nested_function_body():
    inner_if = N.IfStmt(
        clauses=[N.IfClause(cond=N.BinOpExpr(op="==", left=_name("x"), right=_num(1)),
                             body=N.Block(statements=[]))],
        else_body=None,
    )
    fn = N.FunctionExpr(body=N.FunctionBody(params=[], body=N.Block(statements=[inner_if])))
    outer_assign = N.LocalAssignStmt(names=["cb"], type_infos=[None], values=[fn])
    block = N.Block(statements=[outer_assign])

    inj = OpaquePredicateInjector(seed=6, condition_guard_probability=1.0, max_injections=100)
    inj.camouflage(block)

    nested_if = _find_all(block, N.IfStmt)[0]
    # dieu kien ben trong callback long nhau cung phai bi boc (khong con la
    # BinOpExpr "==" don gian nua, ma la 1 lop opaque bao ngoai)
    assert nested_if.clauses[0].cond.op in ("and", "or")


def test_injection_budget_caps_total_insertions():
    block = N.Block(statements=[N.ReturnStmt(values=[]) for _ in range(50)])
    inj = OpaquePredicateInjector(seed=7, dead_branch_probability=1.0, max_injections=5)
    inj.camouflage(block)
    dead_ifs = [s for s in block.statements if isinstance(s, N.IfStmt)]
    assert len(dead_ifs) <= 6, "ngan sach max_injections phai duoc ton trong (co the +1 do cho cuoi block)"


def test_dialect_lua_uses_os_clock_not_tick():
    inj = OpaquePredicateInjector(seed=8, dialect="lua")
    expr = inj.true_expr()
    all_names = [n.name for n in _find_all(expr, N.NameExpr)]
    assert "tick" not in all_names
    assert "os" in all_names


def main() -> None:
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
            failed += 1
    print()
    print(f"Tong: {passed + failed} test, {passed} PASS, {failed} FAIL")

    print("\n--- Vi du minh hoa (debug render, KHONG phai emitter chinh thuc) ---\n")
    demo = OpaquePredicateInjector(seed=42, dialect="roblox")
    sample_if = N.IfStmt(
        clauses=[N.IfClause(cond=N.BinOpExpr(op=">", left=_name("hp"), right=_num(0)),
                             body=N.Block(statements=[N.CallStmt(call=_call(_name("heal")))]))],
        else_body=N.Block(statements=[N.CallStmt(call=_call(_name("die")))]),
    )
    demo_block = N.Block(statements=[sample_if])
    demo.camouflage(demo_block)
    print(_debug_render(demo_block))

    import sys
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()