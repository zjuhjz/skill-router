from __future__ import annotations

import json
from pathlib import Path

from skillrouter.cli import main
from skillrouter.discover import discover
from skillrouter.engine import invoke, list_capabilities
from skillrouter.evidence import evidence_summary
from tests.fakes import make_skill


URL = "https://v.douyin.com/evidence-test/"


def _report(
    path: Path,
    skill_id: str,
    version: str,
    digest: str,
    *,
    verified_at: str = "2026-11-01T00:00:00Z",
    task_success_rate: float = 1,
    p95_latency_ms: float = 10,
    average_cost_usd: float | None = None,
    suite_digest: str = "b" * 64,
) -> Path:
    report = {
        "schema": "skill-eval.report.v1",
        "subject": {"id": skill_id, "version": version, "digest": f"sha256:{digest}"},
        "capability": "video.download@1",
        "suite": {
            "id": "test.video-download.offline",
            "version": "1.0.0",
            "digest": "sha256:" + suite_digest,
            "mode": "offline",
        },
        "environment": {"os": "linux", "arch": "x86_64", "python": "3.12", "runtime": "command"},
        "results": {
            "runs": 1,
            "execution_success_rate": 1,
            "task_success_rate": task_success_rate,
            "p50_latency_ms": 10,
            "p95_latency_ms": p95_latency_ms,
            "average_cost_usd": average_cost_usd,
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


def test_explicit_evaluation_policy_ranks_comparable_evidence_and_explains_choice(
    skills_root, tmp_path, monkeypatch
):
    make_skill(skills_root, "aaa.lower-quality", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.higher-quality", {"action": "ok", "id": "2"})
    skills = {skill.id: skill for skill in discover()[0]}
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "lower.json",
        "aaa.lower-quality",
        skills["aaa.lower-quality"].version,
        skills["aaa.lower-quality"].content_hash,
        task_success_rate=0.5,
        p95_latency_ms=10,
    )
    _report(
        reports / "higher.json",
        "zzz.higher-quality",
        skills["zzz.higher-quality"].version,
        skills["zzz.higher-quality"].content_hash,
        task_success_rate=1,
        p95_latency_ms=50,
    )
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(reports))

    default = invoke("video.download", {"url": URL}, yes=True)
    assert default["served_by"]["skill"] == "aaa.lower-quality"
    assert default["routing"]["policy"] == "default"

    evaluated = invoke("video.download", {"url": URL}, options={"use_evidence": True}, yes=True)
    assert evaluated["served_by"]["skill"] == "zzz.higher-quality"
    assert evaluated["routing"]["policy"] == "evaluation-aware"
    assert evaluated["routing"]["evaluation"]["applied"] is True
    assert evaluated["routing"]["selected"] == "zzz.higher-quality"
    assert evaluated["routing"]["candidates"][0]["evidence"]["task_success_rate"] == 1

    preferred = invoke(
        "video.download",
        {"url": URL},
        options={"use_evidence": True, "prefer_skill": "aaa.lower-quality"},
        yes=True,
    )
    assert preferred["served_by"]["skill"] == "aaa.lower-quality"
    assert "explicit prefer_skill" in preferred["routing"]["candidates"][0]["reasons"]


def test_evaluation_policy_falls_back_when_suites_are_not_comparable(skills_root, tmp_path, monkeypatch):
    make_skill(skills_root, "aaa.first", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.second", {"action": "ok", "id": "2"})
    skills = {skill.id: skill for skill in discover()[0]}
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "first.json",
        "aaa.first",
        skills["aaa.first"].version,
        skills["aaa.first"].content_hash,
        task_success_rate=0.2,
        suite_digest="b" * 64,
    )
    _report(
        reports / "second.json",
        "zzz.second",
        skills["zzz.second"].version,
        skills["zzz.second"].content_hash,
        task_success_rate=1,
        suite_digest="c" * 64,
    )
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(reports))

    result = invoke("video.download", {"url": URL}, options={"use_evidence": True}, yes=True)
    assert result["served_by"]["skill"] == "aaa.first"
    assert result["routing"]["policy"] == "default"
    assert result["routing"]["evaluation"]["reason"] == "NOT_ENOUGH_COMPARABLE_CANDIDATES"


def test_evaluation_never_overrides_free_before_paid(skills_root, tmp_path, monkeypatch):
    make_skill(skills_root, "aaa.paid", {"action": "ok", "id": "1"}, cost="paid")
    make_skill(skills_root, "zzz.free", {"action": "ok", "id": "2"})
    skills = {skill.id: skill for skill in discover()[0]}
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "paid.json",
        "aaa.paid",
        skills["aaa.paid"].version,
        skills["aaa.paid"].content_hash,
        task_success_rate=1,
    )
    _report(
        reports / "free.json",
        "zzz.free",
        skills["zzz.free"].version,
        skills["zzz.free"].content_hash,
        task_success_rate=0.1,
    )
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(reports))

    result = invoke(
        "video.download",
        {"url": URL},
        options={"use_evidence": True, "allow_paid": True},
        yes=True,
    )
    assert result["served_by"]["skill"] == "zzz.free"
    assert result["routing"]["policy"] == "evaluation-aware"


def test_conservative_aggregation_uses_worst_cross_environment_result(
    skills_root, tmp_path, monkeypatch
):
    make_skill(skills_root, "aaa.variable", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.stable", {"action": "ok", "id": "2"})
    skills = {skill.id: skill for skill in discover()[0]}
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "variable-old.json",
        "aaa.variable",
        skills["aaa.variable"].version,
        skills["aaa.variable"].content_hash,
        verified_at="2026-10-31T00:00:00Z",
        task_success_rate=0.4,
        p95_latency_ms=100,
    )
    _report(
        reports / "variable-new.json",
        "aaa.variable",
        skills["aaa.variable"].version,
        skills["aaa.variable"].content_hash,
        verified_at="2026-11-01T00:00:00Z",
        task_success_rate=1,
        p95_latency_ms=10,
    )
    _report(
        reports / "stable.json",
        "zzz.stable",
        skills["zzz.stable"].version,
        skills["zzz.stable"].content_hash,
        task_success_rate=0.8,
        p95_latency_ms=50,
    )
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(reports))

    conservative = invoke("video.download", {"url": URL}, options={"use_evidence": True}, yes=True)
    assert conservative["served_by"]["skill"] == "zzz.stable"
    variable = next(
        item for item in conservative["routing"]["candidates"] if item["skill"] == "aaa.variable"
    )
    assert variable["evidence"]["task_success_rate"] == 0.4
    assert variable["evidence"]["p95_latency_ms"] == 100
    assert variable["evidence"]["report_count"] == 2

    latest = invoke(
        "video.download",
        {"url": URL},
        options={"use_evidence": True, "evidence_aggregation": "latest"},
        yes=True,
    )
    assert latest["served_by"]["skill"] == "aaa.variable"
    assert latest["routing"]["evaluation"]["aggregation"] == "latest"


def test_evidence_limits_demote_candidates_but_keep_them_for_fallback(skills_root, tmp_path, monkeypatch):
    make_skill(
        skills_root,
        "aaa.within-limits",
        {"action": "error", "code": "UPSTREAM_ERROR", "message": "temporarily down"},
    )
    make_skill(skills_root, "zzz.too-slow", {"action": "ok", "id": "2"})
    skills = {skill.id: skill for skill in discover()[0]}
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "within.json",
        "aaa.within-limits",
        skills["aaa.within-limits"].version,
        skills["aaa.within-limits"].content_hash,
        task_success_rate=0.9,
        p95_latency_ms=50,
        average_cost_usd=0.01,
    )
    _report(
        reports / "slow.json",
        "zzz.too-slow",
        skills["zzz.too-slow"].version,
        skills["zzz.too-slow"].content_hash,
        task_success_rate=1,
        p95_latency_ms=200,
        average_cost_usd=0.02,
    )
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(reports))

    result = invoke(
        "video.download",
        {"url": URL},
        options={
            "use_evidence": True,
            "minimum_task_success_rate": 0.8,
            "maximum_p95_latency_ms": 100,
            "maximum_average_cost_usd": 0.05,
        },
        yes=True,
    )
    assert result["served_by"]["skill"] == "zzz.too-slow"
    assert [attempt["skill"] for attempt in result["attempts"]] == [
        "aaa.within-limits",
        "zzz.too-slow",
    ]
    assert result["routing"]["fallback"] is True
    slow = next(item for item in result["routing"]["candidates"] if item["skill"] == "zzz.too-slow")
    assert "P95_LATENCY_ABOVE_MAXIMUM" in slow["reasons"][-1]
    assert result["routing"]["evaluation"]["eligible_candidate_count"] == 1


def test_malformed_optional_policy_values_fall_back_to_safe_defaults(
    skills_root, isolate_router, tmp_path, monkeypatch
):
    make_skill(skills_root, "aaa.lower", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.higher", {"action": "ok", "id": "2"})
    skills = {skill.id: skill for skill in discover()[0]}
    reports = tmp_path / "reports"
    reports.mkdir()
    _report(
        reports / "lower.json",
        "aaa.lower",
        skills["aaa.lower"].version,
        skills["aaa.lower"].content_hash,
        task_success_rate=0.5,
    )
    _report(
        reports / "higher.json",
        "zzz.higher",
        skills["zzz.higher"].version,
        skills["zzz.higher"].content_hash,
        task_success_rate=1,
    )
    monkeypatch.setenv("SKILLROUTER_EVAL_REPORTS", str(reports))
    (isolate_router / "config.json").write_text(
        json.dumps(
            {
                "evaluation_policy": {
                    "enabled": True,
                    "max_age_days": "invalid",
                    "minimum_candidates": "invalid",
                    "aggregation": "invalid",
                }
            }
        ),
        encoding="utf-8",
    )

    result = invoke("video.download", {"url": URL}, yes=True)
    assert result["served_by"]["skill"] == "zzz.higher"
    assert result["routing"]["evaluation"]["aggregation"] == "conservative"
