#!/usr/bin/env python3
"""Paid Gemini ASR. The API key is an env var and is never written to stdout or stderr."""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_DEFAULT_BASE = "https://generativelanguage.googleapis.com/v1beta"
_MODEL = "gemini-4-argon"
_PROMPT = (
    "把这段视频里的人声转写成文本。语言用 BCP-47，中文用 zh，汉字用简体。"
    "只输出一个 JSON 对象，不要 Markdown。"
    '格式：{"language":"zh","text":"整段文本","segments":[{"start_ms":0,"end_ms":1000,"text":"..."}]}。'
    "时间单位是毫秒。没有人声时 text 为空字符串、segments 为空数组。"
)
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
_MAX_INLINE = 20 * 1024 * 1024


def _utf8() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _redact(message: str, key: str) -> str:
    text = str(message)
    if key:
        text = text.replace(key, "***")
    return text[:500]


def fail(code: str, message: str, key: str = "", exit_code: int = 2) -> None:
    sys.stdout.write(
        json.dumps(
            {"ok": False, "error": {"code": code, "message": _redact(message, key), "details": {}}},
            ensure_ascii=False,
        )
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


def download_via_router(url: str, dest: Path, timeout_s: float, key: str) -> Path:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "skillrouter", "invoke", "video.download@1", "--url", url, "--save-to", str(dest), "--yes"]
    env = os.environ.copy()
    env.pop("GEMINI_API_KEY", None)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        completed = subprocess.run(cmd, capture_output=True, timeout=max(30.0, timeout_s), env=env, check=False)
    except subprocess.TimeoutExpired:
        fail("TIMEOUT", "video.download@1 timed out", key)
    text = completed.stdout.decode("utf-8", errors="replace").strip()
    try:
        payload = json.loads(text) if text else {}
    except json.JSONDecodeError:
        fail("UPSTREAM_ERROR", "video.download did not return JSON", key)
    if not payload.get("ok"):
        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
        code = str(error.get("code") or "UPSTREAM_ERROR")
        if code == "ALL_FAILED":
            attempts = payload.get("attempts") or []
            nested = attempts[0].get("code") if attempts and isinstance(attempts[0], dict) else ""
            code = str(nested) if nested in _PASSTHROUGH else "UPSTREAM_ERROR"
        if code not in _PASSTHROUGH:
            code = "UPSTREAM_ERROR"
        fail(code, str(error.get("message") or "video.download failed"), key)
    files = (payload.get("output") or {}).get("files") or []
    if not files or not isinstance(files[0], dict):
        fail("UPSTREAM_ERROR", "video.download returned no file", key)
    path = Path(str(files[0].get("path") or ""))
    if not path.is_file():
        fail("UPSTREAM_ERROR", "downloaded file is missing", key)
    return path


def obtain(req: dict, key: str) -> Path:
    data = req.get("input") or {}
    local = data.get("file")
    if isinstance(local, str) and local.strip():
        return resolve_local(req, local.strip())
    url = str(data.get("url") or "").strip()
    if not url:
        fail("UNSUPPORTED_URL", "need a url or a local file", key)
    timeout_s = float((req.get("context") or {}).get("timeout_s") or 180)
    return download_via_router(url, Path(str((req.get("context") or {}).get("output_dir") or ".")) / "media", timeout_s * 0.45, key)


def _mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mkv": "video/x-matroska",
        ".webm": "video/webm",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
    }.get(suffix, "video/mp4")


def _parse_model_text(text: str) -> dict:
    cleaned = text.strip()
    fence = re.search(r"\{.*\}", cleaned, re.S)
    raw = fence.group(0) if fence else cleaned
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"model text was not JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("model JSON was not an object")
    segments = []
    for item in value.get("segments") or []:
        if not isinstance(item, dict):
            continue
        start = item.get("start_ms")
        end = item.get("end_ms")
        if isinstance(start, bool) or isinstance(end, bool):
            continue
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        segments.append({"start_ms": start, "end_ms": end, "text": str(item.get("text") or "")})
    return {
        "language": str(value.get("language") or "und"),
        "text": str(value.get("text") or ""),
        "segments": segments,
    }


def generate(path: Path, key: str, language: str | None) -> dict:
    if path.stat().st_size > _MAX_INLINE:
        fail("QUALITY_NOT_MET", "file is larger than 20MB; Gemini inline ASR needs a shorter clip", key)
    prompt = _PROMPT
    if language:
        prompt += f" 优先按语言 {language} 转写。"
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"inline_data": {"mime_type": _mime(path), "data": base64.b64encode(path.read_bytes()).decode("ascii")}},
                    {"text": prompt},
                ],
            }
        ],
        "generationConfig": {"responseMimeType": "application/json"},
    }
    base = (os.environ.get("GEMINI_API_BASE") or _DEFAULT_BASE).rstrip("/")
    endpoint = f"{base}/models/{_MODEL}:generateContent"
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in (401, 403):
            fail("NEED_AUTH", f"Gemini HTTP {exc.code}", key)
        if exc.code == 429:
            fail("RATE_LIMITED", f"Gemini HTTP 429 {detail}", key)
        fail("UPSTREAM_ERROR", f"Gemini HTTP {exc.code} {detail}", key)
    except urllib.error.URLError as exc:
        fail("UPSTREAM_ERROR", f"Gemini request failed: {exc.reason}", key)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        fail("UPSTREAM_CHANGED", f"Gemini response was not JSON: {exc}", key)
    candidates = payload.get("candidates") if isinstance(payload, dict) else None
    parts = (((candidates or [{}])[0].get("content") or {}).get("parts") or []) if candidates else []
    text = ""
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            text += part["text"]
    if not text:
        fail("UPSTREAM_CHANGED", "Gemini response has no text", key)
    try:
        parsed = _parse_model_text(text)
    except ValueError as exc:
        fail("UPSTREAM_CHANGED", str(exc), key)
    return parsed


def _script_of(requested: str | None, detected: str) -> str | None:
    chosen = (requested or detected or "").strip().lower().replace("_", "-")
    if chosen.startswith("zh-hant") or chosen in {"zh-tw", "zh-hk", "zh-mo"}:
        return "Hant"
    if chosen.startswith("zh"):
        return "Hans"
    return None


def _to_script(text: str, script: str) -> str:
    try:
        import zhconv
    except ImportError:
        return text
    return zhconv.convert(text, "zh-cn" if script == "Hans" else "zh-hant")


def main() -> None:
    _utf8()
    key = os.environ.get("GEMINI_API_KEY") or ""
    if not key:
        fail("NEED_AUTH", "GEMINI_API_KEY is not set")
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}", key)
    try:
        path = obtain(req, key)
        language = str((req.get("input") or {}).get("language") or "").strip() or None
        parsed = generate(path, key, language)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        fail("UPSTREAM_ERROR", str(exc), key)
    script = _script_of(language, str(parsed.get("language") or ""))
    if script:
        parsed["text"] = _to_script(parsed["text"], script)
        for item in parsed["segments"]:
            item["text"] = _to_script(item["text"], script)
        parsed["script"] = script
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "output": {
                    "language": parsed["language"],
                    "source": "asr",
                    "text": parsed["text"],
                    "segments": parsed["segments"],
                    **({"script": parsed["script"]} if parsed.get("script") else {}),
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
