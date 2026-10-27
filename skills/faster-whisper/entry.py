#!/usr/bin/env python3
"""Local ASR with faster-whisper. Obtains the video via video.download@1, never by importing another Skill."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

_PASSTHROUGH = {
    "NEED_AUTH",
    "GEO_BLOCKED",
    "NOT_FOUND",
    "RATE_LIMITED",
    "DEPENDENCY_MISSING",
    "UNSUPPORTED_URL",
    "TIMEOUT",
    "QUOTA_EXCEEDED",
    "UPSTREAM_CHANGED",
    "UPSTREAM_ERROR",
}


def _utf8() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def fail(code: str, message: str, exit_code: int = 2) -> None:
    sys.stdout.write(
        json.dumps({"ok": False, "error": {"code": code, "message": str(message)[:500], "details": {}}}, ensure_ascii=False)
    )
    raise SystemExit(exit_code)


def resolve_local(req: dict, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        base = str((req.get("context") or {}).get("client_cwd") or "")
        path = (Path(base) / path) if base else path
    try:
        path = path.resolve()
    except OSError as exc:
        fail("NOT_FOUND", f"local file is unreadable: {exc}")
    if not path.is_file():
        fail("NOT_FOUND", "local file does not exist")
    return path


def download_via_router(url: str, dest: Path, timeout_s: float) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "skillrouter",
        "invoke",
        "video.download@1",
        "--url",
        url,
        "--save-to",
        str(dest),
        "--yes",
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(
            cmd,
            capture_output=True,
            timeout=max(30.0, timeout_s),
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail("TIMEOUT", "video.download@1 timed out")
    text = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        tail = completed.stderr.decode("utf-8", errors="replace")[-300:]
        fail("DEPENDENCY_MISSING" if completed.returncode == 3 else "UPSTREAM_ERROR", tail or "video.download did not return JSON", 3 if completed.returncode == 3 else 2)
    if not payload.get("ok"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "UPSTREAM_ERROR")
        if code == "ALL_FAILED":
            attempts = payload.get("attempts") or []
            if attempts and isinstance(attempts[0], dict) and attempts[0].get("code") in _PASSTHROUGH:
                code = str(attempts[0]["code"])
            else:
                code = "UPSTREAM_ERROR"
        if code not in _PASSTHROUGH:
            code = "UPSTREAM_ERROR"
        hints = " ".join(str(item) for item in (payload.get("hints") or []))
        fail(code, (str(error.get("message") or "video.download failed") + " " + hints).strip())
    files = (payload.get("output") or {}).get("files") or []
    if not files or not isinstance(files[0], dict):
        fail("UPSTREAM_ERROR", "video.download returned no file")
    path = Path(str(files[0].get("path") or ""))
    if not path.is_file():
        fail("UPSTREAM_ERROR", "downloaded file is missing")
    return path


def obtain(req: dict) -> Path:
    data = req.get("input") or {}
    local = data.get("file")
    if isinstance(local, str) and local.strip():
        return resolve_local(req, local.strip())
    url = str(data.get("url") or "").strip()
    if not url:
        fail("UNSUPPORTED_URL", "need a url or a local file")
    timeout_s = float((req.get("context") or {}).get("timeout_s") or 180)
    return download_via_router(url, Path(str((req.get("context") or {}).get("output_dir") or ".")) / "media", timeout_s * 0.6)


_MIRROR = "https://hf-mirror.com"
_OFFICIAL_HOSTS = {"huggingface.co", "www.huggingface.co"}
_STALL_S = 15.0
_SLOW_S = 12.0
_SLOW_BYTES = 24 * 1024 * 1024
_HANS_PROMPT = "以下是简体中文普通话，请只使用简体中文，不要输出繁体字。"
_FETCH_HINT = (
    "模型下载没有完成。设置 HF_ENDPOINT=https://hf-mirror.com 后重试，"
    "或先运行 skillrouter warmup example.faster-whisper。"
    "未下完的文件留在 Hugging Face 缓存里，名字以 .incomplete 结尾。"
)


def endpoints_to_try(current: str | None) -> list[str]:
    raw = (current or "").strip().rstrip("/") or "https://huggingface.co"
    host = (urlparse(raw).hostname or "").lower()
    if host in _OFFICIAL_HOSTS:
        if raw == _MIRROR:
            return [_MIRROR]
        return [raw, _MIRROR]
    return [raw]


def fetch_problem(elapsed: float, idle: float, gained: int, *, official: bool) -> str | None:
    """Return why a model download should be aborted, or None to keep waiting."""
    if idle >= _STALL_S:
        return "stalled"
    if official and elapsed >= _SLOW_S and gained < _SLOW_BYTES:
        return "slow"
    return None


def target_script(requested: str | None, detected: str | None) -> str | None:
    chosen = (requested or detected or "").strip().lower().replace("_", "-")
    if not chosen:
        return None
    if chosen.startswith("zh-hant") or chosen in {"zh-tw", "zh-hk", "zh-mo"}:
        return "Hant"
    if chosen.startswith("zh"):
        return "Hans"
    return None


def to_script(text: str, script: str) -> str:
    try:
        import zhconv
    except ImportError as exc:
        fail("DEPENDENCY_MISSING", f"需要安装 zhconv 才能输出简体中文（pip install 'skill-router[asr]'）: {exc}", 3)
    locale = "zh-cn" if script == "Hans" else "zh-hant"
    return zhconv.convert(text, locale)


def _announce_fetch() -> None:
    sys.stderr.write("skillrouter: model-fetch\n")
    sys.stderr.write("skillrouter: model-fetch-hint: " + _FETCH_HINT + "\n")
    sys.stderr.flush()


def _cache_bytes() -> int:
    roots: list[Path] = []
    if os.environ.get("HF_HUB_CACHE"):
        roots.append(Path(os.environ["HF_HUB_CACHE"]))
    if os.environ.get("HUGGINGFACE_HUB_CACHE"):
        roots.append(Path(os.environ["HUGGINGFACE_HUB_CACHE"]))
    if os.environ.get("HF_HOME"):
        roots.append(Path(os.environ["HF_HOME"]) / "hub")
    roots.append(Path.home() / ".cache" / "huggingface" / "hub")
    total = 0
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        for item in root.rglob("*"):
            if not item.is_file():
                continue
            name = item.name.lower()
            if not (name.endswith(".incomplete") or name == "model.bin"):
                continue
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _download_once(model_name: str, endpoint: str) -> str:
    official = (urlparse(endpoint).hostname or "").lower() in _OFFICIAL_HOSTS
    env = os.environ.copy()
    env["HF_ENDPOINT"] = endpoint
    env["HF_HUB_ETAG_TIMEOUT"] = env.get("HF_HUB_ETAG_TIMEOUT") or "10"
    env["HF_HUB_DOWNLOAD_TIMEOUT"] = env.get("HF_HUB_DOWNLOAD_TIMEOUT") or "20"
    env["PYTHONUTF8"] = "1"
    code = (
        "import sys\n"
        "from faster_whisper.utils import download_model\n"
        "print(download_model(sys.argv[1]), flush=True)\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, model_name],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    start = time.monotonic()
    last_change = start
    start_bytes = _cache_bytes()
    last = start_bytes
    problem = None
    while proc.poll() is None:
        time.sleep(1)
        now = time.monotonic()
        size = _cache_bytes()
        if size > last + 64 * 1024:
            last = size
            last_change = now
        problem = fetch_problem(now - start, now - last_change, size - start_bytes, official=official)
        if problem or now - start >= 180:
            problem = problem or "timeout"
            break
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    stdout = proc.stdout.read() if proc.stdout else b""
    if problem:
        raise TimeoutError(problem)
    if proc.returncode != 0:
        raise RuntimeError(f"download exit {proc.returncode}")
    lines = stdout.decode("utf-8", errors="replace").strip().splitlines()
    if not lines:
        raise RuntimeError("download returned no path")
    return lines[-1]


def ensure_model(model_name: str) -> str:
    from faster_whisper.utils import download_model

    try:
        return str(download_model(model_name, local_files_only=True))
    except Exception:
        pass
    _announce_fetch()
    errors: list[str] = []
    for endpoint in endpoints_to_try(os.environ.get("HF_ENDPOINT")):
        try:
            path = _download_once(model_name, endpoint)
            if path and Path(path).exists():
                return path
            errors.append(f"{endpoint}: empty path")
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{endpoint}: {exc}")
    fail("DEPENDENCY_MISSING", (_FETCH_HINT + " " + "; ".join(errors))[:500], 3)
    return ""


def transcribe(path: Path, language: str | None) -> tuple[str, str, list[dict], str | None]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        fail("DEPENDENCY_MISSING", f"需要安装 faster-whisper 和 av<19（pip install 'skill-router[asr]'）: {exc}", 3)
    model_name = os.environ.get("WHISPER_MODEL") or "small"
    device = os.environ.get("WHISPER_DEVICE") or "cpu"
    compute = os.environ.get("WHISPER_COMPUTE_TYPE") or "int8"
    vad = (os.environ.get("WHISPER_VAD") or "").strip().lower() in {"1", "true", "yes", "on"}
    prompt = (os.environ.get("WHISPER_INITIAL_PROMPT") or "").strip() or None
    if prompt is None and language and language.lower().startswith("zh"):
        prompt = _HANS_PROMPT
    try:
        model_path = ensure_model(model_name)
        model = WhisperModel(model_path, device=device, compute_type=compute, local_files_only=True)
        segments_iter, info = model.transcribe(
            str(path),
            language=language or None,
            vad_filter=vad,
            beam_size=5,
            initial_prompt=prompt,
        )
        segments = []
        for item in segments_iter:
            segments.append(
                {
                    "start_ms": int(float(item.start) * 1000),
                    "end_ms": int(float(item.end) * 1000),
                    "text": (item.text or "").strip(),
                }
            )
    except Exception as exc:  # noqa: BLE001
        fail("UPSTREAM_ERROR", f"faster-whisper failed: {exc}")
    text = "\n".join(item["text"] for item in segments if item["text"])
    detected = language or getattr(info, "language", None) or "und"
    script = target_script(language, str(detected))
    if script:
        text = to_script(text, script)
        for item in segments:
            item["text"] = to_script(item["text"], script)
    return str(detected), text, segments, script


def main() -> None:
    _utf8()
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    if (req.get("context") or {}).get("mode") == "setup":
        try:
            from faster_whisper import WhisperModel  # noqa: F401
        except ImportError as exc:
            fail("DEPENDENCY_MISSING", f"需要安装 faster-whisper 和 av<19（pip install 'skill-router[asr]'）: {exc}", 3)
        model_name = os.environ.get("WHISPER_MODEL") or "small"
        ready = ensure_model(model_name)
        sys.stdout.write(json.dumps({"ok": True, "output": {"ready": True, "model": model_name, "path": ready}}, ensure_ascii=False))
        return
    data = req.get("input") or {}
    language = str(data.get("language") or "").strip() or None
    path = obtain(req)
    detected, text, segments, script = transcribe(path, language)
    output = {
        "language": detected,
        "source": "asr",
        "text": text,
        "segments": segments,
    }
    if script:
        output["script"] = script
    sys.stdout.write(json.dumps({"ok": True, "output": output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
