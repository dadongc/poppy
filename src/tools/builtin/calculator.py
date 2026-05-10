from __future__ import annotations

import ast
import math
import operator
from typing import Any, Callable

from src.common.types import ToolResult

# Safe operators and functions allowed in expressions
_BINOPS: dict[type, Callable[..., Any]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARYOPS: dict[type, Callable[..., Any]] = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_SAFE_FUNCS: dict[str, Callable[..., Any]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sum": sum,
    "int": int,
    "float": float,
    "sqrt": math.sqrt,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
}

_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
}


def _evaluate(node: ast.AST) -> Any:
    """Safely evaluate an AST node. Only allows known operators and functions."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return float(node.value)
        raise ValueError(f"unsupported constant type: {type(node.value).__name__}")
    if isinstance(node, ast.BinOp):
        binop_type = type(node.op)
        if binop_type not in _BINOPS:
            raise ValueError(f"unsupported operator: {binop_type.__name__}")
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        return _BINOPS[binop_type](left, right)
    if isinstance(node, ast.UnaryOp):
        unop_type = type(node.op)
        if unop_type not in _UNARYOPS:
            raise ValueError(f"unsupported unary operator: {unop_type.__name__}")
        return _UNARYOPS[unop_type](_evaluate(node.operand))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise ValueError("only simple function calls are allowed")
        if node.func.id not in _SAFE_FUNCS:
            raise ValueError(f"unsupported function: {node.func.id}")
        if node.keywords:
            raise ValueError("keyword arguments are not allowed")
        args = [_evaluate(a) for a in node.args]
        return _SAFE_FUNCS[node.func.id](*args)
    if isinstance(node, ast.Name):
        if node.id in _CONSTANTS:
            return _CONSTANTS[node.id]
        if node.id in _SAFE_FUNCS:
            return _SAFE_FUNCS[node.id]
        raise ValueError(f"unknown name: {node.id}")
    raise ValueError(f"unsupported expression type: {type(node).__name__}")


class CalculatorTool:
    name = "calculator"
    description = "安全地计算数学表达式，支持基础运算和常用数学函数。"
    schema = {
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": (
                    "数学表达式。支持: + - * / // % **、abs round min max sum、"
                    "sqrt sin cos tan log log10 exp、pi e。示例: sqrt(3**2 + 4**2)"
                ),
            },
        },
        "required": ["expression"],
    }
    scopes: list[str] = []
    is_builtin = True
    cacheable = False
    cache_ttl = 0

    async def execute(self, ctx, args):
        expr = args["expression"]
        try:
            tree = ast.parse(expr, mode="eval")
            result = _evaluate(tree.body)
            if isinstance(result, float) and result == int(result):
                result = int(result)
            return ToolResult(
                call_id="",
                name=self.name,
                status="ok",
                content=str(result),
            )
        except Exception as e:
            return ToolResult(
                call_id="",
                name=self.name,
                status="error",
                error_message=str(e),
            )
