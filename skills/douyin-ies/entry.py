#!/usr/bin/env python3
"""Second Douyin Skill: iesdouyin share page, independent of douyin_dl.py."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)
_ID_RE = re.compile(r"(?:(?:video|note|share/video)/|modal_id=|aweme_id=)(\d{15,25})")
_ROUTER_RE = re.compile(r"window\._ROUTER_DATA\s*=\s*(\{.*?)\s*</script>", re.S)
_URI_RE = re.compile(r"^v[0-9A-Za-z]+")


class SkillFail(Exception):
    def __init__(self, code: str, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after_s = retry_after_s


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


def _session():
    requests = _requests()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": IPHONE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    cookie = os.environ.get("DOUYIN_COOKIES")
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def _http(session, method: str, url: str, **kwargs):
    requests = _requests()
    try:
        response = session.request(method, url, timeout=kwargs.pop("timeout", 30), **kwargs)
    except requests.Timeout as exc:
        raise SkillFail("TIMEOUT", str(exc)[:300]) from exc
    except requests.RequestException as exc:
        raise SkillFail("UPSTREAM_ERROR", str(exc)[:300]) from exc
    status = response.status_code
    if status == 412:
        raise SkillFail("GEO_BLOCKED", "HTTP 412")
    if status == 429:
        raise SkillFail("RATE_LIMITED", "HTTP 429", retry_after_s=60)
    if status == 404:
        raise SkillFail("NOT_FOUND", "HTTP 404")
    if status >= 400:
        raise SkillFail("UPSTREAM_ERROR", f"HTTP {status}")
    return response


def _aweme_in(text: str) -> str | None:
    match = _ID_RE.search(text or "")
    return match.group(1) if match else None


def resolve_aweme_id(session, url: str) -> tuple[str, str]:
    host = (urlparse(url).hostname or "").lower().removeprefix("www.")
    found = _aweme_in(url)
    if found and host != "v.douyin.com":
        return found, url
    response = _http(session, "GET", url, allow_redirects=True)
    found = _aweme_in(str(response.url)) or _aweme_in(response.text[:20000])
    if not found:
        raise SkillFail("UNSUPPORTED_URL", f"could not resolve aweme id from {url}")
    return found, str(response.url)


def _item(html: str) -> dict:
    match = _ROUTER_RE.search(html or "")
    if not match:
        raise SkillFail("UPSTREAM_CHANGED", "window._ROUTER_DATA not found")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise SkillFail("UPSTREAM_CHANGED", f"_ROUTER_DATA JSON parse failed: {exc}") from exc
    loader = data.get("loaderData") or {}
    if not isinstance(loader, dict):
        raise SkillFail("UPSTREAM_CHANGED", "loaderData missing")
    page = loader.get("video_(id)/page") or loader.get("video_(id)\\/page")
    if not isinstance(page, dict):
        for value in loader.values():
            if isinstance(value, dict) and "videoInfoRes" in value:
                page = value
                break
    if not isinstance(page, dict):
        raise SkillFail("UPSTREAM_CHANGED", "No video_(id)/page in _ROUTER_DATA")
    info = page.get("videoInfoRes") or {}
    items = info.get("item_list") or []
    if not items:
        filters = info.get("filter_list") or []
        if filters:
            raise SkillFail("NOT_FOUND", "video filtered or removed")
        raise SkillFail("UPSTREAM_CHANGED", "videoInfoRes.item_list empty")
    if not isinstance(items[0], dict):
        raise SkillFail("UPSTREAM_CHANGED", "item_list[0] was not an object")
    return items[0]


def _media(item: dict) -> list[dict]:
    video = item.get("video") or {}
    play = video.get("play_addr") or {}
    uri = play.get("uri") or ""
    found: list[dict] = []

    def add(url: str, watermark: bool | None, quality: str | None = None) -> None:
        if url and url.startswith("http"):
            found.append(
                {
                    "url": url,
                    "role": "muxed",
                    "container": "mp4",
                    "has_watermark": watermark,
                    "quality": quality,
                }
            )

    if isinstance(uri, str) and _URI_RE.match(uri):
        add(f"https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0", False, "720p")
        add(f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0", False, "720p")
    for raw in play.get("url_list") or []:
        if not raw:
            continue
        rewritten = str(raw).replace("playwm", "play")
        add(rewritten, False)
        if rewritten != raw:
            add(str(raw), True)
    deduped = []
    seen: set[str] = set()
    for item_url in found:
        if item_url["url"] in seen:
            continue
        seen.add(item_url["url"])
        deduped.append(item_url)
    return deduped


def _meta(item: dict, aweme_id: str, page_url: str) -> dict:
    author = item.get("author") or {}
    if isinstance(author, dict):
        author_name = author.get("nickname") or author.get("unique_id")
    else:
        author_name = str(author) if author else None
    video = item.get("video") or {}
    return {
        "platform": "douyin",
        "id": str(item.get("aweme_id") or aweme_id),
        "title": item.get("preview_title") or item.get("desc") or None,
        "author": author_name,
        "duration_ms": int(video.get("duration") or 0),
        "page_url": page_url,
    }


def _download(session, urls: list[dict], dest: Path) -> tuple[int, bool | None]:
    last: Exception | None = None
    for media in urls:
        partial = dest.with_suffix(dest.suffix + ".part")
        try:
            response = _http(
                session,
                "GET",
                media["url"],
                headers={"Referer": "https://www.douyin.com/", "User-Agent": IPHONE_UA},
                stream=True,
                allow_redirects=True,
                timeout=120,
            )
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                raise SkillFail("UPSTREAM_ERROR", f"play URL returned HTML ({ctype})")
            size = 0
            with partial.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=256 * 1024):
                    if chunk:
                        handle.write(chunk)
                        size += len(chunk)
            if size < 100_000:
                partial.unlink(missing_ok=True)
                raise SkillFail("UPSTREAM_ERROR", f"file too small ({size})")
            partial.replace(dest)
            return size, media.get("has_watermark")
        except SkillFail as exc:
            last = exc
            partial.unlink(missing_ok=True)
            continue
        except Exception as exc:  # noqa: BLE001
            last = exc
            partial.unlink(missing_ok=True)
            continue
    raise SkillFail("UPSTREAM_ERROR", f"所有播放地址都下载失败: {last}")


def _load(action: str) -> tuple[dict, dict]:
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    url = str((req.get("input") or {}).get("url") or "")
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    out.mkdir(parents=True, exist_ok=True)
    session = _session()
    try:
        aweme_id, page = resolve_aweme_id(session, url)
        share = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
        _http(session, "GET", share, allow_redirects=True)
        page_html = _http(session, "GET", share, params={"is_v_html": "true"}, allow_redirects=True)
        item = _item(page_html.text)
        media = _media(item)
        if not media:
            raise SkillFail("UPSTREAM_CHANGED", "no play URL candidates")
        meta = _meta(item, aweme_id, page)
        if action == "resolve":
            return {"meta": meta, "media": media}, req
        dest = out / f"{meta['id']}.mp4"
        size, watermark = _download(session, media, dest)
        return {
            "meta": meta,
            "files": [
                {
                    "path": dest.name,
                    "role": "muxed",
                    "size_bytes": size,
                    "has_watermark": watermark,
                }
            ],
        }, req
    except SkillFail as exc:
        fail(exc.code, str(exc), retry_after_s=exc.retry_after_s)
    return {}, {}


def main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    output, _req = _load(action)
    sys.stdout.write(json.dumps({"ok": True, "output": output}, ensure_ascii=False))


if __name__ == "__main__":
    main()
