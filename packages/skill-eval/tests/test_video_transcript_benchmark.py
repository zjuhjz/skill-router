import hashlib
import json
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from skilleval.reporters import render


PACKAGE = Path(__file__).resolve().parents[1]
BENCHMARK = PACKAGE / "benchmarks" / "video-transcript"


def _json(name: str) -> dict:
    return json.loads((PACKAGE / "schemas" / name).read_text(encoding="utf-8"))


def test_video_transcript_fixture_is_licensed_and_intact():
    manifest_path = BENCHMARK / "fixtures.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    Draft202012Validator(
        _json("fixture-manifest.schema.json"), format_checker=FormatChecker()
    ).validate(manifest)

    assert len(manifest["samples"]) == 1
    sample = manifest["samples"][0]
    fixture = BENCHMARK / sample["path"]
    data = fixture.read_bytes()
    assert len(data) == sample["size_bytes"]
    assert hashlib.sha256(data).hexdigest() == sample["sha256"]
    assert sample["license"]["spdx"] == "CC-BY-4.0"
    assert sample["license"]["redistribution_allowed"] is True
    assert data[:4] == b"fLaC"
    stream_info_size = int.from_bytes(data[5:8], "big")
    stream_info = data[8 : 8 + stream_info_size]
    packed = int.from_bytes(stream_info[10:18], "big")
    sample_rate = (packed >> 44) & ((1 << 20) - 1)
    channels = ((packed >> 41) & 0x7) + 1
    bit_depth = ((packed >> 36) & 0x1F) + 1
    total_samples = packed & ((1 << 36) - 1)
    assert sample_rate == sample["sample_rate_hz"]
    assert channels == sample["channels"]
    assert bit_depth == sample["bit_depth"]
    assert round(total_samples / sample_rate * 1000) == sample["duration_ms"]


def test_video_transcript_suite_binds_fixture_manifest():
    suite = yaml.safe_load((BENCHMARK / "offline.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(_json("evaluation-suite.schema.json")).validate(suite)
    manifest_bytes = (BENCHMARK / suite["fixtures"]["manifest"]).read_bytes()

    assert suite["capability"] == "video.transcript@1"
    assert suite["mode"] == "offline"
    assert suite["fixtures"]["digest"] == (
        "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    )
    assert [case["id"] for case in suite["cases"]] == ["librispeech-1272-128104-0000"]


def test_checked_in_faster_whisper_report_is_valid_and_current():
    results = BENCHMARK / "results"
    report = json.loads((results / "faster-whisper.json").read_text(encoding="utf-8"))
    Draft202012Validator(_json("report.schema.json")).validate(report)
    suite_digest = "sha256:" + hashlib.sha256((BENCHMARK / "offline.yaml").read_bytes()).hexdigest()
    manifest_digest = "sha256:" + hashlib.sha256(
        (BENCHMARK / "fixtures.yaml").read_bytes()
    ).hexdigest()

    assert report["suite"]["digest"] == suite_digest
    assert report["suite"]["fixtures"]["digest"] == manifest_digest
    assert report["subject"]["id"] == "example.faster-whisper"
    assert report["environment"]["arch"] == "arm64"
    assert report["results"]["task_success_rate"] == 1
    assert report["results"]["metrics"]["wer"]["mean"] < 0.06
    assert (results / "faster-whisper.md").read_text(encoding="utf-8") == render(
        report, "markdown"
    )
