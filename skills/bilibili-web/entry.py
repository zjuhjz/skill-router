#!/usr/bin/env python3
"""Bilibili web API Skill: view + playurl, WBI when the unsigned API is empty."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
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
_QN = {16: "360P", 32: "480P", 64: "720P", 80: "1080P", 112: "1080P+", 116: "1080P60", 120: "4K"}
DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class SkillFail(Exception):
    def __init__(self, code: str, message: str, retry_after_s: float | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after_s = retry_after_s


def mixin_key(raw: str) -> str:
    if len(raw) < 64:
        raise SkillFail("UPSTREAM_CHANGED", "WBI key is shorter than 64 characters")
    return "".join(raw[index] for index in _MIXIN_KEY_ENC_TAB)[:32]


def encode_query(params: dict) -> str:
    items = sorted((key, "".join(ch for ch in str(value) if ch not in _STRIP)) for key, value in params.items())

    def quote_via(text: str, safe: str, encoding: str | None, errors: str | None) -> str:
        return quote(text, safe="", encoding=encoding or "utf-8", errors=errors or "strict")

    return urlencode(items, quote_via=quote_via)


def sign_wbi(params: dict, img_key: str, sub_key: str, *, wts: int | None = None) -> str:
    signed = dict(params)
    signed["wts"] = int(time.time()) if wts is None else wts
    query = encode_query(signed)
    digest = hashlib.md5((query + mixin_key(img_key + sub_key)).encode()).hexdigest()
    return f"{query}&w_rid={digest}"


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
            "User-Agent": DESKTOP_UA,
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com/",
        }
    )
    cookie = os.environ.get("BILIBILI_COOKIES")
    if cookie:
        session.headers["Cookie"] = cookie
    return session


def _get(session, url: str, **kwargs):
    requests = _requests()
    try:
        response = session.get(url, timeout=kwargs.pop("timeout", 30), **kwargs)
    except requests.Timeout as exc:
        raise SkillFail("TIMEOUT", str(exc)[:300]) from exc
    except requests.RequestException as exc:
        raise SkillFail("UPSTREAM_ERROR", str(exc)[:300]) from exc
    if response.status_code == 412:
        raise SkillFail("GEO_BLOCKED", "HTTP 412; this client IP is often blocked from datacenter networks")
    if response.status_code == 429:
        raise SkillFail("RATE_LIMITED", "HTTP 429", retry_after_s=60)
    if response.status_code == 404:
        raise SkillFail("NOT_FOUND", "HTTP 404")
    if response.status_code >= 400:
        raise SkillFail("UPSTREAM_ERROR", f"HTTP {response.status_code}")
    return response


def _video_key(text: str) -> tuple[str, str]:
    match = _BV_RE.search(text or "")
    if match:
        return "bvid", match.group(0)
    match = _AV_RE.search(text or "")
    if match:
        return "aid", match.group(1)
    return "", ""


def _identify(session, url: str) -> tuple[str, str]:
    kind, value = _video_key(url)
    if kind:
        return kind, value
    response = _get(session, url, allow_redirects=True)
    kind, value = _video_key(str(response.url) + "\n" + response.text[:8000])
    if not kind:
        raise SkillFail("UNSUPPORTED_URL", "could not find a BV or av id")
    return kind, value


def _as_int(value: object) -> int | None:
    try:
        if value is None or value is False or value == "":
            return None
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _view(session, kind: str, value: str) -> dict:
    if kind == "bvid":
        try:
            _get(session, f"https://www.bilibili.com/video/{value}/", allow_redirects=True)
        except SkillFail:
            pass
    response = _get(session, "https://api.bilibili.com/x/web-interface/view", params={kind: value})
    try:
        payload = response.json()
    except ValueError as exc:
        raise SkillFail("UPSTREAM_CHANGED", "view API was not JSON") from exc
    if not isinstance(payload, dict):
        raise SkillFail("UPSTREAM_CHANGED", "view API was not an object")
    code = payload.get("code")
    if code not in (0, None):
        message = str(payload.get("message") or code)
        if code in (-404, 62002) or any(token in message for token in ("不存在", "删除", "稿件不可见")):
            raise SkillFail("NOT_FOUND", message)
        raise SkillFail("UPSTREAM_ERROR", f"view API failed: {message}")
    data = payload.get("data") or {}
    if not isinstance(data, dict) or not data.get("bvid"):
        raise SkillFail("UPSTREAM_CHANGED", "view API returned no bvid")
    return data


def _cid(data: dict, url: str) -> int:
    page_no = 1
    match = re.search(r"[?&]p=(\d+)", url)
    if match:
        page_no = max(1, int(match.group(1)))
    pages = data.get("pages") or []
    if isinstance(pages, list) and pages:
        chosen = pages[min(page_no, len(pages)) - 1]
        cid = _as_int(chosen.get("cid") if isinstance(chosen, dict) else None)
        if cid:
            return cid
    cid = _as_int(data.get("cid"))
    if not cid:
        raise SkillFail("UPSTREAM_CHANGED", "view API returned no cid")
    return cid


def _play_ok(payload: dict) -> bool:
    if not payload or payload.get("code") not in (0, None):
        return False
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return False
    if data.get("durl"):
        return True
    dash = data.get("dash") or {}
    return bool(isinstance(dash, dict) and dash.get("video"))


def _json_or_empty(response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _play(session, bvid: str, cid: int) -> dict:
    try:
        direct = _json_or_empty(
            _get(
                session,
                "https://api.bilibili.com/x/player/playurl",
                params={
                    "bvid": bvid,
                    "cid": cid,
                    "qn": 80,
                    "fnval": 1,
                    "fnver": 0,
                    "fourk": 0,
                    "platform": "html5",
                    "otype": "json",
                },
            )
        )
    except SkillFail:
        direct = {}
    if _play_ok(direct):
        return direct
    nav = _json_or_empty(_get(session, "https://api.bilibili.com/x/web-interface/nav"))
    wbi = ((nav.get("data") or {}) if isinstance(nav, dict) else {}).get("wbi_img") or {}
    img = str(wbi.get("img_url") or "").rsplit("/", 1)[-1].split(".", 1)[0]
    sub = str(wbi.get("sub_url") or "").rsplit("/", 1)[-1].split(".", 1)[0]
    if not img or not sub:
        raise SkillFail("UPSTREAM_CHANGED", "nav response missing WBI keys")
    last: dict = {}
    for fnval in (1, 16):
        query = sign_wbi(
            {"bvid": bvid, "cid": cid, "qn": 80, "fnval": fnval, "fnver": 0, "fourk": 0, "try_look": 1},
            img,
            sub,
        )
        try:
            signed = _json_or_empty(_get(session, "https://api.bilibili.com/x/player/wbi/playurl?" + query))
        except SkillFail:
            signed = {}
        if _play_ok(signed):
            return signed
        last = signed
    message = str((last or {}).get("message") or "no playable stream")
    raise SkillFail("UPSTREAM_ERROR", f"playurl failed: {message}")


def _media(payload: dict) -> list[dict]:
    data = payload.get("data") or {}
    if not isinstance(data, dict):
        return []
    quality = _QN.get(_as_int(data.get("quality")) or -1)
    durl = data.get("durl") or []
    if isinstance(durl, list) and durl:
        urls = [piece["url"] for piece in durl if isinstance(piece, dict) and isinstance(piece.get("url"), str)]
        if urls:
            return [
                {
                    "url": urls[0],
                    "extra_urls": urls[1:],
                    "role": "muxed",
                    "container": "mp4",
                    "quality": quality,
                    "has_watermark": False,
                }
            ]
    media: list[dict] = []
    dash = data.get("dash") or {}
    if isinstance(dash, dict):
        for track in dash.get("video") or []:
            if not isinstance(track, dict):
                continue
            url = track.get("baseUrl") or track.get("base_url")
            if isinstance(url, str):
                height = _as_int(track.get("height"))
                media.append(
                    {
                        "url": url,
                        "role": "video",
                        "container": "mp4",
                        "height": height,
                        "width": _as_int(track.get("width")),
                        "has_watermark": False,
                    }
                )
        for track in dash.get("audio") or []:
            if isinstance(track, dict) and isinstance(track.get("baseUrl") or track.get("base_url"), str):
                media.append(
                    {
                        "url": track.get("baseUrl") or track.get("base_url"),
                        "role": "audio",
                        "container": "m4a",
                        "has_watermark": False,
                    }
                )
    return media


def _stream(session, url: str, dest: Path, referer: str) -> int:
    response = _get(
        session,
        url,
        headers={"Referer": referer, "User-Agent": DESKTOP_UA},
        stream=True,
        allow_redirects=True,
        timeout=120,
    )
    size = 0
    with dest.open("wb") as handle:
        for chunk in response.iter_content(chunk_size=256 * 1024):
            if chunk:
                handle.write(chunk)
                size += len(chunk)
    if size < 1000:
        dest.unlink(missing_ok=True)
        raise SkillFail("UPSTREAM_ERROR", f"downloaded file too small ({size})")
    return size


def _merge(video: Path, audio: Path, dest: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SkillFail("DEPENDENCY_MISSING", "DASH streams need ffmpeg on PATH")
    completed = subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-i", str(video), "-i", str(audio), "-c", "copy", "-movflags", "+faststart", str(dest)],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0 or not dest.is_file():
        raise SkillFail("UPSTREAM_ERROR", f"ffmpeg failed: {(completed.stderr or '')[-300:]}")


def _build_meta(data: dict) -> dict:
    owner = data.get("owner") or {}
    stat = data.get("stat") or {}
    duration = _as_int(data.get("duration"))
    bvid = str(data.get("bvid"))
    return {
        "platform": "bilibili",
        "id": bvid,
        "title": data.get("title"),
        "author": owner.get("name"),
        "author_id": str(owner.get("mid")) if owner.get("mid") is not None else None,
        "duration_ms": duration * 1000 if duration else None,
        "cover": data.get("pic"),
        "page_url": f"https://www.bilibili.com/video/{bvid}/",
        "stats": {
            "plays": _as_int(stat.get("view")),
            "likes": _as_int(stat.get("like")),
            "comments": _as_int(stat.get("reply")),
        },
    }


def main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    url = str((req.get("input") or {}).get("url") or "")
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    out.mkdir(parents=True, exist_ok=True)
    session = _session()
    try:
        kind, value = _identify(session, url)
        data = _view(session, kind, value)
        meta = _build_meta(data)
        cid = _cid(data, url)
        play = _play(session, meta["id"], cid)
        media = _media(play)
        if not media:
            raise SkillFail("UPSTREAM_ERROR", "playurl returned no media URL")
        if action == "resolve":
            sys.stdout.write(json.dumps({"ok": True, "output": {"meta": meta, "media": media}}, ensure_ascii=False))
            return
        referer = meta["page_url"]
        muxed = [item for item in media if item.get("role") == "muxed"]
        videos = [item for item in media if item.get("role") == "video"]
        audios = [item for item in media if item.get("role") == "audio"]
        dest = out / f"{meta['id']}.mp4"
        if muxed or not (videos and audios):
            size = _stream(session, muxed[0]["url"] if muxed else media[0]["url"], dest, referer)
        else:
            vpath = out / f"{meta['id']}.video.part"
            apath = out / f"{meta['id']}.audio.part"
            try:
                _stream(session, videos[0]["url"], vpath, referer)
                _stream(session, audios[0]["url"], apath, referer)
                _merge(vpath, apath, dest)
            finally:
                vpath.unlink(missing_ok=True)
                apath.unlink(missing_ok=True)
            size = dest.stat().st_size if dest.is_file() else 0
        if not dest.is_file():
            raise SkillFail("UPSTREAM_ERROR", "download produced no file")
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
                                "has_watermark": False,
                            }
                        ],
                    },
                },
                ensure_ascii=False,
            )
        )
    except SkillFail as exc:
        fail(exc.code, str(exc), exit_code=3 if exc.code == "DEPENDENCY_MISSING" else 2, retry_after_s=exc.retry_after_s)


if __name__ == "__main__":
    main()
