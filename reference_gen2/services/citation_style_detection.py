from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Literal

from reference_gen2.reference_styles import (
    ReferenceStyleHint,
    infer_style_hint_from_profile,
)

StyleDetectionConfidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class CitationStyleDetectionResult:
    detected_style: ReferenceStyleHint = "unknown"
    confidence: StyleDetectionConfidence = "low"
    signals: list[str] = field(default_factory=list)
    source: str = "none"


_APA_AUTHOR_YEAR_RE = re.compile(
    r"^\s*"
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'`-]+,\s+"
    r"(?:[A-Z]\.\s*){1,4}"
    r"[^()]{0,160}"
    r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)\.\s+\S"
)

_AUTHOR_YEAR_RE = re.compile(
    r"^\s*[A-ZÀ-ÖØ-Þ][^.\n]{0,180}"
    r"(?:\(\s*(?:19|20)\d{2}[a-z]?\s*\)|\b(?:19|20)\d{2}[a-z]?\b)"
)
_NUMBERED_REFERENCE_RE = re.compile(r"^\s*(?:\[\d+\]|\(\d+\)|\d+[.)])\s+\S")
_HARVARD_QUOTED_TITLE_RE = re.compile(
    r"^\s*[A-ZÀ-ÖØ-Þ].{0,180}\b(?:19|20)\d{2}[a-z]?\s*,?\s*['‘’][^'‘’]+['‘’]",
    re.IGNORECASE,
)
_HARVARD_UNPARENTHESIZED_YEAR_RE = re.compile(
    r"^\s*[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'`-]+,\s+"
    r"(?:[A-Z]\.\s*){1,4}"
    r"(?:,\s*)?(?:19|20)\d{2}[a-z]?\b"
)
_MLA_QUOTED_TITLE_RE = re.compile(
    r"^\s*[A-ZÀ-ÖØ-Þ][^.\n]{1,120}\.\s+[\"“][^\"”]+[\"”]\s+",
    re.IGNORECASE,
)
_CHICAGO_BOOK_RE = re.compile(
    r"^\s*[A-ZÀ-ÖØ-Þ][^.\n]{1,120}\.\s+[^.]{3,160}\.\s+"
    r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ .'-]+:\s+[^,]+,\s+(?:19|20)\d{2}\.?",
    re.IGNORECASE,
)
_DUTCH_APA_CUE_RE = re.compile(
    r"\b(?:geraadpleegd|opgehaald|bekeken|bezocht|geopend)\s+op\b|\bz\.d\.\b",
    re.IGNORECASE,
)
_ENGLISH_APA_CUE_RE = re.compile(
    r"\b(?:retrieved|accessed)\s+(?:on|from)\b|\bn\.d\.\b",
    re.IGNORECASE,
)
_DUTCH_HEADING_RE = re.compile(
    r"\b(?:"
    r"literatuur(?:lijst)?|"
    r"bibliografie|"
    r"bronnen(?:lijst)?|"
    r"geraadpleegde\s+bronnen|"
    r"gebruikte\s+literatuur|"
    r"referenties"
    r")\b",
    re.IGNORECASE,
)
_ENGLISH_HEADING_RE = re.compile(
    r"\b(?:references|reference\s+list|bibliography|works\s+cited|literature\s+cited)\b",
    re.IGNORECASE,
)


def detect_citation_style(
    *,
    requested_style_hint: ReferenceStyleHint,
    segmentation_profile: str,
    references: list[str],
    bibliography_heading: str | None = None,
) -> CitationStyleDetectionResult:
    """Detect a list-level citation style only from conservative cues."""

    if requested_style_hint != "unknown":
        return CitationStyleDetectionResult(
            detected_style=requested_style_hint,
            confidence="high",
            signals=["explicit_user_style"],
            source="user",
        )

    profile_style = infer_style_hint_from_profile(segmentation_profile)
    if profile_style == "vancouver":
        return CitationStyleDetectionResult(
            detected_style="vancouver",
            confidence="high",
            signals=[f"segmentation_profile={segmentation_profile}", "decision=numeric_profile"],
            source="segmentation_profile",
        )

    sample = [reference.strip() for reference in references if reference.strip()][:40]
    if not sample:
        return CitationStyleDetectionResult(
            detected_style="apa7_en",
            confidence="high",
            signals=[
                f"segmentation_profile={segmentation_profile or 'unknown'}",
                "decision=fallback_apa7_en",
            ],
            source="reference_list",
        )

    numbered = sum(1 for reference in sample if _NUMBERED_REFERENCE_RE.match(reference))
    apa_author_year = sum(1 for reference in sample if _APA_AUTHOR_YEAR_RE.match(reference))
    author_year = sum(1 for reference in sample if _AUTHOR_YEAR_RE.match(reference))
    harvard_quoted = sum(1 for reference in sample if _HARVARD_QUOTED_TITLE_RE.match(reference))
    harvard_unparenthesized = sum(
        1 for reference in sample if _HARVARD_UNPARENTHESIZED_YEAR_RE.match(reference)
    )
    mla_quoted = sum(1 for reference in sample if _is_mla_reference(reference))
    chicago_books = sum(1 for reference in sample if _CHICAGO_BOOK_RE.match(reference))
    dutch_apa_cues = sum(1 for reference in sample if _DUTCH_APA_CUE_RE.search(reference))
    english_apa_cues = sum(1 for reference in sample if _ENGLISH_APA_CUE_RE.search(reference))
    heading = (bibliography_heading or "").strip()
    dutch_heading = bool(_DUTCH_HEADING_RE.search(heading))
    english_heading = bool(_ENGLISH_HEADING_RE.search(heading))
    profile_name = segmentation_profile.strip().lower()
    threshold = _decision_threshold(len(sample))
    signals = [
        f"segmentation_profile={segmentation_profile or 'unknown'}",
        f"numbered_starts={numbered}/{len(sample)}",
        f"apa_author_year_starts={apa_author_year}/{len(sample)}",
        f"author_year_starts={author_year}/{len(sample)}",
        f"harvard_quoted_titles={harvard_quoted}/{len(sample)}",
        f"harvard_unparenthesized_year={harvard_unparenthesized}/{len(sample)}",
        f"mla_quoted_titles={mla_quoted}/{len(sample)}",
        f"chicago_book_shapes={chicago_books}/{len(sample)}",
        f"apa_nl_cues={dutch_apa_cues}/{len(sample)}",
        f"apa_en_cues={english_apa_cues}/{len(sample)}",
    ]
    if heading:
        signals.append(f"bibliography_heading={_heading_signal(heading)}")

    if numbered >= threshold:
        return _decision("vancouver", signals, "numbered_references")
    if harvard_quoted:
        return _decision("harvard", signals, "harvard_single_quoted_title")
    if mla_quoted:
        return _decision("mla", signals, "mla_double_quoted_title")
    if chicago_books >= threshold or profile_name == "notes_bibliography_profile":
        return _decision("chicago", signals, "chicago_notes_bibliography")
    if dutch_heading and not english_heading:
        return _decision("apa7_nl", signals, "dutch_heading_apa_nl")
    if dutch_apa_cues > english_apa_cues:
        return _decision("apa7_nl", signals, "dutch_apa_cues")
    if english_apa_cues > 0:
        return _decision("apa7_en", signals, "english_apa_cues")
    if apa_author_year >= threshold:
        return _decision("apa7_en", signals, "apa_author_year")
    if harvard_unparenthesized >= threshold:
        return _decision("harvard", signals, "harvard_unparenthesized_year")
    if author_year >= threshold:
        return _decision("apa7_en", signals, "author_year_fallback_apa")
    return _decision("apa7_en", signals, "fallback_apa7_en")


def _heading_signal(value: str) -> str:
    return re.sub(r"\s+", "_", value.strip().casefold())[:80]


def _decision(
    style: ReferenceStyleHint,
    signals: list[str],
    decision_signal: str,
) -> CitationStyleDetectionResult:
    return CitationStyleDetectionResult(
        detected_style=style,
        confidence="high",
        signals=signals + [f"decision={decision_signal}"],
        source="reference_list",
    )


def _decision_threshold(sample_size: int) -> int:
    return max(1, min(3, (sample_size + 1) // 2))


def _is_mla_reference(reference: str) -> bool:
    return bool(_MLA_QUOTED_TITLE_RE.match(reference))
