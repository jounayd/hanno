"""Safe, generic evaluator for a candidate's self-reported concrete numeric
witness. self_rated_rigor is just the model's opinion of itself; this is an
independent mechanical check where a candidate volunteers one concrete
arithmetic example that should hold if its claim is true. A single passing
example doesn't prove a universal claim, and a fabricated witness could
still be crafted to pass — but a *failing* example is unambiguous evidence
the claim, as stated, is wrong. This is exactly the kind of error found by
hand in an earlier review: a plausible-looking parametric formula that
actually produced a negative denominator for every case it claimed to cover.

This never executes model-supplied code. The equation string is parsed into
an AST and only a small fixed grammar of arithmetic/comparison operators is
interpreted, plus a narrow allowlist of pure functions (abs, min, max, len,
sum, gcd, floor, ceil) and set/list/tuple literals of numbers — there is no
reachable path to any other function call, attribute access, import, or
name lookup outside the given numeric assignments. The container support
exists because honest combinatorial witnesses need it: "all edge labels
distinct" is len({e1, e2, e3}) == 3, and gcd/floor/ceil exist because a
model that cannot compute a quantity inside the equation will otherwise
precompute it in its head and hand it in as an assignment — which is
exactly the self-fulfilling-witness pattern this module exists to prevent.
"""

from __future__ import annotations

import ast
import math
from fractions import Fraction

_ALLOWED_BINOPS = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.Pow: lambda a, b: a**b,
    ast.Mod: lambda a, b: a % b,
    ast.FloorDiv: lambda a, b: a // b,
}
_ALLOWED_UNARYOPS = {
    ast.USub: lambda a: -a,
    ast.UAdd: lambda a: a,
}
_ALLOWED_COMPARE = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
}

MAX_EXPONENT = 12  # guards against pathological a**b blowups (e.g. huge**huge)


class UnsafeExpression(Exception):
    pass


def _call_abs(args: list) -> Fraction:
    if len(args) != 1:
        raise UnsafeExpression("abs() takes exactly 1 argument")
    return abs(args[0])


def _call_min(args: list) -> Fraction:
    if not args:
        raise UnsafeExpression("min() needs at least 1 argument")
    return min(args)


def _call_max(args: list) -> Fraction:
    if not args:
        raise UnsafeExpression("max() needs at least 1 argument")
    return max(args)


def _call_len(args: list) -> Fraction:
    if len(args) != 1 or not isinstance(args[0], (set, frozenset, tuple)):
        raise UnsafeExpression("len() takes exactly 1 set/list/tuple argument")
    return Fraction(len(args[0]))


def _call_sum(args: list) -> Fraction:
    if len(args) == 1 and isinstance(args[0], (set, frozenset, tuple)):
        return sum(args[0], Fraction(0))
    raise UnsafeExpression("sum() takes exactly 1 set/list/tuple argument")


def _require_integers(args: list, fn: str) -> list[int]:
    out = []
    for a in args:
        if not isinstance(a, Fraction) or a.denominator != 1:
            raise UnsafeExpression(f"{fn}() requires integer arguments")
        out.append(int(a))
    return out


def _call_gcd(args: list) -> Fraction:
    if len(args) < 2:
        raise UnsafeExpression("gcd() needs at least 2 arguments")
    return Fraction(math.gcd(*(abs(v) for v in _require_integers(args, "gcd"))))


def _call_floor(args: list) -> Fraction:
    if len(args) != 1 or not isinstance(args[0], Fraction):
        raise UnsafeExpression("floor() takes exactly 1 numeric argument")
    return Fraction(math.floor(args[0]))


def _call_ceil(args: list) -> Fraction:
    if len(args) != 1 or not isinstance(args[0], Fraction):
        raise UnsafeExpression("ceil() takes exactly 1 numeric argument")
    return Fraction(math.ceil(args[0]))


# Deliberately tiny: pure, total, side-effect-free functions only. Reachable
# ONLY when node.func is a bare ast.Name matching one of these keys —
# ast.Attribute (e.g. "math.something") is never accepted, so there is no
# path to anything beyond this fixed set.
_ALLOWED_CALLS = {
    "abs": _call_abs,
    "min": _call_min,
    "max": _call_max,
    "len": _call_len,
    "sum": _call_sum,
    "gcd": _call_gcd,
    "floor": _call_floor,
    "ceil": _call_ceil,
}


def _eval_node(
    node: ast.AST, assignments: dict[str, Fraction]
) -> Fraction | bool | tuple | frozenset:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, assignments)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise UnsafeExpression(f"unsupported constant: {node.value!r}")
        return Fraction(node.value)
    if isinstance(node, ast.Name):
        if node.id not in assignments:
            raise UnsafeExpression(f"unknown name: {node.id}")
        return assignments[node.id]
    if isinstance(node, ast.Set):
        return frozenset(_eval_node(e, assignments) for e in node.elts)
    if isinstance(node, (ast.List, ast.Tuple)):
        return tuple(_eval_node(e, assignments) for e in node.elts)
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _eval_node(node.left, assignments)
        right = _eval_node(node.right, assignments)
        if type(node.op) is ast.Pow and (
            not isinstance(right, Fraction) or abs(right) > MAX_EXPONENT
        ):
            raise UnsafeExpression("exponent too large")
        return _ALLOWED_BINOPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARYOPS:
        return _ALLOWED_UNARYOPS[type(node.op)](_eval_node(node.operand, assignments))
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
            raise UnsafeExpression(f"unsupported call: {ast.dump(node.func)}")
        if node.keywords or any(isinstance(a, ast.Starred) for a in node.args):
            raise UnsafeExpression("keyword/starred arguments are not supported")
        args = [_eval_node(a, assignments) for a in node.args]
        return _ALLOWED_CALLS[node.func.id](args)
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, assignments)
        result = True
        for op, comparator in zip(node.ops, node.comparators, strict=True):
            if type(op) not in _ALLOWED_COMPARE:
                raise UnsafeExpression(f"unsupported comparison: {ast.dump(op)}")
            right = _eval_node(comparator, assignments)
            result = result and _ALLOWED_COMPARE[type(op)](left, right)
            left = right
        return result
    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, assignments) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval_node(v, assignments) for v in node.values)
        raise UnsafeExpression("unsupported boolean operator")
    raise UnsafeExpression(f"unsupported expression node: {type(node).__name__}")


def evaluate_numeric_check(numeric_check: dict | None) -> bool | None:
    """Returns True/False if the model-supplied numeric witness could be
    safely evaluated, or None if there was no witness or it was malformed —
    a malformed witness contributes no signal either way, it isn't treated
    as a failure."""
    if not numeric_check or not isinstance(numeric_check, dict):
        return None
    assignments_raw = numeric_check.get("assignments")
    equation = numeric_check.get("equation")
    if not isinstance(assignments_raw, dict) or not isinstance(equation, str):
        return None

    try:
        assignments = {
            str(k): Fraction(v)
            for k, v in assignments_raw.items()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        }
        if len(assignments) != len(assignments_raw):
            return None
        tree = ast.parse(equation, mode="eval")
        return bool(_eval_node(tree, assignments))
    except (
        UnsafeExpression,
        ZeroDivisionError,
        SyntaxError,
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
    ):
        return None
