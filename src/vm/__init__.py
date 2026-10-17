# -*- coding: utf-8 -*-
"""
src.vm
======
Package may ao (VM) register-based, Zero-GC cho LuauObfuscator.

Hien tai da co:
    - opcode.py             : dinh nghia 32 opcode + dinh dang bit-packing 32-bit
    - environment.py        : constant pool + register allocator + resolve
                               local/upvalue/global, dung lam ngu canh bien dich
    - compiler.py            : dich AST (src/parser/nodes.py) thanh FunctionProto
                               (bytecode + constant pool), dung opcode.py +
                               environment.py o tren. Xem compiler.py de biet
                               ro cac gioi han hien tai (constant-pool,
                               goto/label...)
    - runtime_template.py    : sinh 1 file .lua HOAN CHINH, tu chay duoc, nhung
                               bytecode vao 1 VM dispatcher 32 opcode (cay nhi
                               phan 5 tang) + "bo giai nen buffer" (giai ma lenh
                               32-bit thanh 4 mang song song luc cold start)
"""

from .opcode import (
    Opcode, OperandKind, OpcodeInfo, OPCODE_INFO,
    RISC_OPCODES, CISC_OPCODES, NAME_TO_OPCODE,
    encode_instruction, decode_instruction, disassemble,
    OpcodeError, OpcodeEncodeError, OpcodeDecodeError,
)
from .environment import (
    ConstantPool, RegisterAllocator, Environment, NameResolution,
    EnvironmentError, ConstantPoolError, ConstantPoolOverflowError,
    RegisterAllocatorError, RegisterOverflowError,
)
from .compiler import Compiler, CompilerError, FunctionProto, compile_chunk
from .runtime_template import render_runtime, compile_and_render

__all__ = [
    "Opcode", "OperandKind", "OpcodeInfo", "OPCODE_INFO",
    "RISC_OPCODES", "CISC_OPCODES", "NAME_TO_OPCODE",
    "encode_instruction", "decode_instruction", "disassemble",
    "OpcodeError", "OpcodeEncodeError", "OpcodeDecodeError",
    "ConstantPool", "RegisterAllocator", "Environment", "NameResolution",
    "EnvironmentError", "ConstantPoolError", "ConstantPoolOverflowError",
    "RegisterAllocatorError", "RegisterOverflowError",
    "Compiler", "CompilerError", "FunctionProto", "compile_chunk",
    "render_runtime", "compile_and_render",
]
