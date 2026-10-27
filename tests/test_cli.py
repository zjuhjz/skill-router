import json
import os
import subprocess
import sys
from pathlib import Path

from tests.fakes import make_skill

REPO = Path(__file__).resolve().parents[1]


def test_cli_prints_utf8_even_if_parent_encoding_is_legacy(skills_root, tmp_path):
    make_skill(skills_root, "aaa.ok", {"action": "ok", "title": "中文标题", "id": "42"})
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "gbk"
    env["PYTHONUTF8"] = "0"
    env["PYTHONPATH"] = str(REPO / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "skillrouter",
            "invoke",
            "video.download",
            "--input",
            json.dumps({"url": "https://v.douyin.com/cli-token/"}, ensure_ascii=False),
            "--yes",
            "--save-to",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    assert "中文标题".encode("utf-8") in proc.stdout
    assert "中文标题".encode("gbk") not in proc.stdout
    payload = json.loads(proc.stdout.decode("utf-8"))
    assert payload["output"]["meta"]["title"] == "中文标题"


def _env() -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO / "src")
    env["PYTHONIOENCODING"] = "gbk"
    env["PYTHONUTF8"] = "0"
    return env


def test_help_text_is_ascii_when_captured(tmp_path):
    for args in (["--help"], ["invoke", "--help"]):
        proc = subprocess.run(
            [sys.executable, "-m", "skillrouter", *args],
            capture_output=True,
            env=_env(),
            cwd=str(REPO),
            check=False,
        )
        assert proc.returncode == 0, proc.stderr
        proc.stdout.decode("ascii")
        assert b"--url" in proc.stdout or args == ["--help"]
    invoke_help = subprocess.run(
        [sys.executable, "-m", "skillrouter", "invoke", "--help"],
        capture_output=True,
        env=_env(),
        cwd=str(REPO),
        check=False,
    )
    assert b"--input-file" in invoke_help.stdout
    assert b"--url" in invoke_help.stdout


def test_url_and_input_file_shorthands(skills_root, tmp_path):
    make_skill(skills_root, "aaa.ok", {"action": "ok", "title": "ok", "id": "42"})
    env = _env()
    env["SKILLROUTER_HOME"] = os.environ["SKILLROUTER_HOME"]
    env["SKILLROUTER_REGISTRY"] = os.environ["SKILLROUTER_REGISTRY"]
    env["SKILLROUTER_SKILL_PATHS"] = os.environ["SKILLROUTER_SKILL_PATHS"]
    env["SKILLROUTER_LAZY_DOCTOR"] = "0"
    url_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "skillrouter",
            "invoke",
            "video.download",
            "--url",
            "https://v.douyin.com/cli-url/",
            "--yes",
            "--save-to",
            str(tmp_path / "out"),
        ],
        capture_output=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )
    assert url_proc.returncode == 0, url_proc.stderr.decode("utf-8", errors="replace")
    assert json.loads(url_proc.stdout.decode("utf-8"))["ok"] is True
    spec = tmp_path / "in.json"
    spec.write_text('{"url": "https://v.douyin.com/cli-file/"}', encoding="utf-8-sig")
    file_proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "skillrouter",
            "invoke",
            "video.download",
            "--input-file",
            str(spec),
            "--yes",
            "--save-to",
            str(tmp_path / "out2"),
        ],
        capture_output=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )
    assert file_proc.returncode == 0, file_proc.stderr.decode("utf-8", errors="replace")
    missing = subprocess.run(
        [sys.executable, "-m", "skillrouter", "invoke", "video.download"],
        capture_output=True,
        env=env,
        cwd=str(REPO),
        check=False,
    )
    assert missing.returncode == 1
    assert json.loads(missing.stdout.decode("utf-8"))["error"]["code"] == "INVALID_INPUT"
