from __future__ import annotations

import ast
from typing import Any

_ALLOWED_NODES = (
    ast.Expression,
    ast.BoolOp,
    ast.BinOp,
    ast.UnaryOp,
    ast.Compare,
    ast.Name,
    ast.Load,
    ast.Constant,
    ast.And,
    ast.Or,
    ast.Not,
    ast.Eq,
    ast.NotEq,
    ast.Lt,
    ast.LtE,
    ast.Gt,
    ast.GtE,
    ast.Is,
    ast.IsNot,
    ast.In,
    ast.NotIn,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.FloorDiv,
    ast.USub,
    ast.UAdd,
    ast.IfExp,
    ast.List,
    ast.Tuple,
    ast.Call,
    ast.keyword,
    ast.Attribute,
)

_ALLOWED_FUNCS = {"len": len, "str": str, "int": int, "bool": bool, "abs": abs, "float": float}


def _assert_safe(node: ast.AST) -> None:
    if not isinstance(node, _ALLOWED_NODES):
        raise ValueError(f"Disallowed expression: {type(node).__name__}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError("Only len/str/int/bool/abs/float() are allowed in expressions")
    if isinstance(node, ast.Attribute):
        raise ValueError("Attribute access is not allowed in expressions")
    for child in ast.iter_child_nodes(node):
        _assert_safe(child)


def safe_eval(expression: str, names: dict[str, Any]) -> Any:
    tree = ast.parse(expression, mode="eval")
    _assert_safe(tree)
    compiled = compile(tree, "<stm>", "eval")
    return eval(compiled, {"__builtins__": {}}, {**_ALLOWED_FUNCS, **names})
