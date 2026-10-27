"""Isolate router state from the real home directory and from example Skills."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def isolate_router(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    skills = tmp_path / "skills"
    skills.mkdir()
    monkeypatch.setenv("SKILLROUTER_HOME", str(home))
    monkeypatch.setenv("SKILLROUTER_REGISTRY", str(REPO / "registry"))
    monkeypatch.setenv("SKILLROUTER_SKILL_PATHS", str(skills))
    monkeypatch.setenv("SKILLROUTER_LAZY_DOCTOR", "0")
    monkeypatch.delenv("SKILLROUTER_YES", raising=False)
    from skillrouter.registry_loader import clear_registry_cache

    clear_registry_cache()
    yield home


@pytest.fixture
def skills_root(isolate_router) -> Path:
    return Path(os.environ["SKILLROUTER_SKILL_PATHS"])
