from __future__ import annotations

import json
from typing import Any
from xml.etree import ElementTree as ET


def _failure_text(case: dict[str, Any]) -> str:
    details = [item["detail"] for item in case.get("assertions", []) if not item.get("passed")]
    if case.get("error") is not None:
        error = case["error"]
        details.append(json.dumps(error, ensure_ascii=False) if isinstance(error, dict) else str(error))
    return "\n".join(details) or "task assertions failed"


def render_junit(report: dict[str, Any]) -> str:
    cases = report.get("cases", [])
    failures = sum(1 for case in cases if not case.get("task_success"))
    total_seconds = sum(float(case.get("latency_ms", 0)) for case in cases) / 1000
    suite = ET.Element(
        "testsuite",
        {
            "name": str(report["suite"]["id"]),
            "tests": str(len(cases)),
            "failures": str(failures),
            "errors": "0",
            "skipped": "0",
            "time": f"{total_seconds:.6f}",
            "timestamp": str(report["verified_at"]),
        },
    )
    properties = ET.SubElement(suite, "properties")
    metadata = {
        "schema": report["schema"],
        "subject.id": report["subject"]["id"],
        "subject.version": report["subject"]["version"],
        "subject.digest": report["subject"]["digest"],
        "capability": report["capability"],
        "suite.digest": report["suite"]["digest"],
        "suite.mode": report["suite"]["mode"],
        "trust.level": report["trust"]["level"],
    }
    for name, value in metadata.items():
        ET.SubElement(properties, "property", {"name": name, "value": str(value)})
    for case in cases:
        test_case = ET.SubElement(
            suite,
            "testcase",
            {
                "classname": str(report["capability"]),
                "name": f"{case['case_id']}[attempt={case['attempt']}]",
                "time": f"{float(case['latency_ms']) / 1000:.6f}",
            },
        )
        if not case.get("task_success"):
            kind = "execution" if not case.get("execution_success") else "assertion"
            failure = ET.SubElement(test_case, "failure", {"type": kind, "message": "evaluation failed"})
            failure.text = _failure_text(case)
    ET.indent(suite, space="  ")
    return ET.tostring(suite, encoding="unicode", xml_declaration=True) + "\n"
