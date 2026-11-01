# Benchmark comparison: image.ocr@1

Suite: `skillcall.image-ocr.offline` `1.1.0` (offline)
Suite digest: `sha256:cdcb72f10b35a5b342f0ec93eba7b351e4be71092e33d8d98a387e9680adeef1`

| Implementation | Execution | Task | CER | P50 latency | P95 latency | Cost/run | Verified |
|---|---:|---:|---:|---:|---:|---:|---|
| `example.tesseract-ocr` `0.1.0` | 100.0% | 100.0% | 14.8% | 86.938 ms | 163.828 ms | not measured | 2026-11-01T04:34:52.361740Z |
| `example.paddleocr` `0.1.0` | 100.0% | 100.0% | 0.0% | 2022.686 ms | 2902.161 ms | not measured | 2026-11-01T04:35:04.280323Z |

## Evidence

- `example.tesseract-ocr`: subject `sha256:c21a67f8bac285e80f10af3cc2a3a4a4fcf32a88a2874d5d4888a2fb41cd3ef3`; darwin/arm64, Python 3.12.16; trust `self-tested` issued by `local`.
- `example.paddleocr`: subject `sha256:f320c093182a1492ea8b6aaab0ad6c854aa9b7e79707a5d14c6f81e5d155710e`; darwin/arm64, Python 3.12.16; trust `self-tested` issued by `local`.

> Latency includes process and model startup. Results are comparable only for the exact suite digest and recorded environments.
