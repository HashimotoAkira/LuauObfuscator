# -*- coding: utf-8 -*-
"""
src.advanced.anti_tamper
========================
Module phong ve runtime (Anti-Tamper) chong can thiep / hook / dump boi decompiler:
Theo dac ta tai muc 3.4 cua README / build.md:
  1. table.freeze() cho moi bang nhay cam (table khoa giai ma, cache table, config).
  2. Khoa metatable: `__metatable = "Locked"`, `__newindex` gay treo vong lap vo tan
     neu bi can thiep.
  3. Khong error("Hack detected") lo lieu: khi phat hien hook tren cac ham
     nguyen thuy, am tham lam sai lech du lieu tinh toan thay vi bao loi de gay
     roi loan decompiler / hacker.
  4. Khung code doc lap voi dialect: tu dong fallback an toan neu chay tren Lua
     thuong (khong co table.freeze) hoac ho tro day du tinh nang tren Luau / Roblox.
"""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Optional


@dataclass
class AntiTamperConfig:
    """Cau hinh cho tang phong ve Anti-Tamper."""
    enable_table_freeze: bool = True
    enable_metatable_lock: bool = True
    enable_silent_corruption: bool = True
    enable_roblox_binding: bool = False
    prefix: str = "_AT"


def _random_ident(prefix: str, length: int = 5) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    suffix = "".join(secrets.choice(alphabet) for _ in range(length))
    return f"{prefix}_{suffix}"


def render_anti_tamper_header(
    config: Optional[AntiTamperConfig] = None,
    dialect: str = "roblox",
) -> str:
    """Sinh doan code Lua/Luau dinh nghia cac co che phong ve Anti-Tamper.

    Tra ve chuoi code Lua can duoc chen vao dau payload (ngay sau string decoder).
    """
    if config is None:
        config = AntiTamperConfig()

    pfx = config.prefix
    freeze_func = f"{pfx}_f"
    lock_func = f"{pfx}_l"
    guard_func = f"{pfx}_g"

    lines: list[str] = [
        "-- [Anti-Tamper Active Defense]",
    ]

    # 1. table.freeze helper: ho tro ca Luau lan fallback Lua 5.1
    if config.enable_table_freeze:
        lines.append(
            f"local {freeze_func} = (table and table.freeze) or function(t) return t end"
        )
    else:
        lines.append(
            f"local {freeze_func} = function(t) return t end"
        )

    # 2. Metatable Locking & Trap:
    # Khoa bang bang __metatable = "Locked", va bat moi hanh vi ghi __newindex
    # bang 1 trap vo han hoac silent drop
    if config.enable_metatable_lock:
        lines.append(
            f"local function {lock_func}(t)\n"
            f"    local _mt = {{\n"
            f"        __metatable = \"Locked\",\n"
            f"        __newindex = function(_, _, _)\n"
            f"            while true do end\n"
            f"        end\n"
            f"    }}\n"
            f"    {freeze_func}(_mt)\n"
            f"    return setmetatable(t, _mt)\n"
            f"end"
        )
    else:
        lines.append(
            f"local function {lock_func}(t) return t end"
        )

    # 3. Silent Corruption Guard:
    # Kiem tra tinh toan ven cua cac primitive runtime (type, pcall, setmetatable).
    # Neu bat thuong (bi hook de doc lenh hoac intercept payload), khong bao gio
    # crash hay error ma am tham tra ve 1 do lech so hoc (offset) != 0 lam sai lech
    # toan bo ket qua tinh toan tiep theo.
    if config.enable_silent_corruption:
        roblox_check = ""
        if config.enable_roblox_binding and dialect == "roblox":
            roblox_check = (
                "    if type(game) ~= \"userdata\" and type(game) ~= \"table\" then d = d + 1337 end\n"
                "    if type(workspace) ~= \"userdata\" and type(workspace) ~= \"table\" then d = d + 7919 end\n"
            )

        lines.append(
            f"local function {guard_func}()\n"
            f"    local d = 0\n"
            f"    if type(type) ~= \"function\" then d = d + 31 end\n"
            f"    if type(pcall) ~= \"function\" then d = d + 17 end\n"
            f"    if type(setmetatable) ~= \"function\" then d = d + 23 end\n"
            f"    local ok, res = pcall(function() return 7 + 11 end)\n"
            f"    if not ok or res ~= 18 then d = d + 97 end\n"
            f"{roblox_check}"
            f"    return d\n"
            f"end"
        )
    else:
        lines.append(
            f"local function {guard_func}() return 0 end"
        )

    return "\n".join(lines)


__all__ = ["AntiTamperConfig", "render_anti_tamper_header"]

