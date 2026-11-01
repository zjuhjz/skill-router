from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .base import InvocationResult


def _command(entrypoint: dict[str, Any]) -> list[str]:
    raw = entrypoint.get("command")
    if not isinstance(raw, list) or not raw:
        raise ValueError("command entrypoint requires a non-empty command array")
    command = [str(part) for part in raw]
    replacements = {"$PYTHON": sys.executable}
    command[0] = replacements.get(command[0], command[0])
    return command


def invoke(
    implementation_dir: Path,
    provision: dict[str, Any],
    request: dict[str, Any],
    timeout_s: float,
) -> InvocationResult:
    command = _command(provision["entrypoint"])
    env = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH"}}
    env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=implementation_dir,
            input=json.dumps(request, ensure_ascii=False),
            text=True,
            encoding="utf-8",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_s,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return InvocationResult(None, (time.perf_counter() - started) * 1000, "TIMEOUT")
    except OSError as exc:
        return InvocationResult(None, (time.perf_counter() - started) * 1000, f"SPAWN_ERROR: {exc}")
    latency_ms = (time.perf_counter() - started) * 1000
    if completed.returncode != 0 and not completed.stdout.strip():
        detail = completed.stderr.strip()[-1000:]
        return InvocationResult(None, latency_ms, f"EXIT_{completed.returncode}: {detail}")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        return InvocationResult(None, latency_ms, f"INVALID_JSON: {exc}")
    if not isinstance(response, dict):
        return InvocationResult(None, latency_ms, "INVALID_RESPONSE: expected object")
    return InvocationResult(response, latency_ms)
