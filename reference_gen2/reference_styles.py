from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias


ReferenceStyleHint: TypeAlias = Literal[
    "unknown",
    "apa7_nl",
    "apa7_en",
    "mla",
    "chicago",
    "harvard",
    "vancouver",
]


@dataclass(frozen=True)
class ReferenceStyleProfile:
    style_hint: ReferenceStyleHint
    label: str
    segmentation_family: str
    parser_profile: str
    citation_style: str
    citation_locale: str


STYLE_PROFILES: dict[ReferenceStyleHint, ReferenceStyleProfile] = {
    "unknown": ReferenceStyleProfile(
        style_hint="unknown",
        label="Auto",
        segmentation_family="auto",
        parser_profile="generic",
        citation_style="apa-standard",
        citation_locale="nl-NL",
    ),
    "apa7_nl": ReferenceStyleProfile(
        style_hint="apa7_nl",
        label="APA 7 Nederlands",
        segmentation_family="author_year",
        parser_profile="apa7_nl",
        citation_style="apa-standard",
        citation_locale="nl-NL",
    ),
    "apa7_en": ReferenceStyleProfile(
        style_hint="apa7_en",
        label="APA 7 English",
        segmentation_family="author_year",
        parser_profile="apa7_en",
        citation_style="apa-standard",
        citation_locale="en-US",
    ),
    "harvard": ReferenceStyleProfile(
        style_hint="harvard",
        label="Harvard",
        segmentation_family="author_year",
        parser_profile="harvard",
        citation_style="harvard",
        citation_locale="en-GB",
    ),
    "chicago": ReferenceStyleProfile(
        style_hint="chicago",
        label="Chicago",
        segmentation_family="notes_bibliography",
        parser_profile="chicago",
        citation_style="chicago",
        citation_locale="en-US",
    ),
    "mla": ReferenceStyleProfile(
        style_hint="mla",
        label="MLA",
        segmentation_family="notes_bibliography",
        parser_profile="mla",
        citation_style="mla",
        citation_locale="en-US",
    ),
    "vancouver": ReferenceStyleProfile(
        style_hint="vancouver",
        label="Vancouver",
        segmentation_family="numeric",
        parser_profile="vancouver",
        citation_style="vancouver",
        citation_locale="en-US",
    ),
}

SUPPORTED_STYLE_HINTS: frozenset[str] = frozenset(STYLE_PROFILES)

_SEGMENTATION_PROFILE_TO_STYLE_HINT: dict[str, ReferenceStyleHint] = {
    "numeric_profile": "vancouver",
}


def normalize_reference_style(value: str | None) -> ReferenceStyleHint | None:
    style_hint = (value or "unknown").strip().lower().replace("-", "_") or "unknown"
    if style_hint not in STYLE_PROFILES:
        return None
    return style_hint  # type: ignore[return-value]


def infer_style_hint_from_profile(profile_name: str) -> ReferenceStyleHint:
    """Infer a concrete style hint from a segmentation profile hint.

    This intentionally keeps inference conservative and only maps strong numeric
    cues to Vancouver. All other profiles remain auto/"unknown".
    """

    return _SEGMENTATION_PROFILE_TO_STYLE_HINT.get(profile_name.strip().lower(), "unknown")


def profile_for_reference_style(style_hint: str | None) -> ReferenceStyleProfile:
    normalized = normalize_reference_style(style_hint) or "unknown"
    return STYLE_PROFILES[normalized]
