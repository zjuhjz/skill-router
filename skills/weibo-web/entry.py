#!/usr/bin/env python3
"""Weibo Skill: visitor session, mobile status JSON, and the h5 video component."""

from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1"
)
_FID_RE = re.compile(r"(1034:\d+)")
_STATUS_RE = re.compile(r"/(?:status|detail)/([A-Za-z0-9]+)")
_TAG_RE = re.compile(r"<[^>]+>")


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


def _abs(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return None


def _plain(text: object) -> str | None:
    if not isinstance(text, str):
        return None
    cleaned = _TAG_RE.sub(" ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or None


def _jsonp(text: str) -> dict:
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return {}
    try:
        value = json.loads(text[start + 1 : end])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def visitor_login(session) -> None:
    requests = _requests()
    fingerprint = {
        "os": "1",
        "browser": "Chrome131",
        "fonts": "Arial",
        "screenInfo": "1920*1080*24",
        "plugins": "",
    }
    try:
        created = session.post(
            "https://passport.weibo.com/visitor/genvisitor",
            data={"cb": "gen_callback", "fp": json.dumps(fingerprint)},
            timeout=20,
        )
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", f"visitor gen failed: {exc}")
    tid = (_jsonp(created.text).get("data") or {}).get("tid")
    if not isinstance(tid, str) or not tid:
        fail("UPSTREAM_CHANGED", "visitor response has no tid")
    try:
        incarnate = session.get(
            "https://passport.weibo.com/visitor/visitor",
            params={
                "a": "incarnate",
                "t": tid,
                "w": 2,
                "c": "095",
                "gc": "",
                "cb": "cross_domain",
                "from": "weibo",
                "_rand": str(time.time()),
            },
            timeout=20,
        )
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", f"visitor incarnate failed: {exc}")
    if (_jsonp(incarnate.text).get("retcode") or 0) not in (20000000, 0):
        fail("NEED_AUTH", "Weibo visitor session was rejected")


def status_id(url: str) -> str:
    match = _STATUS_RE.search(url or "")
    if match:
        return match.group(1)
    parsed = urlparse(url)
    query_id = ""
    if "id=" in (parsed.query or ""):
        for part in parsed.query.split("&"):
            if part.startswith("id="):
                query_id = part.split("=", 1)[1]
    return query_id


def fid_of(url: str) -> str:
    match = _FID_RE.search(url or "")
    return match.group(1) if match else ""


def video_from_status(data: dict) -> list[dict]:
    page = data.get("page_info") if isinstance(data.get("page_info"), dict) else {}
    urls = page.get("urls") if isinstance(page.get("urls"), dict) else {}
    media = page.get("media_info") if isinstance(page.get("media_info"), dict) else {}
    found = []
    seen: set[str] = set()
    ordered = []
    for key in ("mp4_1080p_mp4", "mp4_720p_mp4", "mp4_hd_mp4", "mp4_ld_mp4"):
        if key in urls:
            ordered.append(urls[key])
    for value in urls.values():
        ordered.append(value)
    for raw in (media.get("stream_url_hd"), media.get("stream_url")):
        ordered.append(raw)
    for raw in ordered:
        url = _abs(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "role": "muxed", "container": "mp4", "has_watermark": None})
    return found


def images_from_status(data: dict) -> list[dict]:
    found = []
    for item in data.get("pics") or []:
        if not isinstance(item, dict):
            continue
        large = item.get("large") if isinstance(item.get("large"), dict) else {}
        url = _abs(large.get("url") or item.get("url"))
        if url:
            found.append(
                {
                    "url": url,
                    "width": large.get("width") if isinstance(large.get("width"), int) else None,
                    "height": large.get("height") if isinstance(large.get("height"), int) else None,
                }
            )
    return found


def video_from_component(info: dict) -> list[dict]:
    urls = info.get("urls") if isinstance(info.get("urls"), dict) else {}
    preferred = []
    rest = []
    for key, value in urls.items():
        label = str(key)
        if "1080" in label:
            preferred.append(value)
        elif "720" in label:
            preferred.append(value)
        else:
            rest.append(value)
    found = []
    seen: set[str] = set()
    for raw in preferred + rest:
        url = _abs(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        found.append({"url": url, "role": "muxed", "container": "mp4", "has_watermark": None})
    return found


def _session():
    requests = _requests()
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": MOBILE_UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": "https://m.weibo.cn/",
        }
    )
    cookie = __import__("os").environ.get("WEIBO_COOKIES")
    if cookie:
        session.headers["Cookie"] = cookie
    else:
        visitor_login(session)
    return session


def _get(session, url: str, **kwargs):
    requests = _requests()
    try:
        response = session.get(url, timeout=kwargs.pop("timeout", 25), **kwargs)
    except requests.Timeout as exc:
        fail("TIMEOUT", str(exc)[:300])
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", str(exc)[:300])
    if response.status_code == 412:
        fail("GEO_BLOCKED", "HTTP 412")
    if response.status_code == 429:
        fail("RATE_LIMITED", "HTTP 429", retry_after_s=60)
    return response


def _show(session, ident: str) -> dict | None:
    response = _get(session, "https://m.weibo.cn/statuses/show", params={"id": ident})
    try:
        payload = response.json()
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("ok") == 1 and isinstance(payload.get("data"), dict):
        return payload["data"]
    errno = str(payload.get("errno") or "")
    message = str(payload.get("msg") or "")
    if errno == "20101" or "不存在" in message:
        fail("NOT_FOUND", message or "status does not exist")
    if payload.get("ok") == -100 or "signin" in message.lower() or "登录" in message:
        fail("NEED_AUTH", message or "Weibo asks for a login")
    return None


def _component(session, fid: str) -> dict:
    requests = _requests()
    try:
        response = session.post(
            "https://h5.video.weibo.com/api/component",
            params={"page": f"/show/{fid}"},
            data={"data": json.dumps({"Component_Play_Playinfo": {"oid": fid}})},
            headers={"Referer": "https://h5.video.weibo.com/", "User-Agent": MOBILE_UA},
            timeout=25,
        )
    except requests.Timeout as exc:
        fail("TIMEOUT", str(exc)[:300])
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", str(exc)[:300])
    try:
        payload = response.json()
    except ValueError as exc:
        fail("UPSTREAM_CHANGED", f"h5 component was not JSON: {exc}")
    info = ((payload.get("data") or {}) if isinstance(payload, dict) else {}).get("Component_Play_Playinfo")
    if not isinstance(info, dict):
        fail("NOT_FOUND", "h5 component has no playable video")
    return info


def _resolve(session, url: str) -> tuple[dict, list[dict], list[dict]]:
    response = _get(session, url, allow_redirects=True)
    final = str(response.url)
    fid = fid_of(url) or fid_of(final)
    ident = status_id(url) or status_id(final)
    status = _show(session, ident) if ident else None
    if status is not None:
        user = status.get("user") if isinstance(status.get("user"), dict) else {}
        page = status.get("page_info") if isinstance(status.get("page_info"), dict) else {}
        videos = video_from_status(status)
        object_id = str(page.get("object_id") or "")
        if not videos and object_id.startswith("1034:"):
            videos = video_from_component(_component(session, object_id)) or videos
        media = page.get("media_info") if isinstance(page.get("media_info"), dict) else {}
        meta = {
            "platform": "weibo",
            "id": str(status.get("mid") or status.get("id") or ident),
            "title": _plain(status.get("text")) or page.get("page_title"),
            "author": user.get("screen_name"),
            "author_id": str(user.get("id") or ""),
            "page_url": final,
            "duration_ms": _duration_ms(media.get("duration")),
        }
        return meta, videos, images_from_status(status)
    if fid:
        info = _component(session, fid)
        meta = {
            "platform": "weibo",
            "id": str(info.get("mid") or info.get("media_id") or fid),
            "title": info.get("title"),
            "author": info.get("author") or info.get("nickname"),
            "author_id": str(info.get("author_id") or ""),
            "cover": _abs(info.get("cover_image")),
            "page_url": final,
            "duration_ms": _duration_ms(info.get("duration")),
        }
        return meta, video_from_component(info), []
    fail("UNSUPPORTED_URL", "could not find a Weibo status or video id")
    return {}, [], []


def _duration_ms(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value <= 0:
        return None
    # Mobile status reports seconds. Hour-long values are still seconds.
    if value < 6 * 3600:
        return int(value * 1000)
    return int(value)


def _suffix(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ext
    kind = (content_type or "").lower()
    if "png" in kind:
        return ".png"
    if "webp" in kind:
        return ".webp"
    if "gif" in kind:
        return ".gif"
    return ".jpg"


def _save(session, url: str, dest: Path, min_size: int) -> tuple[int, str]:
    requests = _requests()
    try:
        response = session.get(
            url,
            timeout=120,
            stream=True,
            allow_redirects=True,
            headers={"Referer": "https://weibo.com/", "User-Agent": MOBILE_UA},
        )
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", str(exc)[:300])
    if response.status_code in (401, 403):
        fail("NEED_AUTH", f"media HTTP {response.status_code}")
    if response.status_code >= 400:
        fail("UPSTREAM_ERROR", f"media HTTP {response.status_code}")
    size = 0
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if chunk:
                handle.write(chunk)
                size += len(chunk)
    kind = response.headers.get("content-type") or ""
    if size < min_size or "text/html" in kind.lower():
        dest.unlink(missing_ok=True)
        fail("UPSTREAM_ERROR", f"downloaded file is not usable ({size})")
    return size, kind


def main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    url = str((req.get("input") or {}).get("url") or "")
    host = (urlparse(url).hostname or "").lower()
    if not any(token in host for token in ("weibo.com", "weibo.cn", "t.cn")):
        fail("UNSUPPORTED_URL", "not a Weibo URL")
    session = _session()
    meta, videos, images = _resolve(session, url)
    if action == "resolve":
        if not videos:
            fail("UNSUPPORTED_URL", "this post has no video")
        sys.stdout.write(json.dumps({"ok": True, "output": {"meta": meta, "media": videos}}, ensure_ascii=False))
        return
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    out.mkdir(parents=True, exist_ok=True)
    if action == "images":
        if not images:
            fail("UNSUPPORTED_URL", "this post has no images")
        files = []
        for index, item in enumerate(images):
            dest = out / f"{index}.img"
            size, kind = _save(session, item["url"], dest, 100)
            suffix = _suffix(item["url"], kind)
            renamed = dest.with_suffix(suffix)
            dest.replace(renamed)
            files.append(
                {
                    "path": renamed.name,
                    "role": "image",
                    "size_bytes": size,
                    "index": index,
                    "width": item.get("width"),
                    "height": item.get("height"),
                }
            )
        sys.stdout.write(json.dumps({"ok": True, "output": {"meta": meta, "files": files}}, ensure_ascii=False))
        return
    if not videos:
        fail("UNSUPPORTED_URL", "this post has no video")
    dest = out / f"{meta['id']}.mp4"
    size, _kind = _save(session, videos[0]["url"], dest, 2048)
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
                            "height": videos[0].get("height"),
                            "width": videos[0].get("width"),
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
