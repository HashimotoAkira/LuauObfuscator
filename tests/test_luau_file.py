import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from src.parser import nodes as N
from src.parser.parser import parse
from src.parser.traversal import walk_tree


def contains_node(block, node_type):
    found = False

    def check(node):
        nonlocal found
        if isinstance(node, node_type):
            found = True

    walk_tree(block, check)
    return found


def main() -> None:
    luau_path = os.path.join(ROOT, "test.luau")
    with open(luau_path, "r", encoding="utf-8") as f:
        source = f.read()

    block = parse(source)
    assert block.statements, "Expected at least one top-level statement"

    has_if = contains_node(block, N.IfStmt)
    has_numeric_for = contains_node(block, N.NumericForStmt)
    has_generic_for = contains_node(block, N.GenericForStmt)
    has_repeat = contains_node(block, N.RepeatStmt)
    has_continue = contains_node(block, N.ContinueStmt)
    has_goto = contains_node(block, N.GotoStmt)
    has_label = contains_node(block, N.LabelStmt)
    has_type_alias = contains_node(block, N.TypeAliasStmt)
    assert all((
        has_if,
        has_numeric_for,
        has_generic_for,
        has_repeat,
        has_continue,
        has_type_alias,
    ))
    assert not has_goto and not has_label, "Luau sample should use continue, not goto/labels"

    print(f"Parse OK: {luau_path}")
    print(f"Statements: {len(block.statements)}")
    print("Features found: types, functions, if, numeric/generic for, continue, repeat")


if __name__ == "__main__":
    main()
