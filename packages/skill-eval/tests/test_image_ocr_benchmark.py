import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from skilleval.comparison import load_reports, render_comparison


PACKAGE = Path(__file__).resolve().parents[1]
BENCHMARK = PACKAGE / "benchmarks" / "image-ocr"


def _json(name: str) -> dict:
    return json.loads((PACKAGE / "schemas" / name).read_text(encoding="utf-8"))


def test_image_ocr_fixture_is_reproducible_and_licensed(tmp_path):
    manifest = yaml.safe_load((BENCHMARK / "fixtures.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(
        _json("fixture-manifest.schema.json"), format_checker=FormatChecker()
    ).validate(manifest)
    for sample in manifest["samples"]:
        fixture = BENCHMARK / sample["path"]
        data = fixture.read_bytes()
        assert len(data) == sample["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == sample["sha256"]
        assert struct.unpack(">II", data[16:24]) == (sample["width"], sample["height"])

    module_path = BENCHMARK / "generate_fixture.py"
    spec = importlib.util.spec_from_file_location("image_ocr_fixture_generator", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    module.generate_all(tmp_path)
    for sample in manifest["samples"]:
        assert (tmp_path / Path(sample["path"]).name).read_bytes() == (BENCHMARK / sample["path"]).read_bytes()


def test_image_ocr_offline_suite_is_valid():
    suite = yaml.safe_load((BENCHMARK / "offline.yaml").read_text(encoding="utf-8"))
    Draft202012Validator(_json("evaluation-suite.schema.json")).validate(suite)
    assert suite["capability"] == "image.ocr@1"
    assert suite["mode"] == "offline"
    assert {case["id"] for case in suite["cases"]} == {
        "ocr-basic-en",
        "ocr-low-contrast-en",
        "ocr-rotated-en",
    }


def test_paddleocr_v3_result_parser_normalizes_blocks():
    module_path = PACKAGE / "implementations" / "paddleocr" / "entry.py"
    spec = importlib.util.spec_from_file_location("paddleocr_entry", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    result = [
        {
            "res": {
                "rec_texts": ["SKILLCALL OCR", "VERSION 0.4"],
                "rec_scores": [0.99, 0.98],
                "rec_polys": [
                    [[10, 20], [110, 20], [110, 40], [10, 40]],
                    [[20, 50], [120, 50], [120, 70], [20, 70]],
                ],
            }
        }
    ]

    blocks = module.new_api_blocks(result, (200, 100))
    assert blocks == [
        {"text": "SKILLCALL OCR", "confidence": 0.99, "bbox": [0.05, 0.2, 0.5, 0.2]},
        {"text": "VERSION 0.4", "confidence": 0.98, "bbox": [0.1, 0.5, 0.5, 0.2]},
    ]


def test_checked_in_ocr_reports_are_valid_and_comparison_is_current():
    results = BENCHMARK / "results"
    reports = load_reports(
        [
            results / "tesseract-ocr.json",
            results / "paddleocr.json",
            results / "tesseract-ocr-linux-x64.json",
        ]
    )
    suite_digest = "sha256:" + hashlib.sha256((BENCHMARK / "offline.yaml").read_bytes()).hexdigest()
    assert all(report["suite"]["digest"] == suite_digest for report in reports)
    ci_report = reports[2]
    assert ci_report["environment"]["os"] == "linux"
    assert ci_report["environment"]["arch"] == "x86_64"
    assert ci_report["results"]["task_success_rate"] == 1
    expected = render_comparison(reports[:2])
    assert (results / "COMPARISON.md").read_text(encoding="utf-8") == expected
