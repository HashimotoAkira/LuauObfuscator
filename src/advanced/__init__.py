# -*- coding: utf-8 -*-
"""
src.advanced
============
Tang "lam roi nang cao": cff (control flow flattening), emitter (in AST
ra lai Lua source nen 1 dong), mba (mixed boolean arithmetic),
opaque_predicates, dead_code.

Hien tai da trien khai: emitter, mba.
"""

from .emitter import Emitter, EmitError, emit
from .mba import MBAConfig, MBATransformer, obfuscate as mba_obfuscate
from .anti_tamper import AntiTamperConfig, render_anti_tamper_header
from . import cff
from . import dead_code
from . import opaque_predicates
from . import anti_tamper

__all__ = [
    "Emitter", "EmitError", "emit",
    "MBAConfig", "MBATransformer", "mba_obfuscate",
    "AntiTamperConfig", "render_anti_tamper_header",
    "cff", "dead_code", "opaque_predicates", "anti_tamper",
]