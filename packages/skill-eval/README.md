# skill-eval

Local-first, bring-your-own-environment evaluation for skillcall
implementations. The initial v0.1 slice supports local command implementations
and remote MCP Streamable HTTP tools,
offline/live suite separation, deterministic assertions, repeated runs,
environment fingerprints, and `skill-eval.report.v1` JSON reports.

```bash
pip install -e .
skill-eval run ../../skills/faster-whisper \
  --suite suites/video-transcript/offline.yaml \
  --json report.json \
  --markdown report.md \
  --junit report.xml
```

The runner executes code only in the caller's environment. It does not upload
credentials or provide hosted execution.

An MCP provision uses `entrypoint: {type: mcp, url: https://..., tool: name}`.
Each suite case's `input` object is sent as the MCP tool arguments.

JSON is the canonical machine report. Markdown and JUnit are deterministic
views generated from that same report without rerunning the implementation.
Compatible JSON reports can be rendered into a static comparison table with
`skill-eval compare first.json second.json --output comparison.md`; comparison
rejects reports from different capabilities or suite content.

## GitHub Action

The repository includes a composite action that installs `capability-spec` and
`skill-eval`, runs one suite, and uploads canonical JSON plus Markdown and JUnit
views as a workflow artifact:

```yaml
- uses: actions/setup-python@v5
  with:
    python-version: "3.12"
- uses: owner/skill-router/packages/skill-eval@ref
  with:
    implementation: path/to/implementation
    suite: path/to/offline.yaml
    mode: offline
    artifact-name: skill-eval-evidence
```

The action executes implementation code on the caller's GitHub-hosted or
self-hosted runner. It does not send the implementation or credentials to a
Skillcall service.

Supported assertions are `exact`, `contains`, `regex`, `numeric_tolerance`,
`set_overlap`, `exists`, `cer_at_most`, and `wer_at_most`. Assertion paths are JSON Pointers relative to the
implementation's `output` object.

Public benchmark audio must satisfy
[`schemas/fixture-manifest.schema.json`](schemas/fixture-manifest.schema.json)
and the licensing rules in
[`benchmarks/video-transcript/README.md`](benchmarks/video-transcript/README.md).
