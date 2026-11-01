import json
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from skillcall_spec.conformance import validate_repository
from skillcall_spec.resources import registry_path, schema_path


PACKAGE = Path(__file__).resolve().parents[1]


def _schema(name: str) -> dict:
    return json.loads(
        (PACKAGE / "schemas" / f"{name}.schema.json").read_text(encoding="utf-8")
    )


def test_repository_is_capability_spec_conformant():
    assert validate_repository(PACKAGE) == {
        "capabilities": 5,
        "examples": 11,
        "manifests": 0,
        "provisions": 0,
    }


def test_resource_api_exposes_schema_and_registry():
    assert schema_path("implementation.schema.json").is_file()
    assert (registry_path() / "platforms.yaml").is_file()


def test_implementation_schema_rejects_unversioned_capability():
    manifest = yaml.safe_load(
        (PACKAGE.parents[1] / "skills" / "faster-whisper" / "skill.capability.yaml").read_text(
            encoding="utf-8"
        )
    )
    invalid = deepcopy(manifest)
    invalid["provides"][0]["capability"] = "video.transcript"
    errors = list(Draft202012Validator(_schema("implementation")).iter_errors(invalid))
    assert errors


def test_implementation_schema_requires_remote_mcp_tool_name():
    manifest = yaml.safe_load(
        (PACKAGE.parents[1] / "skills" / "faster-whisper" / "skill.capability.yaml").read_text(
            encoding="utf-8"
        )
    )
    invalid = deepcopy(manifest)
    invalid["provides"][0]["entrypoint"] = {
        "type": "mcp",
        "url": "https://mcp.example.test/api",
    }
    errors = list(Draft202012Validator(_schema("implementation")).iter_errors(invalid))
    assert any("tool" in error.message for error in errors)


@pytest.mark.parametrize(
    "document",
    [
        {"ok": True, "output": {}},
        {"ok": False, "error": {"code": "UPSTREAM_ERROR", "message": "temporary failure"}},
        {
            "skillcall": "0.1",
            "request_id": "r_test",
            "capability": "video.resolve@1",
            "input": {"url": "https://example.com/video"},
            "context": {
                "output_dir": "/tmp/out",
                "temp_dir": "/tmp/work",
                "timeout_s": 30,
                "mode": "invoke",
            },
        },
    ],
)
def test_invocation_schema_accepts_protocol_messages(document):
    Draft202012Validator(_schema("invocation")).validate(document)
