from __future__ import annotations

import hashlib
import json
import platform
import statistics
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from skillcall_spec.resources import schema_path as spec_schema_path

from . import __version__
from .adapters.command import invoke as invoke_command
from .adapters.mcp import invoke as invoke_mcp
from .scorers import evaluate_assertion


class EvaluationError(RuntimeError):
    pass


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _directory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file() and "__pycache__" not in item.parts):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _load_yaml(path: Path) -> dict[str, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise EvaluationError(f"{path} must contain a mapping")
    return data


def _schema_path(name: str) -> Path:
    packaged = Path(__file__).resolve().parent / "schemas" / name
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / name


def _select_provision(manifest: dict[str, Any], capability: str) -> dict[str, Any]:
    candidates = [item for item in manifest.get("provides", []) if item.get("capability") == capability]
    if not candidates:
        raise EvaluationError(f"implementation does not provide {capability}")
    provision = candidates[0]
    entrypoint_type = provision.get("entrypoint", {}).get("type")
    if entrypoint_type not in {"command", "mcp"}:
        raise EvaluationError(f"skill-eval v0.1 does not support {entrypoint_type!r} entrypoints")
    return provision


def run_evaluation(
    implementation_dir: Path,
    suite_path: Path,
    *,
    repeat: int | None = None,
    expected_mode: str | None = None,
) -> dict[str, Any]:
    implementation_dir = implementation_dir.resolve()
    suite_path = suite_path.resolve()
    manifest_path = implementation_dir / "skill.capability.yaml"
    manifest = _load_yaml(manifest_path)
    manifest_schema = json.loads(spec_schema_path("implementation.schema.json").read_text(encoding="utf-8"))
    manifest_errors = sorted(
        Draft202012Validator(manifest_schema).iter_errors(manifest), key=lambda item: list(item.path)
    )
    if manifest_errors:
        raise EvaluationError(f"invalid implementation manifest: {manifest_errors[0].message}")
    subject_digest = _directory_digest(implementation_dir)
    suite_bytes = suite_path.read_bytes()
    suite = _load_yaml(suite_path)
    schema_path = _schema_path("evaluation-suite.schema.json")
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    errors = sorted(Draft202012Validator(schema).iter_errors(suite), key=lambda item: list(item.path))
    if errors:
        raise EvaluationError(f"invalid suite: {errors[0].message}")
    if expected_mode and suite["mode"] != expected_mode:
        raise EvaluationError(f"suite mode is {suite['mode']!r}, expected {expected_mode!r}")
    capability = suite["capability"]
    provision = _select_provision(manifest, capability)
    runtime = provision["entrypoint"]["type"]
    repetitions = repeat if repeat is not None else int(suite.get("repeat", 1))
    if repetitions < 1:
        raise EvaluationError("repeat must be at least 1")
    timeout_s = float(suite.get("limits", {}).get("timeout_s", provision.get("timeout_s", 60)))
    case_results: list[dict[str, Any]] = []
    latencies: list[float] = []
    executions = 0
    tasks = 0
    metric_values: dict[str, list[float]] = {}
    for case in suite["cases"]:
        for attempt in range(1, repetitions + 1):
            with tempfile.TemporaryDirectory(prefix="skill-eval-") as temp:
                temp_root = Path(temp)
                request = {
                    "skillcall": "0.1",
                    "request_id": f"eval_{uuid.uuid4().hex}",
                    "capability": capability,
                    "input": case["input"],
                    "context": {
                        "output_dir": str(temp_root / "out"),
                        "temp_dir": str(temp_root / "tmp"),
                        "timeout_s": timeout_s,
                        "locale": suite.get("environment", {}).get("locale", "en-US"),
                        "mode": "invoke",
                        "client_cwd": str(suite_path.parent),
                    },
                }
                (temp_root / "out").mkdir()
                (temp_root / "tmp").mkdir()
                if runtime == "command":
                    invoked = invoke_command(implementation_dir, provision, request, timeout_s)
                else:
                    invoked = invoke_mcp(provision["entrypoint"], case["input"], timeout_s)
            latencies.append(invoked.latency_ms)
            response = invoked.response or {}
            execution_success = invoked.error is None and response.get("ok") is True
            executions += int(execution_success)
            assertions = []
            if execution_success:
                for assertion in case.get("assertions", []):
                    evaluated = evaluate_assertion(response.get("output"), assertion)
                    assertions.append(evaluated)
                    metric = evaluated.get("metric")
                    if isinstance(metric, dict):
                        metric_values.setdefault(str(metric["name"]), []).append(float(metric["value"]))
            task_success = execution_success and all(item["passed"] for item in assertions)
            tasks += int(task_success)
            case_results.append(
                {
                    "case_id": case["id"],
                    "attempt": attempt,
                    "execution_success": execution_success,
                    "task_success": task_success,
                    "latency_ms": round(invoked.latency_ms, 3),
                    "assertions": assertions,
                    "error": invoked.error or (response.get("error") if not execution_success else None),
                }
            )
    total = len(case_results)
    return {
        "schema": "skill-eval.report.v1",
        "subject": {
            "id": str(manifest["id"]),
            "version": str(manifest["version"]),
            "digest": subject_digest,
        },
        "capability": capability,
        "suite": {
            "id": suite["id"],
            "version": str(suite["version"]),
            "digest": _sha256_bytes(suite_bytes),
            "mode": suite["mode"],
        },
        "environment": {
            "os": platform.system().lower(),
            "os_release": platform.release(),
            "arch": platform.machine().lower(),
            "python": platform.python_version(),
            "runtime": runtime,
            **suite.get("environment", {}),
        },
        "results": {
            "runs": total,
            "execution_success_rate": executions / total,
            "task_success_rate": tasks / total,
            "p50_latency_ms": round(statistics.median(latencies), 3) if latencies else None,
            "p95_latency_ms": round(_percentile(latencies, 0.95), 3) if latencies else None,
            "average_cost_usd": None,
            "user_outcome_score": None,
            "metrics": {
                name: {
                    "samples": len(values),
                    "mean": sum(values) / len(values),
                    "p50": statistics.median(values),
                    "p95": _percentile(values, 0.95),
                }
                for name, values in sorted(metric_values.items())
            },
        },
        "cases": case_results,
        "trust": {"level": "self-tested", "issuer": "local"},
        "runner": {"name": "skill-eval", "version": __version__},
        "verified_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
