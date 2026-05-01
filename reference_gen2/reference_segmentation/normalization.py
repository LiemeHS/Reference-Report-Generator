from __future__ import annotations

import html
import re

from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.extractors.models import DocumentExtraction
from reference_gen2.reference_segmentation.models import (
    ReferenceSegmentationError,
    ReferenceStyleHint,
)

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_UNSAFE_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_AUTHOR_TOKEN = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+"
_AUTHOR_PARTICLE = r"(?:de|den|der|van|von|la|le|del|della|di|du|ten|ter)"
_PARTICLE_FAMILY = (
    rf"(?:{_AUTHOR_PARTICLE}\s+{_AUTHOR_TOKEN}(?:\s+{_AUTHOR_TOKEN})?"
    rf"|{_AUTHOR_TOKEN}\s+{_AUTHOR_PARTICLE}\s+{_AUTHOR_TOKEN}(?:\s+{_AUTHOR_TOKEN})?)"
)
_AUTHOR_FAMILY = rf"(?:{_AUTHOR_TOKEN}|{_PARTICLE_FAMILY})"
_INITIALS = r"(?:[A-Z]\.\s*){1,4}"
_AUTHOR_COMMA_START = rf"{_AUTHOR_FAMILY},\s*(?:{_INITIALS}|[A-Z][A-Za-z\-]+\s*,?\s*)"
_AUTHOR_NO_COMMA_START = rf"(?:{_AUTHOR_TOKEN}(?:\s+{_AUTHOR_TOKEN}){{0,2}})\s+[A-Z](?:[A-Z\-\. ]{{0,18}})"
_YEAR_SOON = r".{0,180}?(?:\(\s*(?:19|20)\d{2}[a-z]?\s*\)|\b(?:19|20)\d{2}[a-z]?\b)"
_FIRST_AUTHOR_COMMA_WITH_YEAR_RE = re.compile(
    rf"{_AUTHOR_COMMA_START}{_YEAR_SOON}",
    re.UNICODE | re.IGNORECASE,
)
_FIRST_AUTHOR_NO_COMMA_WITH_YEAR_RE = re.compile(
    rf"{_AUTHOR_NO_COMMA_START}{_YEAR_SOON}",
    re.UNICODE,
)
_LEADING_PROSE_LINE_RE = re.compile(
    r"^\s*(?:author biographies|biographies|notes on contributors|about the author)\b",
    re.IGNORECASE,
)
_MULTI_BLANK_RE = re.compile(r"\n{3,}")
_TRAILING_SPACE_RE = re.compile(r"[ \t]+\n")
_MARKER_ONLY_PREFIX_RE = re.compile(r"^(?:[\[\(]?\d+[\]\)\.]?|[*•·]+)\s*$")


def _first_author_with_year_match(text: str) -> re.Match[str] | None:
    comma_match = _FIRST_AUTHOR_COMMA_WITH_YEAR_RE.search(text)
    if comma_match is not None:
        return comma_match
    return _FIRST_AUTHOR_NO_COMMA_WITH_YEAR_RE.search(text)


def _strip_html_for_segmentation(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div)>", "\n", text, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&nbsp;", " ")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )


def prepare_reference_text_input(
    value: str,
    *,
    max_chars: int = 120000,
) -> str:
    """Validate and normalize public pasted reference-list text.

    This is the Phase 2 plain-text contract for raw reference lists. It keeps
    file/document validation in Phase 1 while making text input safe to segment.
    """
    if len(value) > max_chars:
        raise ReferenceSegmentationError(
            "reference_text_too_large",
            "Reference text is too large.",
            http_status=413,
            details={"max_chars": max_chars},
        )
    if _UNSAFE_CONTROL_RE.search(value):
        raise ReferenceSegmentationError(
            "reference_text_invalid_characters",
            "Reference text contains unsupported characters.",
            http_status=400,
        )
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _strip_html_for_segmentation(normalized)
    normalized = html.unescape(normalized)
    normalized = normalized.replace("\u00a0", " ").replace("\u00ad", "")
    normalized = re.sub(r"[ \t]+", " ", normalized)
    normalized = _TRAILING_SPACE_RE.sub("\n", normalized)
    normalized = _MULTI_BLANK_RE.sub("\n\n", normalized).strip()
    if not normalized:
        raise ReferenceSegmentationError(
            "empty_reference_text",
            "Reference text is empty.",
            http_status=400,
        )
    return normalized


def _repair_paste_boundaries(text: str) -> str:
    text = text.replace("\u00a0", " ").replace("\u00ad", "")
    text = re.sub(r"-\s*\n\s*", "-", text)
    text = re.sub(r"-\s+", "-", text)
    text = re.sub(r"/\s+", "/", text)
    text = re.sub(
        rf"(https?://\S+)\s+(?=({_AUTHOR_COMMA_START}|{_AUTHOR_NO_COMMA_START}){_YEAR_SOON})",
        r"\1\n",
        text,
        flags=re.UNICODE,
    )
    text = re.sub(
        rf"(doi:\s*10\.\d{{4,}}/\S+)\s+(?=({_AUTHOR_COMMA_START}|{_AUTHOR_NO_COMMA_START}){_YEAR_SOON})",
        r"\1\n",
        text,
        flags=re.UNICODE | re.IGNORECASE,
    )
    text = re.sub(
        rf"(?<!\d)(?<! \. )(\.)\s+(?=({_AUTHOR_COMMA_START}|{_AUTHOR_NO_COMMA_START}){_YEAR_SOON})",
        r"\1\n",
        text,
        flags=re.UNICODE,
    )
    text = re.sub(r"\.(\()", r". \1", text)
    return text


def _repair_pdf_inline_author_boundaries(text: str) -> str:
    text = re.sub(
        rf"(?<=\.)\s+(?=({_AUTHOR_COMMA_START}|{_AUTHOR_NO_COMMA_START}){_YEAR_SOON})",
        "\n",
        text,
        flags=re.UNICODE | re.IGNORECASE,
    )
    text = re.sub(
        rf"(https?://(?:dx\.)?doi\.org/10\.\d{{4,9}}/)(?=({_AUTHOR_COMMA_START}){_YEAR_SOON})",
        r"\1\n",
        text,
        flags=re.UNICODE | re.IGNORECASE,
    )
    text = re.sub(
        rf"(doi:\s*10\.\d{{4,9}}/)(?=({_AUTHOR_COMMA_START}){_YEAR_SOON})",
        r"\1\n",
        text,
        flags=re.UNICODE | re.IGNORECASE,
    )
    text = re.sub(
        rf"((?:https?://|www\.)\S+?)(?=({_AUTHOR_TOKEN},\s*(?:{_INITIALS}|[A-Z][A-Za-z\-]+\s*,?\s*)){_YEAR_SOON})",
        r"\1\n",
        text,
        flags=re.UNICODE,
    )
    return text


def _strip_narrow_glued_pdf_prefix(text: str) -> str:
    lines = text.split("\n")
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        match = _first_author_with_year_match(stripped)
        if not match or match.start() == 0:
            return text
        prefix = stripped[: match.start()].strip()
        if not prefix:
            return text
        if _MARKER_ONLY_PREFIX_RE.match(prefix):
            return text
        if len(prefix) > 140 or re.search(r"(https?://|doi:|10\.\d{4,9}/)", prefix, re.IGNORECASE):
            return text
        lines[index] = stripped[match.start() :].lstrip()
        return "\n".join(lines)
    return text


def _strip_leading_prose(text: str) -> str:
    lines = [line for line in text.split("\n")]
    first_ref_index: int | None = None
    for index, raw_line in enumerate(lines):
        stripped = raw_line.strip()
        if not stripped:
            continue
        if _first_author_with_year_match(stripped) and _first_author_with_year_match(stripped).start() == 0:
            first_ref_index = index
            break
    if first_ref_index is None or first_ref_index == 0:
        return text
    skipped = [line.strip() for line in lines[:first_ref_index] if line.strip()]
    if not skipped:
        return text
    if any(re.search(r"(https?://|doi:|10\.\d{4,9}/|\b(?:19|20)\d{2}\b)", line, re.IGNORECASE) for line in skipped):
        return text
    if len(skipped) > 4 and not any(_LEADING_PROSE_LINE_RE.match(line) for line in skipped):
        return text
    return "\n".join(lines[first_ref_index:])


def normalize_reference_list_text(
    bibliography: BibliographySection,
    extraction: DocumentExtraction,
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> str:
    normalized = bibliography.text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = _strip_html_for_segmentation(normalized)
    normalized = _repair_paste_boundaries(normalized)
    if extraction.source_kind == "pdf":
        normalized = _repair_pdf_inline_author_boundaries(normalized)
        normalized = _strip_narrow_glued_pdf_prefix(normalized)
        normalized = _strip_leading_prose(normalized)
    if style_hint == "vancouver":
        normalized = re.sub(r"(?m)^\s*(\[\d+\]|\d+[.)])\s*", r"\1 ", normalized)
    normalized = _TRAILING_SPACE_RE.sub("\n", normalized)
    normalized = _MULTI_BLANK_RE.sub("\n\n", normalized).strip()
    if not normalized:
        raise ReferenceSegmentationError(
            "empty_reference_list_text",
            "Reference segmentation normalization produced no usable text.",
        )
    return normalized
