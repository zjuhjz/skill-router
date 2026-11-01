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


def _normalize(text: str, *, lowercase: bool) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    return normalized.lower() if lowercase else normalized


def character_error_rate(reference: str, hypothesis: str, *, lowercase: bool = True) -> float:
    reference_chars = list(re.sub(r"\s+", "", _normalize(reference, lowercase=lowercase)))
    hypothesis_chars = list(re.sub(r"\s+", "", _normalize(hypothesis, lowercase=lowercase)))
    if not reference_chars:
        return 0.0 if not hypothesis_chars else 1.0
    return edit_distance(reference_chars, hypothesis_chars) / len(reference_chars)


def word_error_rate(reference: str, hypothesis: str, *, lowercase: bool = True) -> float:
    reference_words = _normalize(reference, lowercase=lowercase).split()
    hypothesis_words = _normalize(hypothesis, lowercase=lowercase).split()
    if not reference_words:
        return 0.0 if not hypothesis_words else 1.0
    return edit_distance(reference_words, hypothesis_words) / len(reference_words)
