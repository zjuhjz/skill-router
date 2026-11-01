from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class InvocationResult:
    response: dict[str, Any] | None
    latency_ms: float
    error: str | None = None
