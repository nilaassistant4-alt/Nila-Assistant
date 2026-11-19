"""Arithmetic nobody can hand a hang to.

The calculator used ``eval`` behind a character filter (digits, ``+ - * / % ( ) . e``).
An external audit passed ``9**9**9**9``: the filter had no objection, and ``eval`` then
ran for an unbounded time — a model request, or a prompt-injected one, could freeze
Nila. It is worse than the report knew: the filter also allowed ``e``, so
``9e999999999`` builds an integer with roughly a billion digits and simply *formatting*
it into the answer kills the process.

So this module does the arithmetic itself, on a small tree, with four limits that are
all cheap to check before any work happens:

* ``**`` may not chain (``2**3**4`` is refused, not bounded) — chaining is where the
  tower of exponents comes from;
* an exponent may not exceed :data:`MAX_EXPONENT`;
* a result may not exceed :data:`MAX_DIGITS` decimal digits;
* the whole evaluation has a :data:`MAX_SECONDS` budget.

Anything inside those limits is exact and identical to Python's own arithmetic
(``int`` for whole numbers, ``float`` when the input has a decimal point or an
exponent), because a calculator that is "safe" and also wrong is not an improvement.
"""
from __future__ import annotations

import ast
import time
from typing import Any, Union

Number = Union[int, float]

#: 1000^100 has 301 digits; 10**100 is a hundred digits. Anything a person asks out
#: loud is far below this, and anything above it is either a typo or an attack.
MAX_EXPONENT = 1000
#: The largest result, in decimal digits, that will be handed back.
MAX_DIGITS = 1000
#: Wall-clock budget for one whole expression. Real sums take microseconds.
MAX_SECONDS = 0.20
#: Refused outright: the only reason to write this is to make the machine work.
MAX_NODES = 200


class CalcError(ValueError):
    """The expression cannot be evaluated *cheaply* — or at all."""


_ALLOWED = (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant,
            ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod,
            ast.Pow, ast.UAdd, ast.USub, ast.Load)


def _digits_of(value: Number) -> int:
    if isinstance(value, int):
        return len(str(abs(value))) if value else 1
    return len(str(int(abs(value)))) if value not in (0, 0.0) else 1


def _check_result(value: Number) -> Number:
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise CalcError("the answer is not a finite number")
        return value
    if _digits_of(value) > MAX_DIGITS:
        raise CalcError(f"the answer would have more than {MAX_DIGITS} digits - "
                        "that is not a sum a person asks for")
    return value


def _pow(base: Number, exp: Number) -> Number:
    if isinstance(exp, float):
        if abs(exp) > MAX_EXPONENT:
            raise CalcError(f"an exponent above {MAX_EXPONENT} is refused")
        try:
            return _check_result(base ** exp)
        except OverflowError as exc:
            raise CalcError("that power is too large to work out") from exc
    if abs(exp) > MAX_EXPONENT:
        raise CalcError(f"an exponent above {MAX_EXPONENT} is refused - "
                        f"'{base}**{exp}' would never finish")
    # Bound BEFORE computing: 2**999999 would be a 300k-digit number, and building it
    # is already the attack. bit_length() of the base tells us the size in advance.
    if isinstance(base, int) and base not in (0, 1, -1) and exp > 0:
        projected = (abs(base).bit_length() - 1) * exp
        if projected > MAX_DIGITS * 3.33:
            raise CalcError(f"the answer would be a number with about "
                            f"{int(projected / 3.33):,} digits - refused")
    try:
        return _check_result(base ** exp)
    except (OverflowError, MemoryError) as exc:
        raise CalcError("that power is too large to work out") from exc


def _eval_node(node: ast.AST, deadline: float) -> Number:
    if time.monotonic() > deadline:
        raise CalcError("that took too long to work out, so it was stopped")
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, deadline)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("only numbers are allowed")
        return _check_result(node.value)
    if isinstance(node, ast.UnaryOp):
        val = _eval_node(node.operand, deadline)
        if isinstance(node.op, ast.USub):
            return -val
        if isinstance(node.op, ast.UAdd):
            return +val
        raise CalcError("only + and - may go in front of a number")
    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, deadline)
        right = _eval_node(node.right, deadline)
        op = node.op
        if isinstance(op, ast.Pow):
            # The one operator that can explode: never chained, always capped.
            if isinstance(node.right, ast.BinOp) and isinstance(node.right.op, ast.Pow):
                raise CalcError("a power of a power (2**3**4) is refused - write it "
                                "in steps if you really mean it")
            return _pow(left, right)
        try:
            if isinstance(op, ast.Add):
                return _check_result(left + right)
            if isinstance(op, ast.Sub):
                return _check_result(left - right)
            if isinstance(op, ast.Mult):
                # Two 500-digit numbers multiplied make 1000 digits; refuse before the
                # multiply so a crafted operand pair cannot allocate first.
                if isinstance(left, int) and isinstance(right, int) \
                        and _digits_of(left) + _digits_of(right) > MAX_DIGITS + 1:
                    raise CalcError("that multiplication is larger than a person needs")
                return _check_result(left * right)
            if isinstance(op, ast.Div):
                if right == 0:
                    raise CalcError("division by zero")
                return left / right
            if isinstance(op, ast.FloorDiv):
                if right == 0:
                    raise CalcError("division by zero")
                return _check_result(left // right)
            if isinstance(op, ast.Mod):
                if right == 0:
                    raise CalcError("division by zero")
                return _check_result(left % right)
        except OverflowError as exc:
            raise CalcError("that sum is too large to work out") from exc
        raise CalcError("only + - * / // % and ** may be used")
    raise CalcError("only numbers and + - * / // % ** ( ) are allowed")


def safe_eval(expr: str) -> Number:
    """Evaluate an arithmetic expression, or raise :class:`CalcError` quickly.

    Raises rather than returning a sentinel so a caller cannot accidentally use a
    failed result as a number.
    """
    text = (expr or "").strip()
    if not text:
        raise CalcError("expression is empty")
    if len(text) > 200:
        raise CalcError("expression is longer than 200 characters")
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"cannot read that expression: {exc.msg}") from exc
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise CalcError("that expression has too many parts")
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise CalcError("only numbers and + - * / // % ** ( ) are allowed")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise CalcError("only numbers are allowed")
        if isinstance(node, ast.Constant) and isinstance(node.value, float) \
                and (node.value != node.value or node.value in (float("inf"), float("-inf"))):
            raise CalcError("only finite numbers are allowed")
    # Defence in depth: the AST checks above are the real gate, but refuse a numeric
    # literal that is already absurd (10**4000 written out) before walking the tree.
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, int) \
                and _digits_of(node.value) > MAX_DIGITS:
            raise CalcError(f"a number with more than {MAX_DIGITS} digits is refused")
    return _eval_node(tree, time.monotonic() + MAX_SECONDS)


def explain(expr: str) -> Any:
    """Convenience for callers that want (ok, value_or_message)."""
    try:
        return True, safe_eval(expr)
    except CalcError as exc:
        return False, str(exc)
