#!/usr/bin/env python3
"""Headless Douyin (抖音) downloader for share / short URLs.

Preferred pipeline (no third party, no yt-dlp):
  1. Follow v.douyin.com redirects → aweme / video id
  2. Warm-up GET on iesdouyin share page → ttwid cookie
  3. GET share page with ?is_v_html=true (iPhone Safari UA)
  4. Parse window._ROUTER_DATA → videoInfoRes.item_list[].video.play_addr
  5. Download via iesdouyin aweme/v1/play (playwm → play / video_id URI)

Fallback: videofetch (KuKuTool wrapper) if installed.
Last resort: yt-dlp (only if --allow-ytdlp).

Usage:
  python3 /workspace/tools/douyin_dl.py 'https://v.douyin.com/xxxx/'
  python3 /workspace/tools/douyin_dl.py URL -o /path/to/dir
  python3 /workspace/tools/douyin_dl.py URL --json   # print metadata + path
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs, unquote

import requests

IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 "
    "Mobile/15E148 Safari/604.1"
)
DEFAULT_OUT_DIR = Path("/workspace/douyin-ref")


class DouyinDownloadError(RuntimeError):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": IPHONE_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        }
    )
    return s


def resolve_aweme_id(url: str, session: requests.Session) -> tuple[str, str]:
    """Return (aweme_id, final_url) by following redirects / parsing path."""
    url = url.strip()
    # Direct video id in URL
    m = re.search(r"(?:video|note)/(\d{15,25})", url)
    if m and "v.douyin.com" not in url:
        return m.group(1), url

    r = session.get(url, allow_redirects=True, timeout=30)
    final = r.url
    m = re.search(r"(?:video|note)/(\d{15,25})", final)
    if m:
        return m.group(1), final

    # Sometimes location-only without following fully
    for h in r.history:
        m = re.search(r"(?:video|note)/(\d{15,25})", h.headers.get("Location", ""))
        if m:
            return m.group(1), h.headers["Location"]

    # Modal / share text may embed the short link only — try modal extract
    m = re.search(r"/share/video/(\d{15,25})", final + r.text[:5000])
    if m:
        return m.group(1), final

    raise DouyinDownloadError(f"Could not resolve aweme id from {url!r} (final={final!r})")


def _extract_router_data(html: str) -> dict[str, Any]:
    m = re.search(r"window\._ROUTER_DATA\s*=\s*(\{.*?)\s*</script>", html, re.S)
    if not m:
        raise DouyinDownloadError("window._ROUTER_DATA not found in share HTML")
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        raise DouyinDownloadError(f"_ROUTER_DATA JSON parse failed: {e}") from e


def _item_from_router(data: dict[str, Any]) -> dict[str, Any]:
    loader = data.get("loaderData") or {}
    page = loader.get("video_(id)/page") or loader.get("video_(id)\\/page")
    if not isinstance(page, dict):
        # fuzzy: any value with videoInfoRes
        for v in loader.values():
            if isinstance(v, dict) and "videoInfoRes" in v:
                page = v
                break
    if not isinstance(page, dict):
        raise DouyinDownloadError("No video_(id)/page in _ROUTER_DATA")

    info = page.get("videoInfoRes") or {}
    items = info.get("item_list") or info.get("filter_list") or []
    if not items:
        raise DouyinDownloadError(
            "videoInfoRes.item_list empty — cookie/is_v_html may have failed"
        )
    return items[0]


def _play_candidates(item: dict[str, Any]) -> list[str]:
    video = item.get("video") or {}
    play = video.get("play_addr") or {}
    uri = play.get("uri") or ""
    urls: list[str] = []

    # Preferred: no-watermark play endpoint by URI
    if uri and re.match(r"^v[0-9a-zA-Z]+", uri):
        urls.append(
            f"https://www.iesdouyin.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0"
        )
        urls.append(
            f"https://aweme.snssdk.com/aweme/v1/play/?video_id={uri}&ratio=720p&line=0"
        )

    for u in play.get("url_list") or []:
        if not u:
            continue
        u2 = u.replace("playwm", "play")
        urls.append(u2)
        if u2 != u:
            urls.append(u)

    # bit_rate variants
    for br in video.get("bit_rate") or []:
        for u in (br.get("play_addr") or {}).get("url_list") or []:
            urls.append(u.replace("playwm", "play"))

    # de-dupe preserve order
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_iesdouyin(aweme_id: str, session: requests.Session) -> dict[str, Any]:
    """Warm cookie + fetch is_v_html share page; return metadata + play URLs."""
    share = f"https://www.iesdouyin.com/share/video/{aweme_id}/"
    # Warm-up for ttwid
    session.get(share, timeout=30)
    r = session.get(share, params={"is_v_html": "true"}, timeout=30)
    r.raise_for_status()
    data = _extract_router_data(r.text)
    item = _item_from_router(data)
    candidates = _play_candidates(item)
    if not candidates:
        raise DouyinDownloadError("No play URL candidates in item")

    author = (item.get("author") or {}).get("nickname") or ""
    desc = item.get("desc") or ""
    duration_ms = (item.get("video") or {}).get("duration") or 0
    return {
        "aweme_id": str(item.get("aweme_id") or aweme_id),
        "desc": desc,
        "author": author,
        "duration_ms": duration_ms,
        "play_urls": candidates,
        "method": "iesdouyin",
    }


def download_file(
    url: str,
    dest: Path,
    session: requests.Session,
    referer: str = "https://www.douyin.com/",
) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    headers = {"Referer": referer, "User-Agent": IPHONE_UA}
    with session.get(url, headers=headers, stream=True, timeout=120, allow_redirects=True) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "text/html" in ctype and int(r.headers.get("Content-Length") or 0) < 50_000:
            raise DouyinDownloadError(f"Play URL returned HTML, not video ({ctype})")
        size = 0
        with open(partial, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
                    size += len(chunk)
    if size < 100_000:
        partial.unlink(missing_ok=True)
        raise DouyinDownloadError(f"Downloaded file too small ({size} bytes)")
    partial.replace(dest)
    return size


def try_videofetch(share_url: str, out_dir: Path) -> dict[str, Any]:
    """Fallback via videofetch package (KuKuTool-based)."""
    try:
        import videofetch  # type: ignore
    except ImportError as e:
        raise DouyinDownloadError(
            "videofetch not installed (pip install videofetch in a venv)"
        ) from e

    # API surface varies by version — try common entry points
    meta: dict[str, Any] = {"method": "videofetch"}
    play_url = None
    aweme_id = None

    if hasattr(videofetch, "VideoFetch"):
        vf = videofetch.VideoFetch()
        result = vf.parse(share_url) if hasattr(vf, "parse") else vf.fetch(share_url)
        if isinstance(result, dict):
            play_url = (
                result.get("video_url")
                or result.get("play_url")
                or result.get("nwm_video_url")
                or result.get("url")
            )
            aweme_id = str(result.get("aweme_id") or result.get("id") or "")
            meta["desc"] = result.get("desc") or result.get("title") or ""
            meta["raw_keys"] = list(result.keys())
    elif hasattr(videofetch, "parse"):
        result = videofetch.parse(share_url)
        if isinstance(result, dict):
            play_url = result.get("video_url") or result.get("url")
            aweme_id = str(result.get("aweme_id") or "")
            meta["desc"] = result.get("desc") or ""

    if not play_url:
        raise DouyinDownloadError(f"videofetch returned no play URL: {meta}")

    aweme_id = aweme_id or "unknown"
    dest = out_dir / f"{aweme_id}.mp4"
    session = _session()
    size = download_file(play_url, dest, session)
    meta.update({"aweme_id": aweme_id, "path": str(dest), "size": size, "play_urls": [play_url]})
    return meta


def try_ytdlp(share_url: str, out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    tmpl = str(out_dir / "%(id)s.%(ext)s")
    cmd = [
        "yt-dlp",
        "--no-playlist",
        "-f",
        "mp4/best",
        "-o",
        tmpl,
        "--print",
        "after_move:filepath",
        share_url,
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if p.returncode != 0:
        raise DouyinDownloadError(f"yt-dlp failed: {p.stderr[-500:]}")
    path = (p.stdout or "").strip().splitlines()[-1].strip()
    if not path or not Path(path).is_file():
        raise DouyinDownloadError(f"yt-dlp produced no file: stdout={p.stdout!r}")
    return {
        "method": "yt-dlp",
        "path": path,
        "size": Path(path).stat().st_size,
        "aweme_id": Path(path).stem,
    }


def download(
    share_url: str,
    out_dir: Path | str = DEFAULT_OUT_DIR,
    allow_ytdlp: bool = False,
) -> dict[str, Any]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    errors: list[str] = []
    session = _session()

    # --- primary: iesdouyin ---
    try:
        aweme_id, final = resolve_aweme_id(share_url, session)
        t_parse0 = time.time()
        meta = parse_iesdouyin(aweme_id, session)
        parse_s = time.time() - t_parse0
        dest = out_dir / f"{meta['aweme_id']}.mp4"
        last_err = None
        t_dl0 = time.time()
        for u in meta["play_urls"]:
            try:
                size = download_file(u, dest, session)
                dl_s = time.time() - t_dl0
                meta.update(
                    {
                        "path": str(dest),
                        "size": size,
                        "play_url_used": u,
                        "parse_seconds": round(parse_s, 3),
                        "download_seconds": round(dl_s, 3),
                        "total_seconds": round(time.time() - t0, 3),
                        "final_url": final,
                    }
                )
                return meta
            except Exception as e:  # noqa: BLE001
                last_err = e
                continue
        raise DouyinDownloadError(f"All play URLs failed; last={last_err}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"iesdouyin: {e}")

    # --- fallback: videofetch ---
    try:
        meta = try_videofetch(share_url, out_dir)
        meta["total_seconds"] = round(time.time() - t0, 3)
        meta["errors_before"] = errors
        return meta
    except Exception as e:  # noqa: BLE001
        errors.append(f"videofetch: {e}")

    if allow_ytdlp:
        try:
            meta = try_ytdlp(share_url, out_dir)
            meta["total_seconds"] = round(time.time() - t0, 3)
            meta["errors_before"] = errors
            return meta
        except Exception as e:  # noqa: BLE001
            errors.append(f"yt-dlp: {e}")

    raise DouyinDownloadError("All methods failed:\n- " + "\n- ".join(errors))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Download a Douyin video from a share URL")
    p.add_argument("url", help="v.douyin.com / iesdouyin / douyin.com share or video URL")
    p.add_argument("-o", "--out-dir", default=str(DEFAULT_OUT_DIR), help="Output directory")
    p.add_argument("--json", action="store_true", help="Print full JSON result")
    p.add_argument("--allow-ytdlp", action="store_true", help="Use yt-dlp as last resort")
    args = p.parse_args(argv)

    try:
        result = download(args.url, args.out_dir, allow_ytdlp=args.allow_ytdlp)
    except DouyinDownloadError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result["path"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
