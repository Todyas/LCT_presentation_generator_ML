from __future__ import annotations

import re
from collections.abc import Iterable

from pydantic import BaseModel

_NUMBER_RE = re.compile(r"(?<![\w])[-+]?\d+(?:[.,]\d+)?\s*%?")
_WORD_RE = re.compile(r"[а-яёa-z]{3,}", re.IGNORECASE)


def _normalized_claim_numbers(text: str) -> set[str]:
    claims: set[str] = set()
    for match in _NUMBER_RE.finditer(text):
        token = match.group(0).replace(" ", "").replace(",", ".")
        bare = token.lstrip("+-").removesuffix("%")
        try:
            value = float(bare)
        except ValueError:
            continue
        # Single digits are commonly structural labels (steps, quarters, slide
        # indices). Larger values and every percentage are business claims and
        # therefore must be grounded in the source brief.
        if "%" in token or value >= 10:
            claims.add(token)
    return claims


def _claim_text(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " ".join(_claim_text(item) for item in value)
    if isinstance(value, dict):
        return " ".join(
            _claim_text(item)
            for key, item in value.items()
            if key not in {"slide_index", "placeholder_idx"}
        )
    return ""


def ungrounded_numbers(value: BaseModel | str | dict, brief: str) -> set[str]:
    if isinstance(value, str):
        text = value
    elif isinstance(value, BaseModel):
        text = _claim_text(value.model_dump())
    else:
        text = _claim_text(value)
    allowed = _normalized_claim_numbers(brief)
    return _normalized_claim_numbers(text) - allowed


def near_duplicate_pairs(
    messages: Iterable[str], threshold: float = 0.78
) -> list[tuple[int, int]]:
    # Prefix normalization catches common Russian inflections (e.g.
    # "подготовки" / "подготовку") without introducing a heavyweight NLP
    # dependency into the generation worker.
    token_sets = [
        {word.lower()[:7] for word in _WORD_RE.findall(message)} for message in messages
    ]
    duplicates: list[tuple[int, int]] = []
    for left in range(len(token_sets)):
        for right in range(left + 1, len(token_sets)):
            a, b = token_sets[left], token_sets[right]
            if not a or not b:
                continue
            similarity = len(a & b) / len(a | b)
            if similarity >= threshold:
                duplicates.append((left, right))
    return duplicates
