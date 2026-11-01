from __future__ import annotations

import json
from pathlib import Path

from skillrouter.cli import main
from skillrouter.discover import discover
from skillrouter.engine import invoke, list_capabilities
from skillrouter.evidence import evidence_summary
from tests.fakes import make_skill


URL = "https://v.douyin.com/evidence-test/"


def _report(path: Path, skill_id: str, version: str, digest: str, *, verified_at: str = "2026-11-01T00:00:00Z") -> Path:
    report = {
        "schema": "skill-eval.report.v1",
        "subject": {"id": skill_id, "version": version, "digest": f"sha256:{digest}"},
        "capability": "video.download@1",
        "suite": {
            "id": "test.video-download.offline",
            "version": "1.0.0",
            "digest": "sha256:" + "b" * 64,
            "mode": "offline",
        },
        "environment": {"os": "linux", "arch": "x86_64", "python": "3.12", "runtime": "command"},
        "results": {
            "runs": 1,
            "execution_success_rate": 1,
            "task_success_rate": 1,
            "p50_latency_ms": 10,
            "p95_latency_ms": 10,
            "average_cost_usd": None,
            "user_outcome_score": None,
            "metrics": {},
        },
        "cases": [],
        "trust": {"level": "ci-attested", "issuer": "test-ci"},
        "runner": {"name": "skill-eval", "version": "0.1.0"},
        "verified_at": verified_at,
    }
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _skill(skill_id: str):
    return next(skill for skill in discover()[0] if skill.id == skill_id)


def test_evidence_requires_exact_subject_version_digest_and_capability(skills_root, tmp_path):
    directory = make_skill(skills_root, "aaa.evaluated", {"action": "ok", "id": "1"})
    skill = _skill("aaa.evaluated")
    report = _report(tmp_path / "report.json", skill.id, skill.version, skill.content_hash)

    loaded = evidence_summary([report])
    assert loaded["ok"] is True
    assert loaded["reports"][0]["applicable"] is True

    body = json.loads(report.read_text(encoding="utf-8"))
    body["subject"]["version"] = "9.0.0"
    report.write_text(json.dumps(body), encoding="utf-8")
    wrong_version = evidence_summary([report])
    assert wrong_version["reports"][0]["inapplicable_reason"] == "VERSION_MISMATCH"

    body["subject"]["version"] = skill.version
    body["capability"] = "image.ocr@1"
    report.write_text(json.dumps(body), encoding="utf-8")
    wrong_capability = evidence_summary([report])
    assert wrong_capability["reports"][0]["inapplicable_reason"] == "CAPABILITY_NOT_PROVIDED"

    body["capability"] = "video.download@1"
    report.write_text(json.dumps(body), encoding="utf-8")
    (directory / "behavior.json").write_text('{"action":"ok","id":"changed"}', encoding="utf-8")
    stale = evidence_summary([report])
    assert stale["reports"][0]["applicable"] is False
    assert stale["reports"][0]["inapplicable_reason"] == "DIGEST_MISMATCH"


def test_configured_evidence_is_visible_but_does_not_change_default_ranking(
    skills_root, tmp_path, monkeypatch
):
    make_skill(skills_root, "aaa.default", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.evaluated", {"action": "ok", "id": "2"})
    skill = _skill("zzz.evaluated")
    report = _report(tmp_path / "evaluated.json", skill.id, skill.version, skill.content_hash)
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(report))

    listed = list_capabilities()
    capability = next(item for item in listed["capabilities"] if item["id"] == "video.download@1")
    platform = next(item for item in capability["platforms"] if item["id"] == "douyin")
    evaluated = next(item for item in platform["skills"] if item["id"] == "zzz.evaluated")
    assert evaluated["evaluation"][0]["results"]["task_success_rate"] == 1
    assert listed["evidence_errors"] == []

    result = invoke("video.download", {"url": URL}, yes=True)
    assert result["served_by"]["skill"] == "aaa.default"


def test_invalid_report_is_rejected_and_cli_returns_failure(skills_root, tmp_path, capsys):
    make_skill(skills_root, "aaa.evaluated", {"action": "ok", "id": "1"})
    skill = _skill("aaa.evaluated")
    report = _report(
        tmp_path / "invalid.json",
        skill.id,
        skill.version,
        skill.content_hash,
        verified_at="not-a-date",
    )

    assert main(["evidence", "--report", str(report)]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert "not a valid date-time" in payload["errors"][0]

