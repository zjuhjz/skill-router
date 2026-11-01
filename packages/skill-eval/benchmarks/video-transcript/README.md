# video.transcript offline benchmark

This directory is reserved for the first public `video.transcript@1` benchmark.
No audio asset may be added until its redistribution rights, source, checksum,
reference transcript, language, duration, and coverage tags are recorded in a
`skill-eval.fixtures.v1` manifest.

The initial fixture set must cover:

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

See [`SOURCES.md`](SOURCES.md) for the current source and license audit. Source
eligibility does not replace per-file provenance in the fixture manifest.
