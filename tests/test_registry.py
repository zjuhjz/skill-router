import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from skillrouter.proc import http_allowed
from skillrouter.registry_loader import load_registry, platform_for_url, resolve_capability

REPO = Path(__file__).resolve().parents[1]


def test_examples_match_schemas():
    registry = load_registry()
    assert set(registry["capabilities"]) == {
        ("video.download", 1),
        ("video.resolve", 1),
        ("image.download", 1),
        ("video.transcript", 1),
    }
    for doc in registry["capabilities"].values():
        for example in doc["examples"]:
            schema = doc["input_schema"] if example["name"].startswith("input") else doc["output_schema"]
            Draft202012Validator(schema).validate(example["body"])


def test_platform_lookup_uses_registry_data_only():
    assert platform_for_url("https://v.douyin.com/abc/") == "douyin"
    assert platform_for_url("https://www.bilibili.com/video/BV1") == "bilibili"
    assert platform_for_url("https://b23.tv/xx") == "bilibili"
    assert platform_for_url("https://xhslink.cn/o/ExampleShare") == "xiaohongshu"
    assert platform_for_url("https://www.xhslink.com/a/ExampleShare") == "xiaohongshu"
    assert platform_for_url("https://example.com/a") is None
    name, version, doc = resolve_capability("video.download")
    assert (name, version) == ("video.download", 1)
    assert doc["status"] == "stable"


def test_router_source_has_no_platform_names():
    banned = (
        "douyin",
        "bilibili",
        "kuaishou",
        "xiaohongshu",
        "weibo",
        "tikhub",
        "kukutool",
        "iesdouyin",
        "抖音",
        "哔哩",
        "快手",
        "小红书",
        "微博",
    )
    root = REPO / "src" / "skillrouter"
    offenders = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8").lower()
        for word in banned:
            if word.lower() in text:
                offenders.append(f"{path.name}: {word}")
    assert offenders == []


def test_resolve_sidecars_declare_quality_hint():
    rich = (
        "xiaohongshu-web",
        "douyin-dl",
        "douyin-ies",
        "bilibili-web",
        "kuaishou-web",
        "weibo-web",
    )
    for name in rich:
        doc = yaml.safe_load((REPO / "skills" / name / "skill.capability.yaml").read_text(encoding="utf-8"))
        resolve = next(item for item in doc["provides"] if item["capability"] == "video.resolve@1")
        assert resolve["metadata"] == "rich"
        assert len(resolve["platforms"]) == 1
    generic = yaml.safe_load((REPO / "skills" / "ytdlp" / "skill.capability.yaml").read_text(encoding="utf-8"))
    resolve = next(item for item in generic["provides"] if item["capability"] == "video.resolve@1")
    assert resolve["metadata"] == "basic"
    assert len(resolve["platforms"]) > 1
    download = next(item for item in generic["provides"] if item["capability"] == "video.download@1")
    assert "metadata" not in download


def test_metadata_address_is_blocked_before_any_request():
    assert http_allowed("http://169.254.169.254/latest", third_party=True)
    assert http_allowed("http://127.0.0.1:9/skillcall", third_party=False) is None
    assert http_allowed("http://example.com/skillcall", third_party=False)


@pytest.mark.parametrize(
    ("flag", "expected"),
    [
        ("precheck", "INVALID_INPUT"),
        ("router_timeout", "TIMEOUT"),
        ("default", "INTERNAL"),
    ],
)
def test_error_policy_flags(flag, expected):
    from skillrouter.registry_loader import code_with

    assert code_with(flag) == expected
