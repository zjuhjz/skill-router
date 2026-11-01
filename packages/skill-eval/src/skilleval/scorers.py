from __future__ import annotations

import re
from typing import Any

from .transcript import character_error_rate, word_error_rate


MISSING = object()


def pointer_get(document: Any, pointer: str) -> Any:
    if pointer == "":
        return document
    current = document
    for raw in pointer.lstrip("/").split("/"):
        part = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return MISSING
    return current


def score_assertion(output: Any, assertion: dict[str, Any]) -> tuple[bool, str]:
    result = evaluate_assertion(output, assertion)
    return result["passed"], result["detail"]


def evaluate_assertion(output: Any, assertion: dict[str, Any]) -> dict[str, Any]:
    path = str(assertion.get("path", ""))
    operation = assertion.get("op")
    actual = pointer_get(output, path)
    expected = assertion.get("value")
    if operation == "exists":
        passed = (actual is not MISSING) == bool(assertion.get("value", True))
    elif actual is MISSING:
        return {"passed": False, "detail": f"{path}: value is missing"}
    elif operation == "exact":
        passed = actual == expected
    elif operation == "contains":
        if isinstance(actual, str):
            passed = str(expected) in actual
        elif isinstance(actual, (list, tuple, set, dict)):
            passed = expected in actual
        else:
            passed = False
    elif operation == "regex":
        try:
            passed = re.search(str(expected), str(actual)) is not None
        except re.error as exc:
            return {"passed": False, "detail": f"{path}: invalid regex: {exc}"}
    elif operation == "numeric_tolerance":
        tolerance = float(assertion.get("tolerance", 0))
        try:
            passed = abs(float(actual) - float(expected)) <= tolerance
        except (TypeError, ValueError):
            passed = False
    elif operation == "set_overlap":
        try:
            left, right = set(actual), set(expected)
            denominator = len(right)
            overlap = len(left & right) / denominator if denominator else 1.0
            passed = overlap >= float(assertion.get("minimum", 1.0))
        except TypeError:
            passed = False
    elif operation in {"cer_at_most", "wer_at_most"}:
        if not isinstance(actual, str) or not isinstance(expected, str):
            return {"passed": False, "detail": f"{path}: transcript and reference must be strings"}
        lowercase = bool(assertion.get("lowercase", True))
        strip_punctuation = bool(assertion.get("strip_punctuation", False))
        metric_name = "cer" if operation == "cer_at_most" else "wer"
        metric_value = (
            character_error_rate(
                expected,
                actual,
                lowercase=lowercase,
                strip_punctuation=strip_punctuation,
            )
            if metric_name == "cer"
            else word_error_rate(
                expected,
                actual,
                lowercase=lowercase,
                strip_punctuation=strip_punctuation,
            )
        )
        maximum = float(assertion["maximum"])
        passed = metric_value <= maximum
        return {
            "passed": passed,
            "detail": f"{path or '/'}: {metric_name}={metric_value:.6f} maximum={maximum:.6f}",
            "metric": {"name": metric_name, "value": metric_value},
        }
    else:
        return {"passed": False, "detail": f"{path}: unsupported assertion {operation!r}"}
    return {"passed": passed, "detail": f"{path or '/'}: {operation} {'passed' if passed else 'failed'}"}
