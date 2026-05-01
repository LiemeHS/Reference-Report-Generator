from __future__ import annotations

import re
import unicodedata

_CONNECTOR_TOKENS = {
    "a",
    "an",
    "and",
    "de",
    "der",
    "des",
    "for",
    "in",
    "la",
    "le",
    "of",
    "on",
    "the",
    "und",
    "van",
    "von",
}


def journal_abbreviation_match(left: str | None, right: str | None) -> bool:
    """Return true when one journal title is a credible abbreviation of another.

    This is deliberately linear over the title tokens. It handles common
    journal-title forms such as ``Ann Intern Med`` vs
    ``Annals of Internal Medicine`` and compact initialisms such as ``AEM`` vs
    ``Annals of Emergency Medicine`` without trying recursive expansions.
    """

    left_tokens = _journal_title_tokens(left)
    right_tokens = _journal_title_tokens(right)
    if not left_tokens or not right_tokens or left_tokens == right_tokens:
        return False
    return _tokens_abbreviate(left_tokens, right_tokens) or _tokens_abbreviate(
        right_tokens,
        left_tokens,
    )


def _tokens_abbreviate(short_tokens: list[str], full_tokens: list[str]) -> bool:
    if len(full_tokens) < 2:
        return False
    if len(short_tokens) == 1:
        return _compact_initialism_matches(short_tokens[0], full_tokens)
    if len(short_tokens) != len(full_tokens):
        return False

    shortened = False
    for short, full in zip(short_tokens, full_tokens):
        matched, was_shortened = _token_abbreviation_matches(short, full)
        if not matched:
            return False
        shortened = shortened or was_shortened
    return shortened


def _compact_initialism_matches(short: str, full_tokens: list[str]) -> bool:
    if not (2 <= len(short) <= 8):
        return False
    return short == "".join(token[0] for token in full_tokens if token)


def _token_abbreviation_matches(short: str, full: str) -> tuple[bool, bool]:
    if not short or not full:
        return False, False
    if short == full:
        return True, False
    if len(short) == 1:
        return short == full[:1], True
    return full.startswith(short), len(short) < len(full)


def _journal_title_tokens(value: str | None) -> list[str]:
    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(char for char in text if not unicodedata.combining(char))
    tokens = re.findall(r"[a-z0-9]+", text)
    return [token for token in tokens if token not in _CONNECTOR_TOKENS]
