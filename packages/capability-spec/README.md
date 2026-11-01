# capability-spec

A vendor-neutral capability contract for AI skills and tools. This package
defines capabilities, implementation manifests, command invocations, standard
errors, and conformance checks. It does not execute implementations, calculate
health, or make routing decisions.

```bash
pip install -e .
skillcall-spec validate
skillcall-spec validate --implementations ../../skills
```

The current v0.4 snapshot contains `video.resolve@1`, `video.download@1`,
`image.download@1`, `image.ocr@1`, and `video.transcript@1`.

Capability contracts may declare ordered `evaluation.ranking_metrics` with a
metric name, report statistic, and `minimize`/`maximize` direction. Routers can
consume these profiles without hard-coding OCR, transcription, or other
domain-specific quality semantics.
