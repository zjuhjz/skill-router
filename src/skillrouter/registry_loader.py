"""Load the skillcall registry from data files. No platform is named here."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import yaml


class RegistryError(RuntimeError):
    pass


def registry_dir() -> Path:
    raw = os.environ.get("SKILLROUTER_REGISTRY", "").strip()
    if raw:
        path = Path(raw).expanduser()
        if not (path / "platforms.yaml").is_file():
            raise RegistryError(f"registry missing platforms.yaml: {path}")
        return path
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "registry" / "platforms.yaml"
        if candidate.is_file():
            return candidate.parent
    bundled = here.parent / "bundled_registry" / "platforms.yaml"
    if bundled.is_file():
        return bundled.parent
    cwd = Path.cwd() / "registry" / "platforms.yaml"
    if cwd.is_file():
        return cwd.parent
    raise RegistryError("skillcall registry not found")


def _load_yaml(path: Path) -> dict:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise RegistryError(f"{path} must be a mapping")
    return data


@lru_cache(maxsize=1)
def load_registry() -> dict:
    root = registry_dir()
    platforms_doc = _load_yaml(root / "platforms.yaml")
    platforms = platforms_doc.get("platforms") or {}
    if not isinstance(platforms, dict):
        raise RegistryError("platforms.yaml: 'platforms' must be a mapping")
    errors_doc = _load_yaml(root / "error_codes.yaml")
    codes = errors_doc.get("codes") or {}
    if not isinstance(codes, dict) or not codes:
        raise RegistryError("error_codes.yaml has no codes")
    capabilities: dict[tuple[str, int], dict] = {}
    cap_root = root / "capabilities"
    if cap_root.is_dir():
        for yaml_path in sorted(cap_root.glob("*/*/capability.yaml")):
            doc = _load_yaml(yaml_path)
            cap_id = str(doc.get("id") or yaml_path.parent.parent.name)
            version = int(doc.get("version") or yaml_path.parent.name)
            folder = yaml_path.parent
            doc["id"] = cap_id
            doc["version"] = version
            doc["input_schema"] = json.loads((folder / "input.schema.json").read_text(encoding="utf-8"))
            doc["output_schema"] = json.loads((folder / "output.schema.json").read_text(encoding="utf-8"))
            examples = folder / "examples"
            doc["examples"] = []
            if examples.is_dir():
                for example in sorted(examples.glob("*.json")):
                    doc["examples"].append(
                        {"name": example.name, "body": json.loads(example.read_text(encoding="utf-8"))}
                    )
            capabilities[(cap_id, version)] = doc
    return {
        "root": root,
        "platforms": platforms,
        "codes": codes,
        "router_codes": errors_doc.get("router_codes") or {},
        "capabilities": capabilities,
    }


def clear_registry_cache() -> None:
    load_registry.cache_clear()


def code_table() -> dict:
    return load_registry()["codes"]


def code_with(flag: str) -> str:
    matches = [name for name, policy in code_table().items() if isinstance(policy, dict) and policy.get(flag)]
    if not matches:
        raise RegistryError(f"error code table has no entry with {flag}")
    return matches[0]


def policy_for(code: str) -> tuple[str, dict]:
    table = code_table()
    item = table.get(code)
    if isinstance(item, dict):
        return code, item
    default = code_with("default")
    return default, table[default]


def latest_stable(cap_id: str) -> tuple[str, int, dict] | None:
    found = []
    for (name, version), doc in load_registry()["capabilities"].items():
        if name == cap_id and doc.get("status") != "deprecated":
            found.append((version, doc))
    if not found:
        return None
    version, doc = max(found, key=lambda item: item[0])
    return cap_id, version, doc


def resolve_capability(requested: str) -> tuple[str, int, dict]:
    text = (requested or "").strip()
    if text.endswith("@") or not text:
        raise RegistryError(f"unknown capability: {requested}")
    if "@" in text:
        name, raw_version = text.split("@", 1)
        try:
            version = int(raw_version)
        except ValueError as exc:
            raise RegistryError(f"unknown capability: {requested}") from exc
        doc = load_registry()["capabilities"].get((name, version))
        if doc is None:
            raise RegistryError(f"unknown capability: {requested}")
        return name, version, doc
    found = latest_stable(text)
    if found is None:
        raise RegistryError(f"unknown capability: {requested}")
    return found


def capability_key(name: str, version: int) -> str:
    return f"{name}@{version}"


def pointer_get(data: object, pointer: str | None) -> object:
    if not pointer:
        return None
    current = data
    for part in str(pointer).lstrip("/").split("/"):
        if not part:
            continue
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


_URL_RE_CACHE = None


def _trim_url(url: str) -> str:
    return url.rstrip(").,;，。；、]}>\"'】》")


def extract_url(text: str) -> str | None:
    import re

    raw = text or ""
    match = re.search(r"https?://[^\s<>\"']+", raw)
    if match:
        return _trim_url(match.group(0))
    hosts: list[str] = []
    for spec in load_registry().get("platforms", {}).values():
        if not isinstance(spec, dict):
            continue
        for pattern in spec.get("hosts") or []:
            host = str(pattern).strip().lower().rstrip(".")
            if host:
                hosts.append(host)
    for host in sorted(set(hosts), key=len, reverse=True):
        found = re.search(rf"(?<![\w.-]){re.escape(host)}/[^\s<>\"']+", raw, re.I)
        if found:
            return _trim_url("https://" + found.group(0))
    return None


def host_of(url: str) -> str:
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.lower().rstrip(".")


def platform_for_host(host: str) -> str | None:
    host = (host or "").lower().rstrip(".")
    if host.startswith("www."):
        bare = host[4:]
    else:
        bare = host
    best: str | None = None
    best_len = -1
    for platform_id, spec in load_registry()["platforms"].items():
        if not isinstance(spec, dict):
            continue
        for pattern in spec.get("hosts") or []:
            pattern = str(pattern).lower().rstrip(".")
            if host == pattern or bare == pattern or host.endswith("." + pattern):
                if len(pattern) > best_len:
                    best = str(platform_id)
                    best_len = len(pattern)
    return best


def platform_for_url(url: str) -> str | None:
    return platform_for_host(host_of(url))


def canary_urls(platform_id: str) -> list[str]:
    spec = load_registry()["platforms"].get(platform_id) or {}
    urls = spec.get("canary_urls") or []
    return [str(item) for item in urls if isinstance(item, str) and item.strip()]
