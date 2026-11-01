# Evaluation: example.faster-whisper

Capability: `video.transcript@1`  
Suite: `skillcall.video-transcript.offline` `0.1.0` (offline)  
Verified: `2026-11-01T14:38:39.137840Z`

## Summary

| Metric | Result |
|---|---:|
| Runs | 1 |
| Execution success | 100.0% |
| Task success | 100.0% |
| User outcome | not measured |
| P50 latency | 2037.358 ms |
| P95 latency | 2037.358 ms |
| CER mean | 0.055 |
| WER mean | 0.059 |
| Average cost | not measured |

## Environment

| Field | Value |
|---|---|
| arch | arm64 |
| locale | en-US |
| os | darwin |
| os_release | 27.0.0 |
| python | 3.12.16 |
| runtime | command |

## Cases

| Case | Attempt | Execution | Task | Latency | Detail |
|---|---:|:---:|:---:|---:|---|
| librispeech-1272-128104-0000 | 1 | pass | pass | 2037.358 ms | — |

## Evidence

- Subject digest: `sha256:769cec89dd7d0e61199e338a4bcbe8e5682a1d50c1fc9f9a776db1fcef85f8f7`
- Suite digest: `sha256:21c9f580d07c50509ec56df67318c4d242777537656af9ec7e182bed9947b6ac`
- Fixture manifest digest: `sha256:2ee83a1c79cde6f7010fce6ee596d365886ad70d698c1ac885dba71fe9c421af`
- Trust: `self-tested` issued by `local`
- Runner: `skill-eval` `0.1.0`
