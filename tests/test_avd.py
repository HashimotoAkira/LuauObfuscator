# -*- coding: utf-8 -*-
"""Test nhanh cff.py + dead_code.py bang cach parse test.luau, bien doi,
roi 'emit' lai thanh text Lua (bo dump toi gian, chi de kiem tra bang mat,
KHONG phai emitter.py chinh thuc cua du an)."""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from src.parser import parse
from src.advanced import cff, dead_code
from src.parser import nodes as N


def write_file(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as file:
        file.write(content)


def dump_expr(e) -> str:
    if e is None:
        return "nil"
    t = type(e).__name__
    if t == "NumberExpr":
        return e.raw
    if t == "StringExpr":
        if e.is_long_string:
            level = 0
            while "]" + ("=" * level) + "]" in e.value:
                level += 1
            equals = "=" * level
            return f"[{equals}[{e.value}]{equals}]"
        return e.raw or repr(e.value)
    if t == "NameExpr":
        return e.name
    if t == "NilExpr":
        return "nil"
    if t == "TrueExpr":
        return "true"
    if t == "FalseExpr":
        return "false"
    if t == "VarargExpr":
        return "..."
    if t == "BinOpExpr":
        return f"({dump_expr(e.left)} {e.op} {dump_expr(e.right)})"
    if t == "UnOpExpr":
        sp = " " if e.op not in ("-", "#") else ""
        return f"({e.op}{sp}{dump_expr(e.operand)})"
    if t == "ParenExpr":
        return f"({dump_expr(e.expr)})"
    if t == "IndexExpr":
        return f"{dump_expr(e.obj)}.{e.field}"
    if t == "IndexBracketExpr":
        return f"{dump_expr(e.obj)}[{dump_expr(e.key)}]"
    if t == "CallExpr":
        return f"{dump_expr(e.func)}({', '.join(dump_expr(a) for a in e.args)})"
    if t == "MethodCallExpr":
        return f"{dump_expr(e.obj)}:{e.method}({', '.join(dump_expr(a) for a in e.args)})"
    if t == "FunctionExpr":
        return f"function(...) {dump_block(e.body.body, 0)} end"
    if t == "TableConstructorExpr":
        return "{" + ", ".join(dump_field(f) for f in e.fields) + "}"
    if t == "TypeCastExpr":
        return dump_expr(e.expr)
    return f"<{t}>"


def dump_field(f) -> str:
    if f.key is None:
        return dump_expr(f.value)
    if f.is_computed_key:
        return f"[{dump_expr(f.key)}] = {dump_expr(f.value)}"
    return f"{f.key} = {dump_expr(f.value)}"


def ind(n):
    return "  " * n


def dump_block(block, depth) -> str:
    lines = []
    for s in block.statements:
        lines.append(dump_stmt(s, depth))
    return "\n".join(lines)


def dump_stmt(s, depth) -> str:
    t = type(s).__name__
    p = ind(depth)
    if t == "LocalAssignStmt":
        names = ", ".join(s.names)
        if s.values:
            return f"{p}local {names} = {', '.join(dump_expr(v) for v in s.values)}"
        return f"{p}local {names}"
    if t == "AssignStmt":
        return f"{p}{', '.join(dump_expr(x) for x in s.targets)} = {', '.join(dump_expr(v) for v in s.values)}"
    if t == "CompoundAssignStmt":
        return f"{p}{dump_expr(s.target)} {s.op}= {dump_expr(s.value)}"
    if t == "CallStmt":
        return f"{p}{dump_expr(s.call)}"
    if t == "DoStmt":
        return f"{p}do\n{dump_block(s.body, depth+1)}\n{p}end"
    if t == "WhileStmt":
        return f"{p}while {dump_expr(s.cond)} do\n{dump_block(s.body, depth+1)}\n{p}end"
    if t == "RepeatStmt":
        return f"{p}repeat\n{dump_block(s.body, depth+1)}\n{p}until {dump_expr(s.cond)}"
    if t == "IfStmt":
        out = []
        for i, c in enumerate(s.clauses):
            kw = "if" if i == 0 else "elseif"
            out.append(f"{p}{kw} {dump_expr(c.cond)} then")
            out.append(dump_block(c.body, depth + 1))
        if s.else_body is not None:
            out.append(f"{p}else")
            out.append(dump_block(s.else_body, depth + 1))
        out.append(f"{p}end")
        return "\n".join(out)
    if t == "NumericForStmt":
        step = f", {dump_expr(s.step)}" if s.step else ""
        return f"{p}for {s.var} = {dump_expr(s.start)}, {dump_expr(s.stop)}{step} do\n{dump_block(s.body, depth+1)}\n{p}end"
    if t == "GenericForStmt":
        return f"{p}for {', '.join(s.names)} in {', '.join(dump_expr(e) for e in s.exprs)} do\n{dump_block(s.body, depth+1)}\n{p}end"
    if t == "FunctionDeclStmt":
        sep = ":" if s.is_method else "."
        path = ".".join(s.name_path[:-1])
        tail = s.name_path[-1] if s.name_path else ""
        full = f"{path}{sep}{tail}" if path else tail
        return f"{p}function {full}(...)\n{dump_block(s.body.body, depth+1)}\n{p}end"
    if t == "LocalFunctionStmt":
        return f"{p}local function {s.name}(...)\n{dump_block(s.body.body, depth+1)}\n{p}end"
    if t == "ReturnStmt":
        return f"{p}return {', '.join(dump_expr(v) for v in s.values)}"
    if t == "BreakStmt":
        return f"{p}break"
    if t == "ContinueStmt":
        return f"{p}continue"
    if t == "GotoStmt":
        return f"{p}goto {s.label}"
    if t == "LabelStmt":
        return f"{p}::{s.name}::"
    if t == "TypeAliasStmt":
        return f"{p}-- type {s.name} (stripped)"
    return f"{p}--<unknown {t}>"


with open(os.path.join(ROOT, "test.luau"), encoding="utf-8") as f:
    src = f.read()
ast = parse(src)
print("=== PARSE OK ===")
print(f"top-level statements: {len(ast.statements)}")

# Lay than ham onHeartbeat de test CFF + dead_code (co nhieu statement
# tuan tu + 1 vai cau truc dieu khien -> ca nhan)
target = None
for s in ast.statements:
    if type(s).__name__ == "LocalFunctionStmt" and s.name == "onHeartbeat":
        target = s
        break

assert target is not None, "khong tim thay onHeartbeat trong test.luau"

print("\n=== BEFORE (onHeartbeat, nguyen ban) ===")
# print(dump_block(target.body.body, 0))

flattened = cff.flatten_deep(target.body.body, seed=42, min_len=2)
print("\n=== AFTER CFF (flatten_deep) ===")
#print(dump_block(flattened, 0))

with_junk = dead_code.inject(flattened, density=0.4, seed=7, deep=True)
print("\n=== AFTER CFF + dead_code.inject ===")
# print(dump_block(with_junk, 0))

# Sanity check: so luong statement phai tang len (co them rac + state machine)
print("\n=== SANITY ===")
# print("stmt count before:", len(target.body.body.statements))
# print("stmt count after cff:", len(flattened.statements))
# print("stmt count after cff+deadcode:", len(with_junk.statements))

def transform_functions(source, *, use_cff, use_dead_code):
    transformed_ast = parse(source)
    for stmt in transformed_ast.statements:
        if isinstance(stmt, N.LocalFunctionStmt):
            seed = 1
        elif isinstance(stmt, N.FunctionDeclStmt):
            seed = 3
        else:
            continue

        body = stmt.body.body
        if use_cff:
            body = cff.flatten_deep(body, seed=seed, min_len=2)
        if use_dead_code:
            body = dead_code.inject(
                body,
                density=0.25,
                seed=seed + 1,
                deep=True,
            )
        stmt.body.body = body

    return dump_block(transformed_ast, 0)


print("\n=== WRITE TRANSFORMED FILES ===")
outputs = (
    ("test_cff_output.luau", True, False),
    ("test_dead_code_output.luau", False, True),
    ("test_cff_dead_code_output.luau", True, True),
)
for filename, use_cff, use_dead_code in outputs:
    output_path = os.path.join(ROOT, filename)
    code = transform_functions(
        src,
        use_cff=use_cff,
        use_dead_code=use_dead_code,
    )
    write_file(output_path, code)
    print(f"Da ghi file: {output_path}")
