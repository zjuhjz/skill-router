"""Find installed Skills by their skill.capability.yaml sidecar."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from skillrouter.paths import home, load_config
from skillrouter.registry_loader import capability_key, load_registry


@dataclass
class Provision:
    capability: str
    platforms: list[str]
    url_patterns: list[str]
    entrypoint: dict
    timeout_s: float
    requires: dict
    cost: str
    side_effects: list[str]
    health_check: dict
    deprecated: bool
    superseded_by: str | None
    priority: int
    metadata: str


@dataclass
class Skill:
    id: str
    version: str
    name: str
    author: dict
    license: str
    directory: Path
    provides: list[Provision] = field(default_factory=list)
    content_hash: str = ""
    parse_error: str | None = None
    warmup: dict | None = None


def _hash_dir(path: Path) -> str:
    digest = hashlib.sha256()
    files = []
    for item in path.rglob("*"):
        if not item.is_file():
            continue
        if "__pycache__" in item.parts or item.suffix in {".pyc", ".part"}:
            continue
        if item.name in {"ran.txt", "child.pid", "outside.bin"}:
            continue
        files.append(item)
    for item in sorted(files, key=lambda candidate: candidate.relative_to(path).as_posix()):
        rel = item.relative_to(path).as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def discovery_roots() -> list[Path]:
    raw = os.environ.get("SKILLROUTER_SKILL_PATHS", "").strip()
    if raw:
        roots = [Path(part).expanduser() for part in raw.split(os.pathsep) if part.strip()]
    else:
        roots = [
            home() / "skills",
            Path.home() / ".claude" / "skills",
            Path.home() / ".cursor" / "skills",
            Path.cwd() / ".skills",
            Path.cwd() / "skills",
        ]
    for extra in load_config().get("extra_skill_paths") or []:
        roots.append(Path(str(extra)).expanduser())
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = str(root)
        if key in seen:
            continue
        seen.add(key)
        unique.append(root)
    return unique


def _skill_dirs(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        if not root.exists():
            continue
        candidates = []
        if (root / "skill.capability.yaml").is_file():
            candidates.append(root)
        else:
            try:
                children = list(root.iterdir())
            except OSError:
                children = []
            for child in children:
                if child.is_dir() and (child / "skill.capability.yaml").is_file():
                    candidates.append(child)
        for candidate in candidates:
            key = str(candidate.resolve())
            if key not in seen:
                seen.add(key)
                found.append(candidate)
    return found


def _as_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def parse_skill(directory: Path) -> tuple[Skill | None, str | None]:
    path = directory / "skill.capability.yaml"
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        return None, f"{directory.name}: {exc}"
    if not isinstance(doc, dict):
        return None, f"{directory.name}: skill.capability.yaml is not a mapping"
    skill_id = str(doc.get("id") or "").strip()
    if not skill_id:
        return None, f"{directory.name}: missing id"
    author = doc.get("author") or {}
    if isinstance(author, str):
        author = {"name": author}
    if not isinstance(author, dict):
        author = {}
    known = load_registry()["capabilities"]
    provides: list[Provision] = []
    notes: list[str] = []
    for item in doc.get("provides") or []:
        if not isinstance(item, dict):
            notes.append(f"{skill_id}: a provides entry is not a mapping")
            continue
        capability = str(item.get("capability") or "").strip()
        if "@" not in capability:
            notes.append(f"{skill_id}: capability must include @version ({capability or 'empty'})")
            continue
        name, raw_version = capability.split("@", 1)
        try:
            version = int(raw_version)
        except ValueError:
            notes.append(f"{skill_id}: bad capability version {capability}")
            continue
        if (name, version) not in known:
            notes.append(f"{skill_id}: unknown capability {capability}")
            continue
        entry = item.get("entrypoint") or {}
        if not isinstance(entry, dict):
            notes.append(f"{skill_id}: entrypoint must be a mapping")
            continue
        requires = item.get("requires") or {}
        if not isinstance(requires, dict):
            requires = {}
        health = item.get("health_check") or {}
        if not isinstance(health, dict):
            health = {}
        hint = item.get("metadata")
        metadata = hint.strip().lower() if isinstance(hint, str) else ""
        if metadata not in {"rich", "basic"}:
            metadata = ""
        provides.append(
            Provision(
                capability=capability_key(name, version),
                platforms=[str(p) for p in _as_list(item.get("platforms"))],
                url_patterns=[str(p) for p in _as_list(item.get("url_patterns"))],
                entrypoint=entry,
                timeout_s=float(item.get("timeout_s") or 60),
                requires=requires,
                cost=str(item.get("cost") or "free"),
                side_effects=[str(p) for p in _as_list(item.get("side_effects"))],
                health_check=health,
                deprecated=bool(item.get("deprecated") or doc.get("deprecated")),
                superseded_by=(item.get("superseded_by") or doc.get("superseded_by") or None),
                priority=int(item.get("priority") or 0),
                metadata=metadata,
            )
        )
    skill = Skill(
        id=skill_id,
        version=str(doc.get("version") or "0"),
        name=str(doc.get("name") or skill_id),
        author=author,
        license=str(doc.get("license") or ""),
        directory=directory,
        provides=provides,
        content_hash=_hash_dir(directory),
        parse_error="; ".join(notes) if notes and not provides else None,
        warmup=doc.get("warmup") if isinstance(doc.get("warmup"), dict) else None,
    )
    warning = "; ".join(notes) if notes else None
    return skill, warning


def discover() -> tuple[list[Skill], list[str]]:
    skills: list[Skill] = []
    errors: list[str] = []
    seen: set[str] = set()
    for directory in _skill_dirs(discovery_roots()):
        skill, warning = parse_skill(directory)
        if warning:
            errors.append(warning)
        if skill is None:
            continue
        if skill.id in seen:
            errors.append(f"duplicate skill id {skill.id}")
            continue
        seen.add(skill.id)
        if skill.provides:
            skills.append(skill)
    skills.sort(key=lambda item: item.id)
    return skills, errors
