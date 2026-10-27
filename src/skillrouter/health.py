"""Health scores, grades, and circuit-breaker state."""

from __future__ import annotations

import time
from typing import Any

from skillrouter.registry_loader import policy_for

HALF_LIFE_S = 3 * 24 * 3600
OPEN_AFTER = 3
WINDOW_S = 10 * 60
OPEN_S = 15 * 60
MAX_OPEN_S = 6 * 3600
SAMPLE_KEEP_S = 30 * 24 * 3600


def health_key(skill_id: str, capability: str, platform: str) -> str:
    return f"{skill_id}\t{capability}\t{platform}"


def weighted_score(samples: list[dict], now: float | None = None) -> float | None:
    now = time.time() if now is None else now
    weights = 0.0
    total = 0.0
    for sample in samples:
        age = max(0.0, now - float(sample.get("ts") or now))
        weight = 0.5 ** (age / HALF_LIFE_S)
        weights += weight
        total += weight * float(sample.get("score") or 0)
    if weights <= 0:
        return None
    return total / weights * 100.0


def grade_of(samples: list[dict], now: float | None = None) -> tuple[str, float | None]:
    if len(samples) < 3:
        return "unknown", weighted_score(samples, now)
    score = weighted_score(samples, now) or 0.0
    if score >= 80:
        return "healthy", score
    if score >= 40:
        return "degraded", score
    return "failing", score


def p50_latency(samples: list[dict]) -> float | None:
    values = sorted(
        float(sample["latency_ms"])
        for sample in samples[-20:]
        if isinstance(sample.get("latency_ms"), (int, float))
    )
    if not values:
        return None
    return values[len(values) // 2]


def circuit_view(record: dict | None, now: float | None = None) -> str:
    now = time.time() if now is None else now
    record = record or {}
    if float(record.get("cooldown_until") or 0) > now:
        return "cooldown"
    open_until = float(record.get("open_until") or 0)
    if open_until > now:
        return "open"
    if record.get("state") == "open":
        return "half_open"
    return "closed"


def note_outcome(
    record: dict,
    *,
    ok: bool,
    scored: bool,
    retry_after_s: float | None,
    now: float | None = None,
) -> dict:
    now = time.time() if now is None else now
    record = dict(record)
    if ok:
        record.update(
            {
                "state": "closed",
                "open_until": 0,
                "failures": [],
                "cooldown_until": 0,
                "duration_s": OPEN_S,
            }
        )
        return record
    if retry_after_s:
        record["cooldown_until"] = now + float(retry_after_s)
    if not scored:
        record.setdefault("state", "closed")
        return record
    if record.get("state") == "open":
        duration = min(float(record.get("duration_s") or OPEN_S) * 2, MAX_OPEN_S)
        record["duration_s"] = duration
        record["state"] = "open"
        record["open_until"] = now + duration
        record["failures"] = []
        return record
    failures = [float(item) for item in record.get("failures") or [] if now - float(item) <= WINDOW_S]
    failures.append(now)
    record["failures"] = failures
    record.setdefault("state", "closed")
    if len(failures) >= OPEN_AFTER:
        record["state"] = "open"
        record["duration_s"] = OPEN_S
        record["open_until"] = now + OPEN_S
        record["failures"] = []
    return record


def score_value(code: str | None, *, ok: bool) -> tuple[bool, float]:
    if ok:
        return True, 1.0
    if not code:
        name, policy = policy_for("INTERNAL")
        return bool(policy.get("scored")), float(policy.get("score") or 0.2)
    name, policy = policy_for(code)
    if name != code:
        policy = policy_for(name)[1]
    if not policy.get("scored"):
        return False, 0.0
    return True, float(policy.get("score") if policy.get("score") is not None else 0.2)


def append_sample(bucket: dict, *, score: float, latency_ms: float | None, source: str, now: float) -> None:
    samples = list(bucket.get("samples") or [])
    item: dict[str, Any] = {"ts": now, "score": score, "source": source}
    if latency_ms is not None:
        item["latency_ms"] = latency_ms
    samples.append(item)
    cutoff = now - SAMPLE_KEEP_S
    bucket["samples"] = [sample for sample in samples if float(sample.get("ts") or 0) >= cutoff][-200:]


def effective_grade(
    *,
    samples: list[dict],
    author_deprecated: bool,
    registry_deprecated: bool,
    auto_deprecated: bool,
    user_restored: bool,
    now: float | None = None,
) -> tuple[str, float | None]:
    grade, score = grade_of(samples, now)
    if user_restored:
        return grade, score
    if author_deprecated or registry_deprecated or auto_deprecated:
        return "deprecated", score
    return grade, score
