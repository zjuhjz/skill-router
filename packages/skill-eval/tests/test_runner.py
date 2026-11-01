import hashlib
import json
from copy import deepcopy
from pathlib import Path
from xml.etree import ElementTree as ET

import yaml
import pytest
from jsonschema import Draft202012Validator

from skilleval.comparison import ComparisonError, load_reports, render_comparison
from skilleval.reporters import render
from skilleval.runner import EvaluationError, _validate_fixtures, run_evaluation
from skilleval.scorers import pointer_get, score_assertion
from skilleval.transcript import character_error_rate, word_error_rate


PACKAGE = Path(__file__).resolve().parents[1]


def test_command_adapter_generates_valid_report(tmp_path):
    implementation = tmp_path / "implementation"
    implementation.mkdir()
    (implementation / "skill.capability.yaml").write_text(
        yaml.safe_dump(
            {
                "skillcall": "0.1",
                "id": "test.echo",
                "version": "1.0.0",
                "name": "Echo",
                "author": {"name": "tests"},
                "license": "MIT",
                "provides": [
                    {
                        "capability": "text.echo@1",
                        "platforms": ["local"],
                        "entrypoint": {"type": "command", "command": ["$PYTHON", "entry.py"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (implementation / "entry.py").write_text(
        "import json, sys\n"
        "request = json.load(sys.stdin)\n"
        "json.dump({'ok': True, 'output': {'text': request['input']['text'], 'score': 0.99}}, sys.stdout)\n",
        encoding="utf-8",
    )
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "schema": "skill-eval.suite.v1",
                "id": "test.echo.offline",
                "version": "1.0.0",
                "capability": "text.echo@1",
                "mode": "offline",
                "repeat": 2,
                "cases": [
                    {
                        "id": "hello",
                        "input": {"text": "hello world"},
                        "assertions": [
                            {"path": "/text", "op": "exact", "value": "hello world"},
                            {"path": "/text", "op": "regex", "value": "^hello"},
                            {"path": "/text", "op": "wer_at_most", "value": "hello world", "maximum": 0},
                            {"path": "/score", "op": "numeric_tolerance", "value": 1, "tolerance": 0.02},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    report = run_evaluation(implementation, suite, expected_mode="offline")
    assert report["results"]["runs"] == 2
    assert report["results"]["execution_success_rate"] == 1
    assert report["results"]["task_success_rate"] == 1
    assert report["results"]["metrics"]["wer"]["mean"] == 0
    schema = json.loads((PACKAGE / "schemas" / "report.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(report)

    markdown = render(report, "markdown")
    assert "# Evaluation: test.echo" in markdown
    assert "| Execution success | 100.0% |" in markdown
    assert "User outcome | not measured" in markdown
    assert "| WER mean | 0.000 |" in markdown

    junit = render(report, "junit")
    xml = ET.fromstring(junit)
    assert xml.tag == "testsuite"
    assert xml.attrib["tests"] == "2"
    assert xml.attrib["failures"] == "0"
    assert len(xml.findall("testcase")) == 2

    failed = deepcopy(report)
    failed["cases"][0]["task_success"] = False
    failed["cases"][0]["assertions"][0] = {"passed": False, "detail": "/text: exact failed"}
    failed_xml = ET.fromstring(render(failed, "junit"))
    assert failed_xml.attrib["failures"] == "1"
    assert failed_xml.find("testcase/failure").text == "/text: exact failed"


def test_scorers_fail_cleanly_on_wrong_types_and_bad_regex():
    assert pointer_get({"": "empty-key"}, "/") == "empty-key"
    assert score_assertion({"value": 3}, {"path": "/value", "op": "contains", "value": 3})[0] is False
    passed, detail = score_assertion(
        {"value": "text"}, {"path": "/value", "op": "regex", "value": "["}
    )
    assert passed is False
    assert "invalid regex" in detail


def test_transcript_error_rates_are_deterministic():
    assert character_error_rate("你好世界", "你好世间") == 0.25
    assert character_error_rate("Ａ B", "a b") == 0
    assert word_error_rate("hello world", "hello brave world") == 0.5
    assert word_error_rate("MISTER QUILTER", "Mr. Quilter") == 0.5
    assert word_error_rate(
        "HELLO WORLD", "Hello, world.", strip_punctuation=True
    ) == 0
    passed, detail = score_assertion(
        {"text": "你好世间"},
        {"path": "/text", "op": "cer_at_most", "value": "你好世界", "maximum": 0.25},
    )
    assert passed is True
    assert "cer=0.250000" in detail


def test_fixture_manifest_requires_redistribution_rights():
    schema = json.loads(
        (PACKAGE / "schemas" / "fixture-manifest.schema.json").read_text(encoding="utf-8")
    )
    manifest = {
        "schema": "skill-eval.fixtures.v1",
        "id": "skillcall.video-transcript.offline",
        "version": "1.0.0",
        "samples": [
            {
                "id": "sample-1",
                "path": "audio/sample-1.wav",
                "sha256": "a" * 64,
                "size_bytes": 1024,
                "media_type": "audio/wav",
                "duration_ms": 1000,
                "language": "zh-CN",
                "reference": {"text": "你好世界"},
                "tags": ["mandarin", "simplified"],
                "license": {
                    "spdx": "CC0-1.0",
                    "source_url": "https://example.test/sample-1",
                    "attribution": "",
                    "redistribution_allowed": True,
                },
            }
        ],
    }
    Draft202012Validator(schema).validate(manifest)
    manifest["samples"][0]["license"]["redistribution_allowed"] = False
    assert list(Draft202012Validator(schema).iter_errors(manifest))


def test_runner_rejects_fixture_manifest_digest_mismatch(tmp_path):
    implementation = tmp_path / "implementation"
    implementation.mkdir()
    (implementation / "skill.capability.yaml").write_text(
        yaml.safe_dump(
            {
                "skillcall": "0.1",
                "id": "test.echo",
                "version": "1.0.0",
                "name": "Echo",
                "author": {"name": "tests"},
                "license": "MIT",
                "provides": [
                    {
                        "capability": "text.echo@1",
                        "platforms": ["local"],
                        "entrypoint": {"type": "command", "command": ["$PYTHON", "entry.py"]},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "fixtures.yaml").write_text("schema: skill-eval.fixtures.v1\n", encoding="utf-8")
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "schema": "skill-eval.suite.v1",
                "id": "test.echo.offline",
                "version": "1.0.0",
                "capability": "text.echo@1",
                "mode": "offline",
                "fixtures": {"manifest": "fixtures.yaml", "digest": "sha256:" + "0" * 64},
                "cases": [{"id": "one", "input": {"text": "hello"}, "assertions": []}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationError, match="fixture manifest digest mismatch"):
        run_evaluation(implementation, suite)


def test_runner_accepts_verified_fixture_bundle(tmp_path):
    fixture = tmp_path / "fixtures" / "sample.txt"
    fixture.parent.mkdir()
    fixture.write_text("sample", encoding="utf-8")
    fixture_bytes = fixture.read_bytes()
    manifest = tmp_path / "fixtures.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "schema": "skill-eval.fixtures.v1",
                "id": "test.fixtures",
                "version": "1",
                "samples": [
                    {
                        "id": "sample",
                        "path": "fixtures/sample.txt",
                        "sha256": hashlib.sha256(fixture_bytes).hexdigest(),
                        "size_bytes": len(fixture_bytes),
                        "media_type": "text/plain",
                        "language": "en",
                        "reference": {"text": "sample"},
                        "tags": ["english"],
                        "license": {
                            "spdx": "MIT",
                            "source_url": "https://example.test/sample",
                            "proof_url": "https://example.test/license",
                            "attribution": "test",
                            "redistribution_allowed": True,
                        },
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    _validate_fixtures(
        tmp_path / "suite.yaml",
        {
            "manifest": "fixtures.yaml",
            "digest": "sha256:" + hashlib.sha256(manifest.read_bytes()).hexdigest(),
        },
    )


def test_cli_writes_all_report_formats_without_rerunning(tmp_path, monkeypatch):
    import skilleval.cli as cli

    report = {
        "schema": "skill-eval.report.v1",
        "subject": {"id": "test.echo", "version": "1", "digest": "sha256:" + "a" * 64},
        "capability": "text.echo@1",
        "suite": {
            "id": "test.echo.offline",
            "version": "1",
            "digest": "sha256:" + "b" * 64,
            "mode": "offline",
        },
        "environment": {"os": "test", "arch": "test", "python": "3.12", "runtime": "command"},
        "results": {
            "runs": 1,
            "execution_success_rate": 1,
            "task_success_rate": 1,
            "user_outcome_score": None,
            "p50_latency_ms": 1,
            "p95_latency_ms": 1,
            "average_cost_usd": None,
        },
        "cases": [
            {
                "case_id": "one",
                "attempt": 1,
                "execution_success": True,
                "task_success": True,
                "latency_ms": 1,
                "assertions": [],
                "error": None,
            }
        ],
        "trust": {"level": "self-tested", "issuer": "local"},
        "runner": {"name": "skill-eval", "version": "0.1.0"},
        "verified_at": "2026-11-01T00:00:00Z",
    }
    calls = []

    def fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return report

    monkeypatch.setattr(cli, "run_evaluation", fake_run)
    json_path = tmp_path / "report.json"
    markdown_path = tmp_path / "report.md"
    junit_path = tmp_path / "report.xml"
    assert (
        cli.main(
            [
                "run",
                str(tmp_path / "implementation"),
                "--suite",
                str(tmp_path / "suite.yaml"),
                "--json",
                str(json_path),
                "--markdown",
                str(markdown_path),
                "--junit",
                str(junit_path),
            ]
        )
        == 0
    )
    assert len(calls) == 1
    assert json.loads(json_path.read_text(encoding="utf-8"))["schema"] == "skill-eval.report.v1"
    assert markdown_path.read_text(encoding="utf-8").startswith("# Evaluation: test.echo")
    assert ET.parse(junit_path).getroot().tag == "testsuite"


def test_compare_validates_compatible_reports_and_renders_markdown(tmp_path):
    base = {
        "schema": "skill-eval.report.v1",
        "subject": {"id": "test.one", "version": "1", "digest": "sha256:" + "a" * 64},
        "capability": "image.ocr@1",
        "suite": {
            "id": "test.ocr.offline",
            "version": "1",
            "digest": "sha256:" + "b" * 64,
            "mode": "offline",
        },
        "environment": {"os": "test", "arch": "arm64", "python": "3.12", "runtime": "command"},
        "results": {
            "runs": 1,
            "execution_success_rate": 1,
            "task_success_rate": 1,
            "user_outcome_score": None,
            "p50_latency_ms": 10,
            "p95_latency_ms": 10,
            "average_cost_usd": None,
            "metrics": {"cer": {"samples": 1, "mean": 0.1, "p50": 0.1, "p95": 0.1}},
        },
        "cases": [
            {
                "case_id": "one",
                "attempt": 1,
                "execution_success": True,
                "task_success": True,
                "latency_ms": 10,
                "assertions": [],
                "error": None,
            }
        ],
        "trust": {"level": "self-tested", "issuer": "local"},
        "runner": {"name": "skill-eval", "version": "0.1.0"},
        "verified_at": "2026-11-01T00:00:00Z",
    }
    second = deepcopy(base)
    second["subject"] = {"id": "test.two", "version": "1", "digest": "sha256:" + "c" * 64}
    second["results"]["metrics"]["cer"]["mean"] = 0
    paths = [tmp_path / "one.json", tmp_path / "two.json"]
    for path, report in zip(paths, [base, second], strict=True):
        path.write_text(json.dumps(report), encoding="utf-8")

    markdown = render_comparison(load_reports(paths))
    assert "# Benchmark comparison: image.ocr@1" in markdown
    assert "| `test.one` `1` | 100.0% | 100.0% | 10.0% |" in markdown
    assert "| `test.two` `1` | 100.0% | 100.0% | 0.0% |" in markdown

    second["suite"]["digest"] = "sha256:" + "d" * 64
    paths[1].write_text(json.dumps(second), encoding="utf-8")
    with pytest.raises(ComparisonError, match="different suite content"):
        load_reports(paths)


def test_github_action_emits_all_report_formats():
    action = yaml.safe_load((PACKAGE / "action.yml").read_text(encoding="utf-8"))
    assert action["runs"]["using"] == "composite"
    assert set(action["inputs"]) >= {"implementation", "suite", "mode", "output-dir"}
    run_step = next(step for step in action["runs"]["steps"] if step["name"] == "Run evaluation")
    command = run_step["run"]
    assert "--json" in command
    assert "--markdown" in command
    assert "--junit" in command
    upload = next(
        step for step in action["runs"]["steps"] if step["name"] == "Upload evaluation evidence"
    )
    assert upload["uses"] == "actions/upload-artifact@v7"
