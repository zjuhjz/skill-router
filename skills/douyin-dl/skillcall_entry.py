#!/usr/bin/env python3
"""skillcall adapter. Does not change douyin_dl.py; the old CLI still works.

Only the iesdouyin path is exposed here. videofetch and yt-dlp stay inside
douyin_dl.py's own CLI so the router can fall back to other Skills instead.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _utf8() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _emit(payload: dict, code: int) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(code)


def fail(code: str, message: str, details: dict | None = None, exit_code: int = 2) -> None:
    _emit(
        {"ok": False, "error": {"code": code, "message": str(message)[:500], "details": details or {}}},
        exit_code,
    )


def _classify_parse(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "TIMEOUT"
    try:
        import requests
    except ImportError:
        requests = None  # type: ignore
    if requests is not None and isinstance(exc, requests.Timeout):
        return "TIMEOUT"
    if requests is not None and isinstance(exc, requests.HTTPError):
        status = exc.response.status_code if exc.response is not None else 0
        if status == 412:
            return "GEO_BLOCKED"
        if status == 404:
            return "NOT_FOUND"
        if status == 429:
            return "RATE_LIMITED"
        if status >= 500:
            return "UPSTREAM_ERROR"
        return "UPSTREAM_ERROR"
    if requests is not None and isinstance(exc, requests.RequestException):
        return "UPSTREAM_ERROR"
    text = str(exc)
    if any(token in text for token in ("删除", "私密", "不存在")):
        return "NOT_FOUND"
    if any(token in text for token in ("_ROUTER_DATA", "video_(id)", "JSON", "No play URL", "item_list")):
        return "UPSTREAM_CHANGED"
    return "UPSTREAM_ERROR"


def main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    try:
        import douyin_dl as dl
    except ImportError as exc:
        fail("DEPENDENCY_MISSING", str(exc), exit_code=3)
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    url = str((req.get("input") or {}).get("url") or "")
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    out.mkdir(parents=True, exist_ok=True)
    session = dl._session()
    cookie = os.environ.get("DOUYIN_COOKIES")
    if cookie:
        session.headers["Cookie"] = cookie
    try:
        aweme_id, final = dl.resolve_aweme_id(url, session)
    except dl.DouyinDownloadError as exc:
        fail("UNSUPPORTED_URL", str(exc)[:300])
    except Exception as exc:  # noqa: BLE001
        fail(_classify_parse(exc), str(exc)[:300])
    try:
        meta = dl.parse_iesdouyin(aweme_id, session)
    except Exception as exc:  # noqa: BLE001
        fail(_classify_parse(exc), str(exc)[:300])
    body = {
        "platform": "douyin",
        "id": str(meta.get("aweme_id") or aweme_id),
        "title": meta.get("desc") or None,
        "author": meta.get("author") or None,
        "duration_ms": int(meta.get("duration_ms") or 0),
        "page_url": final,
    }
    media = [
        {"url": item, "role": "muxed", "container": "mp4", "has_watermark": False}
        for item in meta.get("play_urls") or []
    ]
    if action == "resolve":
        _emit({"ok": True, "output": {"meta": body, "media": media}}, 0)
    dest = out / f"{body['id']}.mp4"
    last = None
    size = 0
    for item in meta.get("play_urls") or []:
        try:
            size = dl.download_file(item, dest, session)
            break
        except Exception as exc:  # noqa: BLE001
            last = exc
            continue
    else:
        fail("UPSTREAM_ERROR", f"所有播放地址都下载失败: {last}"[:300])
    _emit(
        {
            "ok": True,
            "output": {
                "meta": body,
                "files": [
                    {
                        "path": dest.name,
                        "role": "muxed",
                        "size_bytes": size,
                        "has_watermark": False,
                    }
                ],
            },
        },
        0,
    )


if __name__ == "__main__":
    main()
