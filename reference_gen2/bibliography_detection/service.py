from __future__ import annotations

"""Phase 1 bibliography detection only.

This module answers "where is the bibliography?" and intentionally stops before
any per-reference segmentation logic.
"""

import re
from dataclasses import dataclass

from reference_gen2.api.settings import (
    BIB_MAX_CHARS,
    BIB_MIN_CHARS,
    BIB_MIN_UNITS,
    BIB_PDF_HEADING_MIN_LINE_CHARS,
    BIB_PDF_HEADING_SCAN_LINES,
    BIB_REQUIRE_HEADING,
)
from reference_gen2.bibliography.models import (
    BibliographyDetectionError,
    BibliographySection,
)
from reference_gen2.extractors.models import DocumentExtraction, TextUnit

_WHITESPACE_RE = re.compile(r"\s+")
_TRAILING_PUNCT_RE = re.compile(r"[\s:;,\-\u2013\u2014.]+$")
_LEADING_NUMBER_RE = re.compile(r"^\s*(?:\d+|[ivxlcdm]+)\s*[\.\)]\s*", re.IGNORECASE)
_CHAPTER_PREFIX_RE = re.compile(
    r"^\s*(?:chapter|hoofdstuk)\s+(?:\d+|[ivxlcdm]+)\s*[:.\-)]\s*",
    re.IGNORECASE,
)
_PAGE_NUMBER_RE = re.compile(r"^\(?\d+\)?$")
_DOI_RE = re.compile(r"\b10\.\d{4,9}/\S+\b", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_AUTHORISH_RE = re.compile(r"\b[A-Z][A-Za-z'`-]+,\s*(?:[A-Z]\.\s*){1,4}")
_NON_ALNUM_RE = re.compile(r"[^0-9A-Za-z]+")
_OPTIONAL_MARKER_RE = re.compile(r"^\s*[*\u2022]+\s*")
_ORG_YEAR_START_RE = re.compile(
    r"^\s*(?:[*\u2022]+\s*)?[A-Z][\w&'`./-]*(?:\s+[A-Z][\w&'`./-]*){0,6}\s*\(\d{4}[a-z]?\)",
)
_NUMERIC_REFERENCE_START_RE = re.compile(
    r"^\s*(?:\[\d+\]|\d+\.)\s+[A-Z][\w&'`./-]*(?:\s+[A-Z][\w&'`./-]*){0,8}",
)
_BIOGRAPHY_PREFIX_RE = re.compile(
    r"^(?:Jenny Edvardsson|Lotta Leden|Kristina Juter)\s+(?:is|texts\b)",
    re.IGNORECASE,
)
_PDF_JOURNAL_FOOTER_RE = re.compile(
    r"^(?:\d+\s*-\s*)?[A-ZÀ-ÿ][A-Za-zÀ-ÿ0-9 .,'’\-]+-\s*Vol\s*\d+\s*\(\d+\)\s*\d{4}(?:\s*-\s*\d+)?\s*$",
    re.IGNORECASE,
)

_BIBLIOGRAPHY_SYNONYMS = {
    "reference",
    "references",
    "bibliography",
    "works cited",
    "list of references",
    "reference list",
    "referencelist",
    "literatuur",
    "literatuurlijst",
    "literature",
    "bronnenlijst",
    "lijst van bronnen",
    "referenties",
    "bibliografie",
    "source list",
}
_NON_BIB_HEADINGS = {
    "appendix",
    "appendices",
    "appendix a",
    "appendix b",
    "appendix c",
    "appendix d",
    "bijlage",
    "bijlagen",
    "acknowledgments",
    "acknowledgements",
    "dankwoord",
    "about the author",
    "over de auteur",
}
_DOCX_HEADING_STYLES = {
    "heading 1",
    "heading 2",
    "heading 3",
    "kop 1",
    "kop 2",
    "kop 3",
    "title",
    "subtitle",
}


@dataclass(frozen=True)
class _HeadingMatch:
    unit: TextUnit
    line_index: int
    line_text: str


def _collapse_spaces(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", (text or "").strip())


def _normalize_heading_candidate(text: str) -> str:
    normalized = _collapse_spaces(text)
    normalized = _TRAILING_PUNCT_RE.sub("", normalized)
    normalized = _CHAPTER_PREFIX_RE.sub("", normalized)
    normalized = _LEADING_NUMBER_RE.sub("", normalized)
    normalized = _TRAILING_PUNCT_RE.sub("", normalized)
    normalized = _collapse_spaces(normalized)
    return normalized.casefold()


def _docx_label_is_heading(label: str | None) -> bool:
    return (label or "").strip().casefold() in _DOCX_HEADING_STYLES


def _text_has_reference_signals(text: str) -> bool:
    return bool(
        _YEAR_RE.search(text)
        or _DOI_RE.search(text)
        or _URL_RE.search(text)
        or _AUTHORISH_RE.search(text)
    )


def _is_uppercase_heading_like(text: str) -> bool:
    letters = [char for char in text if char.isalpha()]
    if len(text.strip()) <= 10 or len(letters) < 4:
        return False
    uppercase_letters = sum(1 for char in letters if char.isupper())
    return uppercase_letters / len(letters) >= 0.8


def _scanned_pdf_lines(unit: TextUnit) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    for index, raw_line in enumerate(unit.text.splitlines()):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if len(stripped) < BIB_PDF_HEADING_MIN_LINE_CHARS:
            continue
        if _PAGE_NUMBER_RE.fullmatch(stripped):
            continue
        candidates.append((index, stripped))
        if len(candidates) >= BIB_PDF_HEADING_SCAN_LINES:
            break
    return candidates


def _unit_lines_without_heading(
    unit: TextUnit,
    heading_line_index: int | None,
    *,
    start_line_index: int | None = None,
) -> list[str]:
    return _unit_lines_from_indices(
        unit,
        exclude_line_index=heading_line_index,
        start_line_index=start_line_index,
    )


def _unit_lines_from_indices(
    unit: TextUnit,
    *,
    exclude_line_index: int | None = None,
    start_line_index: int | None = None,
) -> list[str]:
    if unit.layout == "blank":
        return []
    lines = [
        line.rstrip()
        for line in unit.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    ]
    filtered: list[str] = []
    for idx, line in enumerate(lines):
        if exclude_line_index is not None and idx == exclude_line_index:
            continue
        if start_line_index is not None and idx < start_line_index:
            continue
        if line.strip():
            filtered.append(line)
    return filtered


def _normalize_pdf_reference_candidate(line: str) -> str:
    return _OPTIONAL_MARKER_RE.sub("", line.strip())


def _looks_like_pdf_reference_start(line: str) -> bool:
    candidate = _normalize_pdf_reference_candidate(line)
    if not candidate:
        return False
    if _NUMERIC_REFERENCE_START_RE.match(candidate):
        return True
    if _AUTHORISH_RE.search(candidate) and _YEAR_RE.search(candidate[:120]):
        return True
    if _ORG_YEAR_START_RE.match(candidate):
        return True
    return False


def _is_pdf_heading_preface_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    lowered = stripped.casefold()
    if lowered == "author biographies":
        return True
    if lowered.startswith("*=reference to an article included in the scoping review"):
        return True
    if lowered.startswith("reference to an article included in the scoping review"):
        return True
    if _BIOGRAPHY_PREFIX_RE.match(stripped):
        return True
    return False


def _find_pdf_reference_start_line_index(
    unit: TextUnit,
    heading_line_index: int,
) -> tuple[int | None, list[str]]:
    warnings: list[str] = []
    lines = unit.text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    preface_trimmed = False
    note_trimmed = False
    for idx in range(heading_line_index + 1, len(lines)):
        stripped = lines[idx].strip()
        if not stripped:
            continue
        next_nonblank = ""
        for follow_idx in range(idx + 1, len(lines)):
            candidate = lines[follow_idx].strip()
            if candidate:
                next_nonblank = candidate
                break
        if _looks_like_pdf_reference_start(stripped) or (
            next_nonblank
            and _looks_like_pdf_reference_start(f"{stripped} {next_nonblank}")
        ):
            if preface_trimmed:
                warnings.append("pdf_bibliography_preface_trimmed")
            if note_trimmed:
                warnings.append("pdf_bibliography_note_line_stripped")
            return idx, warnings
        if _is_pdf_heading_preface_line(stripped):
            preface_trimmed = True
            if "reference to an article included in the scoping review" in stripped.casefold():
                note_trimmed = True
            continue
        preface_trimmed = True
    if preface_trimmed:
        warnings.append("pdf_bibliography_preface_trimmed")
    if note_trimmed:
        warnings.append("pdf_bibliography_note_line_stripped")
    return None, warnings


def _is_pdf_boilerplate_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _PAGE_NUMBER_RE.fullmatch(stripped):
        return True
    if "nnjjssrree" in stripped.casefold():
        return True
    if (
        "et al." in stripped.casefold()
        and not _YEAR_RE.search(stripped)
        and not _DOI_RE.search(stripped)
        and not _URL_RE.search(stripped)
    ):
        return True
    if not any(char.isalnum() for char in stripped):
        return True
    if "|" in stripped and not _AUTHORISH_RE.search(stripped):
        return True
    compact = _NON_ALNUM_RE.sub("", stripped)
    if len(compact) <= 2 and not _text_has_reference_signals(stripped):
        return True
    return False


def _clean_pdf_section_lines(lines: list[str]) -> tuple[list[str], bool]:
    cleaned = [line for line in lines if line.strip()]
    stripped_any = False
    while cleaned and _is_pdf_boilerplate_line(cleaned[0]):
        cleaned.pop(0)
        stripped_any = True
    while cleaned and _is_pdf_boilerplate_line(cleaned[-1]):
        cleaned.pop()
        stripped_any = True
    return cleaned, stripped_any


def _is_pdf_running_header_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    lowered = stripped.casefold()
    if lowered == "use of literary texts in science teaching: a scoping review":
        return True
    if lowered == "jenny edvardsson, lotta leden & kristina juter":
        return True
    return False


def _is_pdf_footer_header_line(line: str) -> bool:
    stripped = _collapse_spaces(line)
    if not stripped:
        return False
    if _PDF_JOURNAL_FOOTER_RE.fullmatch(stripped):
        return True
    return False


def _section_text_for_units(
    extraction: DocumentExtraction,
    start_index: int,
    end_index: int,
    heading_unit_index: int | None,
    heading_line_index: int | None,
    *,
    heading_start_line_index: int | None = None,
) -> tuple[str, list[str]]:
    chunks: list[str] = []
    warnings: list[str] = []
    stripped_pdf_boilerplate = False
    stripped_running_header = False
    stripped_footer_header = False
    for idx in range(start_index, end_index + 1):
        unit = extraction.text_units[idx]
        if extraction.source_kind == "docx" and unit.layout == "blank":
            chunks.append("")
            continue
        unit_lines = _unit_lines_from_indices(
            unit,
            exclude_line_index=heading_line_index if idx == heading_unit_index else None,
            start_line_index=heading_start_line_index if idx == heading_unit_index else None,
        )
        if extraction.source_kind == "pdf":
            running_header_filtered_lines = [
                line for line in unit_lines if not _is_pdf_running_header_line(line)
            ]
            if len(running_header_filtered_lines) != len(unit_lines):
                stripped_running_header = True
            unit_lines = running_header_filtered_lines
            footer_filtered_lines = [
                line for line in unit_lines if not _is_pdf_footer_header_line(line)
            ]
            if len(footer_filtered_lines) != len(unit_lines):
                stripped_footer_header = True
            unit_lines = footer_filtered_lines
            filtered_lines = [
                line for line in unit_lines if not _is_pdf_boilerplate_line(line)
            ]
            if len(filtered_lines) != len(unit_lines):
                stripped_pdf_boilerplate = True
            unit_lines = filtered_lines
            unit_lines, stripped = _clean_pdf_section_lines(unit_lines)
            stripped_pdf_boilerplate = stripped_pdf_boilerplate or stripped
        unit_text = "\n".join(unit_lines).strip()
        if not unit_text:
            continue
        chunks.append(unit_text)
    joiner = "\n\n" if extraction.source_kind == "pdf" else "\n"
    if stripped_pdf_boilerplate:
        warnings.append("pdf_page_boilerplate_stripped")
    if stripped_running_header:
        warnings.append("pdf_bibliography_running_header_stripped")
    if stripped_footer_header:
        warnings.append("pdf_bibliography_footer_header_stripped")
    return joiner.join(chunks).strip(), warnings


def _find_docx_heading(extraction: DocumentExtraction) -> _HeadingMatch | None:
    plain_matches: list[_HeadingMatch] = []
    styled_matches: list[_HeadingMatch] = []
    for unit in extraction.text_units:
        raw_lines = unit.text.splitlines()
        line_match = next(
            ((idx, line.strip()) for idx, line in enumerate(raw_lines) if line.strip()),
            None,
        )
        if line_match is None:
            continue
        raw_index, candidate_line = line_match
        normalized = _normalize_heading_candidate(candidate_line)
        if normalized not in _BIBLIOGRAPHY_SYNONYMS:
            continue
        match = _HeadingMatch(unit=unit, line_index=raw_index, line_text=candidate_line)
        if _docx_label_is_heading(unit.label):
            styled_matches.append(match)
        else:
            plain_matches.append(match)
    if styled_matches:
        return styled_matches[0]
    if plain_matches:
        return plain_matches[0]
    return None


def _find_pdf_heading(extraction: DocumentExtraction) -> _HeadingMatch | None:
    candidates: list[_HeadingMatch] = []
    for unit in extraction.text_units:
        for line_index, line in _scanned_pdf_lines(unit):
            if _normalize_heading_candidate(line) in _BIBLIOGRAPHY_SYNONYMS:
                candidates.append(
                    _HeadingMatch(unit=unit, line_index=line_index, line_text=line)
                )
        for line_index, line in enumerate(unit.text.splitlines()):
            stripped = line.strip()
            if not stripped:
                continue
            if len(stripped) > 80:
                continue
            if _normalize_heading_candidate(stripped) not in _BIBLIOGRAPHY_SYNONYMS:
                continue
            match = _HeadingMatch(unit=unit, line_index=line_index, line_text=stripped)
            if match not in candidates:
                candidates.append(match)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: _score_pdf_heading_candidate(extraction, candidate),
    )


def _score_pdf_heading_candidate(
    extraction: DocumentExtraction,
    match: _HeadingMatch,
) -> tuple[int, int, int, int]:
    probe_lines: list[str] = []
    reference_start_count = 0
    early_reference_bonus = 0
    prose_penalty = 0
    unit_distance_limit = min(len(extraction.text_units), match.unit.unit_index + 3)

    for unit_index in range(match.unit.unit_index, unit_distance_limit):
        unit = extraction.text_units[unit_index]
        start_line = match.line_index + 1 if unit_index == match.unit.unit_index else 0
        for raw_line in unit.text.splitlines()[start_line:]:
            stripped = raw_line.strip()
            if not stripped:
                continue
            if _is_pdf_running_header_line(stripped) or _is_pdf_footer_header_line(stripped):
                continue
            if _is_pdf_boilerplate_line(stripped):
                continue
            probe_lines.append(stripped)
            if _looks_like_pdf_reference_start(stripped):
                reference_start_count += 1
                if len(probe_lines) <= 8:
                    early_reference_bonus += 1
            elif len(probe_lines) <= 8 and not _text_has_reference_signals(stripped):
                prose_penalty += 1
            if len(probe_lines) >= 24:
                break
        if len(probe_lines) >= 24:
            break

    return (
        early_reference_bonus,
        reference_start_count,
        -prose_penalty,
        match.unit.unit_index,
    )


def _find_docx_end_index(extraction: DocumentExtraction, start_index: int) -> int:
    for idx in range(start_index + 1, len(extraction.text_units)):
        unit = extraction.text_units[idx]
        first_line = next(
            (line.strip() for line in unit.text.splitlines() if line.strip()), ""
        )
        if (
            _docx_label_is_heading(unit.label)
            and _normalize_heading_candidate(first_line) not in _BIBLIOGRAPHY_SYNONYMS
        ):
            return idx - 1
    return len(extraction.text_units) - 1


def _has_search_query_pattern(text: str) -> bool:
    """Detect if text looks like database search query syntax (e.g., Boolean operators)."""
    if not text:
        return False
    # Count Boolean operators and database-specific terms
    boolean_count = text.count(" OR ") + text.count(" AND ") + text.count(" ED ") + text.count(" AB ")
    # If we have many Boolean operators relative to text length, it's likely search syntax
    if len(text) > 100 and boolean_count > 10:
        return True
    # Check for reversed text patterns (common in appendix formatting issues)
    words = text.split()
    if len(words) > 20:
        # Sample some words to see if they look reversed
        reversed_count = sum(1 for word in words[:50] if len(word) > 3 and word[::-1].lower() in {
            "grade", "school", "education", "science", "student", "teacher", "physics", "chemistry", "biology"
        })
        if reversed_count > 3:
            return True
    return False


def _find_pdf_end_index(extraction: DocumentExtraction, start_index: int) -> int:
    for idx in range(start_index + 1, len(extraction.text_units)):
        unit = extraction.text_units[idx]
        scanned = _scanned_pdf_lines(unit)
        first_line = scanned[0][1] if scanned else ""
        if not first_line:
            continue
        page_looks_like_refs = _text_has_reference_signals(unit.text)
        if _normalize_heading_candidate(first_line) in _NON_BIB_HEADINGS:
            return idx - 1
        if _is_uppercase_heading_like(first_line) and not page_looks_like_refs:
            return idx - 1
        # Check if the page contains search query patterns (appendix content)
        if _has_search_query_pattern(unit.text):
            return idx - 1
    return len(extraction.text_units) - 1


def detect_bibliography(extraction: DocumentExtraction) -> BibliographySection:
    if extraction.source_kind == "docx":
        heading_match = _find_docx_heading(extraction)
    else:
        heading_match = _find_pdf_heading(extraction)

    if heading_match is None:
        if BIB_REQUIRE_HEADING:
            raise BibliographyDetectionError(
                "bibliography_heading_not_found",
                "No bibliography heading was found in the extracted document text.",
                details={
                    "source_kind": extraction.source_kind,
                    "unit_count": extraction.unit_count,
                    "require_heading": BIB_REQUIRE_HEADING,
                },
            )
        raise BibliographyDetectionError(
            "bibliography_detection_failed",
            "Bibliography detection without a heading is not supported in Phase 3.",
            details={
                "source_kind": extraction.source_kind,
                "unit_count": extraction.unit_count,
                "require_heading": BIB_REQUIRE_HEADING,
            },
        )

    heading_unit = heading_match.unit
    heading_line_index = heading_match.line_index
    heading_start_line_index: int | None = None
    start_warnings: list[str] = []
    start_index = heading_unit.unit_index
    if extraction.source_kind == "pdf":
        heading_start_line_index, start_warnings = _find_pdf_reference_start_line_index(
            heading_unit,
            heading_line_index,
        )
    if not _unit_lines_from_indices(
        heading_unit,
        exclude_line_index=heading_line_index,
        start_line_index=heading_start_line_index if extraction.source_kind == "pdf" else None,
    ):
        start_index += 1
    if extraction.source_kind == "docx":
        end_index = _find_docx_end_index(extraction, start_index)
    else:
        end_index = _find_pdf_end_index(extraction, start_index)

    if start_index > end_index or start_index >= len(extraction.text_units):
        raise BibliographyDetectionError(
            "empty_bibliography_section",
            "The bibliography heading was found, but no usable bibliography text followed it.",
            details={
                "start_unit_index": start_index,
                "end_unit_index": end_index,
                "heading_unit_index": heading_unit.unit_index,
                "text_unit_count": len(extraction.text_units),
            },
        )

    section_text, section_warnings = _section_text_for_units(
        extraction,
        start_index=start_index,
        end_index=end_index,
        heading_unit_index=heading_unit.unit_index,
        heading_line_index=heading_line_index,
        heading_start_line_index=heading_start_line_index,
    )
    section_warnings = start_warnings + [
        warning for warning in section_warnings if warning not in start_warnings
    ]

    if not section_text:
        raise BibliographyDetectionError(
            "empty_bibliography_section",
            "The bibliography heading was found, but no usable bibliography text followed it.",
            details={
                "start_unit_index": start_index,
                "end_unit_index": end_index,
                "heading_unit_index": heading_unit.unit_index,
            },
        )

    effective_unit_count = end_index - start_index + 1
    if effective_unit_count < BIB_MIN_UNITS:
        raise BibliographyDetectionError(
            "bibliography_section_too_short",
            "The detected bibliography section is too short.",
            details={
                "effective_unit_count": effective_unit_count,
                "min_units": BIB_MIN_UNITS,
            },
        )
    if len(section_text) < BIB_MIN_CHARS:
        raise BibliographyDetectionError(
            "bibliography_section_too_short",
            f"The detected bibliography section is shorter than {BIB_MIN_CHARS} characters.",
            details={
                "bibliography_char_count": len(section_text),
                "min_chars": BIB_MIN_CHARS,
            },
        )
    if len(section_text) > BIB_MAX_CHARS:
        raise BibliographyDetectionError(
            "bibliography_section_too_large",
            f"The detected bibliography section exceeds {BIB_MAX_CHARS} characters.",
            details={
                "bibliography_char_count": len(section_text),
                "max_chars": BIB_MAX_CHARS,
            },
        )

    return BibliographySection(
        heading=heading_match.line_text,
        heading_unit_index=heading_unit.unit_index,
        start_unit_index=start_index,
        end_unit_index=end_index,
        text=section_text,
        warnings=section_warnings,
    )
