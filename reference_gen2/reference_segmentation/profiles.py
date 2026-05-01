from __future__ import annotations

from dataclasses import dataclass
import re

from reference_gen2.reference_styles import profile_for_reference_style
from reference_gen2.reference_segmentation.models import ReferenceStyleHint


@dataclass(frozen=True)
class SegmentationProfile:
    name: str
    prefer_author_year: bool
    allow_title_led_starts: bool
    prefer_numeric_starts: bool
    conservative_unknown: bool = False


UNKNOWN_PROFILE = SegmentationProfile(
    name="unknown_profile",
    prefer_author_year=True,
    allow_title_led_starts=False,
    prefer_numeric_starts=False,
    conservative_unknown=True,
)

AUTHOR_YEAR_PROFILE = SegmentationProfile(
    name="author_year_profile",
    prefer_author_year=True,
    allow_title_led_starts=False,
    prefer_numeric_starts=False,
)

NOTES_BIBLIOGRAPHY_PROFILE = SegmentationProfile(
    name="notes_bibliography_profile",
    prefer_author_year=False,
    allow_title_led_starts=True,
    prefer_numeric_starts=False,
)

NUMERIC_PROFILE = SegmentationProfile(
    name="numeric_profile",
    prefer_author_year=False,
    allow_title_led_starts=False,
    prefer_numeric_starts=True,
)

_NUMBERED_START_RE = re.compile(r"^\s*(?:\[\d+\]|\d{1,3}[\.)])\s+\S")
_AUTHOR_YEAR_START_RE = re.compile(
    r"^\s*(?:[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'`-]+,\s*(?:[A-Z]\.|[A-Z][A-Za-zÀ-ÖØ-öø-ÿ-]+)"
    r"[^\n]{0,120}(?:\(\s*(?:19|20)\d{2}[a-z]?\s*\)|\b(?:19|20)\d{2}[a-z]?\b)"
    r"|[A-Z0-9][^\n]{0,80}\(\s*(?:19|20)\d{2}[a-z]?)",
    re.IGNORECASE,
)
_NOTES_STYLE_START_RE = re.compile(
    r"^\s*(?:[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`-]+,\s+[A-Z]|[\"“][^\"”]{8,}[\"”]\.)"
)


def profile_for_style(style_hint: ReferenceStyleHint) -> SegmentationProfile:
    family = profile_for_reference_style(style_hint).segmentation_family
    if family == "author_year":
        return AUTHOR_YEAR_PROFILE
    if family == "notes_bibliography":
        return NOTES_BIBLIOGRAPHY_PROFILE
    if family == "numeric":
        return NUMERIC_PROFILE
    return UNKNOWN_PROFILE


def infer_profile_for_lines(lines: list[str]) -> SegmentationProfile:
    """Infer only a boundary profile from shallow line-start evidence."""

    sample = [line.strip() for line in lines if line.strip()][:80]
    if not sample:
        return UNKNOWN_PROFILE
    numbered = sum(1 for line in sample if _NUMBERED_START_RE.match(line))
    author_year = sum(1 for line in sample if _AUTHOR_YEAR_START_RE.match(line))
    notes_style = sum(1 for line in sample if _NOTES_STYLE_START_RE.match(line))
    threshold = max(2, min(5, len(sample) // 4))
    notes_threshold = max(3, threshold)
    if numbered >= threshold and numbered >= author_year:
        return NUMERIC_PROFILE
    if author_year >= threshold:
        return AUTHOR_YEAR_PROFILE
    if notes_style >= notes_threshold:
        return NOTES_BIBLIOGRAPHY_PROFILE
    return UNKNOWN_PROFILE
