from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ComparisonError(RuntimeError):
    pass


def _schema_path() -> Path:
    packaged = Path(__file__).resolve().parent / "schemas" / "report.schema.json"
    if packaged.is_file():
        return packaged
    return Path(__file__).resolve().parents[2] / "schemas" / "report.schema.json"


def load_reports(paths: list[Path]) -> list[dict[str, Any]]:
    if len(paths) < 2:
        raise ComparisonError("compare requires at least two reports")
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    reports = []
    for path in paths:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ComparisonError(f"cannot read report {path}: {exc}") from exc
        errors = sorted(validator.iter_errors(report), key=lambda item: list(item.path))
        if errors:
            raise ComparisonError(f"invalid report {path}: {errors[0].message}")
        reports.append(report)
    expected = reports[0]
    for report in reports[1:]:
        if report["capability"] != expected["capability"]:
            raise ComparisonError("reports use different capabilities")
        if report["suite"]["digest"] != expected["suite"]["digest"]:
            raise ComparisonError("reports use different suite content")
    return reports


def _percent(value: float | None) -> str:
    return "not measured" if value is None else f"{value * 100:.1f}%"


def _number(value: float | None, suffix: str = "") -> str:
    return "not measured" if value is None else f"{value:.3f}{suffix}"


def render_comparison(reports: list[dict[str, Any]]) -> str:
    first = reports[0]
    lines = [
        f"# Benchmark comparison: {first['capability']}",
        "",
        f"Suite: `{first['suite']['id']}` `{first['suite']['version']}` ({first['suite']['mode']})",
        f"Suite digest: `{first['suite']['digest']}`",
        "",
        "| Implementation | Execution | Task | CER | P50 latency | P95 latency | Cost/run | Verified |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for report in reports:
        results = report["results"]
        cer = results.get("metrics", {}).get("cer", {}).get("mean")
        cost = results.get("average_cost_usd")
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{report['subject']['id']}` `{report['subject']['version']}`",
                    _percent(results.get("execution_success_rate")),
                    _percent(results.get("task_success_rate")),
                    _percent(cer),
                    _number(results.get("p50_latency_ms"), " ms"),
                    _number(results.get("p95_latency_ms"), " ms"),
                    _number(cost, " USD"),
                    str(report["verified_at"]),
                ]
            )
            + " |"
        )
    lines.extend(["", "## Evidence", ""])
    for report in reports:
        environment = report["environment"]
        lines.append(
            f"- `{report['subject']['id']}`: subject `{report['subject']['digest']}`; "
            f"{environment['os']}/{environment['arch']}, Python {environment['python']}; "
            f"trust `{report['trust']['level']}` issued by `{report['trust']['issuer']}`."
        )
    lines.extend(
        [
            "",
            "> Latency includes process and model startup. Results are comparable only for the exact suite digest and recorded environments.",
            "",
        ]
    )
    return "\n".join(lines)
