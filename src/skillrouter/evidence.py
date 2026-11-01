"""Load long-lived evaluation evidence without mixing it into runtime health."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from skillrouter.discover import Skill, discover
from skillrouter.paths import load_config


def _schema_path() -> Path:
    bundled = Path(__file__).resolve().parent / "bundled_eval_schemas" / "report.schema.json"
    if bundled.is_file():
        return bundled
    return Path(__file__).resolve().parents[2] / "packages" / "skill-eval" / "schemas" / "report.schema.json"


def _configured_paths() -> list[Path]:
    configured = load_config().get("eval_reports") or []
    values = [configured] if isinstance(configured, str) else list(configured)
    raw = os.environ.get("SKILLROUTER_EVAL_REPORTS", "").strip()
    if raw:
        values.extend(part for part in raw.split(os.pathsep) if part.strip())
    return [Path(str(value)).expanduser() for value in values]


def _expand_paths(paths: Iterable[Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    errors: list[str] = []
    seen: set[str] = set()
    for raw in paths:
        path = raw.expanduser()
        if path.is_dir():
            candidates = sorted(path.glob("*.json"))
        elif path.is_file():
            candidates = [path]
        else:
            errors.append(f"evaluation evidence path does not exist: {path}")
            continue
        for candidate in candidates:
            key = str(candidate.resolve())
            if key not in seen:
                seen.add(key)
                files.append(candidate)
    return files, errors


def load_evidence(paths: Iterable[Path] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    chosen = list(paths) if paths is not None else _configured_paths()
    files, errors = _expand_paths(chosen)
    schema = json.loads(_schema_path().read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    records: list[dict[str, Any]] = []
    for path in files:
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"invalid evaluation report {path}: {exc}")
            continue
        report_errors = sorted(validator.iter_errors(report), key=lambda item: list(item.path))
        if report_errors:
            errors.append(f"invalid evaluation report {path}: {report_errors[0].message}")
            continue
        try:
            verified_at = datetime.fromisoformat(str(report["verified_at"]).replace("Z", "+00:00"))
        except ValueError:
            verified_at = None
        if verified_at is None or verified_at.tzinfo is None:
            errors.append(f"invalid evaluation report {path}: verified_at is not a valid date-time")
            continue
        records.append({"source": str(path.resolve()), "report": report})
    records.sort(key=lambda item: (item["report"]["verified_at"], item["source"]), reverse=True)
    return records, errors


def applicability(report: dict[str, Any], skills: dict[str, Skill]) -> tuple[bool, str | None]:
    subject = report["subject"]
    skill = skills.get(str(subject["id"]))
    if skill is None:
        return False, "SUBJECT_NOT_INSTALLED"
    if skill.version != str(subject["version"]):
        return False, "VERSION_MISMATCH"
    if f"sha256:{skill.content_hash}" != str(subject["digest"]):
        return False, "DIGEST_MISMATCH"
    if report["capability"] not in {item.capability for item in skill.provides}:
        return False, "CAPABILITY_NOT_PROVIDED"
    return True, None


def public_record(record: dict[str, Any], skills: dict[str, Skill]) -> dict[str, Any]:
    report = record["report"]
    applicable, reason = applicability(report, skills)
    return {
        "source": Path(record["source"]).name,
        "subject": report["subject"],
        "capability": report["capability"],
        "suite": report["suite"],
        "environment": report["environment"],
        "results": report["results"],
        "trust": report["trust"],
        "runner": report["runner"],
        "verified_at": report["verified_at"],
        "applicable": applicable,
        **({"inapplicable_reason": reason} if reason else {}),
    }


def evidence_summary(paths: Iterable[Path] | None = None) -> dict[str, Any]:
    records, errors = load_evidence(paths)
    discovered, discovery_errors = discover()
    skills = {skill.id: skill for skill in discovered}
    return {
        "ok": not errors,
        "schema": "skillrouter.evidence.v1",
        "reports": [public_record(record, skills) for record in records],
        "errors": errors,
        "discovery_errors": discovery_errors,
    }
