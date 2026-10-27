#!/usr/bin/env python3
"""Xiaohongshu Skill: parse the public note page. Video notes and image notes."""

from __future__ import annotations

import json
import os
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_HOSTS = ("xiaohongshu.com", "xhslink.com", "xhslink.cn")
_NOTE_RE = re.compile(r"(?:explore|discovery/item|item)/([0-9a-f]{16,32})", re.I)
_TOKEN_RE = re.compile(r"xsec_token(?:=|\\u003d|\"\s*:\s*\")([A-Za-z0-9_\-+/=]{8,})")
_GENERIC_TITLE = re.compile(r"^XiaoHongShu video #", re.I)
_VIDEO_HINT = "this is a video note; use video.download"
_AUTH_HINT = (
    "Xiaohongshu asked this client to log in or pass a security check. "
    "Set XHS_COOKIES to your own browser Cookie header or a Netscape cookies.txt path, "
    "and grant it to example.xiaohongshu-web."
)
_REGION_MARKERS = (
    "当前地区",
    "所在地区",
    "地区限制",
    "区域限制",
    "不在服务范围",
    "not available in your region",
    "not available in your country",
    "geo restriction",
    "geo-restricted",
)
_SHARE_HINT = (
    "Xiaohongshu did not return this note. Public pages now need a fresh share link "
    "that includes xsec_token (copy it in the app; xhslink.com and xhslink.cn redirects keep the token). "
    "The built-in canary has no token and is not verified; the note may also have been deleted."
)


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


def _js_outside(text: str) -> str:
    text = re.sub(r"\bundefined\b", "null", text)
    text = re.sub(r"\bvoid\s+0\b", "null", text)
    text = re.sub(r"\bNaN\b", "null", text)
    text = re.sub(r"-?\bInfinity\b", "null", text)
    text = re.sub(r"new\s+Map\s*\([^)]*\)", "{}", text)
    text = re.sub(r"new\s+Set\s*\([^)]*\)", "[]", text)
    text = re.sub(r"new\s+Date\s*\([^)]*\)", "null", text)
    text = re.sub(r",\s*([}\]])", r"\1", text)
    return text


def _js_to_json(blob: str) -> str:
    """Make a JS object literal close enough to JSON. Literals inside strings stay put."""
    text = blob.replace("\\u002F", "/").replace("\\/", "/")
    parts: list[str] = []
    buf: list[str] = []
    index = 0
    limit = len(text)
    while index < limit:
        if text[index] != '"':
            buf.append(text[index])
            index += 1
            continue
        if buf:
            parts.append(_js_outside("".join(buf)))
            buf = []
        end = index + 1
        while end < limit:
            if text[end] == "\\" and end + 1 < limit:
                end += 2
                continue
            if text[end] == '"':
                end += 1
                break
            end += 1
        parts.append(text[index:end])
        index = end
    if buf:
        parts.append(_js_outside("".join(buf)))
    return "".join(parts)


def _parse_state_blob(blob: str) -> dict | None:
    try:
        value, _end = json.JSONDecoder().raw_decode(_js_to_json(blob))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _state_has_note(state: dict) -> bool:
    note = state.get("note")
    if isinstance(note, dict) and (note.get("noteDetailMap") or note.get("firstNoteId")):
        return True
    data = state.get("noteData")
    return isinstance(data, dict)


def extract_initial_state(html: str) -> dict | None:
    text = html or ""
    best = None
    start = 0
    while True:
        index = text.find("__INITIAL_STATE__", start)
        if index < 0:
            break
        script_end = text.find("</script>", index)
        chunk = text[index:script_end if script_end > index else index + 2_000_000]
        brace = chunk.find("{")
        if brace >= 0:
            value = _parse_state_blob(chunk[brace:])
            if isinstance(value, dict):
                if _state_has_note(value):
                    return value
                best = best or value
        start = index + len("__INITIAL_STATE__")
    return best


def state_outline(value: object, depth: int = 0) -> object:
    """Keys and types only, three levels down. No leaf values."""
    if isinstance(value, dict):
        if depth >= 3:
            return "object"
        return {str(key): state_outline(item, depth + 1) for key, item in list(value.items())[:40]}
    if isinstance(value, list):
        if depth >= 3 or not value:
            return "array"
        return {"array": len(value), "of": state_outline(value[0], depth + 1)}
    return _type_name(value)


def _type_name(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def meta_tag_names(html: str) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for match in re.finditer(r"<meta\b[^>]*>", html or "", re.I):
        tag = match.group(0)
        found = re.search(r"""(?:property|name|itemprop)\s*=\s*["']([^"']+)["']""", tag, re.I)
        if not found:
            continue
        name = found.group(1).strip().lower()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
    return names


def find_note(value: object) -> dict | None:
    if isinstance(value, dict):
        note_id = value.get("noteId") or value.get("note_id")
        if isinstance(note_id, str) and ("imageList" in value or "video" in value or "title" in value or "desc" in value):
            return value
        for child in value.values():
            found = find_note(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = find_note(child)
            if found:
                return found
    return None


def share_token(url: str) -> str:
    values = parse_qs(urlparse(url or "").query).get("xsec_token") or []
    return values[0].strip() if values and isinstance(values[0], str) else ""


def _query_value(url: str, name: str) -> str:
    values = parse_qs(urlparse(url or "").query).get(name) or []
    return values[0].strip() if values and isinstance(values[0], str) else ""


def _nested_redirect(url: str) -> str:
    return _query_value(url, "redirectPath")


def _urls_mark_video(*urls: str) -> bool:
    pending = [url for url in urls if url]
    seen: set[str] = set()
    while pending:
        url = pending.pop()
        if url in seen:
            continue
        seen.add(url)
        if _query_value(url, "type") == "video":
            return True
        nested = _query_value(url, "redirectPath")
        if nested:
            pending.append(nested)
    return False


def _note_is_video(note: dict | None) -> bool:
    if not isinstance(note, dict):
        return False
    if str(note.get("type") or "").lower() == "video":
        return True
    return bool(_videos(note))


def _on_login_page(url: str) -> bool:
    path = (urlparse(url or "").path or "").rstrip("/")
    return path == "/login" or path.endswith("/login")


def _visible_text(html: str) -> str:
    """Drop scripts and styles. Navbar and bundle text must not decide the page."""
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    return text


def _url_wall(url: str) -> str | None:
    """Login or security interstitial by URL path. The words login/captcha in HTML do not count."""
    parsed = urlparse(url or "")
    path = (parsed.path or "").rstrip("/")
    low = path.lower()
    if low == "/login" or low.endswith("/login"):
        return "login_path"
    query = parsed.query or ""
    query_low = query.lower()
    security = "/404/sec_" in low or "/404/sec_" in query or "%2f404%2fsec_" in query_low
    if security and (low == "/404" or low.endswith("/404") or "/404/sec_" in low):
        return "security_path"
    if low.endswith("/captcha") or "/captcha" in low:
        return "captcha_path"
    return None


def _note_id_from_url(url: str) -> str:
    match = _NOTE_RE.search(url or "")
    return match.group(1) if match else ""


def _as_note(entry: object, fallback_id: str) -> dict | None:
    if not isinstance(entry, dict):
        return None
    note = entry.get("note") if isinstance(entry.get("note"), dict) else None
    if note is None and isinstance(entry.get("noteCard"), dict):
        note = entry["noteCard"]
    if note is None and any(key in entry for key in ("noteId", "note_id", "title", "desc", "imageList", "video", "type")):
        note = entry
    if not isinstance(note, dict):
        return None
    if not any(note.get(key) for key in ("noteId", "note_id", "title", "desc", "imageList", "video", "type")):
        return None
    if not (note.get("noteId") or note.get("note_id")) and fallback_id:
        note = dict(note)
        note["noteId"] = fallback_id
    return note


def _map_entries(mapping: dict, note_id: str, first_id: str) -> list[tuple[str, object]]:
    ordered: list[tuple[str, object]] = []
    seen: set[str] = set()

    def add(key: str) -> None:
        if key in mapping and key not in seen:
            seen.add(key)
            ordered.append((key, mapping[key]))

    add(note_id)
    add(first_id)
    if note_id:
        for key in mapping:
            if str(key).lower() == note_id.lower():
                add(str(key))
    for key in mapping:
        add(str(key))
    return ordered


def note_from_state(state: dict | None, url: str) -> dict | None:
    """Read note.noteDetailMap, firstNoteId, the mobile noteData shape, then a walk."""
    if not isinstance(state, dict):
        return None
    note_id = _note_id_from_url(url)
    detail = state.get("note") if isinstance(state.get("note"), dict) else {}
    first_id = ""
    if isinstance(detail, dict):
        raw_first = detail.get("firstNoteId") or detail.get("currentNoteId")
        if isinstance(raw_first, str) and raw_first and raw_first.lower() != "undefined":
            first_id = raw_first
            note_id = note_id or first_id
        mapping = detail.get("noteDetailMap")
        if isinstance(mapping, dict):
            for key, entry in _map_entries(mapping, note_id, first_id):
                found = _as_note(entry, note_id or first_id or key)
                if found is not None:
                    return found
    mobile = state.get("noteData")
    if isinstance(mobile, dict):
        data = mobile.get("data") if isinstance(mobile.get("data"), dict) else mobile
        inner = data.get("noteData") if isinstance(data, dict) and isinstance(data.get("noteData"), dict) else data
        found = _as_note(inner, note_id or first_id)
        if found is not None:
            return found
    found = find_note(state)
    if found is not None and not (found.get("noteId") or found.get("note_id")) and (note_id or first_id):
        found = dict(found)
        found["noteId"] = note_id or first_id
    return found


def _meta_content(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for match in re.finditer(r"<meta\b[^>]*>", html or "", re.I):
        tag = match.group(0)
        key = re.search(r"""(?:property|name|itemprop)\s*=\s*["']([^"']+)["']""", tag, re.I)
        content = re.search(r"""content\s*=\s*["']([^"']*)["']""", tag, re.I)
        if key and content:
            found.setdefault(key.group(1).strip().lower(), unescape(content.group(1)).strip())
    return found


def _clean_title(title: str) -> str:
    text = re.sub(r"\s*[-|｜]\s*小红书\s*$", "", title or "").strip()
    return text


def note_from_meta(html: str, url: str) -> dict | None:
    """og:title / description / author / og:image when the state object has no note."""
    tags = _meta_content(html)
    title = _clean_title(tags.get("og:title") or tags.get("twitter:title") or "")
    desc = tags.get("description") or tags.get("og:description") or ""
    image = tags.get("og:image") or tags.get("twitter:image") or ""
    author = tags.get("author") or tags.get("og:author") or tags.get("article:author") or ""
    note_id = ""
    for candidate in (url, _nested_redirect(url)):
        note_id = _note_id_from_url(candidate)
        if note_id:
            break
    if not note_id and not title and not desc and not image:
        return None
    if not title and not desc and not image and not author:
        return None
    note: dict = {
        "noteId": note_id,
        "title": title,
        "desc": desc,
        "type": "video" if _urls_mark_video(url) else "normal",
        "user": {"nickname": author} if author else {},
        "imageList": [{"urlDefault": image}] if image else [],
    }
    if not note.get("noteId"):
        return None
    return note


def _origin_key_from_html(html: str) -> str:
    match = re.search(r"""originVideoKey["']?\s*[:=]\s*["']([^"']+)["']""", html or "")
    if not match:
        return ""
    key = match.group(1).strip()
    if not key or key.lower() in {"undefined", "null"} or any(ch.isspace() for ch in key):
        return ""
    return key.lstrip("/")


def _sniff_mp4(html: str, note_id: str = "") -> list[str]:
    """Only note streams. A page script can mention an unrelated mp4."""
    found: list[str] = []
    for match in re.finditer(r"""https?:\\?/\\?/[^"'\\\s<>]+?\.mp4[^"'\\\s<>]*""", html or "", re.I):
        url = match.group(0).replace("\\/", "/")
        host = (urlparse(url).hostname or "").lower()
        if "sns-video" not in host and not (note_id and note_id in url):
            continue
        if url not in found:
            found.append(url)
    return found[:4]


def _video_from_html(html: str, note_id: str = "") -> dict | None:
    urls: list[str] = []
    key = _origin_key_from_html(html)
    if key:
        urls.append("https://sns-video-bd.xhscdn.com/" + key)
    for url in _sniff_mp4(html, note_id):
        if url not in urls:
            urls.append(url)
    if not urls:
        return None
    return {"media": {"stream": {"h264": [{"masterUrl": url} for url in urls[:4]]}}}


def _region_visible(html: str) -> bool:
    visible = _visible_text(html).lower()
    return any(marker.lower() in visible for marker in _REGION_MARKERS)


def explain(action: str, requested: str, final: str, html: str, note: dict | None) -> tuple[str | None, str, str]:
    """Return code, message, and the detector name. Navbar login/captcha text is not a detector."""
    if action == "images" and (_urls_mark_video(requested, final) or _note_is_video(note)):
        return "UNSUPPORTED_URL", _VIDEO_HINT, "video_note"
    if note is not None:
        return None, "", "note"
    for url in (final, requested):
        wall = _url_wall(url)
        if wall:
            return "NEED_AUTH", _AUTH_HINT, wall
    if _region_visible(html):
        return "GEO_BLOCKED", "Xiaohongshu said this note is not available in this region", "region"
    if not (share_token(requested) or share_token(final) or share_token(_nested_redirect(requested)) or share_token(_nested_redirect(final))):
        return "UPSTREAM_CHANGED", _SHARE_HINT, "no_note"
    return "UPSTREAM_CHANGED", "note page has no note object", "no_note"


def failure_for(action: str, requested: str, final: str, html: str, note: dict | None) -> tuple[str, str] | None:
    """Decide an error before treating a navbar 登录 as a login wall."""
    code, message, _detector = explain(action, requested, final, html, note)
    if code is None:
        return None
    return code, message


def page_failure(requested_url: str, final_url: str, html: str) -> tuple[str, str] | None:
    """Code and message when the response is not a note page."""
    code, message, detector = explain("download", requested_url, final_url, html, None)
    if detector in {"login_path", "security_path", "captcha_path", "region"}:
        return code, message
    return None


def classify_page(final_url: str, html: str) -> str | None:
    """Return an error code when the page is not a note, else None."""
    failure = page_failure(final_url, final_url, html)
    return failure[0] if failure else None


def _abs(url: object) -> str | None:
    if not isinstance(url, str) or not url:
        return None
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return None


def _images(note: dict) -> list[dict]:
    found = []
    cover = note.get("cover")
    if isinstance(cover, str) and cover:
        found.append({"url": _abs(cover), "width": None, "height": None})
    elif isinstance(cover, dict):
        cover_url = _abs(cover.get("urlDefault") or cover.get("url"))
        if cover_url:
            found.append({"url": cover_url, "width": None, "height": None})
    found = [item for item in found if item.get("url")]
    for item in note.get("imageList") or []:
        if not isinstance(item, dict):
            continue
        url = _abs(item.get("urlDefault") or item.get("url"))
        if url is None:
            for info in item.get("infoList") or []:
                if isinstance(info, dict):
                    url = _abs(info.get("url"))
                    if url:
                        break
        if url:
            found.append(
                {
                    "url": url,
                    "width": item.get("width") if isinstance(item.get("width"), int) else None,
                    "height": item.get("height") if isinstance(item.get("height"), int) else None,
                }
            )
    return found


def _origin_url(note: dict) -> str | None:
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    consumer = video.get("consumer") if isinstance(video.get("consumer"), dict) else {}
    key = consumer.get("originVideoKey")
    if not isinstance(key, str):
        return None
    key = key.strip().lstrip("/")
    if not key or key.lower() in {"undefined", "null"}:
        return None
    return "https://sns-video-bd.xhscdn.com/" + key


def _videos(note: dict) -> list[dict]:
    video = note.get("video") if isinstance(note.get("video"), dict) else {}
    media = video.get("media") if isinstance(video, dict) else None
    stream = media.get("stream") if isinstance(media, dict) else None
    found = []
    seen: set[str] = set()
    if isinstance(stream, dict):
        for codec in ("h264", "h265", "av1"):
            for item in stream.get(codec) or []:
                if not isinstance(item, dict):
                    continue
                url = _abs(item.get("masterUrl") or item.get("master_url") or item.get("url"))
                if not url:
                    for backup in item.get("backupUrls") or []:
                        url = _abs(backup)
                        if url:
                            break
                if not url or url in seen:
                    continue
                seen.add(url)
                found.append(
                    {
                        "url": url,
                        "role": "muxed",
                        "container": "mp4",
                        "height": item.get("height") if isinstance(item.get("height"), int) else None,
                        "width": item.get("width") if isinstance(item.get("width"), int) else None,
                        "has_watermark": None,
                    }
                )
    direct = _abs(video.get("url"))
    if direct and direct not in seen:
        seen.add(direct)
        found.append({"url": direct, "role": "muxed", "container": "mp4", "height": None, "width": None, "has_watermark": None})
    origin = _origin_url(note)
    if origin and origin not in seen:
        found.append({"url": origin, "role": "muxed", "container": "mp4", "height": None, "width": None, "has_watermark": None})
    found.sort(key=lambda item: int(item.get("height") or 0), reverse=True)
    return found


def _author(note: dict) -> tuple[str | None, str | None]:
    user = note.get("user") if isinstance(note.get("user"), dict) else {}
    if not user and isinstance(note.get("userInfo"), dict):
        user = note["userInfo"]
    name = user.get("nickname") or user.get("nickName") or user.get("nick_name") or user.get("name") or note.get("nickname")
    author_id = user.get("userId") or user.get("user_id") or user.get("userid")
    return (name if isinstance(name, str) and name.strip() else None, author_id if isinstance(author_id, str) else None)


def _meta(note: dict, page: str) -> dict:
    note_id = str(note.get("noteId") or note.get("note_id") or "")
    if not note_id:
        fail("UPSTREAM_CHANGED", "note has no id")
    title = note.get("title") if isinstance(note.get("title"), str) else ""
    desc = note.get("desc") if isinstance(note.get("desc"), str) else ""
    if not title.strip() or _GENERIC_TITLE.match(title.strip()):
        title = desc
    author, author_id = _author(note)
    images = _images(note)
    # Canonical note URL. The share query (xsec_token and the rest) stays out of the result.
    return {
        "platform": "xiaohongshu",
        "id": note_id,
        "title": title.strip() or None,
        "description": note.get("desc") if isinstance(note.get("desc"), str) else None,
        "author": author,
        "author_id": author_id,
        "cover": images[0]["url"] if images else None,
        "page_url": f"https://www.xiaohongshu.com/explore/{note_id}",
    }


def _known_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return any(host == item or host.endswith("." + item) for item in _HOSTS)


_COOKIE_DOMAINS = (".xiaohongshu.com", ".xhslink.com", ".xhslink.cn")
_HOPS: list[dict] = []
_DEBUG = False
_DEBUG_PATH: Path | None = None
_OUTLINE_TYPES = {"null", "bool", "number", "string", "array", "object", "other", "empty"}


def _debug_enabled() -> bool:
    return os.environ.get("SKILLROUTER_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def arm_debug(context: dict | None) -> None:
    """Honor the router request even when the child environment dropped the flag."""
    global _DEBUG, _DEBUG_PATH
    ctx = context if isinstance(context, dict) else {}
    _DEBUG = ctx.get("debug") is True or _debug_enabled()
    raw = ctx.get("debug_log")
    _DEBUG_PATH = Path(raw) if isinstance(raw, str) and raw.strip() else None


def _debug_home() -> Path:
    raw = os.environ.get("SKILLROUTER_HOME", "").strip()
    return Path(raw).expanduser() if raw else Path.home() / ".skillrouter"


def _hop(response) -> dict:
    request = getattr(response, "request", None)
    cookie = ""
    if request is not None:
        cookie = request.headers.get("Cookie") or ""
    parsed = urlparse(getattr(response, "url", "") or "")
    return {
        "host": parsed.hostname,
        "path": parsed.path or "/",
        "status": getattr(response, "status_code", None),
        "cookie_sent": bool(cookie),
        "cookie_length": len(cookie),
    }


def _record_hop(response, *_args, **_kwargs) -> None:
    # The response hook sees the final response. history holds each redirect hop.
    for hop in list(getattr(response, "history", None) or []) + [response]:
        _HOPS.append(_hop(hop))


def _scrub_outline(value: object) -> object:
    """Drop anything that is not a key or a type name, so a note title cannot land in the log."""
    if isinstance(value, dict):
        clean: dict[str, object] = {}
        for key, item in list(value.items())[:40]:
            name = str(key)
            if name == "array" and isinstance(item, int):
                clean["array"] = item
                continue
            if name == "of":
                clean["of"] = _scrub_outline(item)
                continue
            if len(name) > 64:
                name = "key"
            clean[name] = _scrub_outline(item)
        return clean
    if isinstance(value, str) and value in _OUTLINE_TYPES:
        return value
    return "other"


def _debug_record(html: str, detector: str | None, event: str = "page") -> dict:
    state = extract_initial_state(html or "")
    hops = list(_HOPS)
    sent = any(item.get("cookie_sent") for item in hops)
    length = max((int(item.get("cookie_length") or 0) for item in hops), default=0)
    tagged = []
    for index, hop in enumerate(hops):
        item = dict(hop)
        if index == len(hops) - 1:
            item["detector"] = detector
        elif int(hop.get("status") or 0) // 100 == 3:
            item["detector"] = "redirect"
        else:
            item["detector"] = None
        tagged.append(item)
    outline = state_outline(state) if isinstance(state, dict) else None
    return {
        "event": event,
        "detector": detector,
        "cookie_sent": sent,
        "cookie_length": length,
        "hops": tagged,
        "state_outline": outline,
        "meta_tags": meta_tag_names(html or ""),
    }


def write_debug(record: dict) -> None:
    """Append one diagnostic line. Paths have no query. No cookie values, no note text."""
    if not (_DEBUG or _debug_enabled()):
        return
    safe = {
        "source": "example.xiaohongshu-web",
        "event": record.get("event") or "page",
        "detector": record.get("detector"),
        "cookie_sent": bool(record.get("cookie_sent")),
        "cookie_length": int(record.get("cookie_length") or 0),
        "hops": [],
        "state_outline": _scrub_outline(record.get("state_outline")) if record.get("state_outline") is not None else None,
        "meta_tags": [],
    }
    target_host = record.get("target_host")
    target_path = record.get("target_path")
    if isinstance(target_host, str) and target_host:
        safe["target_host"] = target_host[:200]
    if isinstance(target_path, str) and target_path:
        safe["target_path"] = target_path.split("?", 1)[0][:300]
    for hop in record.get("hops") or []:
        if not isinstance(hop, dict):
            continue
        path = str(hop.get("path") or "/").split("?", 1)[0]
        safe["hops"].append(
            {
                "host": hop.get("host"),
                "path": path,
                "status": hop.get("status"),
                "detector": hop.get("detector", record.get("detector")),
                "cookie_sent": bool(hop.get("cookie_sent")),
                "cookie_length": int(hop.get("cookie_length") or 0),
            }
        )
    for name in record.get("meta_tags") or []:
        if isinstance(name, str) and re.fullmatch(r"[a-z0-9:._-]{1,80}", name):
            safe["meta_tags"].append(name)
    line = json.dumps(safe, ensure_ascii=False)
    path = _DEBUG_PATH or (_debug_home() / "logs" / "debug.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")
    sys.stderr.write(line + "\n")


def _session(domains: tuple[str, ...] | None = None):
    global _HOPS
    _HOPS = []
    requests = _requests()
    session = requests.Session()
    # Same document-navigation headers yt-dlp sends. A homepage prefetch is not
    # done here: www.xiaohongshu.com/ often 302s to /login and would make the
    # next note request look logged out. Anonymous login/activate needs an
    # X-S signature and returned 406 without one, so it is not a visitor cookie.
    session.headers.update(
        {
            "User-Agent": DESKTOP_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-us,en;q=0.5",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Site": "none",
            "Upgrade-Insecure-Requests": "1",
        }
    )
    # requests deletes a raw Cookie header on every redirect and rebuilds it
    # from the cookie jar only. The xhslink hop would otherwise arrive at
    # /discovery/item/ or /explore/ with no cookie.
    header = cookie_header(os.environ.get("XHS_COOKIES") or "")
    apply_cookies(session, header, domains or _COOKIE_DOMAINS)
    session.hooks["response"] = [_record_hop]
    return session


def cookie_header(raw: str) -> str:
    """Accept a Cookie header string or a Netscape cookies.txt path."""
    text = (raw or "").strip()
    if not text:
        return ""
    if _is_cookie_file(text):
        text = Path(text).read_text(encoding="utf-8-sig", errors="replace").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if "\t" in text:
        pairs = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) >= 7 and parts[5] and parts[6]:
                pairs.append(f"{parts[5]}={parts[6]}")
        if pairs:
            return "; ".join(pairs)
    return text


def _is_cookie_file(text: str) -> bool:
    """A Cookie header is not a path. Windows rejects `;` and very long names."""
    if not text or ";" in text or "\n" in text or len(text) > 240:
        return False
    if text.lower().startswith("cookie:"):
        return False
    try:
        return Path(text).is_file()
    except OSError:
        return False


def apply_cookies(session, header: str, domains: tuple[str, ...] | list[str]) -> int:
    """Put the cookie in the jar for each share host. Returns the header length."""
    text = (header or "").strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        return 0
    for part in text.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        for domain in domains:
            session.cookies.set(name, value, domain=domain, path="/")
    return len(text)


def _get(session, url: str):
    requests = _requests()
    try:
        response = session.get(url, timeout=25, allow_redirects=True)
    except requests.Timeout:
        write_debug(_debug_record("", "timeout", event="transport"))
        fail("TIMEOUT", "note page timed out")
    except requests.RequestException:
        write_debug(_debug_record("", "request_error", event="transport"))
        fail("UPSTREAM_ERROR", "note page request failed")
    return response


def _with_token(url: str, html: str) -> str | None:
    pages = alternate_pages(url, url, html)
    return pages[0] if pages else None


def _page_key(url: str) -> str:
    parsed = urlparse(url or "")
    return ((parsed.hostname or "").lower() + (parsed.path or "").rstrip("/")).lower()


def alternate_pages(requested: str, final: str, html: str) -> list[str]:
    """Explore and discovery/item forms that carry the same xsec_token."""
    pool = [requested, final, _nested_redirect(requested), _nested_redirect(final)]
    note_id = ""
    token = ""
    source = "pc_share"
    for url in pool:
        if not url:
            continue
        match = _NOTE_RE.search(url)
        if match and not note_id:
            note_id = match.group(1)
        if not token:
            token = share_token(url)
        found = _query_value(url, "xsec_source")
        if found:
            source = found
    if not token:
        match = _TOKEN_RE.search(html or "")
        if match:
            token = match.group(1)
    if not note_id:
        match = _NOTE_RE.search(html or "")
        if match:
            note_id = match.group(1)
    if not note_id or not token:
        return []
    pages = []
    for kind in ("explore", "discovery/item"):
        pages.append(f"https://www.xiaohongshu.com/{kind}/{note_id}?xsec_token={token}&xsec_source={source}")
    return pages


def _enrich_note(note: dict | None, html: str, *urls: str) -> dict | None:
    """Fill a thin state object from meta tags, then attach a stream when this is a video note."""
    meta = None
    for url in urls:
        meta = note_from_meta(html, url)
        if meta is not None:
            break
    if note is None:
        note = meta
    elif meta is not None:
        note = dict(note)
        if not str(note.get("title") or "").strip():
            note["title"] = meta.get("title") or ""
        if not str(note.get("desc") or "").strip():
            note["desc"] = meta.get("desc") or ""
        if _author(note)[0] is None and _author(meta)[0]:
            user = dict(note["user"]) if isinstance(note.get("user"), dict) else {}
            user["nickname"] = _author(meta)[0]
            note["user"] = user
        if not _images(note) and meta.get("imageList"):
            note["imageList"] = meta["imageList"]
        if not (note.get("noteId") or note.get("note_id")) and meta.get("noteId"):
            note["noteId"] = meta["noteId"]
    if not isinstance(note, dict):
        return None
    if _videos(note):
        return note
    wants_video = str(note.get("type") or "").lower() == "video" or _urls_mark_video(*urls) or bool(_origin_key_from_html(html))
    if not wants_video:
        return note
    note_id = str(note.get("noteId") or note.get("note_id") or "")
    sniffed = _video_from_html(html, note_id)
    if not sniffed:
        return note
    note = dict(note)
    note["video"] = sniffed
    note["type"] = "video"
    return note


def _read_note(html: str, *urls: str) -> dict | None:
    state = extract_initial_state(html)
    note = None
    if isinstance(state, dict):
        candidates = list(urls) or [""]
        for url in candidates:
            note = note_from_state(state, url)
            if note is not None:
                break
    return _enrich_note(note, html, *urls)


def _finish(action: str, requested: str, final: str, html: str, note: dict | None, status: int) -> tuple[dict, str]:
    code, message, detector = explain(action, requested, final, html, note)
    write_debug(_debug_record(html, detector))
    if note is not None and code is None:
        return note, final
    if code in {"NEED_AUTH", "GEO_BLOCKED", "UNSUPPORTED_URL"}:
        fail(code, message, retry_after_s=60 if code == "RATE_LIMITED" else None)
    if status == 404:
        fail("NOT_FOUND", "HTTP 404")
    if status >= 400:
        fail("UPSTREAM_ERROR", f"HTTP {status}")
    if code:
        fail(code, message)
    fail("UPSTREAM_CHANGED", "note page has no note object")
    return {}, final


def _load_note(session, url: str, action: str) -> tuple[dict, str]:
    global _HOPS
    _HOPS = []
    seen: set[str] = set()
    queue = [url]
    expanded = False
    last_html = ""
    last_final = url
    last_status = 0
    last_note = None
    while queue and len(seen) < 4:
        target = queue.pop(0)
        key = _page_key(target)
        if not target or key in seen:
            continue
        seen.add(key)
        response = _get(session, target)
        html = response.text or ""
        final = str(response.url)
        seen.add(_page_key(final))
        note = _read_note(html, final, target, url)
        last_html, last_final, last_status, last_note = html, final, response.status_code, note
        if note is not None:
            break
        if not expanded:
            expanded = True
            for candidate in alternate_pages(url, final, html):
                if _page_key(candidate) not in seen:
                    queue.append(candidate)
    return _finish(action, url, last_final, last_html, last_note, last_status)


def _suffix(url: str, content_type: str) -> str:
    path = urlparse(url).path.lower()
    for ext in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        if path.endswith(ext):
            return ext
    kind = content_type.lower()
    if "png" in kind:
        return ".png"
    if "webp" in kind:
        return ".webp"
    if "gif" in kind:
        return ".gif"
    return ".jpg"


def _save(session, url: str, dest: Path, min_size: int) -> int:
    requests = _requests()
    try:
        response = session.get(url, timeout=120, stream=True, allow_redirects=True)
    except requests.RequestException as exc:
        fail("UPSTREAM_ERROR", str(exc)[:300])
    if response.status_code == 403:
        fail("NEED_AUTH", "media URL returned 403")
    if response.status_code >= 400:
        fail("UPSTREAM_ERROR", f"media HTTP {response.status_code}")
    size = 0
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if chunk:
                handle.write(chunk)
                size += len(chunk)
    kind = (response.headers.get("content-type") or "").lower()
    if size < min_size or "text/html" in kind:
        dest.unlink(missing_ok=True)
        fail("UPSTREAM_ERROR", f"downloaded file is not usable ({size}, {kind})")
    return size


def main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    context = req.get("context") if isinstance(req.get("context"), dict) else {}
    arm_debug(context)
    url = str((req.get("input") or {}).get("url") or "")
    parsed = urlparse(url)
    header = cookie_header(os.environ.get("XHS_COOKIES") or "")
    write_debug(
        {
            "event": "start",
            "detector": None,
            "cookie_sent": bool(header),
            "cookie_length": len(header),
            "hops": [],
            "target_host": parsed.hostname,
            "target_path": parsed.path or "/",
            "state_outline": None,
            "meta_tags": [],
        }
    )
    if not _known_host(url):
        fail("UNSUPPORTED_URL", "not a Xiaohongshu URL")
    session = _session()
    note, page = _load_note(session, url, action)
    meta = _meta(note, page)
    videos = _videos(note)
    images = _images(note)
    note_type = str(note.get("type") or "")
    if action == "resolve":
        if not videos:
            fail("UNSUPPORTED_URL", "this note has no video")
        sys.stdout.write(json.dumps({"ok": True, "output": {"meta": meta, "media": videos}}, ensure_ascii=False))
        return
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    out.mkdir(parents=True, exist_ok=True)
    if action == "images":
        if _note_is_video(note) or note_type == "video":
            fail("UNSUPPORTED_URL", _VIDEO_HINT)
        if not images:
            fail("UNSUPPORTED_URL", "this note has no images")
        files = []
        for index, item in enumerate(images):
            suffix = _suffix(item["url"], "")
            dest = out / f"{index}{suffix}"
            size = _save(session, item["url"], dest, 100)
            # suffix may change after content-type; rename if the saved header differs
            files.append(
                {
                    "path": dest.name,
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
        fail("UNSUPPORTED_URL", "this note has no video")
    dest = out / f"{meta['id']}.mp4"
    size = _save(session, videos[0]["url"], dest, 2048)
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
