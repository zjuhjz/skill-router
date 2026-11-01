from pathlib import Path

import yaml
from mcp.types import CallToolResult, TextContent

import skilleval.runner as runner
from skilleval.adapters.base import InvocationResult
from skilleval.adapters.mcp import normalize_result


def test_mcp_result_normalization():
    structured = CallToolResult(content=[], structuredContent={"text": "hello"})
    assert normalize_result(structured) == {"ok": True, "output": {"text": "hello"}}

    standard = CallToolResult(content=[], structuredContent={"ok": True, "output": {"value": 3}})
    assert normalize_result(standard) == {"ok": True, "output": {"value": 3}}

    text = CallToolResult(content=[TextContent(type="text", text='{"value": 4}')])
    assert normalize_result(text) == {"ok": True, "output": {"value": 4}}

    error = CallToolResult(content=[TextContent(type="text", text="provider failed")], isError=True)
    normalized = normalize_result(error)
    assert normalized["ok"] is False
    assert normalized["error"]["code"] == "MCP_TOOL_ERROR"


def test_runner_evaluates_remote_mcp_provision(tmp_path, monkeypatch):
    implementation = tmp_path / "mcp-implementation"
    implementation.mkdir()
    (implementation / "skill.capability.yaml").write_text(
        yaml.safe_dump(
            {
                "skillcall": "0.1",
                "id": "test.remote-mcp",
                "version": "1.0.0",
                "name": "Remote MCP",
                "author": {"name": "tests"},
                "license": "MIT",
                "provides": [
                    {
                        "capability": "text.echo@1",
                        "platforms": ["local"],
                        "entrypoint": {
                            "type": "mcp",
                            "url": "https://mcp.example.test/api",
                            "tool": "echo",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    suite = tmp_path / "suite.yaml"
    suite.write_text(
        yaml.safe_dump(
            {
                "schema": "skill-eval.suite.v1",
                "id": "test.remote-mcp.offline",
                "version": "1.0.0",
                "capability": "text.echo@1",
                "mode": "offline",
                "cases": [
                    {
                        "id": "hello",
                        "input": {"text": "hello"},
                        "assertions": [{"path": "/text", "op": "exact", "value": "hello"}],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls = []

    def fake_invoke(entrypoint, arguments, timeout_s):
        calls.append((entrypoint, arguments, timeout_s))
        return InvocationResult({"ok": True, "output": {"text": arguments["text"]}}, 2.5)

    monkeypatch.setattr(runner, "invoke_mcp", fake_invoke)
    report = runner.run_evaluation(implementation, suite)
    assert len(calls) == 1
    assert calls[0][0]["tool"] == "echo"
    assert calls[0][1] == {"text": "hello"}
    assert report["environment"]["runtime"] == "mcp"
    assert report["results"]["task_success_rate"] == 1
