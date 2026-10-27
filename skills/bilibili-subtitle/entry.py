#!/usr/bin/env python3
"""Bilibili subtitle Skill: CC and AI tracks from the public player API."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from urllib.parse import quote, urlencode

_MIXIN_KEY_ENC_TAB = [
    46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
    33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
    61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
    36, 20, 34, 44, 52,
]
_BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
_AV_RE = re.compile(r"av(\d+)", re.I)
_STRIP = set("!'()*")
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_LANG_RANK = {"zh-CN": 0, "zh-Hans": 1, "zh": 2, "ai-zh": 3}


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


def mixin_key(raw: str) -> str:
    return "".join(raw[index] for index in _MIXIN_KEY_ENC_TAB)[:32]


def encode_query(params: dict) -> str:
    items = sorted((key, "".join(ch for ch in str(value) if ch not in _STRIP)) for key, value in params.items())

    def quote_via(text: str, safe: str, encoding: str | None, errors: str | None) -> str:
        return quote(text, safe="", encoding=encoding or "utf-8", errors=errors or "strict")

    return urlencode(items, quote_via=quote_via)


def sign_wbi(params: dict, img_key: str, sub_key: str) -> str:
    signed = dict(params)
    signed["wts"] = int(time.time())
    query = encode_query(signed)
    digest = hashlib.md5((query + mixin_key(img_key + sub_key)).encode()).hexdigest()
    return f"{query}&w_rid={digest}"


def _requests():
    try:
        import requests
    except ImportError as exc:
        fail("DEPENDENCY_MISSING", f"需要安装 requests: {exc}", 3)
    return requests


def _session():
    requests = _requests()
    session = requests.Session()
    session.headers.update({"User-Agent": DESKTOP_UA, "Referer": "https://www.bilibili.com/"})
    cookie = os.environ.get("BILIBILI_COOKIES")
    if cookie:
        session.headers["Cookie"] = cookie
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
        fail("GEO_BLOCKED", "HTTP 412; this client IP is often blocked from datacenter networks")
    if response.status_code == 429:
        fail("RATE_LIMITED", "HTTP 429")
    if response.status_code >= 400:
        fail("UPSTREAM_ERROR", f"HTTP {response.status_code}")
    return response


def _video_key(text: str) -> tuple[str, str]:
    match = _BV_RE.search(text or "")
    if match:
        return "bvid", match.group(0)
    match = _AV_RE.search(text or "")
    if match:
        return "aid", match.group(1)
    return "", ""


def _view(session, url: str) -> dict:
    kind, value = _video_key(url)
    if not kind:
        response = _get(session, url, allow_redirects=True)
        kind, value = _video_key(str(response.url))
    if not kind:
        fail("UNSUPPORTED_URL", "could not find a BV or av id")
    response = _get(session, "https://api.bilibili.com/x/web-interface/view", params={kind: value})
    try:
        payload = response.json()
    except ValueError as exc:
        fail("UPSTREAM_CHANGED", f"view API was not JSON: {exc}")
    code = payload.get("code") if isinstance(payload, dict) else None
    if code not in (0, None):
        message = str((payload or {}).get("message") or code)
        if code in (-404, 62002) or any(token in message for token in ("不存在", "删除", "稿件不可见")):
            fail("NOT_FOUND", message)
        fail("UPSTREAM_ERROR", message)
    data = (payload or {}).get("data") or {}
    if not isinstance(data, dict) or not data.get("bvid") or not data.get("cid"):
        fail("UPSTREAM_CHANGED", "view API returned no bvid/cid")
    return data


def _tracks(data: dict) -> list[dict]:
    subtitle = data.get("subtitle") if isinstance(data.get("subtitle"), dict) else {}
    tracks = subtitle.get("subtitles") if isinstance(subtitle.get("subtitles"), list) else []
    return [
        item
        for item in tracks
        if isinstance(item, dict) and (item.get("subtitle_url") or item.get("subtitleUrl"))
    ]


def _player_wbi(session, bvid: str, cid: int) -> dict | None:
    nav = _get(session, "https://api.bilibili.com/x/web-interface/nav")
    try:
        nav_json = nav.json()
    except ValueError as exc:
        fail("UPSTREAM_CHANGED", f"nav was not JSON: {exc}")
    wbi = ((nav_json.get("data") or {}) if isinstance(nav_json, dict) else {}).get("wbi_img") or {}
    img = str(wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".", 1)[0]
    sub = str(wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".", 1)[0]
    if len(img) < 32 or len(sub) < 32:
        return None
    query = sign_wbi({"bvid": bvid, "cid": cid}, img, sub)
    signed = _get(session, "https://api.bilibili.com/x/player/wbi/v2?" + query)
    try:
        signed_json = signed.json()
    except ValueError as exc:
        fail("UPSTREAM_CHANGED", f"wbi player was not JSON: {exc}")
    data = (signed_json or {}).get("data") if isinstance(signed_json, dict) else None
    return data if isinstance(data, dict) else None


def _player_unsigned(session, bvid: str, cid: int) -> dict | None:
    response = _get(session, "https://api.bilibili.com/x/player/v2", params={"bvid": bvid, "cid": cid})
    try:
        payload = response.json()
    except ValueError:
        return None
    data = payload.get("data") if isinstance(payload, dict) else None
    if isinstance(payload, dict) and payload.get("code") in (0, None) and isinstance(data, dict):
        return data
    return None


def _player(session, bvid: str, cid: int) -> dict:
    # Unsigned /x/player/v2 can return HTTP 200 with an empty or unrelated track list.
    # The signed player API is the one that lists this video's CC and AI subtitles.
    signed = _player_wbi(session, bvid, cid)
    if isinstance(signed, dict) and _tracks(signed):
        return signed
    unsigned = _player_unsigned(session, bvid, cid)
    if isinstance(unsigned, dict) and _tracks(unsigned):
        return unsigned
    if isinstance(signed, dict):
        return signed
    if isinstance(unsigned, dict):
        return unsigned
    fail("UPSTREAM_CHANGED", "player API returned no subtitle list")
    return {}


def _pick(tracks: list[dict], hint: str | None) -> dict | None:
    ranked = []
    for item in tracks:
        if not isinstance(item, dict):
            continue
        url = item.get("subtitle_url") or item.get("subtitleUrl")
        if not isinstance(url, str) or not url:
            continue
        lan = str(item.get("lan") or "")
        if hint and hint.lower() in lan.lower():
            rank = -1
        else:
            rank = _LANG_RANK.get(lan, 50)
        ranked.append((rank, item))
    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1]


def _segments(payload: dict) -> list[dict]:
    body = payload.get("body") if isinstance(payload, dict) else None
    if not isinstance(body, list):
        return []
    segments = []
    for item in body:
        if not isinstance(item, dict):
            continue
        text = str(item.get("content") or "").strip()
        start = item.get("from")
        end = item.get("to")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)):
            continue
        segments.append({"start_ms": int(float(start) * 1000), "end_ms": int(float(end) * 1000), "text": text})
    return segments


def main() -> None:
    _utf8()
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    data = req.get("input") or {}
    url = str(data.get("url") or "")
    if not url:
        fail("UNSUPPORTED_URL", "platform subtitles need a video URL")
    session = _session()
    view = _view(session, url)
    player = _player(session, str(view["bvid"]), int(view["cid"]))
    subtitle = player.get("subtitle") if isinstance(player.get("subtitle"), dict) else {}
    tracks = subtitle.get("subtitles") if isinstance(subtitle.get("subtitles"), list) else []
    chosen = _pick(tracks, str(data.get("language") or "") or None)
    if chosen is None:
        fail("UNSUPPORTED_URL", "this video has no CC or AI subtitle track")
    raw_url = str(chosen.get("subtitle_url") or "")
    if raw_url.startswith("//"):
        raw_url = "https:" + raw_url
    track = _get(session, raw_url)
    try:
        body = track.json()
    except ValueError as exc:
        fail("UPSTREAM_CHANGED", f"subtitle file was not JSON: {exc}")
    segments = _segments(body if isinstance(body, dict) else {})
    if not segments:
        fail("UPSTREAM_CHANGED", "subtitle file has no cues")
    text = "\n".join(item["text"] for item in segments if item["text"])
    lan = str(chosen.get("lan") or data.get("language") or "zh")
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "output": {
                    "language": lan,
                    "source": "platform_subtitle",
                    "text": text,
                    "segments": segments,
                    "meta": {
                        "platform": "bilibili",
                        "id": str(view.get("bvid") or ""),
                        "title": view.get("title"),
                        "author": (view.get("owner") or {}).get("name") if isinstance(view.get("owner"), dict) else None,
                    },
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
