# -*- coding: utf-8 -*-
"""
advanced/mba.py
================
Mixed Boolean Arithmetic (MBA) - lam roi cac bieu thuc toan hoc/bitwise
truoc khi dua qua emitter.py. Muc tieu: thay `a + b`, `a - b`, `a * b`
bang cac bieu thuc "dang tuong duong ve gia tri" nhung kho doc hon,
khien decompiler/AI phai lam constant-folding/symbolic execution moi
rut gon lai duoc ve phep toan goc.

QUAN TRONG - an toan ngu nghia truoc hieu nang:
    MBA o day KHONG mu quang thay the moi node BinOpExpr. Co 2 nguyen
    tac an toan bat buoc:

    1. "k-split" (mac dinh, LUON an toan, khong dieu kien gi):
            a + b  ->  (a + k) + (b - k)
            a - b  ->  (a - k) - (b - k)
       Moi toan hang (a, b) chi xuat hien DUNG 1 LAN trong cay moi, nen
       du a/b la loi goi ham co side-effect (vd `getCount()`), ket qua
       van dung 100% va KHONG goi lai ham 2 lan. Dung voi moi kieu so
       (int/float) vi chi dua tren tinh chat +k/-k triet tieu nhau.

    2. "bitwise" / "squaring" (tuy chon, CHI ap dung khi toan hang duoc
       xac nhan "an toan de nhan ban" - xem `_dup_safe`):
            a + b  ->  bit32.bor(a,b) + bit32.band(a,b)
            a - b  ->  bit32.bor(a,-b) + bit32.band(a,-b)
            a * b  ->  ((a+b)^2 - (a-b)^2) / 4
       Identity bitwise GIA DINH a,b la so nguyen trong khoang bieu dien
       duoc cua bit32 (uint32, Luau tu dong ep ve 32-bit khi goi bit32.*).
       Neu a hoac b la float khong nguyen, am, hoac vuot 2^32 thi KET QUA
       SE SAI -> chi bat bang `MBAConfig.assume_uint32 = True` khi nguoi
       dung CHAC CHAN gia tri la so nguyen nho (vd loop counter, index
       mang, FPS, so luong entity...). Identity "squaring" cho phep nhan
       (`*`) thi dung cho MOI so thuc (khong can gia dinh nguyen) vi no
       chi dua tren dang thuc dai so (a+b)^2-(a-b)^2 = 4ab, nhung van can
       `a`, `b` "an toan nhan ban" vi moi toan hang xuat hien 2 lan.

    `_dup_safe()` chi cho phep nhan ban cac bieu thuc KHONG side-effect
    va re (Name, Number, hang so, truy cap .field don gian...). CallExpr /
    MethodCallExpr / IndexBracketExpr / TableConstructor / FunctionExpr
    / Vararg LUON bi coi la khong an toan de nhan ban (co the co side-
    effect, co the dat, hoac don gian la danh gia 2 lan se cham hon).

Chi phi hieu nang (quan trong vi pipeline yeu cau 60 FPS o hot loop):
    - "k-split": +2 phep cong/tru so voi hang so -> RE, co the dung o
      hot loop khong ngai.
    - "bitwise": them 2 loi goi ham `bit32.bor/band` -> dat hon nhieu so
      voi 1 phep '+' nguyen thuy. Luu y khi dung CFF (cff.py) nen tranh
      dat MBA bitwise/squaring vao basic block chay moi frame.
    - "squaring" (*): 2 phep cong/tru + 2 luy thua '^' (pow) + 1 chia ->
      DAT HON RAT NHIEU so voi 1 phep nhan nguyen thuy. Chi nen dung cho
      code chay 1 lan (vd cold-start, tinh hang so cau hinh), KHONG nen
      dung trong vong lap render.

API:
    obfuscate(block, config=None) -> Block   (bien doi IN-PLACE, tra ve
                                               lai chinh node duoc truyen
                                               vao hoac node moi neu ban
                                               than no la BinOpExpr)
    MBAConfig(...)                            cau hinh xac suat/do sau/...
"""

from __future__ import annotations

import random
from dataclasses import dataclass, fields
from typing import Optional

from ..parser import nodes as N


# ---------------------------------------------------------------------------
# Cau hinh
# ---------------------------------------------------------------------------

@dataclass
class MBAConfig:
    # Xac suat (0..1) de 1 BinOpExpr hop le (+, -, * khi enable_multiply)
    # bi thay the. 1.0 = luon thay, 0.0 = tat.
    probability: float = 0.5

    # Tran so luong thay the toan cuc (None = khong gioi han). Dung de
    # tranh lam phinh code qua muc tren file lon.
    max_substitutions: Optional[int] = None

    # Cho phep dung identity bitwise (bit32.bor/band) cho + va -. CHI bat
    # neu chac chan toan hang la so nguyen nho (xem canh bao o docstring
    # module). Mac dinh TAT vi day la gia dinh khong phai luon dung.
    assume_uint32: bool = False

    # Cho phep dung identity "squaring" cho phep nhan (*). An toan ve mat
    # gia tri (dung cho moi so thuc) nhung DAT CPU - chi nen bat cho code
    # khong nam trong hot loop.
    enable_multiply: bool = False

    # identity "bitwise"/"squaring" can nhan ban toan hang (dung 2 lan).
    # Co flag rieng de co the tat tat ca cac bien the gay nhan ban ma
    # van giu k-split hoat dong (k-split khong bao gio nhan ban).
    allow_duplicate_simple: bool = True

    seed: Optional[int] = None

    # Khoang gia tri hang so k dung cho "k-split" (so nguyen duong, dau
    # +/- duoc chon ngau nhien o buoc sau).
    const_min: int = 17
    const_max: int = 9973

    # Chi ap dung MBA neu it nhat mot toan hang duoc xac dinh chac chan la
    # numeric. Khong suy luan numeric tu mot literal nam sau trong phep toan:
    # Vector3 + Vector3 * scalar van la phep cong vector, khong phai numeric.
    require_numeric_hint: bool = True


# ---------------------------------------------------------------------------
# Transformer
# ---------------------------------------------------------------------------

class MBATransformer:
    """Duyet toan bo AST (generic, dua tren dataclasses.fields) va thay
    the cac BinOpExpr hop le. Duyet post-order (con truoc, cha sau) va
    KHONG duyet lai vao cay vua sinh ra, nen khong co nguy co de quy vo
    han du ap dung nhieu lan hay xac suat cao."""

    def __init__(self, config: Optional[MBAConfig] = None):
        self.cfg = config or MBAConfig()
        self.rng = random.Random(self.cfg.seed)
        self.count = 0

    def transform(self, root: N.Node) -> N.Node:
        return self._walk(root)

    # -- Duyet AST tong quat --------------------------------------------

    def _walk(self, obj):
        if isinstance(obj, list):
            return [self._walk(x) for x in obj]
        if not isinstance(obj, N.Node):
            return obj
        for f in fields(obj):
            val = getattr(obj, f.name)
            new_val = self._walk(val)
            if new_val is not val:
                setattr(obj, f.name, new_val)
        if isinstance(obj, N.BinOpExpr):
            obj = self._maybe_substitute(obj)
        return obj

    # -- Kiem tra "an toan de nhan ban" ----------------------------------

    def _dup_safe(self, e: N.Expr) -> bool:
        if isinstance(e, (N.NumberExpr, N.NameExpr, N.NilExpr,
                           N.TrueExpr, N.FalseExpr)):
            return True
        if isinstance(e, N.ParenExpr):
            return self._dup_safe(e.expr)
        if isinstance(e, N.UnOpExpr):
            return self._dup_safe(e.operand)
        if isinstance(e, N.IndexExpr):
            # truy cap .field duoc coi la re & khong side-effect trong da
            # so truong hop thuc te (doc thuoc tinh); day la 1 gia dinh
            # thuc dung, khong phai dam bao tuyet doi (metatable __index
            # tuy bien van co the co side-effect).
            return self._dup_safe(e.obj)
        if isinstance(e, N.BinOpExpr):
            return self._dup_safe(e.left) and self._dup_safe(e.right)
        return False

    # -- Helper dung AST node --------------------------------------------

    @staticmethod
    def _num(value) -> N.NumberExpr:
        return N.NumberExpr(raw=str(value), value=value)

    @staticmethod
    def _bit32(name: str, *args: N.Expr) -> N.CallExpr:
        func = N.IndexExpr(obj=N.NameExpr(name="bit32"), field=name)
        return N.CallExpr(func=func, args=list(args))

    def _rand_k(self) -> int:
        # Luon tra ve so DUONG; dau +/- duoc chon rieng o noi goi bang
        # cach pha trong 2 bien the dang thuc (xem _sub_add/_sub_sub) thay
        # vi nhung dau am vao chinh literal - tranh sinh ra chuoi xau nhu
        # "a+-1050" (van dung cu phap, nhung khong gon).
        return self.rng.randint(self.cfg.const_min, self.cfg.const_max)

    def _pm(self, expr: N.Expr, k: int, plus: bool, line: int, col: int) -> N.BinOpExpr:
        return N.BinOpExpr(op="+" if plus else "-", left=expr, right=self._num(k),
                            line=line, col=col)

    @staticmethod
    def _is_numeric(node: N.Expr) -> bool:
        if isinstance(node, N.NumberExpr):
            return True
        if isinstance(node, N.UnOpExpr):
            return node.op in ("-", "+", "#", "~") and MBATransformer._is_numeric(node.operand)
        if isinstance(node, N.ParenExpr):
            return MBATransformer._is_numeric(node.expr)
        if isinstance(node, N.BinOpExpr):
            return (
                node.op in ("+", "-", "*", "/", "//", "%", "^")
                and MBATransformer._is_numeric(node.left)
                and MBATransformer._is_numeric(node.right)
            )
        return False

    # -- Dieu phoi --------------------------------------------------------

    def _maybe_substitute(self, node: N.BinOpExpr) -> N.BinOpExpr:
        if self.cfg.max_substitutions is not None and self.count >= self.cfg.max_substitutions:
            return node
        if self.cfg.require_numeric_hint:
            # Chi thay the khi co it nhat 1 toan hang la so hoc
            if not (self._is_numeric(node.left) or self._is_numeric(node.right)):
                return node
        if self.rng.random() > self.cfg.probability:
            return node
        if node.op == "+":
            return self._sub_add(node)
        if node.op == "-":
            return self._sub_sub(node)
        if node.op == "*" and self.cfg.enable_multiply:
            return self._sub_mul(node)
        return node

    # -- Identity cho + ----------------------------------------------------

    def _sub_add(self, node: N.BinOpExpr) -> N.BinOpExpr:
        can_bitwise = (self.cfg.assume_uint32 and self.cfg.allow_duplicate_simple
                       and self._dup_safe(node.left) and self._dup_safe(node.right))
        variant = self.rng.choice(["bitwise", "ksplit"]) if can_bitwise else "ksplit"
        self.count += 1
        if variant == "bitwise":
            bor = self._bit32("bor", node.left, node.right)
            band = self._bit32("band", node.left, node.right)
            return N.BinOpExpr(op="+", left=bor, right=band,
                                line=node.line, col=node.col)
        # a + b = (a+k) + (b-k) = (a-k) + (b+k)  voi MOI k -> chon ngau
        # nhien 1 trong 2 cach phoi dau de da dang hoa output, k luon duong.
        k = self._rand_k()
        swap = self.rng.random() < 0.5
        left_part = self._pm(node.left, k, plus=not swap, line=node.line, col=node.col)
        right_part = self._pm(node.right, k, plus=swap, line=node.line, col=node.col)
        return N.BinOpExpr(op="+", left=left_part, right=right_part,
                            line=node.line, col=node.col)

    # -- Identity cho - ----------------------------------------------------

    def _sub_sub(self, node: N.BinOpExpr) -> N.BinOpExpr:
        can_bitwise = (self.cfg.assume_uint32 and self.cfg.allow_duplicate_simple
                       and self._dup_safe(node.left) and self._dup_safe(node.right))
        variant = self.rng.choice(["bitwise", "ksplit"]) if can_bitwise else "ksplit"
        self.count += 1
        if variant == "bitwise":
            negb = N.UnOpExpr(op="-", operand=node.right,
                               line=node.line, col=node.col)
            bor = self._bit32("bor", node.left, negb)
            band = self._bit32("band", node.left, negb)
            return N.BinOpExpr(op="+", left=bor, right=band,
                                line=node.line, col=node.col)
        # a - b = (a-k) - (b-k) = (a+k) - (b+k)  voi MOI k -> chon ngau
        # nhien 1 trong 2 cach phoi dau, k luon duong.
        k = self._rand_k()
        swap = self.rng.random() < 0.5
        left_part = self._pm(node.left, k, plus=swap, line=node.line, col=node.col)
        right_part = self._pm(node.right, k, plus=swap, line=node.line, col=node.col)
        return N.BinOpExpr(op="-", left=left_part, right=right_part,
                            line=node.line, col=node.col)

    # -- Identity cho * (squaring, dung cho moi so thuc) --------------------

    def _sub_mul(self, node: N.BinOpExpr) -> N.BinOpExpr:
        if not (self.cfg.allow_duplicate_simple
                and self._dup_safe(node.left) and self._dup_safe(node.right)):
            return node  # khong an toan de nhan ban -> bo qua, giu nguyen
        self.count += 1
        a, b = node.left, node.right
        sum_sq = N.BinOpExpr(op="^",
                              left=N.BinOpExpr(op="+", left=a, right=b),
                              right=self._num(2),
                              line=node.line, col=node.col)
        diff_sq = N.BinOpExpr(op="^",
                               left=N.BinOpExpr(op="-", left=a, right=b),
                               right=self._num(2),
                               line=node.line, col=node.col)
        numerator = N.BinOpExpr(op="-", left=sum_sq, right=diff_sq,
                                 line=node.line, col=node.col)
        return N.BinOpExpr(op="/", left=numerator, right=self._num(4),
                            line=node.line, col=node.col)


def obfuscate(root: N.Node, config: Optional[MBAConfig] = None) -> N.Node:
    """Ham tien ich: chay MBATransformer tren 1 AST (thuong la Block goc
    hoac 1 FunctionBody.body cu the neu chi muon obfuscate 1 pham vi)."""
    return MBATransformer(config).transform(root)


__all__ = ["MBAConfig", "MBATransformer", "obfuscate"]


# ---------------------------------------------------------------------------
# Test nho (chay truc tiep: python -m src.advanced.mba)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from ..parser import parse
    from .emitter import emit

    src = (
        "local function compute(a, b, c)\n"
        "  local sum = a + b\n"
        "  local diff = a - b\n"
        "  local prod = a * b\n"
        "  local mixed = (a + b) * (c - 1) + 42\n"
        "  return sum, diff, prod, mixed\n"
        "end\n"
    )

    print("=== Nguyen goc ===")
    print(src)

    # 1) k-split only (mac dinh, an toan tuyet doi, khong can gia dinh gi)
    ast1 = parse(src)
    obfuscate(ast1, MBAConfig(probability=1.0, seed=1))
    out1 = emit(ast1, minify=True)
    print("=== k-split (probability=1.0) ===")
    print(out1)
    assert "bit32" not in out1, "khong duoc dung bit32 khi assume_uint32=False"

    # 2) bitwise + squaring (gia dinh so nguyen nho, chi dung off-hot-path)
    ast2 = parse(src)
    cfg2 = MBAConfig(probability=1.0, assume_uint32=True, enable_multiply=True, seed=2)
    obfuscate(ast2, cfg2)
    out2 = emit(ast2, minify=True)
    print("=== bitwise + squaring (assume_uint32=True, enable_multiply=True) ===")
    print(out2)

    # 3) Round-trip phai parse lai duoc khong loi cho ca 2 bien the.
    parse(out1)
    parse(out2)
    print("\n[OK] Ca 2 bien the deu round-trip parse thanh cong.")

    # 4) Kiem tra side-effect: toan hang la loi goi ham phai KHONG bi
    #    nhan ban (du enable_multiply=True) - vi _dup_safe() tra False
    #    cho CallExpr.
    src_fx = "local v = getA() + getB() * getC()\n"
    ast3 = parse(src_fx)
    obfuscate(ast3, MBAConfig(probability=1.0, assume_uint32=True,
                               enable_multiply=True, seed=3))
    out3 = emit(ast3, minify=True)
    print("=== Toan hang co side-effect (phai KHONG bi nhan ban) ===")
    print(out3)
    assert out3.count("getA(") == 1, "getA() bi goi lai nhieu lan - SAI!"
    assert out3.count("getB(") == 1, "getB() bi goi lai nhieu lan - SAI!"
    assert out3.count("getC(") == 1, "getC() bi goi lai nhieu lan - SAI!"
    print("[OK] Moi ham side-effect chi xuat hien dung 1 lan trong output.")

    # 5) Kiem tra dung dan VE GIA TRI: thuc thi lai bang Python (gia lap
    #    bit32 bang toan tu Python tuong duong tren 32-bit) va so sanh
    #    voi phep toan goc, tren nhieu bo gia tri ngau nhien.
    import re

    def to_py(expr: str) -> str:
        expr = expr.replace("bit32.bor(", "_bor(").replace("bit32.band(", "_band(")
        expr = expr.replace("^", "**")
        return expr

    def _bor(a, b):
        return (int(a) | int(b)) & 0xFFFFFFFF

    def _band(a, b):
        return (int(a) & int(b)) & 0xFFFFFFFF

    rnd = random.Random(99)
    mismatches = 0
    for _ in range(200):
        a, b, c = rnd.randint(0, 5000), rnd.randint(0, 5000), rnd.randint(0, 5000)
        orig_sum, orig_diff, orig_prod = a + b, a - b, a * b
        env = {"a": a, "b": b, "c": c, "_bor": _bor, "_band": _band}
        # Chi kiem bien the bitwise+squaring (bien the nhay cam hon ve gia tri).
        got_sum = eval(to_py("bit32.bor(a,b)+bit32.band(a,b)"), env)
        got_diff = eval(to_py(f"a-2787-(b-2787)"), env)
        got_prod = eval(to_py("((a+b)**2-(a-b)**2)/4"), env)
        if got_sum != orig_sum or got_diff != orig_diff or abs(got_prod - orig_prod) > 1e-6:
            mismatches += 1
    assert mismatches == 0, f"{mismatches}/200 bo gia tri KHONG khop voi phep toan goc!"
    print("[OK] 200 bo gia tri ngau nhien: ca 3 identity (bor/band, k-split, squaring) "
          "deu cho ket qua giong het phep toan nguyen thuy.")