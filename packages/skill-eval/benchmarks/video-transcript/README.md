# video.transcript offline benchmark

This directory contains the first public `video.transcript@1` benchmark slice.
Version 0.1 starts with one clean English LibriSpeech utterance whose source,
license, checksum, transcript, language, duration, and coverage tags are bound
through `fixtures.yaml`. `skill-eval` verifies the manifest digest and every
fixture's byte length and SHA-256 before executing the suite.

The complete v1 fixture set is intended to cover:

- Mandarin;
- English;
- Mandarin/English code-switching;
- background noise;
- multiple speakers;
- numbers, dates, and proper nouns;
- simplified and traditional Chinese where the reference permits both.

Each asset must have `redistribution_allowed: true`. A public download link is
not sufficient evidence of redistribution rights. Dataset-level or asset-level
license proof must be retained in `proof_url`, and attribution must be included
when required. User recordings, scraped videos, and generated speech with
unclear output rights must not be committed.

The benchmark suite will use `cer_at_most` for Chinese references and
`wer_at_most` for whitespace-tokenized English references. Offline and live
results remain separate.

Run the current slice from the repository root:

```bash
skill-eval run skills/faster-whisper \
  --suite packages/skill-eval/benchmarks/video-transcript/offline.yaml \
  --json packages/skill-eval/benchmarks/video-transcript/results/faster-whisper.json \
  --markdown packages/skill-eval/benchmarks/video-transcript/results/faster-whisper.md
```

The checked-in fixture is an unchanged extract from the official LibriSpeech
`dev-clean` archive. See `SOURCES.md` for the selection and byte-level
provenance check. Mandarin, code-switching, noisy, and multi-speaker assets
remain explicit coverage gaps rather than synthetic substitutes with unclear
rights.

The checked-in macOS arm64 / Python 3.12 evidence uses the implementation's
default `small` model. It achieved 100% execution/task success, 5.88% normalized
WER, 5.48% normalized CER, and about 2.04 seconds latency for the 5.855-second
utterance. This is a single-machine development result, not a cross-environment
leaderboard. Punctuation removal is declared in the assertions; the remaining
error is the model's `Mr.` versus the reference `MISTER`.

See [`SOURCES.md`](SOURCES.md) for the current source and license audit. Source
eligibility does not replace per-file provenance in the fixture manifest.
