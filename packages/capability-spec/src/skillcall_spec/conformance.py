"""Validate a capability-spec snapshot and its implementation manifests."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator, FormatChecker

from .resources import package_root


class ConformanceError(RuntimeError):
    """Raised when one or more contract checks fail."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("\n".join(errors))


def default_spec_root() -> Path:
    return package_root()


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConformanceError([f"{path}: {exc}"]) from exc


def _load_yaml(path: Path) -> Any:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConformanceError([f"{path}: {exc}"]) from exc


def _format_validation_error(path: Path, error: Any) -> str:
    location = "/".join(str(part) for part in error.absolute_path)
    suffix = f" at /{location}" if location else ""
    return f"{path}{suffix}: {error.message}"


def _validate_document(path: Path, document: Any, schema: dict) -> list[str]:
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    return [
        _format_validation_error(path, error)
        for error in sorted(validator.iter_errors(document), key=lambda item: list(item.absolute_path))
    ]


def _capability_key(text: str) -> tuple[str, int] | None:
    match = re.fullmatch(r"(.+)@([1-9][0-9]*)", text)
    if not match:
        return None
    return match.group(1), int(match.group(2))


def validate_repository(root: Path, implementations_root: Path | None = None) -> dict[str, int]:
    """Validate a spec snapshot and, optionally, implementation manifests."""

    root = root.resolve()
    schema_root = root / "schemas"
    registry_root = root / "registry"
    schemas = {
        name: _load_json(schema_root / f"{name}.schema.json")
        for name in ("capability", "implementation", "invocation", "error")
    }
    errors: list[str] = []
    for name, schema in schemas.items():
        try:
            Draft202012Validator.check_schema(schema)
        except Exception as exc:  # jsonschema exposes several schema error subclasses
            errors.append(f"{schema_root / f'{name}.schema.json'}: invalid schema: {exc}")

    platforms_doc = _load_yaml(registry_root / "platforms.yaml")
    platforms = platforms_doc.get("platforms") if isinstance(platforms_doc, dict) else None
    if not isinstance(platforms, dict) or not platforms:
        errors.append(f"{registry_root / 'platforms.yaml'}: platforms must be a non-empty mapping")
        platforms = {}

    error_codes_doc = _load_yaml(registry_root / "error_codes.yaml")
    error_codes = error_codes_doc.get("codes") if isinstance(error_codes_doc, dict) else None
    if not isinstance(error_codes, dict) or not error_codes:
        errors.append(f"{registry_root / 'error_codes.yaml'}: codes must be a non-empty mapping")
        error_codes = {}

    capabilities: dict[tuple[str, int], Path] = {}
    example_count = 0
    for path in sorted((registry_root / "capabilities").glob("*/*/capability.yaml")):
        document = _load_yaml(path)
        errors.extend(_validate_document(path, document, schemas["capability"]))
        if not isinstance(document, dict):
            continue
        key = (str(document.get("id", "")), document.get("version"))
        folder_key = (path.parent.parent.name, int(path.parent.name))
        if key != folder_key:
            errors.append(f"{path}: id/version {key!r} does not match folder {folder_key!r}")
        if key in capabilities:
            errors.append(f"{path}: duplicate capability {key[0]}@{key[1]}")
        capabilities[key] = path
        for rule in document.get("output_rules") or []:
            if isinstance(rule, dict) and rule.get("on_fail") not in error_codes:
                errors.append(f"{path}: output rule uses unknown error code {rule.get('on_fail')!r}")

        input_schema_path = path.parent / "input.schema.json"
        output_schema_path = path.parent / "output.schema.json"
        input_schema = _load_json(input_schema_path)
        output_schema = _load_json(output_schema_path)
        for schema_path, schema in ((input_schema_path, input_schema), (output_schema_path, output_schema)):
            try:
                Draft202012Validator.check_schema(schema)
            except Exception as exc:
                errors.append(f"{schema_path}: invalid schema: {exc}")
        for example_path in sorted((path.parent / "examples").glob("*.json")):
            example = _load_json(example_path)
            target = input_schema if example_path.name.startswith("input") else output_schema
            errors.extend(_validate_document(example_path, example, target))
            example_count += 1

    manifest_count = 0
    provision_count = 0
    manifest_glob = (
        implementations_root.resolve().glob("*/skill.capability.yaml")
        if implementations_root is not None
        else []
    )
    for path in sorted(manifest_glob):
        manifest = _load_yaml(path)
        errors.extend(_validate_document(path, manifest, schemas["implementation"]))
        if not isinstance(manifest, dict):
            continue
        manifest_count += 1
        for provision in manifest.get("provides") or []:
            if not isinstance(provision, dict):
                continue
            provision_count += 1
            capability = _capability_key(str(provision.get("capability", "")))
            if capability and capability not in capabilities:
                errors.append(f"{path}: unknown capability {provision.get('capability')}")
            for platform in provision.get("platforms") or []:
                if platform != "*" and platform not in platforms:
                    errors.append(f"{path}: unknown platform {platform!r}")
            for pattern in provision.get("url_patterns") or []:
                try:
                    re.compile(pattern)
                except re.error as exc:
                    errors.append(f"{path}: invalid url pattern {pattern!r}: {exc}")

    if errors:
        raise ConformanceError(errors)
    return {
        "capabilities": len(capabilities),
        "examples": example_count,
        "manifests": manifest_count,
        "provisions": provision_count,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a skillcall capability-spec snapshot")
    parser.add_argument("root", nargs="?", type=Path, default=default_spec_root())
    parser.add_argument(
        "--implementations",
        type=Path,
        help="optional directory containing implementation subdirectories",
    )
    args = parser.parse_args(argv)
    try:
        counts = validate_repository(args.root, args.implementations)
    except ConformanceError as exc:
        for error in exc.errors:
            print(f"ERROR: {error}")
        return 1
    summary = ", ".join(f"{name}={count}" for name, count in counts.items())
    print(f"skillcall conformance: OK ({summary})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
