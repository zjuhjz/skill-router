import time

from skillrouter.health import grade_of, note_outcome, weighted_score


def test_grade_thresholds_and_decay():
    now = time.time()
    assert grade_of([{"ts": now, "score": 1}], now)[0] == "unknown"
    samples = [{"ts": now, "score": 1}, {"ts": now, "score": 1}, {"ts": now, "score": 1}]
    grade, score = grade_of(samples, now)
    assert grade == "healthy"
    assert score == 100
    mixed = [{"ts": now, "score": 1}, {"ts": now, "score": 0}, {"ts": now, "score": 1}]
    grade, score = grade_of(mixed, now)
    assert grade == "degraded"
    assert 66 < score < 67
    old = [{"ts": now - 3 * 86400, "score": 0}, {"ts": now, "score": 1}, {"ts": now, "score": 1}]
    assert weighted_score(old, now) > weighted_score(mixed, now)


def test_circuit_opens_on_three_scored_failures_and_doubles():
    now = 1_000_000.0
    rec = {}
    for offset in (0, 10, 20):
        rec = note_outcome(rec, ok=False, scored=True, retry_after_s=None, now=now + offset)
    assert rec["state"] == "open"
    assert rec["open_until"] == now + 20 + 15 * 60
    rec["open_until"] = now
    rec = note_outcome(rec, ok=False, scored=True, retry_after_s=None, now=now + 30)
    assert rec["open_until"] - (now + 30) == 30 * 60
    rec = note_outcome(rec, ok=True, scored=True, retry_after_s=None, now=now + 40)
    assert rec["state"] == "closed"
    assert rec["failures"] == []


def test_unscored_failure_does_not_open():
    rec = {}
    now = 10_000.0
    for _ in range(5):
        rec = note_outcome(rec, ok=False, scored=False, retry_after_s=None, now=now)
        now += 1
    assert rec.get("state", "closed") == "closed"
