"""Per-skill credential grants. Declaring a name does not grant it."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from skillrouter.paths import credentials_path

_PREFIXES = ("AWS_", "GITHUB_", "SSH_", "OPENAI_", "ANTHROPIC_", "AZURE_")
_BASE_POSIX = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "SSL_CERT_FILE")
_BASE_WINDOWS = (
    "SystemRoot",
    "SYSTEMDRIVE",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
    "PATHEXT",
    "COMSPEC",
)
_PROXY = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


def load_credentials() -> dict:
    path = credentials_path()
    data = {"values": {}, "grants": {}, "overrides": []}
    if path.is_file():
        # utf-8-sig accepts Notepad's UTF-8 BOM. A BOM would otherwise
        # make json.loads fail and the grant would never reach the Skill.
        loaded = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(loaded, dict):
            if isinstance(loaded.get("values"), dict):
                data["values"] = {str(k): str(v) for k, v in loaded["values"].items()}
            if isinstance(loaded.get("grants"), dict):
                data["grants"] = {
                    str(skill): [str(name) for name in names]
                    for skill, names in loaded["grants"].items()
                    if isinstance(names, list)
                }
            if isinstance(loaded.get("overrides"), list):
                data["overrides"] = [str(name) for name in loaded["overrides"]]
    return data


def blacklisted(name: str, overrides: list[str]) -> bool:
    if name in overrides:
        return False
    if any(name.startswith(prefix) for prefix in _PREFIXES):
        return True
    if name.endswith("_API_KEY"):
        return True
    return False


def granted_names(skill_id: str, store: dict | None = None) -> list[str]:
    store = store if store is not None else load_credentials()
    names = list(store.get("grants", {}).get(skill_id) or [])
    return names


def credential_value(name: str, store: dict | None = None) -> str | None:
    store = store if store is not None else load_credentials()
    if name in store.get("values", {}):
        value = store["values"][name]
        return value if value != "" else None
    env_name = "SKILLROUTER_CRED_" + name
    raw = os.environ.get(env_name)
    if raw:
        return raw
    return None


def credential_available(skill_id: str, name: str, store: dict | None = None) -> bool:
    store = store if store is not None else load_credentials()
    if name not in granted_names(skill_id, store):
        return False
    if blacklisted(name, store.get("overrides") or []):
        return False
    return credential_value(name, store) is not None


def injection_map(skill_id: str, requested: list[str], store: dict | None = None) -> dict[str, str]:
    """Return name → value for this skill only. Ungranted and blacklisted names are absent."""
    store = store if store is not None else load_credentials()
    granted = set(granted_names(skill_id, store))
    overrides = store.get("overrides") or []
    found: dict[str, str] = {}
    for name in requested:
        if name not in granted or blacklisted(name, overrides):
            continue
        value = credential_value(name, store)
        if value:
            found[name] = value
    return found


# Router control variables a Skill may need when it calls skillrouter again.
# Credentials stay out of this list; nested invokes read the credentials file.
_ROUTER_PASSTHROUGH = (
    "PYTHONPATH",
    "SKILLROUTER_HOME",
    "SKILLROUTER_REGISTRY",
    "SKILLROUTER_SKILL_PATHS",
    "SKILLROUTER_CONFIG",
    "SKILLROUTER_YES",
    "SKILLROUTER_NETWORK_KIND",
    "SKILLROUTER_LAZY_DOCTOR",
    "SKILLROUTER_MAX_TOTAL_S",
    "SKILLROUTER_MAX_ATTEMPTS",
    "SKILLROUTER_DEBUG",
    "SKILLROUTER_MAX_OUTPUT_BYTES",
    "HF_ENDPOINT",
    "HF_HOME",
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "HF_HUB_ETAG_TIMEOUT",
    "HF_HUB_DOWNLOAD_TIMEOUT",
    "HF_HUB_DISABLE_TELEMETRY",
    "WHISPER_MODEL",
    "WHISPER_DEVICE",
    "WHISPER_COMPUTE_TYPE",
    "WHISPER_VAD",
    "WHISPER_INITIAL_PROMPT",
)


def child_env(skill_id: str, requested: list[str], *, proxy: str | None = None, store: dict | None = None) -> dict[str, str]:
    env: dict[str, str] = {}
    names = list(_BASE_POSIX)
    if os.name == "nt":
        names.extend(_BASE_WINDOWS)
    names.extend(_PROXY)
    names.extend(_ROUTER_PASSTHROUGH)
    for name in names:
        if os.environ.get(name):
            env[name] = os.environ[name]
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "0"
    if proxy:
        env["HTTP_PROXY"] = proxy
        env["HTTPS_PROXY"] = proxy
        env["http_proxy"] = proxy
        env["https_proxy"] = proxy
    env.update(injection_map(skill_id, requested, store))
    return env


def known_secrets(store: dict | None = None) -> list[str]:
    store = store if store is not None else load_credentials()
    secrets = [value for value in store.get("values", {}).values() if value]
    for key, value in os.environ.items():
        if key.startswith("SKILLROUTER_CRED_") and value:
            secrets.append(value)
    return secrets


_BEARER = re.compile(r"(?i)(bearer\s+)\S+")
# Query names that are secrets even when they do not contain "token" or "sign".
_EXACT_PARAMS = r"xsec_source|share_id|shareRedId|a_bogus|cookies"
# Also any name containing token or sign (xsec_token, msToken, access_token, signature, ...).
_PARAM_NAME = rf"(?:[A-Za-z0-9_.%\-]*?(?:token|sign)|{_EXACT_PARAMS})"
_PARAM_VALUE = r"(?:(?!%26)[^&\s\"'<>#\\])*"
_QUERY_PARAM = re.compile(rf"(?i)({_PARAM_NAME})(?:=|%3[Dd]|\\u003[dD])({_PARAM_VALUE})")
_JSON_PARAM = re.compile(rf'(?i)("(?:{_PARAM_NAME})"\s*:\s*")([^"]*)(")')


def _secret_pieces(secrets: list[str]) -> list[str]:
    """Whole credential values, plus each cookie value inside a Cookie header."""
    found: list[str] = []
    for secret in secrets:
        if not secret:
            continue
        if len(secret) >= 4:
            found.append(secret)
        if ";" not in secret or "=" not in secret:
            continue
        for part in secret.split(";"):
            if "=" not in part:
                continue
            value = part.split("=", 1)[1].strip().strip('"')
            if len(value) >= 8:
                found.append(value)
    found.sort(key=len, reverse=True)
    return found


def redact(text: str, secrets: list[str] | None = None) -> str:
    """Strip credential values and sensitive query parameters from a message."""
    if not text:
        return ""
    redacted = text
    for secret in _secret_pieces(secrets if secrets is not None else known_secrets()):
        redacted = redacted.replace(secret, "***")
    redacted = _BEARER.sub(r"\1***", redacted)
    redacted = _JSON_PARAM.sub(r"\1***\3", redacted)
    redacted = _QUERY_PARAM.sub(r"\1=***", redacted)
    return redacted


def redact_tree(value: object, secrets: list[str] | None = None) -> object:
    """Redact every string in a log, attempt, or health record."""
    if isinstance(value, str):
        return redact(value, secrets)
    if isinstance(value, list):
        return [redact_tree(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact_tree(item, secrets) for key, item in value.items()}
    return value


def save_credentials(data: dict) -> None:
    path: Path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
