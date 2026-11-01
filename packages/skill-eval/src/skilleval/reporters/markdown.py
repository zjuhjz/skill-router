from __future__ import annotations

import json
from typing import Any


def _percent(value: float | None) -> str:
    return "not measured" if value is None else f"{value * 100:.1f}%"


def _number(value: float | None, suffix: str = "") -> str:
    return "not measured" if value is None else f"{value:.3f}{suffix}"


def _escape(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    subject = report["subject"]
    suite = report["suite"]
    results = report["results"]
    environment = report["environment"]
    lines = [
        f"# Evaluation: {_escape(subject['id'])}",
        "",
        f"Capability: `{_escape(report['capability'])}`  ",
        f"Suite: `{_escape(suite['id'])}` `{_escape(suite['version'])}` ({_escape(suite['mode'])})  ",
        f"Verified: `{_escape(report['verified_at'])}`",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Runs | {results['runs']} |",
        f"| Execution success | {_percent(results['execution_success_rate'])} |",
        f"| Task success | {_percent(results['task_success_rate'])} |",
        f"| User outcome | {_percent(results.get('user_outcome_score'))} |",
        f"| P50 latency | {_number(results.get('p50_latency_ms'), ' ms')} |",
        f"| P95 latency | {_number(results.get('p95_latency_ms'), ' ms')} |",
        *(
            f"| {_escape(name.upper())} mean | {_number(metric.get('mean'))} |"
            for name, metric in sorted(results.get("metrics", {}).items())
        ),
        f"| Average cost | {_number(results.get('average_cost_usd'), ' USD')} |",
        "",
        "## Environment",
        "",
        "| Field | Value |",
        "|---|---|",
    ]
    for key in sorted(environment):
        lines.append(f"| {_escape(key)} | {_escape(environment[key])} |")
    lines.extend(
        [
            "",
            "## Cases",
            "",
            "| Case | Attempt | Execution | Task | Latency | Detail |",
            "|---|---:|:---:|:---:|---:|---|",
        ]
    )
    for case in report.get("cases", []):
        details = [item["detail"] for item in case.get("assertions", []) if not item.get("passed")]
        if case.get("error") is not None:
            error = case["error"]
            details.append(json.dumps(error, ensure_ascii=False) if isinstance(error, dict) else str(error))
        lines.append(
            "| {case} | {attempt} | {execution} | {task} | {latency:.3f} ms | {detail} |".format(
                case=_escape(case["case_id"]),
                attempt=case["attempt"],
                execution="pass" if case["execution_success"] else "fail",
                task="pass" if case["task_success"] else "fail",
                latency=float(case["latency_ms"]),
                detail=_escape("; ".join(details) if details else "—"),
            )
        )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Subject digest: `{_escape(subject['digest'])}`",
            f"- Suite digest: `{_escape(suite['digest'])}`",
            *(
                [f"- Fixture manifest digest: `{_escape(suite['fixtures']['digest'])}`"]
                if suite.get("fixtures")
                else []
            ),
            f"- Trust: `{_escape(report['trust']['level'])}` issued by `{_escape(report['trust']['issuer'])}`",
            f"- Runner: `{_escape(report['runner']['name'])}` `{_escape(report['runner']['version'])}`",
            "",
        ]
    )
    return "\n".join(lines)
