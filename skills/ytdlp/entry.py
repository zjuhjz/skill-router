#!/usr/bin/env python3
"""yt-dlp Skill for the platforms declared in skill.capability.yaml."""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

_HOSTS = {
    "douyin.com": "douyin",
    "v.douyin.com": "douyin",
    "iesdouyin.com": "douyin",
    "bilibili.com": "bilibili",
    "m.bilibili.com": "bilibili",
    "b23.tv": "bilibili",
    "xiaohongshu.com": "xiaohongshu",
    "xhslink.com": "xiaohongshu",
    "xhslink.cn": "xiaohongshu",
    "weibo.com": "weibo",
    "weibo.cn": "weibo",
    "video.weibo.com": "weibo",
    "t.cn": "weibo",
}
_MERGED_FORMAT = "bv*+ba/b"
_PROGRESSIVE_FORMAT = "b[ext=mp4]/b[ext=webm]/b[ext=mkv]/b"
_FFMPEG_HINT = (
    "ffmpeg is not installed or not on PATH. "
    "Install ffmpeg and retry; this site serves separate video and audio that must be merged into one file."
)


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
        json.dumps(
            {"ok": False, "error": {"code": code, "message": redact_text(str(message))[:500], "details": {}}},
            ensure_ascii=False,
        )
    )
    raise SystemExit(exit_code)


def _platform(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    for pattern, platform in sorted(_HOSTS.items(), key=lambda item: len(item[0]), reverse=True):
        if host == pattern or host.endswith("." + pattern):
            return platform
    fail("UNSUPPORTED_URL", f"yt-dlp skill does not claim host {host or url}")
    return ""


def _bin(name: str) -> Path | None:
    found = shutil.which(name)
    if not found:
        return None
    path = Path(found)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    return resolved if resolved.is_file() else path if path.is_file() else None


def _ffmpeg_missing() -> bool:
    ffmpeg = _bin("ffmpeg")
    if ffmpeg is None:
        return True
    if _bin("ffprobe") is not None:
        return False
    sibling = ffmpeg.parent / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
    return not sibling.is_file()


def _ffmpeg_location() -> str | None:
    ffmpeg = _bin("ffmpeg")
    if ffmpeg is None:
        return None
    return str(ffmpeg.parent)


def _ffmpeg_problem(text: str) -> bool:
    low = (text or "").lower()
    mentions = any(token in low for token in ("ffmpeg", "ffprobe", "avconv", "winerror 2", "not recognized"))
    if not mentions:
        return False
    needles = (
        "not found",
        "not installed",
        "no such file",
        "please install",
        "ffmpeg-location",
        "winerror 2",
        "cannot find the file specified",
        "is not recognized",
        "the system cannot find",
    )
    return any(token in low for token in needles)


_PARAM_NAME = r"(?:[A-Za-z0-9_.%\-]*?(?:token|sign)|xsec_source|share_id|shareRedId|a_bogus|cookies)"
_PARAM_VALUE = r"(?:(?!%26)[^&\s\"'<>#\\])*"
_QUERY_PARAM = re.compile(rf"(?i)({_PARAM_NAME})(?:=|%3[Dd]|\\u003[dD])({_PARAM_VALUE})")
_JSON_PARAM = re.compile(rf'(?i)("(?:{_PARAM_NAME})"\s*:\s*")([^"]*)(")')
_XHS_AUTH = (
    "Xiaohongshu sent this request to a login page. "
    "Set XHS_COOKIES to your browser Cookie header or a Netscape cookies.txt path, "
    "and grant it to example.ytdlp."
)


def redact_text(text: str) -> str:
    """Drop signed query values before they reach stdout or a router log."""
    if not text:
        return ""
    cleaned = _JSON_PARAM.sub(r"\1***\3", text)
    return _QUERY_PARAM.sub(r"\1=***", cleaned)


def _login_wall(text: str) -> bool:
    low = (text or "").lower()
    return "/login" in low and ("xiaohongshu" in low or "xhslink" in low or "redirectpath" in low or "unsupported url" in low)


def _classify(text: str, *, ffmpeg_missing: bool = False) -> str:
    low = (text or "").lower()
    if ffmpeg_missing and any(token in low for token in ("ffmpeg", "ffprobe", "merger", "format is not available")):
        return "DEPENDENCY_MISSING"
    if _ffmpeg_problem(text):
        return "DEPENDENCY_MISSING"
    if _login_wall(text):
        return "NEED_AUTH"
    if "412" in low or "precondition failed" in low:
        return "GEO_BLOCKED"
    if any(token in low for token in ("sign in", "login required", "cookies", "fresh cookies")):
        return "NEED_AUTH"
    if any(token in low for token in ("404", "unavailable", "private video", "已删除", "does not exist")):
        return "NOT_FOUND"
    if "not found" in low and "format" not in low:
        return "NOT_FOUND"
    return "UPSTREAM_ERROR"


def _fail_extract(text: str, *, ffmpeg_missing: bool) -> None:
    code = _classify(text, ffmpeg_missing=ffmpeg_missing)
    if code == "NEED_AUTH" and _login_wall(text):
        message = _XHS_AUTH
    elif code == "DEPENDENCY_MISSING" and (_ffmpeg_problem(text) or ffmpeg_missing):
        message = _FFMPEG_HINT
    else:
        message = redact_text(text)
    fail(code, message, exit_code=3 if code == "DEPENDENCY_MISSING" else 2)


def _path_key(path: Path) -> str:
    text = str(path).replace("/", "\\")
    if text.startswith("\\\\?\\UNC\\"):
        text = "\\\\" + text[8:]
    elif text.startswith("\\\\?\\"):
        text = text[4:]
    parts: list[str] = []
    for part in text.split("\\"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    return os.path.normcase("\\".join(parts))


def _inside(root: Path, candidate: Path) -> bool:
    try:
        child = candidate.resolve()
        parent = root.resolve()
    except OSError:
        return False
    parent_key = _path_key(parent)
    child_key = _path_key(child)
    if parent_key == child_key:
        return True
    prefix = parent_key if parent_key.endswith("\\") else parent_key + "\\"
    return child_key.startswith(prefix)


def _relative_output(path: Path, out: Path) -> str | None:
    if not _inside(out, path):
        return None
    try:
        relative = Path(os.path.relpath(path.resolve(), out.resolve()))
    except (ValueError, OSError):
        return None
    if ".." in relative.parts:
        return None
    return relative.as_posix()


def _partial(path: Path) -> bool:
    name = path.name.lower()
    return name.endswith(".part") or name.endswith(".ytdl") or ".part." in name or ".temp." in name


def _finished_in(out: Path) -> list[Path]:
    if not out.is_dir():
        return []
    found = []
    for candidate in out.rglob("*"):
        if not candidate.is_file() or candidate.is_symlink() or _partial(candidate):
            continue
        if candidate.suffix.lower() not in {".mp4", ".mkv", ".webm", ".flv", ".mov", ".m4v"}:
            continue
        found.append(candidate)

    def rank(item: Path) -> tuple:
        fragment = 1 if re.search(r"\.f[0-9a-z_-]{1,24}\.", item.name.lower()) else 0
        try:
            size = item.stat().st_size
        except OSError:
            size = 0
        return (fragment, 0 if item.suffix.lower() == ".mp4" else 1, -size)

    found.sort(key=rank)
    return found


def _detach(path: Path) -> Path:
    """Copy off a hardlink or symlink so the router can accept the file on Windows."""
    try:
        linked = path.is_symlink() or path.stat().st_nlink > 1
    except OSError:
        return path
    if not linked:
        return path
    dest = path.with_name(path.stem + ".muxed" + path.suffix)
    shutil.copyfile(path, dest)
    return dest


def _place(path: Path | None, out: Path) -> Path | None:
    inside = _finished_in(out)
    if inside:
        return _detach(inside[0])
    if path is None or not path.is_file() or path.is_symlink() or _partial(path):
        return None
    dest = out / path.name
    try:
        same = dest.resolve() == path.resolve()
    except OSError:
        same = False
    if not same:
        shutil.copyfile(path, dest)
    return _detach(dest if dest.is_file() else path)


def _available() -> str:
    if importlib.util.find_spec("yt_dlp") is not None:
        return "module"
    if shutil.which("yt-dlp"):
        return "cli"
    fail("DEPENDENCY_MISSING", "yt-dlp is not installed", exit_code=3)
    return ""


def _netscape_file(header: str, dest: Path) -> str:
    dest.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Netscape HTTP Cookie File"]
    for part in header.split(";"):
        piece = part.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        name = name.strip()
        value = value.strip()
        if not name:
            continue
        lines.append(f".xiaohongshu.com\tTRUE\t/\tFALSE\t0\t{name}\t{value}")
    dest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(dest)


def _as_netscape(raw: str, dest: Path) -> str | None:
    text = (raw or "").strip()
    if not text:
        return None
    path = Path(text)
    if path.is_file():
        body = path.read_text(encoding="utf-8", errors="replace")
        tabular = [
            line
            for line in body.splitlines()
            if line.strip() and not line.startswith("#") and line.count("\t") >= 5
        ]
        if tabular:
            return str(path)
        text = body.strip()
    if text.lower().startswith("cookie:"):
        text = text.split(":", 1)[1].strip()
    if not text:
        return None
    return _netscape_file(text, dest)


def _cookies(platform: str, temp: Path) -> str | None:
    if platform == "xiaohongshu":
        made = _as_netscape(os.environ.get("XHS_COOKIES") or "", temp / "xhs-cookies.txt")
        if made:
            return made
    raw = os.environ.get("YTDLP_COOKIES") or ""
    if raw and Path(raw).is_file():
        return raw
    return None


def _formats(info: dict) -> list[dict]:
    found = []
    seen: set[str] = set()
    for fmt in info.get("formats") or []:
        if not isinstance(fmt, dict):
            continue
        url = fmt.get("url")
        if not isinstance(url, str) or not url.startswith("http") or url in seen:
            continue
        vcodec = fmt.get("vcodec") or "none"
        acodec = fmt.get("acodec") or "none"
        if vcodec == "none" and acodec == "none":
            continue
        if vcodec != "none" and acodec != "none":
            role = "muxed"
        elif vcodec != "none":
            role = "video"
        else:
            role = "audio"
        seen.add(url)
        height = fmt.get("height")
        found.append(
            {
                "url": url,
                "role": role,
                "container": fmt.get("ext"),
                "height": int(height) if isinstance(height, int) else None,
                "has_watermark": None,
            }
        )
    direct = info.get("url")
    if isinstance(direct, str) and direct.startswith("http") and direct not in seen:
        found.append({"url": direct, "role": "muxed", "container": info.get("ext"), "has_watermark": None})
    return found[:12]


def _meta(info: dict, platform: str, source: str) -> dict:
    video_id = str(info.get("id") or "").strip()
    if not video_id:
        fail("UPSTREAM_ERROR", "yt-dlp returned no id")
    duration = info.get("duration")
    duration_ms = int(round(float(duration) * 1000)) if isinstance(duration, (int, float)) and duration > 0 else None
    return {
        "platform": platform,
        "id": video_id,
        "title": info.get("title"),
        "author": info.get("uploader") or info.get("channel") or info.get("creator"),
        "duration_ms": duration_ms,
        "cover": info.get("thumbnail"),
        "page_url": info.get("webpage_url") or source,
    }


def _extract_module(url: str, out: Path, download: bool, cookies: str | None) -> tuple[dict, Path | None]:
    import yt_dlp

    opts: dict = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": not download,
        "socket_timeout": 30,
        "ignoreconfig": True,
    }
    if cookies:
        opts["cookiefile"] = cookies
    if download:
        out.mkdir(parents=True, exist_ok=True)
        # Relative template plus paths.home. An absolute template makes yt-dlp
        # ignore --paths, and on Windows sanitize_path can rewrite a directory
        # that ends in a dot or space so the merged file no longer sits in out.
        opts["outtmpl"] = {"default": "%(id)s.%(ext)s"}
        opts["paths"] = {"home": str(out), "temp": str(out)}
        opts["noprogress"] = True
        opts["overwrites"] = True
        if os.name == "nt":
            opts["windowsfilenames"] = True
        location = _ffmpeg_location()
        if location and not _ffmpeg_missing():
            opts["ffmpeg_location"] = location
        if _ffmpeg_missing():
            opts["format"] = _PROGRESSIVE_FORMAT
        else:
            opts["format"] = _MERGED_FORMAT
            opts["merge_output_format"] = "mp4"
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=download)
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        cause = getattr(exc, "msg", None)
        if cause:
            detail = f"{detail}\n{cause}"
        _fail_extract(detail[-800:], ffmpeg_missing=_ffmpeg_missing())
    if not isinstance(info, dict):
        fail("UPSTREAM_ERROR", "yt-dlp returned no metadata")
    path = None
    if download:
        requested = info.get("requested_downloads") or []
        if requested and isinstance(requested[0], dict) and requested[0].get("filepath"):
            path = Path(requested[0]["filepath"])
        elif info.get("filepath"):
            path = Path(info["filepath"])
        else:
            video_id = str(info.get("id") or "")
            for candidate in out.glob(f"{video_id}.*"):
                if candidate.is_file() and not candidate.name.endswith(".part"):
                    path = candidate
                    break
        path = _place(path if isinstance(path, Path) else None, out)
        if path is None or not path.is_file():
            fail("UPSTREAM_ERROR", "yt-dlp finished without a file inside output_dir")
    return info, path


def _extract_cli(url: str, out: Path, download: bool, cookies: str | None) -> tuple[dict, Path | None]:
    binary = shutil.which("yt-dlp")
    if not binary:
        fail("DEPENDENCY_MISSING", "yt-dlp is not on PATH", exit_code=3)
    cmd = [binary, "--ignore-config", "--no-playlist", "--no-warnings"]
    if cookies:
        cmd.extend(["--cookies", cookies])
    if download:
        out.mkdir(parents=True, exist_ok=True)
        cmd.extend(["-f", _PROGRESSIVE_FORMAT if _ffmpeg_missing() else _MERGED_FORMAT])
        location = _ffmpeg_location()
        if location and not _ffmpeg_missing():
            cmd.extend(["--ffmpeg-location", location])
        if not _ffmpeg_missing():
            cmd.extend(["--merge-output-format", "mp4"])
        cmd.extend(
            [
                "-P",
                f"home:{out}",
                "-P",
                f"temp:{out}",
                "-o",
                "%(id)s.%(ext)s",
                "--print",
                "after_move:filepath",
                url,
            ]
        )
    else:
        cmd.extend(["--skip-download", "--dump-single-json", url])
    try:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=170, check=False)
    except subprocess.TimeoutExpired:
        fail("TIMEOUT", "yt-dlp timed out")
    if completed.returncode != 0:
        tail = (completed.stderr or completed.stdout or "")[-800:]
        _fail_extract(tail, ffmpeg_missing=_ffmpeg_missing())
    if not download:
        try:
            info = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            fail("UPSTREAM_ERROR", f"yt-dlp JSON parse failed: {exc}")
        if not isinstance(info, dict):
            fail("UPSTREAM_ERROR", "yt-dlp JSON was not an object")
        return info, None
    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    reported = Path(lines[-1]) if lines else None
    path = _place(reported, out)
    if path is None or not path.is_file():
        fail("UPSTREAM_ERROR", "yt-dlp finished without a file inside output_dir")
    return {"id": path.stem, "title": None, "webpage_url": url, "formats": []}, path


def main() -> None:
    try:
        _main()
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        _fail_extract(str(exc)[-800:], ffmpeg_missing=_ffmpeg_missing())


def _main() -> None:
    _utf8()
    action = sys.argv[1] if len(sys.argv) > 1 else "download"
    try:
        req = json.load(sys.stdin)
    except json.JSONDecodeError as exc:
        fail("INVALID_INPUT", f"stdin 不是 JSON: {exc}")
    url = str((req.get("input") or {}).get("url") or "")
    platform = _platform(url)
    out = Path(str((req.get("context") or {}).get("output_dir") or "."))
    temp = Path(str((req.get("context") or {}).get("temp_dir") or out))
    kind = _available()
    cookies = _cookies(platform, temp)
    download = action != "resolve"
    if kind == "module":
        info, path = _extract_module(url, out, download, cookies)
    else:
        info, path = _extract_cli(url, out, download, cookies)
    meta = _meta(info, platform, url)
    if not download:
        sys.stdout.write(json.dumps({"ok": True, "output": {"meta": meta, "media": _formats(info)}}, ensure_ascii=False))
        return
    assert path is not None
    relative = _relative_output(path, out)
    if relative is None:
        fail("UPSTREAM_ERROR", f"yt-dlp file is not inside output_dir ({path.name})")
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "output": {
                    "meta": meta,
                    "files": [
                        {
                            "path": relative,
                            "role": "muxed",
                            "size_bytes": path.stat().st_size,
                            "has_watermark": None,
                        }
                    ],
                },
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
