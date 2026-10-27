import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _entry():
    path = REPO / "skills" / "faster-whisper" / "entry.py"
    spec = importlib.util.spec_from_file_location("whisper_entry", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_official_endpoint_falls_back_to_mirror_and_stall_is_detected():
    module = _entry()
    assert module.endpoints_to_try(None) == ["https://huggingface.co", "https://hf-mirror.com"]
    assert module.endpoints_to_try("https://hf-mirror.com") == ["https://hf-mirror.com"]
    assert module.endpoints_to_try("https://example.test/hub") == ["https://example.test/hub"]
    assert module.fetch_problem(1, 1, 100, official=True) is None
    assert module.fetch_problem(16, 16, 0, official=True) == "stalled"
    assert module.fetch_problem(13, 1, 1024, official=True) == "slow"
    assert module.fetch_problem(13, 1, 1024, official=False) is None
    assert module.target_script("zh", "en") == "Hans"
    assert module.target_script("zh-TW", "zh") == "Hant"
    assert module.target_script(None, "en") is None


def test_zh_output_is_simplified():
    zhconv = pytest.importorskip("zhconv")
    module = _entry()
    traditional = "曾經全心全意對你"
    simplified = module.to_script(traditional, "Hans")
    assert simplified == zhconv.convert(traditional, "zh-cn")
    assert simplified == "曾经全心全意对你"
    assert "經" not in simplified
