from __future__ import annotations

import re
import unicodedata


def edit_distance(reference: list[str], hypothesis: list[str]) -> int:
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (reference_item != hypothesis_item),
                )
            )
        previous = current
    return previous[-1]


def _normalize(text: str, *, lowercase: bool, strip_punctuation: bool) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    if strip_punctuation:
        normalized = "".join(
            " " if unicodedata.category(char).startswith("P") else char
            for char in normalized
        )
    return normalized.lower() if lowercase else normalized


def character_error_rate(
    reference: str,
    hypothesis: str,
    *,
    lowercase: bool = True,
    strip_punctuation: bool = False,
) -> float:
    reference_chars = list(
        re.sub(
            r"\s+",
            "",
            _normalize(
                reference,
                lowercase=lowercase,
                strip_punctuation=strip_punctuation,
            ),
        )
    )
    hypothesis_chars = list(
        re.sub(
            r"\s+",
            "",
            _normalize(
                hypothesis,
                lowercase=lowercase,
                strip_punctuation=strip_punctuation,
            ),
        )
    )
    if not reference_chars:
        return 0.0 if not hypothesis_chars else 1.0
    return edit_distance(reference_chars, hypothesis_chars) / len(reference_chars)


def word_error_rate(
    reference: str,
    hypothesis: str,
    *,
    lowercase: bool = True,
    strip_punctuation: bool = False,
) -> float:
    reference_words = _normalize(
        reference, lowercase=lowercase, strip_punctuation=strip_punctuation
    ).split()
    hypothesis_words = _normalize(
        hypothesis, lowercase=lowercase, strip_punctuation=strip_punctuation
    ).split()
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0
    return edit_distance(reference_words, hypothesis_words) / len(reference_words)
