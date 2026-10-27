import json
import os
import time
from pathlib import Path

import pytest

from skillrouter.engine import invoke
from skillrouter.state import load_state, update_state
from tests.fakes import make_skill

URL = "https://v.douyin.com/router-test-token/"


def _invoke(data=None, **kwargs):
    options = kwargs.pop("options", {})
    yes = kwargs.pop("yes", True)
    return invoke("video.download", data or {"url": URL}, options=options, yes=yes)


def test_fallback_between_two_skills(skills_root):
    make_skill(skills_root, "aaa.first", {"action": "error", "code": "UPSTREAM_CHANGED", "message": "page changed"})
    make_skill(
        skills_root,
        "bbb.second",
        {"action": "ok", "title": "中文标题", "id": "9988776655443322110"},
    )
    result = _invoke()
    assert result["ok"] is True
    assert result["served_by"]["skill"] == "bbb.second"
    assert result["attempts"][0]["code"] == "UPSTREAM_CHANGED"
    assert result["attempts"][1]["ok"] is True
    assert "code" not in result["attempts"][1]
    assert result["output"]["meta"]["title"] == "中文标题"
    assert Path(result["output"]["files"][0]["path"]).is_file()


def test_free_ranks_before_paid_even_if_paid_id_is_earlier(skills_root):
    make_skill(skills_root, "aaa.paid", {"action": "ok", "id": "1"}, cost="paid")
    make_skill(skills_root, "zzz.free", {"action": "ok", "id": "2"})
    blocked = _invoke()
    assert blocked["served_by"]["skill"] == "zzz.free"
    allowed = _invoke(options={"allow_paid": True})
    assert allowed["served_by"]["skill"] == "zzz.free"
    assert len(allowed["attempts"]) == 1


def test_paid_is_used_only_after_free_fails(skills_root):
    make_skill(skills_root, "aaa.paid", {"action": "ok", "id": "9"}, cost="paid")
    make_skill(skills_root, "zzz.free", {"action": "error", "code": "UPSTREAM_ERROR", "message": "down"})
    result = _invoke(options={"allow_paid": True})
    assert result["ok"] is True
    assert [item["skill"] for item in result["attempts"]] == ["zzz.free", "aaa.paid"]


def test_skill_invalid_input_does_not_stop_fallback(skills_root):
    make_skill(skills_root, "aaa.bad", {"action": "error", "code": "INVALID_INPUT", "message": "nope"})
    make_skill(skills_root, "bbb.good", {"action": "ok"})
    result = _invoke()
    assert result["ok"] is True
    assert result["attempts"][0]["code"] == "UNSUPPORTED_URL"
    state = load_state()
    assert state["circuits"] == {} or all(not rec.get("failures") for rec in state["circuits"].values())


def test_not_found_stops_after_one_confirmation(skills_root):
    make_skill(skills_root, "aaa.one", {"action": "error", "code": "NOT_FOUND", "message": "gone", "write": False})
    make_skill(skills_root, "bbb.two", {"action": "error", "code": "NOT_FOUND", "message": "gone", "write": False})
    third = make_skill(skills_root, "ccc.three", {"action": "ok"})
    result = _invoke()
    assert result["ok"] is False
    assert result["error"]["code"] == "NOT_FOUND"
    assert [item["skill"] for item in result["attempts"]] == ["aaa.one", "bbb.two"]
    assert not (third / "ran.txt").exists()


def test_circuit_opens_and_half_open_retries_once(skills_root):
    make_skill(skills_root, "aaa.flaky", {"action": "error", "code": "UPSTREAM_CHANGED", "message": "changed", "write": False})
    for _ in range(3):
        failed = _invoke()
        assert failed["attempts"][0]["code"] == "UPSTREAM_CHANGED"
    opened = _invoke()
    assert opened["attempts"] == []
    assert opened["skipped"][0]["reason"] == "CIRCUIT_OPEN"

    def expire(state):
        for rec in state["circuits"].values():
            rec["open_until"] = 1

    update_state(expire)
    retried = _invoke()
    assert len(retried["attempts"]) == 1
    assert retried["attempts"][0]["code"] == "UPSTREAM_CHANGED"
    closed_again = _invoke()
    assert closed_again["attempts"] == []
    assert closed_again["skipped"][0]["reason"] == "CIRCUIT_OPEN"


def test_path_escape(skills_root):
    make_skill(skills_root, "aaa.escape", {"action": "escape"})
    escaped = _invoke()
    assert escaped["ok"] is False
    assert escaped["attempts"][0]["code"] == "INTERNAL"
    assert "escapes" in escaped["attempts"][0]["message"]


def test_symlink_rejected(skills_root):
    make_skill(skills_root, "aaa.link", {"action": "symlink"})
    linked = _invoke()
    assert linked["attempts"][0]["code"] == "INTERNAL"
    assert "symlink" in linked["attempts"][0]["message"]


def test_hardlink_rejected(skills_root):
    try:
        probe = skills_root / "link-probe"
        probe.write_text("a", encoding="utf-8")
        os.link(probe, skills_root / "link-probe-2")
        (skills_root / "link-probe-2").unlink()
        probe.unlink()
    except OSError:
        pytest.skip("filesystem does not support hardlinks")
    make_skill(skills_root, "aaa.hard", {"action": "hardlink"})
    hard = _invoke()
    assert hard["attempts"][0]["code"] == "INTERNAL"
    assert "hardlink" in hard["attempts"][0]["message"]


def test_save_to_never_overwrites(skills_root, tmp_path):
    make_skill(skills_root, "aaa.ok", {"action": "ok", "id": "42", "platform": "douyin"})
    dest = tmp_path / "saved"
    first = _invoke(options={"save_to": str(dest)})
    second = _invoke(options={"save_to": str(dest)})
    path_a = Path(first["output"]["files"][0]["path"])
    path_b = Path(second["output"]["files"][0]["path"])
    assert path_a.is_file() and path_b.is_file()
    assert path_a != path_b
    assert path_b.stem.endswith("_1")
    assert path_a.read_bytes() == path_b.read_bytes()


def test_quality_not_met_returns_best_degraded(skills_root, tmp_path):
    make_skill(skills_root, "aaa.low", {"action": "ok", "height": 360, "size": 80, "id": "42"})
    make_skill(skills_root, "bbb.mid", {"action": "ok", "height": 480, "size": 50, "id": "42"})
    dest = tmp_path / "q"
    result = _invoke({"url": URL, "min_height": 720}, options={"save_to": str(dest)})
    assert result["ok"] is True
    assert result["served_by"]["skill"] == "bbb.mid"
    assert result["hints"]
    assert Path(result["output"]["files"][0]["path"]).is_file()


def test_explicit_quality_error_is_kept(skills_root, tmp_path):
    make_skill(
        skills_root,
        "aaa.only",
        {"action": "error", "code": "QUALITY_NOT_MET", "message": "watermark", "height": 100, "size": 40},
    )
    result = _invoke(options={"save_to": str(tmp_path / "out")})
    assert result["ok"] is True
    assert result["attempts"][0]["code"] == "QUALITY_NOT_MET"
    assert Path(result["output"]["files"][0]["path"]).is_file()


def test_credential_grant_and_blacklist(skills_root, isolate_router):
    make_skill(
        skills_root,
        "aaa.cred",
        {"action": "ok", "probe": ["TOKEN", "OPENAI_API_KEY", "LEAKED_TOKEN"], "leak": ["TOKEN"]},
        requires={"optional_env": ["TOKEN", "OPENAI_API_KEY", "LEAKED_TOKEN"]},
    )
    os.environ["TOKEN"] = "from-parent"
    os.environ["OPENAI_API_KEY"] = "parent-openai"
    os.environ["LEAKED_TOKEN"] = "parent-leak"
    try:
        (isolate_router / "credentials.json").write_text(
            json.dumps(
                {
                    "values": {"TOKEN": "sekret-value-42", "OPENAI_API_KEY": "blocked-key-99"},
                    "grants": {"aaa.cred": ["TOKEN", "OPENAI_API_KEY", "LEAKED_TOKEN"]},
                    "overrides": [],
                }
            ),
            encoding="utf-8",
        )
        result = _invoke()
        seen = result["output"]["extra"]["values"]
        assert seen["TOKEN"] == "***"
        assert seen["OPENAI_API_KEY"] is None
        assert seen["LEAKED_TOKEN"] is None
        log = next((isolate_router / "logs").glob("calls-*.jsonl"))
        text = log.read_text(encoding="utf-8")
        assert "sekret-value-42" not in text
        assert "router-test-token" not in text
        assert "sha256:" in text
    finally:
        os.environ.pop("TOKEN", None)
        os.environ.pop("OPENAI_API_KEY", None)
        os.environ.pop("LEAKED_TOKEN", None)


def test_blacklist_override_is_explicit(skills_root, isolate_router):
    make_skill(
        skills_root,
        "aaa.cred",
        {"action": "ok", "probe": ["OPENAI_API_KEY"]},
        requires={"optional_env": ["OPENAI_API_KEY"]},
    )
    (isolate_router / "credentials.json").write_text(
        json.dumps(
            {
                "values": {"OPENAI_API_KEY": "blocked-key-99"},
                "grants": {"aaa.cred": ["OPENAI_API_KEY"]},
                "overrides": ["OPENAI_API_KEY"],
            }
        ),
        encoding="utf-8",
    )
    result = _invoke()
    # The override injected the key; the success result still must not echo it.
    assert result["output"]["extra"]["values"]["OPENAI_API_KEY"] == "***"
    assert "blocked-key-99" not in json.dumps(result)


def test_required_env_skips_without_calling(skills_root):
    directory = make_skill(
        skills_root,
        "aaa.needs",
        {"action": "ok"},
        requires={"env": ["TOKEN"]},
    )
    result = _invoke()
    assert result["ok"] is False
    assert result["error"]["code"] == "ALL_FAILED"
    assert result["skipped"][0]["reason"] == "NEED_AUTH"
    assert result["attempts"] == []
    assert not (directory / "ran.txt").exists()


def test_need_confirm_without_yes(skills_root):
    make_skill(skills_root, "aaa.ok", {"action": "ok"})
    blocked = invoke("video.download", {"url": URL}, yes=False)
    assert blocked["ok"] is False
    assert blocked["error"]["code"] == "NEED_CONFIRM"
    assert blocked["attempts"] == []
    allowed = invoke("video.download", {"url": URL}, yes=True)
    assert allowed["ok"] is True
    again = invoke("video.download", {"url": URL}, yes=False)
    assert again["ok"] is True


def test_share_text_url_is_extracted(skills_root):
    make_skill(skills_root, "aaa.ok", {"action": "ok"})
    raw = "7.43 复制打开抖音 https://v.douyin.com/router-test-token/ 复制"
    result = _invoke({"url": raw})
    assert result["output"]["extra"]["url"] == "https://v.douyin.com/router-test-token/"
    assert result["output"]["extra"]["raw"] == raw


def test_attempt_logs_and_health_redact_signed_query(skills_root):
    secret = "SuperSecretTokenValue99"
    message = (
        "ERROR: Unsupported URL: https://www.xiaohongshu.com/login?redirectPath="
        "https%3A%2F%2Fwww.xiaohongshu.com%2Fdiscovery%2Fitem%2Fid"
        f"%3Ftype%3Dvideo%26xsec_token%3D{secret}%26xsec_source%3Dapp_share%26share_id%3DShareIdValue88"
        f"&xsec_token={secret}&shareRedId=RedIdValue77&a_bogus=BogusValue66&msToken=MsValue55"
        f'&cookies=CookieValue44 json "custom_sign": "{secret}"'
    )
    make_skill(
        skills_root,
        "aaa.leak",
        {"action": "error", "code": "UPSTREAM_ERROR", "message": message},
    )
    result = _invoke()
    blob = json.dumps(result, ensure_ascii=False)
    for leaked in (secret, "ShareIdValue88", "RedIdValue77", "BogusValue66", "MsValue55", "CookieValue44"):
        assert leaked not in blob
    assert "xsec_token=***" in blob or "xsec_token%3D***" in blob or "xsec_token=" in blob
    log_dir = Path(os.environ["SKILLROUTER_HOME"]) / "logs"
    logged = "\n".join(path.read_text(encoding="utf-8") for path in log_dir.glob("*.jsonl"))
    for leaked in (secret, "ShareIdValue88", "RedIdValue77", "BogusValue66", "MsValue55", "CookieValue44"):
        assert leaked not in logged
    from skillrouter.state import load_state, update_state

    update_state(lambda state: state["health"].__setitem__("sample", {"message": f"xsec_token={secret}&share_id=ShareIdValue88"}))
    stored = json.dumps(load_state()["health"], ensure_ascii=False)
    assert secret not in stored
    assert "ShareIdValue88" not in stored


def test_success_output_redacts_page_url_and_cookie_values(skills_root, isolate_router, monkeypatch):
    secret = "SuperSecretTokenValue99"
    cookie = "web_session=UniqueCookieValue99; a1=AnotherCookieValue88"
    (isolate_router / "credentials.json").write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {"values": {"XHS_COOKIES": cookie}, "grants": {"aaa.ok": ["XHS_COOKIES"]}},
            ensure_ascii=False,
        ).encode("utf-8")
    )
    page = (
        "https://www.xiaohongshu.com/explore/649c46ab000000002702ad36"
        f"?xsec_token={secret}&xsec_source=app_share&share_id=ShareIdValue88&shareRedId=RedIdValue77"
    )
    make_skill(
        skills_root,
        "aaa.ok",
        {
            "action": "ok",
            "output": {
                "meta": {
                    "platform": "douyin",
                    "id": "1234567890123456789",
                    "title": "中文标题",
                    "page_url": page,
                    "source_url": page,
                    "cover": f"https://cdn.example/cover.jpg?xsec_token={secret}",
                    "description": "session UniqueCookieValue99",
                },
                "files": [{"path": "video.mp4", "role": "muxed", "size_bytes": 64}],
            },
        },
    )
    result = _invoke()
    assert result["ok"] is True, result
    blob = json.dumps(result, ensure_ascii=False)
    for leaked in (secret, "ShareIdValue88", "RedIdValue77", "UniqueCookieValue99", "AnotherCookieValue88"):
        assert leaked not in blob
    assert "xsec_token=***" in result["output"]["meta"]["page_url"]
    assert "xsec_token=***" in result["output"]["meta"]["source_url"]
    logged = "\n".join(path.read_text(encoding="utf-8") for path in (isolate_router / "logs").glob("calls-*.jsonl"))
    for leaked in (secret, "UniqueCookieValue99", "ShareIdValue88"):
        assert leaked not in logged
    assert "xsec_token=***" in logged
    from skillrouter.creds import child_env, load_credentials
    from skillrouter.engine import _debug_grant

    store = load_credentials()
    assert store["values"]["XHS_COOKIES"].startswith("web_session=")
    monkeypatch.setenv("SKILLROUTER_DEBUG", "1")
    env = child_env("aaa.ok", ["XHS_COOKIES"], store=store)
    assert env["SKILLROUTER_DEBUG"] == "1"
    assert "UniqueCookieValue99" in env["XHS_COOKIES"]
    _debug_grant("aaa.ok", env, ["XHS_COOKIES"])
    debug = (isolate_router / "logs" / "debug.jsonl").read_text(encoding="utf-8")
    assert "UniqueCookieValue99" not in debug
    assert "XHS_COOKIES" in debug
    assert "length" in debug


def test_need_auth_does_not_bench_and_a_new_grant_revives(skills_root, isolate_router):
    directory = make_skill(
        skills_root,
        "aaa.auth",
        {"action": "error", "code": "NEED_AUTH", "message": "login wall"},
        requires={"optional_env": ["XHS_COOKIES"]},
    )
    (isolate_router / "credentials.json").write_text(
        json.dumps(
            {"values": {"XHS_COOKIES": "a1=alpha-value-111111"}, "grants": {"aaa.auth": ["XHS_COOKIES"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    for _ in range(3):
        result = _invoke()
    assert result["ok"] is False
    assert result["attempts"][0]["code"] == "NEED_AUTH"
    state = load_state()
    assert state["deprecated"] == {}
    for record in state["circuits"].values():
        assert record.get("state") != "open"
    keys = [key for key in state["health"] if key.startswith("aaa.auth\t")]
    assert keys
    assert all(state["health"][key].get("last_code") == "NEED_AUTH" for key in keys)

    def stick(code: str):
        def mutate(current: dict) -> None:
            for key in keys:
                current["deprecated"][key] = True
                bucket = dict(current["health"].get(key) or {})
                bucket["last_code"] = code
                current["health"][key] = bucket

        update_state(mutate)

    stick("NEED_AUTH")
    (directory / "ran.txt").unlink()
    revived = _invoke()
    assert (directory / "ran.txt").is_file()
    assert revived["attempts"][0]["skill"] == "aaa.auth"
    assert load_state()["deprecated"] == {}

    stick("UPSTREAM_CHANGED")
    (directory / "ran.txt").unlink()
    benched = _invoke()
    assert not (directory / "ran.txt").exists()
    assert any(item.get("reason") == "DEPRECATED" and item.get("skill") == "aaa.auth" for item in benched["skipped"])

    (isolate_router / "credentials.json").write_text(
        json.dumps(
            {"values": {"XHS_COOKIES": "a1=beta-value-222222"}, "grants": {"aaa.auth": ["XHS_COOKIES"]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    granted = _invoke()
    assert (directory / "ran.txt").is_file()
    assert granted["attempts"][0]["skill"] == "aaa.auth"
    assert load_state()["deprecated"] == {}


def test_restore_skill_id_clears_every_key(isolate_router):
    from skillrouter.engine import restore
    from skillrouter.health import health_key

    def seed(state: dict) -> None:
        for capability in ("video.download@1", "video.resolve@1"):
            key = health_key("aaa.multi", capability, "douyin")
            state["deprecated"][key] = True
            state["circuits"][key] = {
                "state": "open",
                "open_until": 9_999_999_999,
                "failures": [1, 2, 3],
                "cooldown_until": 0,
                "duration_s": 900,
            }
            state["failing_since"][key] = 10

    update_state(seed)
    missing = restore("aaa.multi", "video.download@1", None)
    assert missing["ok"] is False
    assert load_state()["deprecated"]
    restored = restore("aaa.multi")
    assert restored["ok"] is True
    assert len(restored["restored"]) == 2
    state = load_state()
    assert state["deprecated"] == {}
    assert state["failing_since"] == {}
    assert set(state["user_restored"]) == {item["key"] for item in restored["restored"]}
    assert all(record.get("state") == "closed" for record in state["circuits"].values())


def _plant_state(home: Path, body: dict) -> None:
    (home / "state.json").write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")


def _bench_state(key: str, *, version: str, content_hash: str, cred_hash: str | None = None, last_code: str | None = None) -> dict:
    """pre-0.3.6 health record: samples and a bench, no credential fingerprint unless asked."""
    bucket = {
        "samples": [
            {"ts": 1700000000, "score": 0, "source": "invoke", "latency_ms": 12},
            {"ts": 1700000010, "score": 0, "source": "invoke", "latency_ms": 12},
            {"ts": 1700000020, "score": 0, "source": "invoke", "latency_ms": 12},
        ],
        "content_hash": content_hash,
        "skill_version": version,
    }
    if cred_hash is not None:
        bucket["cred_hash"] = cred_hash
    if last_code is not None:
        bucket["last_code"] = last_code
    return {
        "circuits": {
            key: {
                "state": "open",
                "open_until": 9_999_999_999,
                "failures": [1700000000, 1700000010, 1700000020],
                "cooldown_until": 0,
                "duration_s": 900,
            }
        },
        "health": {key: bucket},
        "trust": {},
        "deprecated": {key: True},
        "user_restored": {},
        "failing_since": {key: 1_700_000_000},
        "doctor_dates": {key: ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04", "2026-10-05"]},
    }


def test_pre_036_state_without_fingerprint_is_eligible(skills_root, isolate_router):
    """A bench written before cred_hash existed must not skip the skill on upgrade."""
    web = make_skill(
        skills_root,
        "example.xiaohongshu-web",
        {"action": "error", "code": "UPSTREAM_CHANGED", "message": "note page has no note object"},
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        {
            "action": "ok",
            "platform": "xiaohongshu",
            "id": "69ce2b520000000023010e52",
            "output": {
                "meta": {"platform": "xiaohongshu", "id": "69ce2b520000000023010e52", "title": "夜景", "author": "乙"},
                "media": [{"url": "https://cdn.example/a.mp4", "role": "muxed"}],
            },
        },
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    key = "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu"
    _plant_state(
        isolate_router,
        _bench_state(key, version="0.3.5", content_hash="stale-hash-from-0.3.5"),
    )
    result = invoke(
        "video.resolve",
        {"url": "https://www.xiaohongshu.com/explore/69ce2b520000000023010e52"},
        yes=True,
    )
    assert (web / "ran.txt").is_file()
    assert any(
        item.get("skill") == "example.xiaohongshu-web" and item.get("code") == "UPSTREAM_CHANGED"
        for item in result["attempts"]
    )
    assert not any(item.get("skill") == "example.xiaohongshu-web" for item in result["skipped"])
    state = load_state()
    assert key not in state["deprecated"]
    assert float(state["failing_since"].get(key) or time.time()) > 1_700_000_000 + 86400 * 30
    assert "2026-10-01" not in (state["doctor_dates"].get(key) or [])


def test_missing_fingerprint_revives_even_when_version_and_hash_match(skills_root, isolate_router):
    web = make_skill(
        skills_root,
        "example.xiaohongshu-web",
        {
            "action": "ok",
            "output": {
                "meta": {"platform": "xiaohongshu", "id": "69ce2b520000000023010e52", "title": "夜景"},
                "media": [{"url": "https://cdn.example/a.mp4"}],
            },
        },
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
        requires={"optional_env": ["XHS_COOKIES"]},
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        {
            "action": "ok",
            "output": {
                "meta": {"platform": "xiaohongshu", "id": "69ce2b520000000023010e52", "title": "generic"},
                "media": [{"url": "https://cdn.example/b.mp4"}],
            },
        },
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    from skillrouter.discover import discover

    current = next(skill for skill in discover()[0] if skill.id == "example.xiaohongshu-web")
    key = "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu"
    _plant_state(
        isolate_router,
        _bench_state(key, version=current.version, content_hash=current.content_hash),
    )
    result = invoke(
        "video.resolve",
        {"url": "https://www.xiaohongshu.com/explore/69ce2b520000000023010e52"},
        yes=True,
    )
    assert result["ok"] is True, result
    assert result["served_by"]["skill"] == "example.xiaohongshu-web"
    assert (web / "ran.txt").is_file()
    assert key not in load_state()["deprecated"]


def test_same_fingerprint_stays_benched_until_skill_version_changes(skills_root, isolate_router):
    (isolate_router / "credentials.json").write_text(
        json.dumps({"values": {"XHS_COOKIES": "a1=alpha-value-111111"}, "grants": {"example.xiaohongshu-web": ["XHS_COOKIES"]}}),
        encoding="utf-8",
    )
    web = make_skill(
        skills_root,
        "example.xiaohongshu-web",
        {
            "action": "ok",
            "output": {
                "meta": {"platform": "xiaohongshu", "id": "69ce2b520000000023010e52", "title": "夜景"},
                "media": [{"url": "https://cdn.example/a.mp4"}],
            },
        },
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
        requires={"optional_env": ["XHS_COOKIES"]},
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        {
            "action": "ok",
            "output": {
                "meta": {"platform": "xiaohongshu", "id": "69ce2b520000000023010e52", "title": "generic"},
                "media": [{"url": "https://cdn.example/b.mp4"}],
            },
        },
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    from skillrouter.creds import load_credentials
    from skillrouter.discover import discover
    from skillrouter.engine import _cred_fingerprint

    current = next(skill for skill in discover()[0] if skill.id == "example.xiaohongshu-web")
    provision = next(item for item in current.provides if item.capability == "video.resolve@1")
    fingerprint = _cred_fingerprint(current.id, provision.requires, load_credentials())
    key = "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu"
    _plant_state(
        isolate_router,
        _bench_state(
            key,
            version=current.version,
            content_hash=current.content_hash,
            cred_hash=fingerprint,
            last_code="UPSTREAM_CHANGED",
        ),
    )
    held = invoke(
        "video.resolve",
        {"url": "https://www.xiaohongshu.com/explore/69ce2b520000000023010e52"},
        yes=True,
    )
    assert not (web / "ran.txt").exists()
    assert held["served_by"]["skill"] == "example.ytdlp"
    assert any(item.get("reason") == "DEPRECATED" and item.get("skill") == "example.xiaohongshu-web" for item in held["skipped"])

    _plant_state(
        isolate_router,
        _bench_state(
            key,
            version="0.3.5",
            content_hash=current.content_hash,
            cred_hash=fingerprint,
            last_code="UPSTREAM_CHANGED",
        ),
    )
    (web / "ran.txt").unlink(missing_ok=True)
    upgraded = invoke(
        "video.resolve",
        {"url": "https://www.xiaohongshu.com/explore/69ce2b520000000023010e52"},
        yes=True,
    )
    assert (web / "ran.txt").is_file()
    assert upgraded["served_by"]["skill"] == "example.xiaohongshu-web"
    assert key not in load_state()["deprecated"]


def test_xiaohongshu_success_page_url_is_canonical(skills_root):
    secret = "SuperSecretTokenValue99"
    note_id = "69ce2b520000000023010e52"
    page = (
        f"https://www.xiaohongshu.com/discovery/item/{note_id}"
        f"?type=video&xsec_token={secret}&xsec_source=app_share&share_id=ShareIdValue88"
    )
    make_skill(
        skills_root,
        "aaa.xhs",
        {
            "action": "ok",
            "output": {
                "meta": {
                    "platform": "xiaohongshu",
                    "id": note_id,
                    "title": "夜景",
                    "page_url": page,
                    "source_url": page,
                },
                "files": [{"path": "video.mp4", "role": "muxed", "size_bytes": 64}],
            },
        },
        platforms=["xiaohongshu"],
    )
    result = _invoke({"url": f"https://www.xiaohongshu.com/explore/{note_id}"})
    assert result["ok"] is True, result
    canonical = f"https://www.xiaohongshu.com/explore/{note_id}"
    assert result["output"]["meta"]["page_url"] == canonical
    assert result["output"]["meta"]["source_url"] == canonical
    blob = json.dumps(result, ensure_ascii=False)
    assert secret not in blob
    assert "ShareIdValue88" not in blob
    assert "xsec_token" not in result["output"]["meta"]["page_url"]


def test_xhslink_cn_share_text_is_xiaohongshu(skills_root):
    make_skill(
        skills_root,
        "aaa.xhs",
        {"action": "ok", "platform": "xiaohongshu", "id": "649c46ab000000002702ad36"},
        platforms=["xiaohongshu"],
    )
    raw = "周末看看 https://xhslink.cn/o/ExampleShare 复制后直接打开【小红书】看看【笔记】"
    result = _invoke({"url": raw})
    assert result["ok"] is True, result
    assert result["output"]["extra"]["url"] == "https://xhslink.cn/o/ExampleShare"
    assert result["output"]["meta"]["platform"] == "xiaohongshu"
    bare = _invoke({"url": "再看一条 xhslink.cn/o/ExampleShare ，复制后直接打开【小红书】"})
    assert bare["output"]["extra"]["url"] == "https://xhslink.cn/o/ExampleShare"


def test_unknown_host_is_unsupported(skills_root):
    make_skill(skills_root, "aaa.ok", {"action": "ok"})
    result = _invoke({"url": "https://example.com/video/1"})
    assert result["ok"] is False
    assert result["error"]["code"] == "UNSUPPORTED_URL"
    assert result["attempts"] == []


def test_schema_invalid_input_starts_no_skill(skills_root):
    directory = make_skill(skills_root, "aaa.ok", {"action": "ok"})
    result = invoke("video.download", {}, yes=True)
    assert result["error"]["code"] == "INVALID_INPUT"
    assert not (directory / "ran.txt").exists()


def test_max_output_bytes(skills_root, monkeypatch):
    monkeypatch.setenv("SKILLROUTER_MAX_OUTPUT_BYTES", "10")
    make_skill(skills_root, "aaa.ok", {"action": "ok", "size": 64})
    result = _invoke()
    assert result["attempts"][0]["code"] == "INTERNAL"
    assert "max_output_bytes" in result["attempts"][0]["message"]


def test_timeout_kills_process_group(skills_root):
    if os.name == "nt":
        pytest.skip("posix process-group kill is exercised here")
    directory = make_skill(skills_root, "aaa.slow", {"action": "sleep"}, timeout_s=1)
    result = _invoke()
    assert result["attempts"][0]["code"] == "TIMEOUT"
    pid = int((directory / "child.pid").read_text(encoding="utf-8"))
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_http_metadata_address_is_refused(skills_root):
    make_skill(
        skills_root,
        "aaa.http",
        {"action": "ok"},
        entrypoint={"type": "http", "url": "http://169.254.169.254/latest/meta-data"},
    )
    make_skill(skills_root, "bbb.ok", {"action": "ok"})
    result = _invoke()
    assert result["ok"] is True
    assert result["served_by"]["skill"] == "bbb.ok"
    assert result["attempts"][0]["skill"] == "aaa.http"
    assert "169.254.169.254" in result["attempts"][0]["message"] or "refused" in result["attempts"][0]["message"]


def test_dependency_missing_is_hinted(skills_root):
    make_skill(
        skills_root,
        "aaa.needffmpeg",
        {"action": "error", "code": "DEPENDENCY_MISSING", "message": "ffmpeg is not installed or not on PATH"},
    )
    result = _invoke()
    assert result["ok"] is False
    assert result["attempts"][0]["code"] == "DEPENDENCY_MISSING"
    assert any("缺少依赖" in hint and "ffmpeg" in hint for hint in result["hints"])


def test_transcript_falls_back_when_subtitles_are_missing(skills_root):
    transcript = {
        "language": "zh",
        "source": "asr",
        "text": "你好",
        "segments": [{"start_ms": 0, "end_ms": 800, "text": "你好"}],
    }
    make_skill(
        skills_root,
        "aaa.subs",
        {"action": "error", "code": "UNSUPPORTED_URL", "message": "no subtitle track", "write": False},
        capability="video.transcript@1",
        platforms=["bilibili"],
    )
    make_skill(
        skills_root,
        "bbb.asr",
        {"action": "ok", "output": transcript},
        capability="video.transcript@1",
        platforms=["bilibili"],
    )
    result = invoke("video.transcript", {"url": "https://www.bilibili.com/video/BV1GJ411x7h7"}, yes=True)
    assert result["ok"] is True
    assert result["served_by"]["skill"] == "bbb.asr"
    assert result["output"]["source"] == "asr"
    assert result["attempts"][0]["code"] == "UNSUPPORTED_URL"


def test_local_file_uses_file_platform(skills_root):
    transcript = {
        "language": "zh",
        "source": "asr",
        "text": "本地",
        "segments": [{"start_ms": 0, "end_ms": 10, "text": "本地"}],
    }
    bilibili = make_skill(
        skills_root,
        "aaa.subs",
        {"action": "ok", "output": transcript},
        capability="video.transcript@1",
        platforms=["bilibili"],
    )
    make_skill(
        skills_root,
        "bbb.local",
        {"action": "ok", "output": transcript},
        capability="video.transcript@1",
        platforms=["local"],
    )
    result = invoke("video.transcript", {"file": "C:/clips/a.mp4"}, yes=True)
    assert result["ok"] is True
    assert result["served_by"]["skill"] == "bbb.local"
    assert not (bilibili / "ran.txt").exists()


def test_doctor_skips_disabled_health_checks(skills_root):
    from skillrouter.engine import doctor

    make_skill(
        skills_root,
        "aaa.quiet",
        {"action": "ok"},
        capability="video.transcript@1",
        platforms=["local"],
        health_check={"enabled": False},
    )
    report = doctor(yes=True, skill_id="aaa.quiet")
    assert report["checks"] == []


def test_gemini_key_needs_explicit_override(skills_root, isolate_router):
    from skillrouter.creds import save_credentials

    transcript = {
        "language": "zh",
        "source": "asr",
        "text": "ok",
        "segments": [{"start_ms": 0, "end_ms": 1, "text": "ok"}],
    }
    make_skill(
        skills_root,
        "example.gemini-asr",
        {"action": "ok", "output": transcript},
        capability="video.transcript@1",
        platforms=["local"],
        cost="paid",
        requires={"env": ["GEMINI_API_KEY"]},
    )
    save_credentials(
        {
            "values": {"GEMINI_API_KEY": "super-secret-gemini"},
            "grants": {"example.gemini-asr": ["GEMINI_API_KEY"]},
            "overrides": [],
        }
    )
    blocked = invoke(
        "video.transcript",
        {"file": "C:/clips/a.mp4"},
        yes=True,
        options={"allow_paid": True},
    )
    assert blocked["ok"] is False
    assert any(item["reason"] == "NEED_AUTH" for item in blocked["skipped"])
    save_credentials(
        {
            "values": {"GEMINI_API_KEY": "super-secret-gemini"},
            "grants": {"example.gemini-asr": ["GEMINI_API_KEY"]},
            "overrides": ["GEMINI_API_KEY"],
        }
    )
    allowed = invoke(
        "video.transcript",
        {"file": "C:/clips/a.mp4"},
        yes=True,
        options={"allow_paid": True},
    )
    assert allowed["ok"] is True
    assert "super-secret-gemini" not in json.dumps(allowed)


def test_doctor_deletes_files_and_hides_urls(skills_root, isolate_router):
    from skillrouter.engine import doctor

    make_skill(skills_root, "aaa.ok", {"action": "ok", "size": 32, "title": "中文标题"})
    report = doctor(yes=True)
    assert report["schema_version"] == "skillcall.health.v1"
    assert report["checks"][0]["ok"] is True
    blob = json.dumps(report)
    assert "http" not in blob
    assert "中文标题" not in blob
    assert list(isolate_router.rglob("*.mp4")) == []


def test_model_fetch_timeout_is_dependency_missing():
    from skillrouter.engine import _path_key, _timeout_outcome

    code, message = _timeout_outcome("skillrouter: model-fetch\nskillrouter: model-fetch-hint: set HF_ENDPOINT=https://hf-mirror.com\n")
    assert code == "DEPENDENCY_MISSING"
    assert "hf-mirror.com" in message
    code, message = _timeout_outcome("still working")
    assert code == "TIMEOUT"
    assert _path_key(Path(r"\\?\C:\Users\a\out\clip.mp4")) == _path_key(Path(r"C:\Users\a\out\clip.mp4"))


def test_warmup_runs_setup_mode(skills_root):
    from skillrouter.engine import warmup

    folder = skills_root / "warm"
    folder.mkdir()
    (folder / "skill.capability.yaml").write_text(
        "\n".join(
            [
                'skillcall: "0.1"',
                "id: example.warm",
                "version: 0.3.1",
                "name: warm",
                "license: MIT",
                "provides:",
                "  - capability: video.resolve@1",
                "    platforms: [douyin]",
                "    entrypoint:",
                "      type: command",
                '      command: ["$PYTHON", "entry.py"]',
                "    cost: free",
                "    side_effects: []",
                "    health_check:",
                "      enabled: false",
                "warmup:",
                "  timeout_s: 30",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (folder / "entry.py").write_text(
        "import json,sys\n"
        "req=json.load(sys.stdin)\n"
        "assert req['context']['mode']=='setup'\n"
        "json.dump({'ok': True, 'output': {'ready': True}}, sys.stdout)\n",
        encoding="utf-8",
    )
    result = warmup("example.warm")
    assert result["ok"] is True, result
    assert result["results"][0]["skill"] == "example.warm"


XHS_URL = "https://www.xiaohongshu.com/explore/69ce2b520000000023010e52"
BILI_URL = "https://www.bilibili.com/video/BV1GJ411x7h7"
MANY = ["douyin", "bilibili", "xiaohongshu", "weibo"]


def _resolve_body(platform: str, item_id: str, title: str) -> dict:
    return {
        "action": "ok",
        "output": {
            "meta": {"platform": platform, "id": item_id, "title": title, "author": "甲"},
            "media": [{"url": "https://cdn.example/media.mp4", "role": "muxed"}],
        },
    }


def _skills_by_id() -> dict:
    from skillrouter.discover import discover

    return {skill.id: skill for skill in discover()[0]}


def _fresh_samples(score: float, count: int) -> list[dict]:
    now = time.time()
    return [{"ts": now - index, "score": score, "source": "invoke", "latency_ms": 15} for index in range(count)]


def _plant_health(home: Path, rows: list[tuple]) -> None:
    """rows are (skill, key, score, count). Version and hash must match or reconcile drops the samples."""
    health = {}
    for skill, key, score, count in rows:
        health[key] = {
            "samples": _fresh_samples(score, count),
            "content_hash": skill.content_hash,
            "skill_version": skill.version,
            "cred_hash": "present",
        }
    _plant_state(
        home,
        {
            "circuits": {},
            "health": health,
            "trust": {},
            "deprecated": {},
            "user_restored": {},
            "failing_since": {},
            "doctor_dates": {},
        },
    )


def test_unknown_specific_beats_healthy_generic(skills_root, isolate_router):
    web = make_skill(
        skills_root,
        "example.xiaohongshu-web",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "夜景"),
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
        priority=10,
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "generic"),
        platforms=MANY,
        capability="video.resolve@1",
        priority=100,
        metadata="basic",
    )
    found = _skills_by_id()
    _plant_health(
        isolate_router,
        [(found["example.ytdlp"], "example.ytdlp\tvideo.resolve@1\txiaohongshu", 1.0, 3)],
    )
    result = invoke("video.resolve", {"url": XHS_URL}, yes=True)
    assert result["ok"] is True, result
    assert result["served_by"]["skill"] == "example.xiaohongshu-web"
    assert [item["skill"] for item in result["attempts"]] == ["example.xiaohongshu-web"]
    assert result["skipped"] == []
    assert (web / "ran.txt").is_file()
    assert result["output"]["meta"]["title"] == "夜景"


def test_clearly_bad_specific_yields_to_healthy_generic(skills_root, isolate_router):
    web = make_skill(
        skills_root,
        "example.xiaohongshu-web",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "夜景"),
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "generic"),
        platforms=MANY,
        capability="video.resolve@1",
    )
    found = _skills_by_id()
    _plant_health(
        isolate_router,
        [
            (found["example.xiaohongshu-web"], "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu", 0.0, 3),
            (found["example.ytdlp"], "example.ytdlp\tvideo.resolve@1\txiaohongshu", 1.0, 3),
        ],
    )
    result = invoke("video.resolve", {"url": XHS_URL}, yes=True)
    assert result["served_by"]["skill"] == "example.ytdlp"
    assert [item["skill"] for item in result["attempts"]] == ["example.ytdlp"]
    assert not (web / "ran.txt").exists()


def test_degraded_but_not_bad_specific_still_beats_generic(skills_root, isolate_router):
    make_skill(
        skills_root,
        "example.xiaohongshu-web",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "夜景"),
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "generic"),
        platforms=MANY,
        capability="video.resolve@1",
    )
    found = _skills_by_id()
    _plant_health(
        isolate_router,
        [
            (found["example.xiaohongshu-web"], "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu", 0.6, 3),
            (found["example.ytdlp"], "example.ytdlp\tvideo.resolve@1\txiaohongshu", 1.0, 3),
        ],
    )
    held = invoke("video.resolve", {"url": XHS_URL}, yes=True)
    assert held["served_by"]["skill"] == "example.xiaohongshu-web"

    _plant_health(
        isolate_router,
        [
            (found["example.xiaohongshu-web"], "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu", 0.49, 3),
            (found["example.ytdlp"], "example.ytdlp\tvideo.resolve@1\txiaohongshu", 1.0, 3),
        ],
    )
    yielded = invoke("video.resolve", {"url": XHS_URL}, yes=True)
    assert yielded["served_by"]["skill"] == "example.ytdlp"


def test_fewer_than_three_failures_are_not_clearly_bad(skills_root, isolate_router):
    make_skill(
        skills_root,
        "example.xiaohongshu-web",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "夜景"),
        platforms=["xiaohongshu"],
        capability="video.resolve@1",
    )
    make_skill(
        skills_root,
        "example.ytdlp",
        _resolve_body("xiaohongshu", "69ce2b520000000023010e52", "generic"),
        platforms=MANY,
        capability="video.resolve@1",
    )
    found = _skills_by_id()
    _plant_health(
        isolate_router,
        [
            (found["example.xiaohongshu-web"], "example.xiaohongshu-web\tvideo.resolve@1\txiaohongshu", 0.0, 2),
            (found["example.ytdlp"], "example.ytdlp\tvideo.resolve@1\txiaohongshu", 1.0, 3),
        ],
    )
    result = invoke("video.resolve", {"url": XHS_URL}, yes=True)
    assert result["served_by"]["skill"] == "example.xiaohongshu-web"


def test_douyin_specifics_rank_before_healthy_generic(skills_root, isolate_router):
    make_skill(skills_root, "example.douyin-dl", {"action": "ok", "id": "111"})
    make_skill(skills_root, "example.douyin-ies", {"action": "ok", "id": "222"})
    make_skill(skills_root, "example.ytdlp", {"action": "ok", "id": "333"}, platforms=MANY)
    found = _skills_by_id()
    _plant_health(isolate_router, [(found["example.ytdlp"], "example.ytdlp\tvideo.download@1\tdouyin", 1.0, 5)])
    result = _invoke()
    assert result["served_by"]["skill"] == "example.douyin-dl"
    assert [item["skill"] for item in result["attempts"]] == ["example.douyin-dl"]


def test_second_douyin_specific_runs_before_generic(skills_root, isolate_router):
    make_skill(skills_root, "example.douyin-dl", {"action": "error", "code": "UPSTREAM_CHANGED", "message": "changed"})
    ies = make_skill(skills_root, "example.douyin-ies", {"action": "ok", "id": "222"})
    generic = make_skill(skills_root, "example.ytdlp", {"action": "ok", "id": "333"}, platforms=MANY)
    found = _skills_by_id()
    _plant_health(isolate_router, [(found["example.ytdlp"], "example.ytdlp\tvideo.download@1\tdouyin", 1.0, 5)])
    result = _invoke()
    assert result["ok"] is True, result
    assert result["served_by"]["skill"] == "example.douyin-ies"
    assert [item["skill"] for item in result["attempts"]] == ["example.douyin-dl", "example.douyin-ies"]
    assert (ies / "ran.txt").is_file()
    assert not (generic / "ran.txt").exists()


def test_bilibili_specific_ranks_before_healthy_generic(skills_root, isolate_router):
    make_skill(skills_root, "example.bilibili-web", {"action": "ok", "id": "BV1GJ411x7h7"}, platforms=["bilibili"])
    make_skill(skills_root, "example.ytdlp", {"action": "ok", "id": "generic"}, platforms=MANY)
    found = _skills_by_id()
    _plant_health(isolate_router, [(found["example.ytdlp"], "example.ytdlp\tvideo.download@1\tbilibili", 1.0, 4)])
    result = invoke("video.download", {"url": BILI_URL}, yes=True)
    assert result["served_by"]["skill"] == "example.bilibili-web"
    assert [item["skill"] for item in result["attempts"]] == ["example.bilibili-web"]


def test_wildcard_coverage_loses_to_unknown_specific(skills_root, isolate_router):
    make_skill(skills_root, "aaa.specific", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.catchall", {"action": "ok", "id": "2"}, platforms=["*"])
    found = _skills_by_id()
    _plant_health(isolate_router, [(found["zzz.catchall"], "zzz.catchall\tvideo.download@1\tdouyin", 1.0, 3)])
    result = _invoke()
    assert result["served_by"]["skill"] == "aaa.specific"


def test_resolve_rich_metadata_beats_earlier_id(skills_root):
    make_skill(
        skills_root,
        "aaa.basic",
        _resolve_body("douyin", "1234567890123456789", "basic"),
        capability="video.resolve@1",
        metadata="basic",
    )
    make_skill(
        skills_root,
        "bbb.rich",
        _resolve_body("douyin", "1234567890123456789", "rich"),
        capability="video.resolve@1",
        metadata="rich",
    )
    result = invoke("video.resolve", {"url": URL}, yes=True)
    assert result["served_by"]["skill"] == "bbb.rich"
    assert result["output"]["meta"]["title"] == "rich"


def test_download_ignores_metadata_hint(skills_root):
    make_skill(skills_root, "aaa.basic", {"action": "ok", "id": "1"}, metadata="basic")
    make_skill(skills_root, "bbb.rich", {"action": "ok", "id": "2"}, metadata="rich")
    result = _invoke()
    assert result["served_by"]["skill"] == "aaa.basic"


def test_paid_specific_stays_behind_free_generic(skills_root):
    make_skill(skills_root, "aaa.paid", {"action": "ok", "id": "1"}, cost="paid")
    make_skill(skills_root, "zzz.generic", {"action": "ok", "id": "2"}, platforms=MANY)
    result = _invoke(options={"allow_paid": True})
    assert result["served_by"]["skill"] == "zzz.generic"
    assert [item["skill"] for item in result["attempts"]] == ["zzz.generic"]


def test_pin_and_prefer_still_override_coverage(skills_root, isolate_router):
    make_skill(skills_root, "aaa.specific", {"action": "ok", "id": "1"})
    make_skill(skills_root, "zzz.generic", {"action": "ok", "id": "2"}, platforms=MANY)
    (isolate_router / "config.json").write_text(json.dumps({"pin": ["zzz.generic"]}), encoding="utf-8")
    pinned = _invoke()
    assert pinned["served_by"]["skill"] == "zzz.generic"
    preferred = _invoke(options={"prefer_skill": "aaa.specific"})
    assert preferred["served_by"]["skill"] == "aaa.specific"
