"""Opt-in network checks. Run with: pytest -m live"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from skillrouter.engine import invoke
from tests.fakes import make_skill

pytestmark = pytest.mark.live

REPO = Path(__file__).resolve().parents[1]
DOUYIN = "https://v.douyin.com/e3x2fjE/"
BILIBILI = "https://www.bilibili.com/video/BV1GJ411x7h7"
# Public 2021 upload. Home PC: no CC/AI track, so this is not a subtitle canary.
BILIBILI_SUB = "https://www.bilibili.com/video/BV16h411m7NG"


@pytest.fixture
def live_skills(tmp_path, monkeypatch):
    root = tmp_path / "live-skills"
    root.mkdir()
    make_skill(
        root,
        "aaa.fail",
        {"action": "error", "code": "UPSTREAM_CHANGED", "message": "forced failure", "write": False},
        capabilities=["video.download@1", "video.resolve@1"],
        health_check={"samples": []},
    )
    monkeypatch.setenv(
        "SKILLROUTER_SKILL_PATHS",
        os.pathsep.join(
            [
                str(root),
                str(REPO / "skills" / "douyin-dl"),
                str(REPO / "skills" / "douyin-ies"),
                str(REPO / "skills" / "bilibili-web"),
            ]
        ),
    )
    return root


def test_douyin_falls_back_from_a_failing_skill(live_skills, tmp_path):
    if shutil.which("python3") is None and not os.environ.get("PYTHON"):
        pytest.skip("python missing")
    result = invoke(
        "video.resolve",
        {"url": DOUYIN},
        yes=True,
        options={"max_attempts": 3},
    )
    assert result["attempts"], result
    assert result["attempts"][0]["skill"] == "aaa.fail"
    assert result["attempts"][0]["code"] == "UPSTREAM_CHANGED"
    assert result["ok"] is True, result
    assert result["output"]["meta"]["id"]
    assert result["output"]["media"]
    assert result["served_by"]["skill"] in {"example.douyin-dl", "example.douyin-ies"}


def test_douyin_download(live_skills, tmp_path):
    dest = tmp_path / "downloads"
    result = invoke(
        "video.download",
        {"url": DOUYIN},
        yes=True,
        options={"save_to": str(dest), "prefer_skill": "example.douyin-dl", "max_attempts": 3},
    )
    assert result["ok"] is True, result
    path = Path(result["output"]["files"][0]["path"])
    assert path.is_file()
    assert path.stat().st_size > 100_000
    assert path.read_bytes()[4:8] == b"ftyp"
    path.unlink()


def test_bilibili_resolve_may_be_geo_blocked(live_skills):
    result = invoke("video.resolve", {"url": BILIBILI}, yes=True, options={"max_attempts": 2})
    if result["ok"]:
        assert result["output"]["meta"]["platform"] == "bilibili"
        return
    codes = [item.get("code") for item in result["attempts"]]
    assert codes, result
    assert any(code in {"GEO_BLOCKED", "UPSTREAM_ERROR", "UPSTREAM_CHANGED", "TIMEOUT", "NOT_FOUND"} for code in codes)


def _use(*folders: str, monkeypatch):
    monkeypatch.setenv(
        "SKILLROUTER_SKILL_PATHS",
        os.pathsep.join(str(REPO / "skills" / name) for name in folders),
    )


def test_kuaishou_resolve_and_download(tmp_path, monkeypatch):
    _use("kuaishou-web", monkeypatch=monkeypatch)
    url = "https://www.kuaishou.com/short-video/3x3zxz4mjrsc8ke"
    resolved = invoke("video.resolve", {"url": url}, yes=True, options={"max_attempts": 1})
    assert resolved["ok"] is True, resolved
    assert resolved["output"]["meta"]["platform"] == "kuaishou"
    assert resolved["output"]["meta"]["id"]
    assert resolved["output"]["media"]
    dest = tmp_path / "ks"
    downloaded = invoke(
        "video.download",
        {"url": url},
        yes=True,
        options={"save_to": str(dest), "max_attempts": 1},
    )
    assert downloaded["ok"] is True, downloaded
    path = Path(downloaded["output"]["files"][0]["path"])
    assert path.stat().st_size > 10_000
    assert path.read_bytes()[4:8] == b"ftyp"
    path.unlink()


def test_weibo_resolve(monkeypatch):
    _use("weibo-web", monkeypatch=monkeypatch)
    status = invoke(
        "video.resolve",
        {"url": "https://m.weibo.cn/status/5052484659707960"},
        yes=True,
        options={"max_attempts": 1},
    )
    assert status["ok"] is True, status
    assert status["output"]["meta"]["platform"] == "weibo"
    assert status["output"]["media"]
    show = invoke(
        "video.resolve",
        {"url": "https://weibo.com/tv/show/1034:5302868002144396"},
        yes=True,
        options={"max_attempts": 1},
    )
    assert show["ok"] is True, show
    assert show["output"]["media"]


def test_xiaohongshu_reports_a_clear_block_or_a_note(monkeypatch):
    _use("xiaohongshu-web", monkeypatch=monkeypatch)
    result = invoke(
        "video.resolve",
        {"url": "https://www.xiaohongshu.com/explore/649c46ab000000002702ad36"},
        yes=True,
        options={"max_attempts": 2},
    )
    if result["ok"]:
        assert result["output"]["meta"]["platform"] == "xiaohongshu"
        return
    codes = [item.get("code") for item in result["attempts"]]
    assert codes, result
    assert any(code in {"GEO_BLOCKED", "NEED_AUTH", "NOT_FOUND", "RATE_LIMITED", "UPSTREAM_CHANGED", "UPSTREAM_ERROR"} for code in codes)


def test_bilibili_subtitle_or_geo_block(monkeypatch):
    _use("bilibili-subtitle", monkeypatch=monkeypatch)
    result = invoke("video.transcript", {"url": BILIBILI_SUB, "language": "zh"}, yes=True, options={"max_attempts": 1})
    if result["ok"]:
        assert result["output"]["source"] == "platform_subtitle"
        assert result["output"]["segments"]
        return
    codes = [item.get("code") for item in result["attempts"]]
    assert any(code in {"GEO_BLOCKED", "UNSUPPORTED_URL", "NOT_FOUND", "UPSTREAM_CHANGED", "UPSTREAM_ERROR", "TIMEOUT"} for code in codes)


def test_faster_whisper_transcribes_douyin(tmp_path, monkeypatch):
    pytest.importorskip("faster_whisper")
    monkeypatch.setenv("SKILLROUTER_MAX_TOTAL_S", "900")
    _use("faster-whisper", "douyin-dl", "douyin-ies", monkeypatch=monkeypatch)
    result = invoke(
        "video.transcript",
        {"url": DOUYIN, "language": "zh"},
        yes=True,
        options={"prefer_skill": "example.faster-whisper", "max_attempts": 1},
    )
    assert result["ok"] is True, result
    text = result["output"]["text"]
    assert result["output"]["source"] == "asr"
    assert result["output"]["script"] == "Hans"
    assert result["output"]["segments"]
    assert any("\u4e00" <= ch <= "\u9fff" for ch in text), text
    import zhconv

    assert zhconv.convert(text, "zh-cn") == text
