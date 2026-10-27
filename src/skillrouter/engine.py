"""Match, rank, invoke, fall back, and doctor. Platform data stays in the registry."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from skillrouter import health as healthmod
from skillrouter.creds import (
    blacklisted,
    child_env,
    credential_available,
    credential_value,
    granted_names,
    known_secrets,
    load_credentials,
    redact,
    redact_tree,
)
from skillrouter.discover import Skill, discover, discovery_roots
from skillrouter.expr import all_expect
from skillrouter.paths import downloads_dir, load_config, logs_dir, runs_dir
from skillrouter.proc import http_allowed, run_command, run_http
from skillrouter.registry_loader import (
    RegistryError,
    canary_urls,
    capability_key,
    code_table,
    code_with,
    extract_url,
    load_registry,
    platform_for_url,
    pointer_get,
    policy_for,
    resolve_capability,
)
from skillrouter.state import load_state, update_state
from skillrouter.version import __version__

_COST_RANK = {"free": 0, "metered": 1, "paid": 2}
_GRADE_RANK = {"healthy": 0, "unknown": 1, "degraded": 2, "failing": 3}
# A provision that names many platforms, or "*", is a catch-all. One named platform is specific.
_GENERIC_PLATFORM = {"*", "any"}
# Sample scores are 0–1. The stored health score is that average times 100.
_CLEARLY_BAD_SCORE = 50.0
_CLEARLY_BAD_SAMPLES = 3


@dataclass
class Attempt:
    skill_id: str
    version: str
    ok: bool
    code: str | None
    message: str
    latency_ms: int
    output: dict | None = None
    output_dir: Path | None = None
    temp_dir: Path | None = None
    stderr: str = ""
    retry_after_s: float | None = None
    details: dict = field(default_factory=dict)

    def public(self) -> dict[str, Any]:
        item: dict[str, Any] = {
            "skill": self.skill_id,
            "version": self.version,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
        }
        if self.code:
            item["code"] = self.code
        if self.message:
            item["message"] = self.message
        return item


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _fail(capability: str, code: str, message: str, **extra: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "ok": False,
        "capability": capability,
        "error": {"code": code, "message": message, "details": extra.get("details") or {}},
        "attempts": extra.get("attempts") or [],
        "skipped": extra.get("skipped") or [],
        "hints": extra.get("hints") or [],
    }
    return body


def _schema_message(schema: dict, instance: Any) -> str | None:
    errors = sorted(Draft202012Validator(schema).iter_errors(instance), key=lambda item: list(item.path))
    if not errors:
        return None
    parts = []
    for error in errors[:5]:
        loc = "/".join(str(part) for part in error.path) or "(root)"
        parts.append(f"{loc}: {error.message}")
    return "; ".join(parts)


def _pointer_set(data: dict, pointer: str, value: Any) -> None:
    parts = [part for part in str(pointer).lstrip("/").split("/") if part]
    if not parts:
        return
    current = data
    for part in parts[:-1]:
        nxt = current.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            current[part] = nxt
        current = nxt
    current[parts[-1]] = value


def _prepare_input(doc: dict, raw_input: dict) -> tuple[dict, str | None, str | None, str | None]:
    data = copy.deepcopy(raw_input)
    routing = doc.get("routing") or {}
    if routing == "none":
        return data, None, None, None
    if not isinstance(routing, dict):
        return data, None, None, "routing is not a mapping"
    platform = None
    pointed = pointer_get(data, routing.get("platform_field"))
    if isinstance(pointed, str) and pointed.strip():
        platform = pointed.strip()
    raw_text = None
    url_value = None
    url_field = routing.get("url_field")
    if url_field:
        current = pointer_get(data, url_field)
        if isinstance(current, str):
            url_value = current.strip()
            if routing.get("extract_url_from_text", True):
                extracted = extract_url(current)
                if extracted:
                    if extracted != current.strip():
                        raw_text = current
                    url_value = extracted
                    _pointer_set(data, str(url_field), extracted)
    if platform is None and url_value:
        platform = platform_for_url(url_value)
    file_value = None
    file_field = routing.get("file_field")
    if file_field:
        current_file = pointer_get(data, file_field)
        if isinstance(current_file, str) and current_file.strip():
            file_value = current_file.strip()
    if platform is None and file_value:
        named = str(routing.get("file_platform") or "").strip()
        if named and named in load_registry()["platforms"]:
            platform = named
    if platform is not None and platform not in load_registry()["platforms"]:
        platform = None
    if routing.get("platform_field") or routing.get("url_field") or routing.get("file_field"):
        if platform is None:
            unsupported = _code_by_class("unsupported")
            return data, None, raw_text, unsupported
    return data, platform, raw_text, None


def _code_by_class(kind: str) -> str:
    for name, policy in code_table().items():
        if isinstance(policy, dict) and policy.get("class") == kind:
            return name
    return code_with("default")


def _safe_token(value: object) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or ""))[:80].strip("._")
    return cleaned or "item"


def _rm(path: Path | None) -> None:
    if path is not None and path.exists():
        shutil.rmtree(path, ignore_errors=True)


def _path_key(path: Path) -> str:
    """Compare paths even when Windows resolve() adds a \\\\?\\ prefix or changes case."""
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


def _is_inside(root: Path, candidate: Path) -> bool:
    parent = _path_key(root)
    child = _path_key(candidate)
    if parent == child:
        return True
    prefix = parent if parent.endswith("\\") else parent + "\\"
    return child.startswith(prefix)


def _check_files(output_dir: Path, output: dict, max_bytes: int) -> str | None:
    try:
        root = output_dir.resolve()
    except OSError:
        root = output_dir
    total = 0
    files = output.get("files") or []
    if not isinstance(files, list):
        return "files is not a list"
    for item in files:
        if not isinstance(item, dict):
            return "file entry is not an object"
        rel = str(item.get("path") or "")
        path = Path(rel)
        if not rel or path.is_absolute() or ".." in path.parts:
            # ".." is rejected even if it would normalize back inside.
            if not rel or path.is_absolute():
                return "file path must be relative"
        candidate = output_dir / rel
        if candidate.is_symlink():
            return "refusing a symlink"
        try:
            resolved = candidate.resolve()
        except OSError as exc:
            return f"unreadable file: {exc}"
        if not _is_inside(root, resolved):
            return "file path escapes output_dir"
        if ".." in Path(rel).parts:
            return "file path escapes output_dir"
        if not resolved.is_file() or resolved.is_symlink():
            return "file path is not a regular file"
        try:
            if resolved.is_fifo():
                return "refusing a fifo"
        except OSError:
            return "unreadable file"
        stat = resolved.stat()
        if stat.st_nlink > 1:
            return "refusing a hardlink"
        if int(item.get("size_bytes") or -1) != stat.st_size:
            return "size_bytes does not match the file"
        digest = item.get("sha256")
        if isinstance(digest, str) and digest:
            actual = hashlib.sha256(resolved.read_bytes()).hexdigest()
            if actual != digest:
                return "sha256 does not match the file"
        total += stat.st_size
        if total > max_bytes:
            return "output exceeds max_output_bytes"
    return None


def _output_rule_failure(rules: list[dict] | None, inp: dict, output: dict) -> str | None:
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        kind = rule.get("type")
        on_fail = str(rule.get("on_fail") or _code_by_class("quality"))
        files = [item for item in (output.get("files") or []) if isinstance(item, dict)]
        role = rule.get("role")
        if role:
            files = [item for item in files if item.get("role") == role]
        if kind == "require_file_role":
            suffixes = {str(item).lower() for item in (rule.get("suffixes") or [])}
            ok = False
            for item in files:
                suffix = Path(str(item.get("path") or "")).suffix.lower()
                if not suffixes or suffix in suffixes:
                    ok = True
                    break
            if not ok:
                return on_fail
        elif kind == "input_flag_forbids_field":
            if inp.get(str(rule.get("flag"))) is True:
                for item in files:
                    if item.get(rule.get("field")) == rule.get("forbidden"):
                        return on_fail
        elif kind == "input_number_minimum":
            minimum = inp.get(str(rule.get("input")))
            if isinstance(minimum, (int, float)) and not isinstance(minimum, bool):
                values = [
                    item.get(rule.get("field"))
                    for item in files
                    if isinstance(item.get(rule.get("field")), (int, float))
                    and not isinstance(item.get(rule.get("field")), bool)
                ]
                if values and max(values) < minimum:
                    return on_fail
    return None


def _degraded_key(output: dict) -> tuple[int, int]:
    files = [item for item in (output.get("files") or []) if isinstance(item, dict)]
    muxed = [item for item in files if item.get("role") == "muxed"] or files
    height = max((int(item.get("height") or 0) for item in muxed), default=0)
    size = max((int(item.get("size_bytes") or 0) for item in muxed), default=0)
    return height, size


def _unique_dest(directory: Path, name: str) -> Path:
    dest = directory / name
    if not dest.exists():
        return dest
    stem = dest.stem
    suffix = dest.suffix
    index = 1
    while True:
        candidate = directory / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def _relocate(output: dict, output_dir: Path, dest_root: Path) -> dict:
    dest_root.mkdir(parents=True, exist_ok=True)
    meta = output.get("meta") if isinstance(output.get("meta"), dict) else {}
    platform = _safe_token(meta.get("platform"))
    ident = _safe_token(meta.get("id"))
    moved = []
    for item in output.get("files") or []:
        src = (output_dir / str(item["path"])).resolve()
        ext = src.suffix or ".bin"
        role = str(item.get("role") or "file")
        index = item.get("index")
        index_part = f"_{int(index)}" if isinstance(index, int) and not isinstance(index, bool) else ""
        if role == "muxed" and not index_part:
            name = f"{platform}_{ident}{ext}"
        else:
            name = f"{platform}_{ident}_{_safe_token(role)}{index_part}{ext}"
        dest = _unique_dest(dest_root, name)
        shutil.move(str(src), str(dest))
        copied = dict(item)
        copied["path"] = str(dest.resolve())
        moved.append(copied)
    updated = dict(output)
    updated["files"] = moved
    return updated


def _parse_stdout(text: str) -> dict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        value, end = json.JSONDecoder().raw_decode(raw)
    except json.JSONDecodeError:
        return None
    if raw[end:].strip():
        return None
    if not isinstance(value, dict):
        return None
    return value


def _requested_env(requires: dict) -> list[str]:
    names = [str(item) for item in (requires.get("env") or [])]
    names.extend(str(item) for item in (requires.get("optional_env") or []))
    return names


def _missing_required(skill_id: str, requires: dict, store: dict) -> list[str]:
    missing = []
    for name in [str(item) for item in (requires.get("env") or [])]:
        if not credential_available(skill_id, name, store):
            missing.append(name)
    return missing


def _missing_binaries(requires: dict) -> list[str]:
    missing = []
    for name in [str(item) for item in (requires.get("binaries") or [])]:
        if shutil.which(name) is None:
            missing.append(name)
    return missing


def _patterns_match(patterns: list[str], url: str | None) -> bool:
    if not patterns:
        return True
    if not url:
        return False
    for pattern in patterns:
        try:
            if re.search(pattern, url):
                return True
        except re.error:
            continue
    return False


def _confirm_view(skill: Skill, provision) -> dict[str, Any]:
    return {
        "id": skill.id,
        "version": skill.version,
        "author": skill.author,
        "side_effects": provision.side_effects,
        "requires": provision.requires,
        "hash": skill.content_hash,
    }


def _trusted(state: dict, skill: Skill) -> bool:
    return state.get("trust", {}).get(skill.id) == skill.content_hash


def _remember_trust(skill: Skill) -> None:
    def mutate(state: dict) -> None:
        state["trust"][skill.id] = skill.content_hash

    update_state(mutate)


def _bench_blocks(state: dict, key: str, now: float) -> bool:
    """True when this health key would be skipped before the skill can run."""
    if state["deprecated"].get(key):
        return True
    circuit = state["circuits"].get(key) or {}
    if not isinstance(circuit, dict):
        return False
    if circuit.get("state") == "open":
        return True
    for field in ("open_until", "cooldown_until"):
        try:
            if float(circuit.get(field) or 0) > now:
                return True
        except (TypeError, ValueError):
            return True
    return False


def _unbench(state: dict, key: str) -> None:
    """Drop a router bench. Author and registry deprecation are not stored here."""
    state["deprecated"].pop(key, None)
    state["failing_since"].pop(key, None)
    state["doctor_dates"].pop(key, None)
    state["circuits"][key] = _closed_circuit()


def _skill_keys(state: dict, skill_id: str) -> list[str]:
    prefix = skill_id + "\t"
    found: list[str] = []
    for name in ("health", "circuits", "deprecated", "failing_since", "doctor_dates", "user_restored"):
        for key in state.get(name) or {}:
            if str(key).startswith(prefix) and str(key) not in found:
                found.append(str(key))
    return found


def reconcile_skills(skills: list[Skill]) -> None:
    """Expire benches written by an older skill version, file hash, or pre-fingerprint state."""

    def mutate(state: dict) -> None:
        now = time.time()
        for skill in skills:
            for key in _skill_keys(state, skill.id):
                raw = state["health"].get(key)
                bucket = dict(raw) if isinstance(raw, dict) else {}
                stored_version = bucket.get("skill_version")
                stored_hash = bucket.get("content_hash")
                version_changed = stored_version != skill.version
                hash_changed = bool(stored_hash) and stored_hash != skill.content_hash
                # State from before cred_hash existed. A NEED_AUTH or UPSTREAM_CHANGED
                # bench must not survive the upgrade that adds fingerprints.
                pre_fingerprint = "cred_hash" not in bucket and _bench_blocks(state, key, now)
                if not version_changed and not hash_changed and not pre_fingerprint:
                    continue
                _unbench(state, key)
                if version_changed or hash_changed:
                    state["user_restored"].pop(key, None)
                kept = {"skill_version": skill.version, "content_hash": skill.content_hash}
                if isinstance(bucket.get("cred_hash"), str):
                    kept["cred_hash"] = bucket["cred_hash"]
                state["health"][key] = kept

    update_state(mutate)


def _bucket_view(state: dict, key: str, *, author_deprecated: bool, registry_deprecated: bool) -> dict[str, Any]:
    bucket = state["health"].get(key) or {}
    samples = list(bucket.get("samples") or [])
    restored = bool(state["user_restored"].get(key))
    auto = bool(state["deprecated"].get(key)) and not author_deprecated and not registry_deprecated
    grade, score = healthmod.effective_grade(
        samples=samples,
        author_deprecated=author_deprecated,
        registry_deprecated=registry_deprecated,
        auto_deprecated=bool(state["deprecated"].get(key)),
        user_restored=restored,
    )
    return {
        "grade": grade,
        "score": score,
        "latency": healthmod.p50_latency(samples),
        "circuit": healthmod.circuit_view(state["circuits"].get(key)),
        "last_doctor_ts": float(bucket.get("last_doctor_ts") or 0),
        "samples": len(samples),
    }


def _remember(
    skill: Skill,
    capability: str,
    platform: str,
    *,
    ok: bool,
    code: str | None,
    latency_ms: int,
    source: str,
    retry_after_s: float | None,
    author_deprecated: bool,
    registry_deprecated: bool,
    count_doctor: bool,
) -> None:
    key = healthmod.health_key(skill.id, capability, platform)
    now = time.time()
    scored, value = healthmod.score_value(code, ok=ok)
    policy = policy_for(code or "")[1] if code else {}
    cool = None
    if policy.get("cooldown") == "retry_after":
        cool = float(retry_after_s) if retry_after_s else 60.0

    def mutate(state: dict) -> None:
        state["circuits"][key] = healthmod.note_outcome(
            state["circuits"].get(key) or {},
            ok=ok,
            scored=scored,
            retry_after_s=cool,
            now=now,
        )
        bucket = dict(state["health"].get(key) or {})
        bucket["content_hash"] = skill.content_hash
        bucket["skill_version"] = skill.version
        if scored:
            healthmod.append_sample(bucket, score=value, latency_ms=latency_ms, source=source, now=now)
        if source == "doctor":
            bucket["last_doctor_ts"] = now
        state["health"][key] = bucket
        grade, _score = healthmod.grade_of(list(bucket.get("samples") or []), now)
        if grade == "failing":
            state["failing_since"].setdefault(key, now)
        else:
            state["failing_since"].pop(key, None)
        if count_doctor and source == "doctor":
            day = time.strftime("%Y-%m-%d", time.gmtime(now))
            dates = list(state["doctor_dates"].get(key) or [])
            if day not in dates:
                dates.append(day)
            state["doctor_dates"][key] = dates
        since = state["failing_since"].get(key)
        auto = bool(since and now - float(since) >= 14 * 86400 and len(state["doctor_dates"].get(key) or []) >= 5)
        restored = bool(state["user_restored"].get(key))
        bucket["last_code"] = code or ("ok" if ok else "")
        missing_credentials = bool(policy.get("mark") == "missing_credentials")
        if missing_credentials:
            _unbench(state, key)
        elif not restored and (author_deprecated or registry_deprecated or auto):
            state["deprecated"][key] = True
        elif not (author_deprecated or registry_deprecated):
            state["deprecated"].pop(key, None)

    update_state(mutate)


def _execute(
    skill: Skill,
    provision,
    *,
    capability: str,
    doc: dict,
    data: dict,
    platform: str | None,
    raw_text: str | None,
    mode: str,
    timeout_s: float,
    store: dict,
) -> Attempt:
    started = time.perf_counter()
    cfg = load_config()
    request_id = "r_" + uuid.uuid4().hex
    base = runs_dir() / request_id
    output_dir = base / "out"
    temp_dir = base / "tmp"
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    max_bytes = int(cfg.get("max_output_bytes") or 4294967296)
    proxy = cfg.get("proxy")
    request = {
        "skillcall": "0.1",
        "request_id": request_id,
        "capability": capability,
        "input": data,
        "context": {
            "output_dir": str(output_dir),
            "temp_dir": str(temp_dir),
            "timeout_s": timeout_s,
            "locale": cfg.get("locale") or "zh-CN",
            "mode": mode,
            "raw_input_text": raw_text,
            "proxy": proxy,
            "max_output_bytes": max_bytes,
            "client_cwd": str(Path.cwd()),
            "debug": _debug_enabled(),
            "debug_log": str(logs_dir() / "debug.jsonl") if _debug_enabled() else None,
        },
    }
    requested_env = _requested_env(provision.requires)
    env = child_env(skill.id, requested_env, proxy=proxy if isinstance(proxy, str) else None, store=store)
    if _debug_enabled():
        env["SKILLROUTER_DEBUG"] = os.environ.get("SKILLROUTER_DEBUG", "1")
    _debug_grant(skill.id, env, requested_env)
    try:
        depth = int(os.environ.get("SKILLROUTER_CALL_DEPTH") or "0")
    except ValueError:
        depth = 0
    env["SKILLROUTER_CALL_DEPTH"] = str(depth + 1)
    if "SKILLROUTER_SKILL_PATHS" not in env:
        env["SKILLROUTER_SKILL_PATHS"] = os.pathsep.join(str(item) for item in discovery_roots())
    entry = provision.entrypoint
    kind = str(entry.get("type") or "command")
    if kind == "command":
        argv = entry.get("command")
        if not isinstance(argv, list) or not argv:
            return _attempt_error(skill, started, output_dir, temp_dir, code_with("default"), "entrypoint command is empty")
        proc = run_command([str(part) for part in argv], cwd=skill.directory, request=request, env=env, timeout_s=timeout_s)
    elif kind == "http":
        third_party = "third_party_service" in provision.side_effects
        proc = run_http(str(entry.get("url") or ""), request, timeout_s=timeout_s, third_party=third_party)
    else:
        dep = _dependency_code()
        return _attempt_error(
            skill,
            started,
            output_dir,
            temp_dir,
            dep,
            f"entrypoint type {kind} is not supported",
        )
    return _interpret(skill, provision, doc, data, proc, started, output_dir, temp_dir, max_bytes)


def _timeout_outcome(stderr: str) -> tuple[str, str]:
    """A kill during model download is a missing local dependency, not a bare timeout."""
    text = stderr or ""
    if "skillrouter: model-fetch" not in text:
        return code_with("router_timeout"), "skill exceeded timeout_s"
    hint = ""
    prefix = "skillrouter: model-fetch-hint:"
    for line in text.splitlines():
        if line.startswith(prefix):
            hint = line[len(prefix) :].strip()
    message = hint or (
        "model download did not finish before the call timeout. "
        "Set HF_ENDPOINT=https://hf-mirror.com or run skillrouter warmup."
    )
    return _dependency_code(), message[:500]


def _dependency_code() -> str:
    for name, policy in code_table().items():
        if isinstance(policy, dict) and policy.get("exit_code"):
            return name
    return code_with("default")


def _attempt_error(skill: Skill, started: float, output_dir: Path, temp_dir: Path, code: str, message: str) -> Attempt:
    return Attempt(
        skill_id=skill.id,
        version=skill.version,
        ok=False,
        code=code,
        message=message[:500],
        latency_ms=int((time.perf_counter() - started) * 1000),
        output_dir=output_dir,
        temp_dir=temp_dir,
    )


def _interpret(skill, provision, doc, data, proc, started, output_dir, temp_dir, max_bytes) -> Attempt:
    latency_ms = int((time.perf_counter() - started) * 1000)
    stderr = redact((proc.stderr or ""))[-4096:]
    if proc.timed_out:
        code, message = _timeout_outcome(stderr)
        return Attempt(
            skill.id,
            skill.version,
            False,
            code,
            message,
            latency_ms,
            output_dir=output_dir,
            temp_dir=temp_dir,
            stderr=stderr,
        )
    payload = _parse_stdout(proc.stdout)
    dep = _dependency_code()
    default = code_with("default")
    if payload is None:
        if proc.spawn_error or proc.returncode == int(code_table()[dep].get("exit_code") or 3):
            return Attempt(
                skill.id,
                skill.version,
                False,
                dep,
                (proc.spawn_error or proc.stderr or "dependency missing")[:500],
                latency_ms,
                output_dir=output_dir,
                temp_dir=temp_dir,
                stderr=stderr,
            )
        return Attempt(
            skill.id,
            skill.version,
            False,
            default,
            (proc.stderr or "stdout was not a JSON object")[:500],
            latency_ms,
            output_dir=output_dir,
            temp_dir=temp_dir,
            stderr=stderr,
        )
    if payload.get("ok") is True:
        output = payload.get("output")
        if not isinstance(output, dict):
            return Attempt(skill.id, skill.version, False, default, "output was not an object", latency_ms, output_dir=output_dir, temp_dir=temp_dir, stderr=stderr)
        schema_error = _schema_message(doc["output_schema"], output)
        if schema_error:
            return Attempt(skill.id, skill.version, False, default, schema_error, latency_ms, output_dir=output_dir, temp_dir=temp_dir, stderr=stderr)
        file_error = _check_files(output_dir, output, max_bytes) if output.get("files") else None
        if file_error:
            return Attempt(skill.id, skill.version, False, default, file_error, latency_ms, output_dir=output_dir, temp_dir=temp_dir, stderr=stderr)
        rule_code = _output_rule_failure(doc.get("output_rules"), data, output)
        if rule_code:
            mapped, policy = policy_for(rule_code)
            return Attempt(
                skill.id,
                skill.version,
                False,
                mapped,
                "output did not meet capability rules",
                latency_ms,
                output=output if policy.get("keep_degraded") else None,
                output_dir=output_dir,
                temp_dir=temp_dir,
                stderr=stderr,
                details={"output": output} if policy.get("keep_degraded") else {},
            )
        output = redact_tree(output, known_secrets())
        if not isinstance(output, dict):
            output = {}
        return Attempt(skill.id, skill.version, True, None, "", latency_ms, output=output, output_dir=output_dir, temp_dir=temp_dir, stderr=stderr)
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    code = str(error.get("code") or "")
    mapped, policy = policy_for(code)
    if policy_for(code)[0] != code:
        mapped, policy = policy_for(code)
    original = code_table().get(code)
    if isinstance(original, dict) and original.get("if_from_skill"):
        mapped, policy = policy_for(str(original["if_from_skill"]))
    elif code not in code_table():
        mapped, policy = policy_for(default)
    retry = error.get("retry_after_s")
    retry_after = float(retry) if isinstance(retry, (int, float)) and not isinstance(retry, bool) else None
    message = redact(str(error.get("message") or ""))[:500]
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    if isinstance(details, dict):
        details = redact_tree(details)
        if not isinstance(details, dict):
            details = {}
    degraded_output = None
    if policy.get("keep_degraded"):
        candidate = details.get("output")
        if not isinstance(candidate, dict) and isinstance(details.get("files"), list):
            candidate = {"meta": details.get("meta") or {}, "files": details.get("files")}
        if isinstance(candidate, dict):
            file_error = _check_files(output_dir, candidate, max_bytes)
            schema_error = _schema_message(doc["output_schema"], candidate)
            if file_error is None and schema_error is None:
                degraded_output = candidate
    return Attempt(
        skill.id,
        skill.version,
        False,
        mapped,
        message,
        latency_ms,
        output=degraded_output,
        output_dir=output_dir,
        temp_dir=temp_dir,
        stderr=stderr,
        retry_after_s=retry_after,
        details=details,
    )


def _registry_deprecated(capability: str) -> bool:
    name, version = capability.split("@", 1)
    doc = load_registry()["capabilities"].get((name, int(version))) or {}
    return doc.get("status") == "deprecated"


def _hints(attempts: list[Attempt], skipped: list[dict], *, degraded: bool) -> list[str]:
    hints: list[str] = []
    auth = []
    deps = []
    for item in skipped:
        if item.get("reason") == "NEED_AUTH" and item.get("hint"):
            auth.append(str(item["hint"]))
        if item.get("reason") == "DEPENDENCY_MISSING" and item.get("hint"):
            deps.append(f"{item.get('skill')}: {item['hint']}")
        if item.get("reason") == "PAID_NOT_ALLOWED":
            hints.append(f"{item.get('skill')} 是付费或计量 Skill，允许 allow_paid 之后才会调用")
    if auth:
        hints.append("有 Skill 因为缺少已授权的凭证被跳过：" + "；".join(auth))
    if deps:
        hints.append("有 Skill 未就绪：" + "；".join(deps))
    for attempt in attempts:
        policy = policy_for(attempt.code)[1] if attempt.code else {}
        if policy.get("mark") == "not_ready" and attempt.message:
            hints.append(f"{attempt.skill_id} 缺少依赖：{attempt.message}")
        if attempt.code and policy.get("score") == 0.0:
            hints.append(f"{attempt.skill_id} 返回 {attempt.code}，实现可能需要更新")
        if policy.get("record_network"):
            hints.append(f"{attempt.skill_id} 被网络环境拒绝（{attempt.code}）")
    if degraded:
        hints.append("没有 Skill 达到画质或封装要求，返回了现有结果里最好的一份")
    # preserve order, drop duplicates
    unique = []
    for hint in hints:
        if hint not in unique:
            unique.append(hint)
    return unique


def _debug_enabled() -> bool:
    return os.environ.get("SKILLROUTER_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}


def _debug_grant(skill_id: str, env: dict[str, str], requested: list[str]) -> None:
    """Record which granted credentials reached the child. Lengths only."""
    if not _debug_enabled():
        return
    present = {}
    missing = []
    for name in requested:
        value = env.get(name)
        if value:
            present[name] = {"present": True, "length": len(value)}
        else:
            missing.append(name)
    record = {
        "ts": _iso(),
        "source": "router",
        "skill": skill_id,
        "credentials": present,
        "missing": missing,
    }
    path = logs_dir() / "debug.jsonl"
    line = redact(json.dumps(record, ensure_ascii=False), known_secrets())
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _cred_fingerprint(skill_id: str, requires: dict, store: dict) -> str:
    """Hash of this skill's granted credential values. The values themselves are not stored."""
    parts = []
    for name in sorted(set(_requested_env(requires))):
        if name not in granted_names(skill_id, store):
            parts.append(name + "\tunset")
            continue
        value = credential_value(name, store) or ""
        parts.append(name + "\t" + hashlib.sha256(value.encode("utf-8")).hexdigest())
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _closed_circuit() -> dict[str, Any]:
    return {"state": "closed", "open_until": 0, "failures": [], "cooldown_until": 0, "duration_s": healthmod.OPEN_S}


def _remember_cred_hash(state: dict, key: str, fingerprint: str) -> None:
    bucket = dict(state["health"].get(key) or {})
    bucket["cred_hash"] = fingerprint
    state["health"][key] = bucket


def _maybe_revive(state: dict, key: str, skill: Skill, provision, store: dict) -> None:
    """Re-open a bench when the grant, the skill version, or a pre-fingerprint record changed."""
    author = bool(provision.deprecated or provision.superseded_by)
    registry = _registry_deprecated(provision.capability)
    if author or registry:
        return
    fingerprint = _cred_fingerprint(skill.id, provision.requires, store)
    bucket = state["health"].get(key) if isinstance(state["health"].get(key), dict) else {}
    now = time.time()
    benched = _bench_blocks(state, key, now)
    missing = "cred_hash" not in bucket
    changed = bucket.get("cred_hash") != fingerprint
    auth_stuck = bucket.get("last_code") == "NEED_AUTH"
    version_old = bucket.get("skill_version") != skill.version
    if not (benched and (missing or changed or auth_stuck or version_old)) and not missing and not changed:
        return

    def mutate(disk: dict) -> None:
        current = disk["health"].get(key) if isinstance(disk["health"].get(key), dict) else {}
        disk_benched = _bench_blocks(disk, key, time.time())
        disk_missing = "cred_hash" not in current
        disk_changed = current.get("cred_hash") != fingerprint
        disk_auth = current.get("last_code") == "NEED_AUTH"
        disk_version = current.get("skill_version") != skill.version
        if disk_benched and (disk_missing or disk_changed or disk_auth or disk_version):
            _unbench(disk, key)
        if disk_missing or disk_changed:
            _remember_cred_hash(disk, key, fingerprint)

    update_state(mutate)
    fresh = load_state()
    state.clear()
    state.update(fresh)


def canonicalize_output(output: dict) -> dict:
    """Replace page_url using the platform registry template. No platform name is hardcoded."""
    if not isinstance(output, dict):
        return output
    meta = output.get("meta")
    if not isinstance(meta, dict):
        return output
    platform = meta.get("platform")
    doc = load_registry()["platforms"].get(platform) if isinstance(platform, str) else None
    if not isinstance(doc, dict):
        return output
    template = doc.get("canonical_page")
    if not isinstance(template, str) or "{id}" not in template:
        return output
    note_id = meta.get("id") if isinstance(meta.get("id"), str) else ""
    pattern = doc.get("id_from_url")
    if not note_id and isinstance(pattern, str):
        for field in ("page_url", "source_url"):
            value = meta.get(field)
            if isinstance(value, str):
                match = re.search(pattern, value)
                if match:
                    note_id = match.group(1)
                    break
    if not note_id:
        return output
    page = template.replace("{id}", note_id)
    meta = dict(meta)
    meta["id"] = note_id
    meta["page_url"] = page
    if "source_url" in meta:
        meta["source_url"] = page
    output = dict(output)
    output["meta"] = meta
    return output


def _log_call(record: dict, secrets: list[str]) -> None:
    if load_config().get("log_raw_urls"):
        pass
    day = time.strftime("%Y%m%d", time.gmtime())
    path = logs_dir() / f"calls-{day}.jsonl"
    line = redact(json.dumps(record, ensure_ascii=False), secrets)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _url_hash(url: str | None) -> str | None:
    if not url:
        return None
    return "sha256:" + hashlib.sha256(url.encode("utf-8")).hexdigest()


def list_capabilities() -> dict[str, Any]:
    skills, errors = discover()
    reconcile_skills(skills)
    state = load_state()
    grouped: dict[str, dict[str, Any]] = {}
    registry = load_registry()
    for (name, version), doc in sorted(registry["capabilities"].items(), key=lambda item: (item[0][0], item[0][1])):
        if doc.get("status") == "deprecated":
            continue
        grouped[capability_key(name, version)] = {
            "id": capability_key(name, version),
            "summary": doc.get("summary") or "",
            "status": doc.get("status") or "stable",
            "platforms": {},
        }
    for skill in skills:
        for provision in skill.provides:
            bucket = grouped.get(provision.capability)
            if bucket is None:
                continue
            author_deprecated = bool(provision.deprecated or provision.superseded_by)
            registry_deprecated = _registry_deprecated(provision.capability)
            for platform in provision.platforms:
                if platform in _GENERIC_PLATFORM:
                    continue
                view = _bucket_view(
                    state,
                    healthmod.health_key(skill.id, provision.capability, platform),
                    author_deprecated=author_deprecated,
                    registry_deprecated=registry_deprecated,
                )
                slot = bucket["platforms"].setdefault(
                    platform,
                    {
                        "id": platform,
                        "name": (registry["platforms"].get(platform) or {}).get("name") or platform,
                        "skills": [],
                    },
                )
                slot["skills"].append(
                    {
                        "id": skill.id,
                        "version": skill.version,
                        "name": skill.name,
                        "cost": provision.cost,
                        "health": view["grade"],
                        "score": view["score"],
                        "needs_env": [str(item) for item in (provision.requires.get("env") or [])],
                        "optional_env": [str(item) for item in (provision.requires.get("optional_env") or [])],
                        "side_effects": provision.side_effects,
                    }
                )
    capabilities = []
    for item in grouped.values():
        platforms = []
        for platform in item["platforms"].values():
            ranks = [_GRADE_RANK.get(skill["health"], 9) for skill in platform["skills"] if skill["health"] != "deprecated"]
            platform["health"] = (
                min(platform["skills"], key=lambda skill: _GRADE_RANK.get(skill["health"], 9))["health"]
                if ranks
                else "unknown"
            )
            platforms.append(platform)
        item["platforms"] = platforms
        capabilities.append(item)
    return {"ok": True, "capabilities": capabilities, "discovery_errors": errors}


def describe_capability(capability: str) -> dict[str, Any]:
    try:
        name, version, doc = resolve_capability(capability)
    except RegistryError as exc:
        return _fail(capability, code_with("precheck"), str(exc))
    examples = []
    for example in doc.get("examples") or []:
        examples.append({"name": example["name"], "body": example["body"]})
    return {
        "ok": True,
        "capability": capability_key(name, version),
        "summary": doc.get("summary") or "",
        "status": doc.get("status") or "",
        "compliance": doc.get("compliance") or "",
        "input_schema": doc["input_schema"],
        "output_schema": doc["output_schema"],
        "examples": examples,
    }


def _select(capability: str, platform: str | None, url: str | None, *, allow_paid: bool, yes: bool):
    skills, errors = discover()
    reconcile_skills(skills)
    state = load_state()
    store = load_credentials()
    cfg = load_config()
    disabled = {str(item) for item in (cfg.get("disabled") or [])}
    runnable = []
    skipped = []
    pending_confirm = []
    for skill in skills:
        for provision in skill.provides:
            if provision.capability != capability:
                continue
            if not _platform_matches(provision.platforms, platform):
                continue
            if not _patterns_match(provision.url_patterns, url):
                skipped.append({"skill": skill.id, "reason": "URL_PATTERN", "hint": "url_patterns did not match"})
                continue
            if skill.id in disabled:
                skipped.append({"skill": skill.id, "reason": "DISABLED", "hint": "disabled in config"})
                continue
            if cfg.get("disable_third_party") and "third_party_service" in provision.side_effects:
                skipped.append({"skill": skill.id, "reason": "THIRD_PARTY_DISABLED", "hint": "third_party_service is disabled"})
                continue
            if not _cost_allowed(provision.cost, allow_paid):
                skipped.append({"skill": skill.id, "reason": "PAID_NOT_ALLOWED", "hint": provision.cost})
                continue
            missing_bins = _missing_binaries(provision.requires)
            if missing_bins:
                skipped.append(
                    {
                        "skill": skill.id,
                        "reason": "DEPENDENCY_MISSING",
                        "hint": "缺少可执行文件: " + ", ".join(missing_bins),
                    }
                )
                continue
            kind = str(provision.entrypoint.get("type") or "command")
            if kind not in {"command", "http"}:
                skipped.append(
                    {
                        "skill": skill.id,
                        "reason": "DEPENDENCY_MISSING",
                        "hint": f"entrypoint type {kind} is not supported",
                    }
                )
                continue
            missing_env = _missing_required(skill.id, provision.requires, store)
            if missing_env:
                blocked = [name for name in missing_env if blacklisted(name, store.get("overrides") or [])]
                hint = "需要授权环境变量: " + ", ".join(missing_env)
                if blocked:
                    hint += "（其中被黑名单拦住: " + ", ".join(blocked) + "）"
                skipped.append({"skill": skill.id, "reason": "NEED_AUTH", "hint": hint})
                continue
            author_deprecated = bool(provision.deprecated or provision.superseded_by)
            registry_deprecated = _registry_deprecated(provision.capability)
            key = healthmod.health_key(skill.id, provision.capability, platform or "")
            _maybe_revive(state, key, skill, provision, store)
            view = _bucket_view(
                state,
                key,
                author_deprecated=author_deprecated,
                registry_deprecated=registry_deprecated,
            )
            if view["grade"] == "deprecated":
                skipped.append({"skill": skill.id, "reason": "DEPRECATED", "hint": provision.superseded_by or "deprecated"})
                continue
            if view["circuit"] == "open":
                skipped.append({"skill": skill.id, "reason": "CIRCUIT_OPEN", "hint": "circuit open"})
                continue
            if view["circuit"] == "cooldown":
                skipped.append({"skill": skill.id, "reason": "COOLDOWN", "hint": "cooldown"})
                continue
            if not yes and not _trusted(state, skill):
                pending_confirm.append(_confirm_view(skill, provision))
                skipped.append(
                    {
                        "skill": skill.id,
                        "reason": "NEED_CONFIRM",
                        "hint": "新安装或升级过的 Skill 需要确认",
                        "confirm": _confirm_view(skill, provision),
                    }
                )
                continue
            priority = int((cfg.get("priority") or {}).get(skill.id, provision.priority))
            runnable.append(
                {
                    "skill": skill,
                    "provision": provision,
                    "half_open": view["circuit"] == "half_open",
                    "grade": view["grade"],
                    "score": view["score"] if view["score"] is not None else -1,
                    "latency": view["latency"],
                    "priority": priority,
                    "samples": int(view.get("samples") or 0),
                    "author_deprecated": author_deprecated,
                    "registry_deprecated": registry_deprecated,
                }
            )
    return runnable, skipped, pending_confirm, errors, store


def _cost_allowed(cost: str, allow_paid: bool) -> bool:
    if cost == "free":
        return True
    if cost in {"paid", "metered"}:
        return allow_paid
    return allow_paid


def _generic_coverage(platforms: list[str]) -> bool:
    """Many named platforms, or a wildcard, means this provision is a catch-all."""
    names = [str(item) for item in platforms]
    if any(item in _GENERIC_PLATFORM for item in names):
        return True
    return len([item for item in names if item not in _GENERIC_PLATFORM]) > 1


def _platform_matches(platforms: list[str], platform: str | None) -> bool:
    if not platform:
        return False
    if any(item in _GENERIC_PLATFORM for item in platforms):
        return True
    return platform in platforms


def _clearly_bad(samples: int, score: float | None) -> bool:
    """Unknown health is not bad. A low score needs at least three samples."""
    if samples < _CLEARLY_BAD_SAMPLES or score is None or score < 0:
        return False
    return float(score) < _CLEARLY_BAD_SCORE


def _coverage_rank(item: dict) -> int:
    generic = _generic_coverage(item["provision"].platforms)
    bad = _clearly_bad(int(item.get("samples") or 0), item.get("score"))
    if not generic and not bad:
        return 0
    if generic:
        return 1
    return 2


def _quality_rank(item: dict) -> int:
    """Resolve-only tiebreaker. rich beats basic. Other capabilities ignore the hint."""
    name = str(item["provision"].capability).split("@", 1)[0]
    if not name.endswith(".resolve"):
        return 0
    hint = getattr(item["provision"], "metadata", "") or ""
    if hint == "rich":
        return 0
    if hint == "basic":
        return 1
    return 1


def _rank(runnable: list[dict], *, prefer: str | None) -> list[dict]:
    cfg = load_config()
    pin = [str(item) for item in (cfg.get("pin") or [])]

    def key(item: dict) -> tuple:
        skill = item["skill"]
        pin_index = pin.index(skill.id) if skill.id in pin else len(pin) + 1
        latency = item["latency"] if item["latency"] is not None else 10**18
        return (
            0 if prefer and skill.id == prefer else 1,
            pin_index,
            _COST_RANK.get(item["provision"].cost, 2),
            _coverage_rank(item),
            1 if item["half_open"] else 0,
            _GRADE_RANK.get(item["grade"], 9),
            -float(item["score"] if item.get("score") is not None else -1),
            float(latency),
            -int(item["priority"]),
            _quality_rank(item),
            skill.id,
        )

    return sorted(runnable, key=key)


def invoke(capability: str, data: dict | None = None, *, options: dict | None = None, yes: bool = False) -> dict[str, Any]:
    options = dict(options or {})
    if os.environ.get("SKILLROUTER_YES") == "1":
        yes = True
    try:
        call_depth = int(os.environ.get("SKILLROUTER_CALL_DEPTH") or "0")
    except ValueError:
        call_depth = 0
    if call_depth >= 3:
        return _fail(str(capability), code_with("default"), "skillcall nesting is too deep")
    data = dict(data or {})
    try:
        name, version, doc = resolve_capability(capability)
    except RegistryError as exc:
        return _fail(str(capability), code_with("precheck"), str(exc))
    full_name = capability_key(name, version)
    schema_error = _schema_message(doc["input_schema"], data)
    if schema_error:
        return _fail(full_name, code_with("precheck"), schema_error)
    prepared, platform, raw_text, route_code = _prepare_input(doc, data)
    if route_code:
        message = "没有匹配到平台，也没有 Skill 可以接这条输入"
        return _fail(full_name, route_code, message)
    url = None
    routing = doc.get("routing") or {}
    if isinstance(routing, dict) and routing.get("url_field"):
        pointed = pointer_get(prepared, routing.get("url_field"))
        if isinstance(pointed, str):
            url = pointed
    cfg = load_config()
    allow_paid = bool(options.get("allow_paid", cfg.get("allow_paid")))
    prefer = options.get("prefer_skill")
    runnable, skipped, pending_confirm, discovery_errors, store = _select(
        full_name, platform, url, allow_paid=allow_paid, yes=yes
    )
    ranked = _rank(runnable, prefer=str(prefer) if prefer else None)
    max_attempts = int(options.get("max_attempts") or cfg.get("max_attempts") or 4)
    deadline = time.monotonic() + float(cfg.get("max_total_s") or 300)
    attempts: list[Attempt] = []
    degraded: list[Attempt] = []
    confirm_counts: dict[str, int] = {}
    stop_code: str | None = None
    quality_codes = {code for code, policy in code_table().items() if isinstance(policy, dict) and policy.get("keep_degraded")}
    for item in ranked:
        if len(attempts) >= max_attempts or stop_code:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        skill = item["skill"]
        provision = item["provision"]
        if yes:
            _remember_trust(skill)
        timeout_s = min(float(provision.timeout_s), remaining)
        attempt = _execute(
            skill,
            provision,
            capability=full_name,
            doc=doc,
            data=prepared,
            platform=platform,
            raw_text=raw_text,
            mode="invoke",
            timeout_s=timeout_s,
            store=store,
        )
        attempt.message = redact(attempt.message, known_secrets(store))
        attempts.append(attempt)
        _remember(
            skill,
            full_name,
            platform or "",
            ok=attempt.ok,
            code=attempt.code,
            latency_ms=attempt.latency_ms,
            source="invoke",
            retry_after_s=attempt.retry_after_s,
            author_deprecated=item["author_deprecated"],
            registry_deprecated=item["registry_deprecated"],
            count_doctor=False,
        )
        if attempt.ok and attempt.output is not None:
            return _finish_success(
                full_name,
                attempt,
                attempts,
                skipped,
                degraded,
                options,
                platform,
                url,
                discovery_errors,
                store,
                degraded_result=False,
            )
        policy = policy_for(attempt.code or "")[1]
        if policy.get("keep_degraded") and attempt.output is not None:
            degraded.append(attempt)
        else:
            _rm(attempt.temp_dir)
            _rm(attempt.output_dir)
            _rm(attempt.output_dir.parent if attempt.output_dir else None)
        limit = policy.get("confirm_different")
        if isinstance(limit, int) and attempt.code:
            confirm_counts[attempt.code] = confirm_counts.get(attempt.code, 0) + 1
            if confirm_counts[attempt.code] > limit:
                stop_code = attempt.code
                break
        if not policy.get("fallback", True):
            stop_code = attempt.code
            break
    if (
        not stop_code
        and degraded
        and attempts
        and all((not item.ok) and item.code in quality_codes for item in attempts)
    ):
        best = max(degraded, key=lambda item: _degraded_key(item.output or {}))
        return _finish_success(
            full_name,
            best,
            attempts,
            skipped,
            [item for item in degraded if item is not best],
            options,
            platform,
            url,
            discovery_errors,
            store,
            degraded_result=True,
        )
    for item in degraded:
        _rm(item.temp_dir)
        _rm(item.output_dir)
        _rm(item.output_dir.parent if item.output_dir else None)
    if stop_code and stop_code == _code_by_class("not_found"):
        result = _fail(
            full_name,
            stop_code,
            "多个实现都认为内容不存在",
            attempts=[item.public() for item in attempts],
            skipped=skipped,
            hints=_hints(attempts, skipped, degraded=False),
        )
    elif not attempts and any(item.get("reason") == "NEED_CONFIRM" for item in skipped):
        result = _fail(
            full_name,
            "NEED_CONFIRM",
            "有新安装或升级过的 Skill 需要确认后才能执行",
            attempts=[],
            skipped=skipped,
            hints=["加上 --yes，或在交互环境里确认 id、作者、side_effects 和 requires"],
            details={"skills": pending_confirm},
        )
    else:
        result = _fail(
            full_name,
            "ALL_FAILED",
            "没有 Skill 成功完成这次调用",
            attempts=[item.public() for item in attempts],
            skipped=skipped,
            hints=_hints(attempts, skipped, degraded=False),
        )
    result["discovery_errors"] = discovery_errors
    _log_call(
        {
            "ts": _iso(),
            "capability": full_name,
            "platform": platform,
            "url_hash": _url_hash(url),
            "attempts": [item.public() for item in attempts],
            "result": result["error"]["code"],
        },
        known_secrets(store),
    )
    _maybe_lazy(full_name, platform)
    return result


def _finish_success(
    capability: str,
    winner: Attempt,
    attempts: list[Attempt],
    skipped: list[dict],
    other_degraded: list[Attempt],
    options: dict,
    platform: str | None,
    url: str | None,
    discovery_errors: list[str],
    store: dict,
    *,
    degraded_result: bool,
) -> dict[str, Any]:
    save_to = options.get("save_to")
    dest = Path(str(save_to)).expanduser() if save_to else downloads_dir()
    output = winner.output or {}
    if output.get("files") and winner.output_dir is not None:
        output = _relocate(output, winner.output_dir, dest)
    _rm(winner.temp_dir)
    _rm(winner.output_dir)
    if winner.output_dir is not None:
        _rm(winner.output_dir.parent)
    for item in other_degraded:
        _rm(item.temp_dir)
        _rm(item.output_dir)
        if item.output_dir is not None:
            _rm(item.output_dir.parent)
    for item in attempts:
        if item is winner:
            continue
        _rm(item.temp_dir)
        _rm(item.output_dir)
    hints = _hints(attempts, skipped, degraded=degraded_result)
    secrets = known_secrets(store)
    cleaned_output = redact_tree(output, secrets)
    if not isinstance(cleaned_output, dict):
        cleaned_output = {}
    else:
        cleaned_output = canonicalize_output(cleaned_output)
    result = {
        "ok": True,
        "capability": capability,
        "output": cleaned_output,
        "served_by": {"skill": winner.skill_id, "version": winner.version},
        "attempts": [item.public() for item in attempts],
        "skipped": skipped,
        "hints": hints,
        "discovery_errors": discovery_errors,
    }
    result = redact_tree(result, secrets)
    if not isinstance(result, dict):
        result = {"ok": True, "capability": capability, "output": cleaned_output}
    _log_call(
        {
            "ts": _iso(),
            "capability": capability,
            "platform": platform,
            "url_hash": _url_hash(url),
            "attempts": [item.public() for item in attempts],
            "result": "degraded" if degraded_result else "ok",
            "output_meta": cleaned_output.get("meta") if isinstance(cleaned_output.get("meta"), dict) else None,
            "stderr": [redact(item.stderr, secrets) for item in attempts if item.stderr],
        },
        secrets,
    )
    _maybe_lazy(capability, platform)
    return result


def _maybe_lazy(capability: str, platform: str | None) -> None:
    if os.environ.get("SKILLROUTER_LAZY_DOCTOR", "1") == "0":
        return
    if os.environ.get("SKILLROUTER_IN_DOCTOR") == "1":
        return
    if not load_config().get("lazy_doctor", True):
        return
    if not platform:
        return
    threading.Thread(target=_lazy_worker, args=(capability, platform), daemon=True).start()


def _lazy_worker(capability: str, platform: str) -> None:
    os.environ["SKILLROUTER_IN_DOCTOR"] = "1"
    try:
        skills, _errors = discover()
        state = load_state()
        now = time.time()
        for skill in skills:
            for provision in skill.provides:
                if provision.capability != capability or not _platform_matches(provision.platforms, platform):
                    continue
                key = healthmod.health_key(skill.id, capability, platform)
                view = _bucket_view(
                    state,
                    key,
                    author_deprecated=bool(provision.deprecated),
                    registry_deprecated=_registry_deprecated(capability),
                )
                interval = float(provision.health_check.get("interval_h") or 12) * 3600
                if view["grade"] in {"degraded", "failing"} and now - view["last_doctor_ts"] >= interval:
                    doctor(yes=True, skill_id=skill.id, capability=capability, platform=platform)
    finally:
        os.environ.pop("SKILLROUTER_IN_DOCTOR", None)


def warmup(skill_id: str | None = None) -> dict[str, Any]:
    """Run Skill-declared model downloads outside the invoke deadline."""
    skills, errors = discover()
    store = load_credentials()
    cfg = load_config()
    proxy = cfg.get("proxy")
    results = []
    for skill in skills:
        if skill_id and skill.id != skill_id:
            continue
        spec = skill.warmup if isinstance(skill.warmup, dict) else None
        if not spec:
            continue
        provision = next((item for item in skill.provides if str((item.entrypoint or {}).get("type") or "command") == "command"), None)
        if provision is None:
            results.append({"skill": skill.id, "ok": False, "code": "DEPENDENCY_MISSING", "message": "warmup needs a command entrypoint"})
            continue
        timeout_s = float(spec.get("timeout_s") or 900)
        output_dir = runs_dir() / ("warmup_" + uuid.uuid4().hex) / "out"
        output_dir.mkdir(parents=True, exist_ok=True)
        request = {
            "skillcall": "0.1",
            "request_id": "warmup",
            "capability": provision.capability,
            "input": {},
            "context": {
                "output_dir": str(output_dir),
                "temp_dir": str(output_dir.parent / "tmp"),
                "timeout_s": timeout_s,
                "mode": "setup",
                "client_cwd": str(Path.cwd()),
            },
        }
        env = child_env(
            skill.id,
            _requested_env(provision.requires),
            proxy=proxy if isinstance(proxy, str) else None,
            store=store,
        )
        env["SKILLROUTER_CALL_DEPTH"] = "1"
        if "SKILLROUTER_SKILL_PATHS" not in env:
            env["SKILLROUTER_SKILL_PATHS"] = os.pathsep.join(str(item) for item in discovery_roots())
        argv = provision.entrypoint.get("command")
        proc = run_command(
            [str(part) for part in argv] if isinstance(argv, list) else [],
            cwd=skill.directory,
            request=request,
            env=env,
            timeout_s=timeout_s,
        )
        payload = _parse_stdout(proc.stdout)
        if proc.timed_out:
            code, message = _timeout_outcome(proc.stderr or "")
        elif isinstance(payload, dict) and payload.get("ok") is True:
            code, message = "", ""
        elif isinstance(payload, dict):
            error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
            code = str(error.get("code") or code_with("default"))
            message = redact(str(error.get("message") or ""))[:500]
        else:
            code, message = code_with("default"), (proc.stderr or "stdout was not a JSON object")[:500]
        results.append(
            {
                "skill": skill.id,
                "ok": code == "",
                "code": code or None,
                "message": message,
                "latency_ms": None,
            }
        )
        _rm(output_dir)
        _rm(output_dir.parent)
    if skill_id and not results and not errors:
        results.append({"skill": skill_id, "ok": False, "code": "NOT_FOUND", "message": "no warmup declared for this skill"})
    return {"ok": not errors and all(item.get("ok") for item in results), "results": results, "discovery_errors": errors}


def doctor(
    *,
    yes: bool = False,
    skill_id: str | None = None,
    capability: str | None = None,
    platform: str | None = None,
) -> dict[str, Any]:
    if os.environ.get("SKILLROUTER_YES") == "1":
        yes = True
    skills, errors = discover()
    reconcile_skills(skills)
    state = load_state()
    store = load_credentials()
    checks = []
    for skill in skills:
        if skill_id and skill.id != skill_id:
            continue
        for provision in skill.provides:
            if capability and provision.capability != capability:
                continue
            if (provision.health_check or {}).get("enabled") is False:
                continue
            try:
                _name, _version, doc = resolve_capability(provision.capability)
            except RegistryError:
                continue
            for plat in provision.platforms:
                if plat in _GENERIC_PLATFORM:
                    continue
                if platform and plat != platform:
                    continue
                inputs = _doctor_inputs(provision, plat)
                author_deprecated = bool(provision.deprecated or provision.superseded_by)
                registry_deprecated = _registry_deprecated(provision.capability)
                base = {
                    "skill": skill.id,
                    "skill_version": skill.version,
                    "capability": provision.capability,
                    "platform": plat,
                    "checked_at": _iso(),
                }
                if not inputs:
                    checks.append({**base, "ok": False, "code": "NO_CANARY", "latency_ms": 0})
                    continue
                if not yes and not _trusted(state, skill):
                    checks.append({**base, "ok": False, "code": "NEED_CONFIRM", "latency_ms": 0})
                    continue
                if yes:
                    _remember_trust(skill)
                not_found = _code_by_class("not_found")
                saw_not_found = 0
                chosen: Attempt | None = None
                for sample in inputs:
                    attempt = _execute(
                        skill,
                        provision,
                        capability=provision.capability,
                        doc=doc,
                        data=sample,
                        platform=plat,
                        raw_text=None,
                        mode="health_check",
                        timeout_s=float(provision.timeout_s),
                        store=store,
                    )
                    if attempt.code == not_found:
                        saw_not_found += 1
                        _rm(attempt.temp_dir)
                        _rm(attempt.output_dir)
                        if attempt.output_dir is not None:
                            _rm(attempt.output_dir.parent)
                        continue
                    chosen = attempt
                    break
                if chosen is None and saw_not_found == len(inputs):
                    checks.append({**base, "ok": False, "code": "CANARY_EXPIRED", "latency_ms": 0})
                    _touch_doctor(skill, provision.capability, plat)
                    continue
                if chosen is None:
                    checks.append({**base, "ok": False, "code": "NO_CANARY", "latency_ms": 0})
                    continue
                ok = chosen.ok
                code = chosen.code
                if ok and chosen.output is not None:
                    passed, failed_expr = all_expect(provision.health_check.get("expect"), chosen.output)
                    if not passed:
                        ok = False
                        code = code_with("default")
                        chosen = Attempt(
                            chosen.skill_id,
                            chosen.version,
                            False,
                            code,
                            f"expect failed: {failed_expr}",
                            chosen.latency_ms,
                            output_dir=chosen.output_dir,
                            temp_dir=chosen.temp_dir,
                        )
                _remember(
                    skill,
                    provision.capability,
                    plat,
                    ok=ok,
                    code=code,
                    latency_ms=chosen.latency_ms,
                    source="doctor",
                    retry_after_s=chosen.retry_after_s,
                    author_deprecated=author_deprecated,
                    registry_deprecated=registry_deprecated,
                    count_doctor=True,
                )
                checks.append(
                    {
                        **base,
                        "ok": ok,
                        "code": code,
                        "latency_ms": chosen.latency_ms,
                    }
                )
                _rm(chosen.temp_dir)
                _rm(chosen.output_dir)
                if chosen.output_dir is not None:
                    _rm(chosen.output_dir.parent)
    kind = os.environ.get("SKILLROUTER_NETWORK_KIND", "").strip() or (load_config().get("network") or {}).get("kind") or "unknown"
    if kind not in {"residential", "datacenter", "unknown"}:
        kind = "unknown"
    region = (load_config().get("network") or {}).get("region")
    report = {
        "schema_version": "skillcall.health.v1",
        "generated_at": _iso(),
        "router": {"name": "skill-router", "version": __version__},
        "network": {"kind": kind, "region": region},
        "checks": checks,
        "discovery_errors": errors,
    }
    cleaned = redact_tree(report)
    return cleaned if isinstance(cleaned, dict) else report


def _touch_doctor(skill: Skill, capability: str, platform: str) -> None:
    key = healthmod.health_key(skill.id, capability, platform)
    now = time.time()

    def mutate(state: dict) -> None:
        bucket = dict(state["health"].get(key) or {})
        bucket["last_doctor_ts"] = now
        bucket["content_hash"] = skill.content_hash
        bucket["skill_version"] = skill.version
        state["health"][key] = bucket

    update_state(mutate)


def _doctor_inputs(provision, platform: str) -> list[dict]:
    health = provision.health_check or {}
    inputs: list[dict] = []
    seen: set[str] = set()

    def add(item: dict) -> None:
        if not isinstance(item, dict):
            return
        detected = None
        url = item.get("url")
        if isinstance(url, str):
            detected = platform_for_url(extract_url(url) or url)
        explicit = item.get("platform")
        if isinstance(explicit, str):
            detected = explicit
        if detected not in (None, platform):
            return
        key = json.dumps(item, sort_keys=True, ensure_ascii=False)
        if key in seen:
            return
        seen.add(key)
        inputs.append(item)

    if health.get("use_platform_canaries"):
        for url in canary_urls(platform):
            add({"url": url})
    for sample in health.get("samples") or []:
        if isinstance(sample, dict) and isinstance(sample.get("input"), dict):
            add(dict(sample["input"]))
    return inputs


def restore(skill_id: str, capability: str | None = None, platform: str | None = None) -> dict[str, Any]:
    keys: list[str] | None = None
    if capability or platform:
        if not capability or not platform:
            return {
                "ok": False,
                "error": {"code": code_with("precheck"), "message": "pass capability and platform together, or only the skill id"},
            }
        full = capability
        try:
            name, version, _doc = resolve_capability(full)
            full = capability_key(name, version)
        except RegistryError as exc:
            return {"ok": False, "error": {"code": code_with("precheck"), "message": str(exc)}}
        keys = [healthmod.health_key(skill_id, full, platform)]

    def mutate(state: dict) -> None:
        chosen = keys
        if chosen is None:
            prefix = skill_id + "\t"
            found = []
            for bucket in ("deprecated", "circuits", "health", "failing_since", "user_restored"):
                for key in state.get(bucket) or {}:
                    if str(key).startswith(prefix) and key not in found:
                        found.append(str(key))
            chosen = found
        for key in chosen:
            state["user_restored"][key] = True
            _unbench(state, key)

    update_state(mutate)
    restored = keys
    if restored is None:
        prefix = skill_id + "\t"
        restored = [key for key in load_state()["user_restored"] if str(key).startswith(prefix)]
    return {"ok": True, "restored": [{"skill": skill_id, "key": key} for key in restored]}
