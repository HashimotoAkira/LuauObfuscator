# -*- coding: utf-8 -*-
"""
environment.py
===============
("enviroment" trong ten file cu la loi chinh ta - sua lai dung chinh ta
o day, theo dung yeu cau trong README muc 2).

Quan ly "moi truong bien dich" (compile-time environment) ma `vm/compiler.py`
can de dich AST thanh bytecode dung dinh dang trong `opcode.py`:

    1. ConstantPool  - bang hang so (K[]) cho tung function prototype:
                        so, chuoi, bool, nil. Tu dong khu trung lap (dedup)
                        de LOADK/GETGLOBAL/... tro toi cung 1 chi so neu
                        gia tri giong het nhau.
    2. RegisterAllocator - cap phat thanh ghi (R[]) theo scope (khoi lenh),
                        ho tro "mark/release" de tai su dung thanh ghi tam
                        thoi trong luc dinh gia bieu thuc (giong ky thuat
                        cua Lua FuncState that).
    3. Environment    - goi 2 thu tren lai lam "ngu canh bien dich" cho
                        1 function dang duoc dich, cong them lien ket toi
                        Environment cha (ham bao quanh) de phan giai
                        local / upvalue / global cho tung ten bien.

QUAN TRONG - gioi han thiet ke (de compiler.py biet truoc):
    - Thanh ghi dich (truong A, 8 bit) toi da 256 thanh ghi/ham. Neu 1 ham
      qua phuc tap vuot qua con so nay, compiler.py can tach nho ham do
      (xem README muc 4 "Quy uoc code nguon dau vao": ham dai 30-50 dong
      phai cat nho) - environment.py chi phat hien & bao loi ro rang,
      khong tu dong "spill" ra bo nho.
    - Constant pool gioi han theo suc chua cua cac operand mo rong trong
      bytecode; cac opcode truy cap bang K co duong fallback qua LOADK.
    - Upvalue duoc danh dau theo thanh ghi cua ham cha va truyen qua cac
      closure trung gian. Cac thanh ghi bi capture duoc compiler box de
      cac closure cung thay duoc thay doi gia tri.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .opcode import A_MAX


class EnvironmentError(Exception):
    """Loi chung cho module environment (constant pool day, thanh ghi tran...)."""
    pass


class ConstantPoolError(EnvironmentError):
    pass


class ConstantPoolOverflowError(ConstantPoolError):
    pass


class RegisterAllocatorError(EnvironmentError):
    pass


class RegisterOverflowError(RegisterAllocatorError):
    pass


# ---------------------------------------------------------------------------
# 1. ConstantPool
# ---------------------------------------------------------------------------

class ConstantPool:
    """Bang hang so (K[]) cho 1 function prototype.

    Ho tro 4 kieu gia tri nguyen thuy ma Luau co the dung lam hang so:
    nil (None), bool, so (int/float), chuoi (str). Tu dong dedup: them 2
    lan cung 1 gia tri chi tra ve CUNG 1 chi so.

    Luu y ve dedup: Python coi `True == 1` va `1 == 1.0`, nen neu chi dung
    `value` lam key se dedup NHAM (vd hang so `true` va hang so `1` bi gop
    lam 1!). Vi vay key dedup phai bao gom CA KIEU DU LIEU.
    """

    def __init__(self, max_size: int = 262656):
        self._max_size = max_size
        self._values: List[Any] = []
        self._index: Dict[Tuple[str, Any], int] = {}

    @staticmethod
    def _dedup_key(value: Any) -> Tuple[str, Any]:
        if value is None:
            return ("nil", None)
        if isinstance(value, bool):          # PHAI kiem tra truoc int (bool la subclass cua int)
            return ("bool", value)
        if isinstance(value, int):
            return ("int", value)
        if isinstance(value, float):
            return ("float", value)
        if isinstance(value, str):
            return ("str", value)
        raise ConstantPoolError(
            f"Kieu hang so khong duoc ho tro trong constant pool: {type(value).__name__}"
        )

    def add(self, value: Any) -> int:
        """Them 1 hang so (neu chua co) va tra ve chi so cua no trong K[]."""
        key = self._dedup_key(value)
        existing = self._index.get(key)
        if existing is not None:
            return existing
        if len(self._values) >= self._max_size:
            raise ConstantPoolOverflowError(
                f"Constant pool day (da co {self._max_size} hang so). "
                f"Can chien luoc 'spill' (vd gop hang so vao 1 table literal "
                f"roi GETTABLE_K voi chi so so) - chua duoc trien khai o buoc nay."
            )
        idx = len(self._values)
        self._values.append(value)
        self._index[key] = idx
        return idx

    def get(self, index: int) -> Any:
        if not (0 <= index < len(self._values)):
            raise ConstantPoolError(f"Chi so hang so khong hop le: {index}")
        return self._values[index]

    def to_list(self) -> List[Any]:
        """Tra ve ban sao danh sach hang so, theo dung thu tu chi so (dung
        de serialize vao blob nhi phan o buoc dong goi - xem basic/strings.py
        va utils/crypto.py cho phan XOR + gop chuoi thanh 1 blob).
        """
        return list(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        return f"ConstantPool({len(self._values)}/{self._max_size} hang so)"


# ---------------------------------------------------------------------------
# 2. RegisterAllocator
# ---------------------------------------------------------------------------

class RegisterAllocator:
    """Cap phat thanh ghi (R[]) cho 1 function prototype, theo scope.

    Mo hinh giong 1 "stack" thanh ghi:
        - push_scope()/pop_scope(): ung voi 1 khoi lenh Block trong AST
          (than if/while/for/do...). Bien local khai bao trong scope se
          duoc "giai phong" (tai su dung thanh ghi) khi scope ket thuc.
        - mark()/release_to(): phien ban "nhe" hon, dung TRONG LUC dinh
          gia 1 bieu thuc (vd tinh toan hang nhieu thanh ghi tam cho cac
          toan hang trung gian) - KHONG gan ten bien, chi la vung nho tam.

    Luu y rieng cho RepeatStmt (`repeat ... until cond`): theo ngu nghia
    Lua, bien local khai bao trong than lap VAN con hieu luc khi danh gia
    `cond`. Vi vay compiler.py KHONG duoc goi pop_scope() cho than repeat
    truoc khi dich xong dieu kien `until` (xem ghi chu tai nodes.RepeatStmt).
    """

    def __init__(self):
        self._next_free = 0
        self._max_used = 0
        self._scopes: List[Dict[str, int]] = []
        self._scope_marks: List[int] = []

    # -- Scope (gan voi 1 Block trong AST) -------------------------------

    def push_scope(self) -> None:
        self._scopes.append({})
        self._scope_marks.append(self._next_free)

    def pop_scope(self) -> None:
        if not self._scopes:
            raise RegisterAllocatorError("pop_scope() duoc goi nhung khong co scope nao dang mo")
        self._scopes.pop()
        self._next_free = self._scope_marks.pop()

    @property
    def scope_depth(self) -> int:
        return len(self._scopes)

    # -- Cap phat thanh ghi ------------------------------------------------

    def alloc(self, name: Optional[str] = None) -> int:
        """Cap phat 1 thanh ghi moi. Neu `name` duoc cho, thanh ghi duoc
        gan (bind) vao ten do trong SCOPE HIEN TAI (can mo scope truoc
        bang push_scope()), de resolve() sau nay tim lai duoc.
        """
        idx = self._next_free
        if idx > A_MAX:
            raise RegisterOverflowError(
                f"Vuot qua so thanh ghi toi da ({A_MAX + 1}) cho 1 ham. "
                f"Ham qua phuc tap - can tach nho theo README muc 4 "
                f"(Rule of Three / ham > 30-50 dong phai cat nho)."
            )
        self._next_free += 1
        if self._next_free > self._max_used:
            self._max_used = self._next_free

        if name is not None:
            if not self._scopes:
                raise RegisterAllocatorError(
                    "alloc(name=...) can it nhat 1 scope dang mo - goi push_scope() truoc"
                )
            self._scopes[-1][name] = idx
        return idx

    def bind(self, name: str, register: int) -> None:
        """Gan `name` vao 1 thanh ghi DA CAP PHAT TU TRUOC (khong cap phat
        moi) trong scope hien tai. Dung cho truong hop thanh ghi can duoc
        tao THEO 1 THU TU CO DINH (vd 3 thanh ghi lien tuc index/limit/step
        cua numeric-for) roi MOI gan ten sau, thay vi dung alloc(name=...)
        thong thuong (von luon cap phat 1 thanh ghi MOI).
        """
        if not self._scopes:
            raise RegisterAllocatorError(
                "bind() can it nhat 1 scope dang mo - goi push_scope() truoc"
            )
        self._scopes[-1][name] = register

    def alloc_temp(self) -> int:
        """Cap phat 1 thanh ghi TAM (khong gan ten), dung lam noi chua
        ket qua trung gian khi dinh gia bieu thuc. Nen di kem mark()/
        release_to() de tai su dung sau khi dung xong.
        """
        return self.alloc(name=None)

    # -- Mark / release (tai su dung thanh ghi tam trong 1 bieu thuc) ----

    def mark(self) -> int:
        """Chup lai vi tri "dinh stack" thanh ghi hien tai."""
        return self._next_free

    def release_to(self, mark: int) -> None:
        """Giai phong moi thanh ghi tam da cap phat SAU moc `mark`
        (dua con tro cap phat ve lai `mark`). KHONG duoc giai phong xuong
        thap hon muc cua scope dang mo (se de ghi de len bien local that).
        """
        if mark > self._next_free:
            raise RegisterAllocatorError(
                f"release_to({mark}): mark lon hon vi tri cap phat hien tai ({self._next_free})"
            )
        if self._scope_marks and mark < self._scope_marks[-1]:
            raise RegisterAllocatorError(
                f"release_to({mark}): khong duoc giai phong xuong duoi muc scope hien tai "
                f"({self._scope_marks[-1]}) - se de ghi de bien local dang con hieu luc"
            )
        self._next_free = mark

    # -- Tra ten bien ve thanh ghi ----------------------------------------

    def resolve(self, name: str) -> Optional[int]:
        """Tim thanh ghi gan voi `name`, duyet tu scope trong cung ra
        ngoai (gan nhat thang - dung ngu nghia shadowing cua Lua). Tra
        None neu khong tim thay trong CAC SCOPE CUA HAM NAY (goi la bien
        chua duoc khai bao local trong ham -> upvalue hoac global, do
        Environment ben ngoai xu ly tiep).
        """
        for scope in reversed(self._scopes):
            if name in scope:
                return scope[name]
        return None

    @property
    def max_registers_used(self) -> int:
        """So thanh ghi lon nhat tung dung cung luc - dung de sizing mang
        R[] (`table.create(max_registers_used)`) khi emit runtime_template.
        """
        return self._max_used

    def __repr__(self) -> str:
        return (f"RegisterAllocator(next_free={self._next_free}, "
                f"max_used={self._max_used}, scope_depth={len(self._scopes)})")


# ---------------------------------------------------------------------------
# 3. Environment: ngu canh bien dich cho 1 function
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class NameResolution:
    """Ket qua phan giai 1 ten bien (NameExpr.name) trong ngu canh bien dich.

    kind:
        'local'   - bien local cua CHINH ham dang dich; `register` la chi
                    so thanh ghi R[] chua no.
        'upvalue' - bien local cua 1 ham BAO QUANH (closure). `register`
                    la index trong bang upvalue cua function hien tai.
        'global'  - khong tim thay o bat ky ham bao quanh nao -> truy cap
                    qua GETGLOBAL/SETGLOBAL (vd `print`, `game`, `workspace`,
                    hoac bien toan cuc nguoi dung tu khai bao khong co 'local').
    """
    kind: str
    register: Optional[int] = None


class Environment:
    """Ngu canh bien dich cho 1 function prototype dang duoc dich.

    Moi FunctionBody (ham) trong AST tuong ung 1 Environment rieng (thanh
    ghi la "cuc bo" cho tung ham, giong Lua that), lien ket toi Environment
    cua ham bao quanh qua `parent` de ho tro resolve upvalue/global.
    """

    def __init__(self, parent: Optional["Environment"] = None):
        self.parent = parent
        self.constants = ConstantPool()
        self.registers = RegisterAllocator()
        self.captured_registers: set[int] = set()
        self.upvalues: List[Tuple[str, int]] = []
        self._upvalue_indexes: Dict[Tuple[str, int], int] = {}

    def child(self) -> "Environment":
        """Tao Environment moi cho 1 ham LONG BEN TRONG ham hien tai
        (vd khi dich 1 FunctionExpr/FunctionDeclStmt nam trong than ham nay).
        """
        return Environment(parent=self)

    def resolve_name(self, name: str) -> NameResolution:
        """Phan giai 1 ten bien theo thu tu: local cua ham nay -> local
        cua cac ham bao quanh (upvalue) -> global (fallback cuoi cung,
        luon thanh cong vi Lua cho phep doc/ghi global tu do).
        """
        local_reg = self.registers.resolve(name)
        if local_reg is not None:
            return NameResolution(kind="local", register=local_reg)

        if self.parent is not None:
            parent_resolution = self.parent.resolve_name(name)
            if parent_resolution.kind == "local":
                register = parent_resolution.register
                self.parent.captured_registers.add(register)
                return self._add_upvalue("local", register)
            if parent_resolution.kind == "upvalue":
                return self._add_upvalue("upvalue", parent_resolution.register)

        return NameResolution(kind="global")

    def _add_upvalue(self, source_kind: str, source_index: int) -> NameResolution:
        key = (source_kind, source_index)
        index = self._upvalue_indexes.get(key)
        if index is None:
            index = len(self.upvalues)
            self._upvalue_indexes[key] = index
            self.upvalues.append(key)
        return NameResolution(kind="upvalue", register=index)

    def name_constant(self, name: str) -> int:
        """Tien ich: them ten bien (string) vao constant pool, dung lam
        toan hang K[] cho GETGLOBAL/SETGLOBAL/GETTABLE_K/SETTABLE_K/
        CALL_GLOBAL/CALL_METHOD.
        """
        return self.constants.add(name)

    def __repr__(self) -> str:
        depth = 0
        env = self.parent
        while env is not None:
            depth += 1
            env = env.parent
        return f"Environment(nesting_depth={depth}, {self.constants!r}, {self.registers!r})"


__all__ = [
    "ConstantPool", "RegisterAllocator", "Environment", "NameResolution",
    "EnvironmentError", "ConstantPoolError", "ConstantPoolOverflowError",
    "RegisterAllocatorError", "RegisterOverflowError",
]
