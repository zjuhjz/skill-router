#!/usr/bin/env python3
"""Kuaishou Skill: read the public mobile share page. No third-party scraper."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
_ID_RE = re.compile(r"(?:short-video|fw/photo)/([A-Za-z0-9_-]{6,})")
_STATE_RE = re.compile(r"window\.INIT_STATE\s*=\s*(\{.*?\})\s*;?\s*</script>", re.S)


def _utf8() -> None:
    for name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def fail(code: str, message: str, exit_code: int = 2, retry_after_s: float | None = None) -> None:
    error: dict = {"code": code, "message": str(message)[:500], "details": {}}
    if retry_after_s is not None:
        error["retry_after_s"] = retry_after_s
    sys.stdout.write(json.dumps({"ok": False, "error": error}, ensure_ascii=False))
    raise SystemExit(exit_code)


def _requests():
    try:
        import requests
    except ImportError as exc:
        fail("DEPENDENCY_MISSING", f"需要安装 requests: {exc}", exit_code=3)
    return requests


def find_photo(value: object) -> dict | None:
    """Return the photo object embedded in a share-page INIT_STATE tree."""
    if isinstance(value, dict):
        if isinstance(value.get("mainMvUrls"), list) or (
            value.get("photoType") == "VIDEO" and "caption" in value
        ):
            return value
        for child in value.values():
            found = find_photo(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_photo(child)
            if found:
                return found
    return None


def parse_share_html(html: str) -> dict | None:
    match = _STATE_RE.search(html or "")
    if not match:
        return None
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return find_photo(state)


def _photo_id_from_url(url: str) -> str:
    match = _ID_RE.search(url or "")
    return match.group(1) if match else ""


def _session():
    requests = _requests()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": MOBILE_UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Referer": "https://www.kuaishou.com/",
        }
    )
    cookie = __import__("os").environ.get("KUAISHOU_COOKIES")
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def _get(session, url: str):
    requests = _requests()
    try:
        response = session.get(url, timeout=25, allow_redirects=True)
    except requests.Timeout as exc:
        fail("TIMEOUT", str(exc)[:300])
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", str(exc)[:300])
    if response.status_code == 412:
        fail("GEO_BLOCKED", "HTTP 412")
    if response.status_code == 429:
        fail("RATE_LIMITED", "HTTP 429", retry_after_s=60)
    if response.status_code == 404:
        fail("NOT_FOUND", "HTTP 404")
    if response.status_code >= 400:
        fail("UPSTREAM_ERROR", f"HTTP {response.status_code}")
    return response


def _load_photo(session, url: str) -> tuple[dict, str]:
    photo_id = _photo_id_from_url(url)
    pages = []
    if photo_id:
        pages.append(f"https://c.kuaishou.com/fw/photo/{photo_id}")
    pages.append(url)
    last_html = ""
    for page in pages:
        response = _get(session, page)
        last_html = response.text or ""
        if not photo_id:
            photo_id = _photo_id_from_url(str(response.url)) or _photo_id_from_url(last_html)
        photo = parse_share_html(last_html)
        if photo:
            return photo, photo_id or str(photo.get("photoId") or "")
        if any(token in last_html for token in ("captcha", "验证码")):
            fail("RATE_LIMITED", "Kuaishou asked for a captcha", retry_after_s=60)
    if "登录" in last_html and "photo" not in last_html:
        fail("NEED_AUTH", "share page asks for a login")
    fail("UPSTREAM_CHANGED", "share page has no photo object")
    return {}, ""


def _representations(photo: dict) -> list[dict]:
    found = []
    manifest = photo.get("manifest") or {}
    sets = manifest.get("adaptationSet") if isinstance(manifest, dict) else None
    for group in sets or []:
        if not isinstance(group, dict):
            continue
        for item in group.get("representation") or []:
            if isinstance(item, dict) and isinstance(item.get("url"), str):
                found.append(item)
    found.sort(key=lambda item: int(item.get("height") or 0), reverse=True)
    return found


def _media(photo: dict) -> list[dict]:
    media = []
    seen: set[str] = set()
    for item in _representations(photo):
        url = str(item.get("url"))
        if url in seen:
            continue
        seen.add(url)
        height = item.get("height")
        width = item.get("width")
        media.append(
            {
                "url": url,
                "role": "muxed",
                "container": "mp4",
                "height": int(height) if isinstance(height, int) else None,
                "width": int(width) if isinstance(width, int) else None,
                "quality": item.get("qualityType") or item.get("qualityLabel"),
                "has_watermark": None,
            }
        )
    for item in photo.get("mainMvUrls") or []:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and url not in seen:
            seen.add(url)
            media.append({"url": url, "role": "muxed", "container": "mp4", "has_watermark": None})
    return media


def _meta(photo: dict, photo_id: str, page: str) -> dict:
    duration = photo.get("duration")
    duration_ms = None
    if isinstance(duration, (int, float)) and duration > 0:
        duration_ms = int(duration if duration >= 1000 else duration * 1000)
    ident = photo_id or str(photo.get("photoId") or "")
    if not ident:
        fail("UPSTREAM_CHANGED", "photo has no id")
    cover = None
    covers = photo.get("coverUrls") or []
    if covers and isinstance(covers[0], dict):
        cover = covers[0].get("url")
    return {
        "platform": "kuaishou",
        "id": ident,
        "title": photo.get("caption"),
        "author": photo.get("userName"),
        "author_id": photo.get("userEid") or photo.get("kwaiId"),
        "duration_ms": duration_ms,
        "cover": cover,
        "page_url": page,
    }


def _download(session, media: list[dict], dest: Path) -> int:
    requests = _requests()
    last = "no media url"
    for item in media:
        url = str(item.get("url") or "")
        if not url.startswith("http"):
            continue
        try:
            response = session.get(url, timeout=120, stream=True, allow_redirects=True)
        except requests.RequestException as exc:
            last = str(exc)[:200]
            continue
        if response.status_code >= 400:
            last = f"HTTP {response.status_code}"
            continue
        size = 0
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if chunk:
                    handle.write(chunk)
                    size += len(chunk)
        kind = (response.headers.get("content-type") or "").lower()
        if size >= 2048 and "text/html" not in kind:
            return size
        dest.unlink(missing_ok=True)
        last = f"file too small or not media ({size})"
    fail("UPSTREAM_ERROR", last)
    return 0


def main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    url = str((req.get("input") or {}).get("url") or "")
    host = (urlparse(url).hostname or "").lower()
    if not host or not any(token in host for token in ("kuaishou", "gifshow", "chenzhongtech")):
        fail("UNSUPPORTED_URL", "not a Kuaishou URL")
    session = _session()
    photo, photo_id = _load_photo(session, url)
    if str(photo.get("photoType") or "VIDEO").upper() not in {"VIDEO", ""} and not _media(photo):
        fail("UNSUPPORTED_URL", "this Kuaishou post is not a video")
    media = _media(photo)
    if not media:
        fail("UPSTREAM_CHANGED", "photo object has no playable url")
    page = f"https://www.kuaishou.com/short-video/{photo_id}" if photo_id else url
    meta = _meta(photo, photo_id, page)
    if action == "resolve":
        sys.stdout.write(json.dumps({"ok": True, "output": {"meta": meta, "media": media}}, ensure_ascii=False))
        return
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    out.mkdir(parents=True, exist_ok=True)
    dest = out / f"{meta['id']}.mp4"
    size = _download(session, media, dest)
    height = media[0].get("height")
    width = media[0].get("width")
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "output": {
                    "meta": meta,
                    "files": [
                        {
                            "path": dest.name,
                            "role": "muxed",
                            "size_bytes": size,
                            "has_watermark": None,
                            "height": height,
                            "width": width,
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
