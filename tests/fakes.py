"""Build a Skill directory whose entrypoint is a local fake."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

ENTRY = textwrap.dedent(
    '''\
    import json, os, sys, time, subprocess
    from pathlib import Path

    def _utf8():
        for name in ("stdin", "stdout", "stderr"):
            stream = getattr(sys, name, None)
            if stream is not None and hasattr(stream, "reconfigure"):
                try:
                    stream.reconfigure(encoding="utf-8", errors="replace")
                except Exception:
                    pass

    def main():
        _utf8()
        Path("ran.txt").write_text("1", encoding="utf-8")
        req = json.load(sys.stdin)
        beh = json.loads(Path("behavior.json").read_text(encoding="utf-8"))
        action = beh.get("action", "ok")
        out = Path(req["context"]["output_dir"])
        out.mkdir(parents=True, exist_ok=True)
        if action == "sleep":
            proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            Path("child.pid").write_text(str(proc.pid), encoding="utf-8")
            time.sleep(30)
        if action == "escape":
            target = out.parent / "evil.mp4"
            target.write_bytes(b"x" * 16)
            _ok(req, beh, "../evil.mp4", 16)
        if action == "symlink":
            outside = Path("outside.bin")
            outside.write_bytes(b"y" * 16)
            link = out / "video.mp4"
            link.symlink_to(outside.resolve())
            _ok(req, beh, "video.mp4", 16)
        if action == "hardlink":
            outside = Path("outside.bin")
            payload = b"z" * 32
            outside.write_bytes(payload)
            dest = out / "video.mp4"
            os.link(outside, dest)
            _ok(req, beh, "video.mp4", len(payload))
        if action == "error":
            if beh.get("write", True) and beh.get("code") == "QUALITY_NOT_MET":
                size = int(beh.get("size") or 64)
                _write(out / "video.mp4", size)
                details = {"output": _output(req, beh, "video.mp4", size)}
            else:
                details = beh.get("details") or {}
            _error(beh.get("code") or "UPSTREAM_ERROR", beh.get("message") or "failed", details)
        if action == "spawn_error":
            sys.exit(1)
        size = int(beh.get("size") or 64)
        _write(out / beh.get("filename", "video.mp4"), size)
        for name in beh.get("leak") or []:
            sys.stderr.write(os.environ.get(name) or "")
        _ok(req, beh, beh.get("filename", "video.mp4"), size)

    def _write(path, size):
        path.write_bytes(b"f" * size)

    def _output(req, beh, path, size):
        if isinstance(beh.get("output"), dict):
            return beh["output"]
        extra = {
            "url": (req.get("input") or {}).get("url"),
            "raw": (req.get("context") or {}).get("raw_input_text"),
            "seen": {name: bool(os.environ.get(name)) for name in beh.get("probe") or []},
            "values": {name: os.environ.get(name) for name in beh.get("probe") or []},
        }
        return {
            "meta": {
                "platform": beh.get("platform") or "douyin",
                "id": beh.get("id") or "1234567890123456789",
                "title": beh.get("title") or "中文标题",
            },
            "files": [{
                "path": path,
                "role": beh.get("role") or "muxed",
                "size_bytes": size,
                "has_watermark": beh.get("has_watermark"),
                "height": beh.get("height"),
            }],
            "extra": extra,
        }

    def _ok(req, beh, path, size):
        sys.stdout.write(json.dumps({"ok": True, "output": _output(req, beh, path, size)}, ensure_ascii=False))
        raise SystemExit(0)

    def _error(code, message, details):
        sys.stdout.write(json.dumps({"ok": False, "error": {"code": code, "message": message, "details": details}}, ensure_ascii=False))
        raise SystemExit(2)

    if __name__ == "__main__":
        main()
    '''
)


def make_skill(
    root: Path,
    skill_id: str,
    behavior: dict,
    *,
    platforms: list[str] | None = None,
    capability: str = "video.download@1",
    capabilities: list[str] | None = None,
    cost: str = "free",
    requires: dict | None = None,
    timeout_s: int = 30,
    entrypoint: dict | None = None,
    side_effects: list[str] | None = None,
    health_check: dict | None = None,
    folder: str | None = None,
    priority: int | None = None,
    metadata: str | None = None,
) -> Path:
    directory = root / (folder or skill_id.replace(".", "-"))
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "entry.py").write_text(ENTRY, encoding="utf-8")
    (directory / "behavior.json").write_text(json.dumps(behavior), encoding="utf-8")
    names = capabilities or [capability]
    provide = {
        "platforms": platforms or ["douyin"],
        "entrypoint": entrypoint or {"type": "command", "command": ["$PYTHON", "entry.py"]},
        "timeout_s": timeout_s,
        "requires": requires or {},
        "cost": cost,
        "side_effects": side_effects or ["writes_files"],
        **({"priority": priority} if priority is not None else {}),
        **({"metadata": metadata} if metadata is not None else {}),
        "health_check": health_check
        or {"samples": [{"input": {"url": "https://v.douyin.com/health/"}}], "expect": ["output.files[0].size_bytes > 1"]},
    }
    body = {
        "skillcall": "0.1",
        "id": skill_id,
        "version": "0.0.1",
        "name": skill_id,
        "license": "MIT",
        "provides": [{"capability": name, **provide} for name in names],
    }
    import yaml

    (directory / "skill.capability.yaml").write_text(yaml.safe_dump(body, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return directory
