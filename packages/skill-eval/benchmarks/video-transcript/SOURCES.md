# Fixture source audit

This audit records source-level decisions before any audio is committed. An
individual sample still needs its own checksum, transcript, duration, and
license record in `skill-eval.fixtures.v1`.

## Candidate: LibriSpeech / OpenSLR SLR12

- Official source: https://www.openslr.org/12/
- Dataset license: CC BY 4.0.
- Intended coverage: clean English read speech.
- Decision: eligible. The first slice uses the unchanged
  `dev-clean/1272/128104/1272-128104-0000.flac` utterance and its official split
  transcript. The fixture manifest preserves attribution to LibriSpeech,
  Vassil Panayotov, Daniel Povey, contributors, and the underlying LibriVox
  source.
- Verification: the selected file is extracted from the official
  `dev-clean.tar.gz` archive and compared byte-for-byte before it is committed.
  Its SHA-256, byte length, duration, reference transcript, and original archive
  path are recorded in `fixtures.yaml`. The downloaded archive matched the
  OpenSLR-published MD5 `42e2234ba48799c1f50f24a7926300a1`; its bundled
  `LibriSpeech/LICENSE.TXT` identifies the corpus as CC BY 4.0, and the bundled
  `1272-128104.trans.txt` contains the committed reference transcript.

## Candidate: AISHELL-1 / OpenSLR SLR33

- Official source: https://www.openslr.org/33/
- OpenSLR license label: Apache-2.0.
- Intended coverage: clean Mandarin read speech.
- Decision: conditionally eligible. The OpenSLR page also describes the data as
  free for academic use, so the license file inside the archive must be checked
  before redistributing an extracted utterance.
- Remaining work: inspect the archive license and select a small utterance with
  an official transcript and checksum. The 15 GB archive must not be committed.

## Excluded for repository mirroring: Mozilla Common Voice

- Official dataset catalog: https://commonvoice.mozilla.org/datasets
- Terms: https://commonvoice.mozilla.org/terms
- Dataset license: normally CC0-1.0.
- Decision: do not copy Common Voice audio into this repository. Mozilla's
  current terms ask users not to post, distribute, or mirror the dataset, in
  whole or in part, on other services and to direct access through Mozilla Data
  Collective. A future live/downloaded suite may reference an MDC-provided local
  checkout, but the public offline fixture bundle will not mirror these files.

## Coverage not yet sourced

The following remain open and must not be synthesized from system voices with
unclear output rights:

- Mandarin/English code-switching;
- noisy speech with a redistribution-compatible clean/noise derivation chain;
- multi-speaker speech;
- traditional Chinese reference speech;
- speech containing dates, numbers, and proper nouns.
