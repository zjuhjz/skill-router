"""Tiny expect language: path lookup, comparisons, len(), exists(), matches()."""

from __future__ import annotations

import re
from typing import Any

_MISSING = object()
_OPS = (">=", "<=", "==", "!=", ">", "<")


class ExpectError(ValueError):
    pass


def lookup(output: Any, path: str) -> Any:
    path = path.strip()
    if path == "output":
        return output
    if path.startswith("output."):
        path = path[len("output.") :]
    current: Any = output
    for part in re.findall(r"[^.\[\]]+|\[\d+\]", path):
        if not part:
            continue
        if part.startswith("["):
            index = int(part[1:-1])
            if not isinstance(current, list) or index >= len(current) or index < 0:
                return _MISSING
            current = current[index]
        else:
            if not isinstance(current, dict) or part not in current:
                return _MISSING
            current = current[part]
    return current


def _literal(text: str) -> Any:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    if text == "true":
        return True
    if text == "false":
        return False
    if text == "null":
        return None
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError as exc:
        raise ExpectError(f"bad literal: {text}") from exc


def _compare(left: Any, op: str, right: Any) -> bool:
    if left is _MISSING:
        return op == "!="
    try:
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        if op == ">":
            return left > right
        if op == ">=":
            return left >= right
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
    except TypeError:
        return False
    raise ExpectError(f"bad operator: {op}")


def evaluate(expr: str, output: Any) -> bool:
    text = (expr or "").strip()
    exists = re.fullmatch(r"exists\((.+)\)", text)
    if exists:
        return lookup(output, exists.group(1)) is not _MISSING
    length = re.fullmatch(r"len\((.+)\)\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)", text)
    if length:
        value = lookup(output, length.group(1))
        if value is _MISSING:
            return False
        try:
            size = len(value)
        except TypeError:
            return False
        return _compare(size, length.group(2), _literal(length.group(3)))
    matched = re.fullmatch(r"matches\((.+),\s*(.+)\)", text)
    if matched:
        value = lookup(output, matched.group(1))
        if not isinstance(value, str):
            return False
        pattern = _literal(matched.group(2))
        return re.search(str(pattern), value) is not None
    for op in _OPS:
        if op in text:
            left, right = text.split(op, 1)
            return _compare(lookup(output, left), op, _literal(right))
    raise ExpectError(f"cannot parse expect: {expr}")


def all_expect(exprs: list[str] | None, output: Any) -> tuple[bool, str | None]:
    for expr in exprs or []:
        try:
            ok = evaluate(str(expr), output)
        except ExpectError as exc:
            return False, str(exc)
        if not ok:
            return False, str(expr)
    return True, None
