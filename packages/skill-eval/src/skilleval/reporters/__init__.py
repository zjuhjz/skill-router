from __future__ import annotations

from typing import Any, Callable

from .json import render_json
from .junit import render_junit
from .markdown import render_markdown


REPORTERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "json": render_json,
    "markdown": render_markdown,
    "junit": render_junit,
}


def render(report: dict[str, Any], format_name: str) -> str:
    try:
        reporter = REPORTERS[format_name]
    except KeyError as exc:
        raise ValueError(f"unknown report format: {format_name}") from exc
    return reporter(report)


__all__ = ["REPORTERS", "render", "render_json", "render_junit", "render_markdown"]
