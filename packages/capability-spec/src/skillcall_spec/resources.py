from __future__ import annotations

from pathlib import Path


def package_root() -> Path:
    packaged = Path(__file__).resolve().parent
    if (packaged / "schemas").is_dir() and (packaged / "registry").is_dir():
        return packaged
    source = Path(__file__).resolve().parents[2]
    if (source / "schemas").is_dir() and (source / "registry").is_dir():
        return source
    raise FileNotFoundError("skillcall-spec schemas and registry are not available")


def schema_path(name: str) -> Path:
    path = package_root() / "schemas" / name
    if not path.is_file():
        raise FileNotFoundError(f"unknown skillcall schema: {name}")
    return path


def registry_path() -> Path:
    return package_root() / "registry"
