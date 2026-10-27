"""Home directory, config, and run folders."""

from __future__ import annotations

import json
import os
from pathlib import Path


def home() -> Path:
    raw = os.environ.get("SKILLROUTER_HOME", "").strip()
    path = Path(raw).expanduser() if raw else Path.home() / ".skillrouter"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    raw = os.environ.get("SKILLROUTER_CONFIG", "").strip()
    return Path(raw).expanduser() if raw else home() / "config.json"


def load_config() -> dict:
    data = {
        "extra_skill_paths": [],
        "pin": [],
        "priority": {},
        "disabled": [],
        "allow_paid": False,
        "disable_third_party": False,
        "max_attempts": 4,
        "max_total_s": 300,
        "max_output_bytes": 4294967296,
        "proxy": None,
        "log_raw_urls": False,
        "lazy_doctor": True,
        "network": {"kind": "unknown", "region": None},
        "locale": "zh-CN",
    }
    path = config_path()
    if path.is_file():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            data.update(loaded)
    if os.environ.get("SKILLROUTER_MAX_OUTPUT_BYTES", "").strip():
        data["max_output_bytes"] = int(os.environ["SKILLROUTER_MAX_OUTPUT_BYTES"])
    if os.environ.get("SKILLROUTER_MAX_TOTAL_S", "").strip():
        data["max_total_s"] = float(os.environ["SKILLROUTER_MAX_TOTAL_S"])
    if os.environ.get("SKILLROUTER_MAX_ATTEMPTS", "").strip():
        data["max_attempts"] = int(os.environ["SKILLROUTER_MAX_ATTEMPTS"])
    return data


def credentials_path() -> Path:
    return home() / "credentials.json"


def logs_dir() -> Path:
    path = home() / "logs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    path = home() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir() -> Path:
    path = home() / "downloads"
    path.mkdir(parents=True, exist_ok=True)
    return path
