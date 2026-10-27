"""JSON state for health, circuits, and trust. A lock file serializes writers."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Callable

from skillrouter.paths import home


def state_path() -> Path:
    return home() / "state.json"


def empty_state() -> dict:
    return {
        "circuits": {},
        "health": {},
        "trust": {},
        "deprecated": {},
        "user_restored": {},
        "failing_since": {},
        "doctor_dates": {},
    }


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return empty_state()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return empty_state()
    if not isinstance(data, dict):
        return empty_state()
    base = empty_state()
    base.update(data)
    for key in empty_state():
        if not isinstance(base.get(key), dict):
            base[key] = {}
    return base


def save_state(data: dict) -> None:
    from skillrouter.creds import redact_tree

    # Health and circuit files are kept on disk. Scrub them so a sample
    # message cannot retain xsec_token or another signed query value.
    cleaned = redact_tree(data)
    if isinstance(cleaned, dict):
        data.clear()
        data.update(cleaned)
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_state(mutator: Callable[[dict], None]) -> dict:
    lock = state_path().with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    for _ in range(100):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > 15:
                    lock.unlink()
                    continue
            except OSError:
                pass
            time.sleep(0.02)
    if fd is None:
        fd = os.open(str(lock), os.O_CREAT | os.O_WRONLY)
    try:
        data = load_state()
        mutator(data)
        save_state(data)
        return data
    finally:
        os.close(fd)
        try:
            lock.unlink()
        except OSError:
            pass
