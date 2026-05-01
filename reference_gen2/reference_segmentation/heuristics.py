from __future__ import annotations

import re

from reference_gen2.extractors.models import DocumentExtraction
from reference_gen2.reference_segmentation.profiles import SegmentationProfile

_AUTHOR_TOKEN = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+"
_AUTHOR_PARTICLE = r"(?:de|den|der|van|von|la|le|del|della|di|du|ten|ter)"
_PARTICLE_FAMILY = (
    rf"(?:{_AUTHOR_PARTICLE}\s+{_AUTHOR_TOKEN}(?:\s+{_AUTHOR_TOKEN})?"
    rf"|{_AUTHOR_TOKEN}\s+{_AUTHOR_PARTICLE}\s+{_AUTHOR_TOKEN}(?:\s+{_AUTHOR_TOKEN})?)"
)
_AUTHOR_FAMILY = rf"(?:{_AUTHOR_TOKEN}|{_PARTICLE_FAMILY})"
_INITIALS = r"(?:[A-Z]\.\s*){1,4}"
_AUTHOR_COMMA_START_RE = re.compile(
    rf"^\s*{_AUTHOR_FAMILY},\s*(?:{_INITIALS}|[A-Z][A-Za-z\-]+\s*,?\s*)",
    re.IGNORECASE,
)
_AUTHOR_NO_COMMA_START_RE = re.compile(
    rf"^\s*(?:{_AUTHOR_TOKEN}(?:\s+{_AUTHOR_TOKEN}){{0,2}})\s+[A-Z](?:[A-Z\-\. ]{{0,18}})"
)
_YEAR_RE = re.compile(r"(?:\(\s*(?:19|20)\d{2}[a-z]?\s*\)|\b(?:19|20)\d{2}[a-z]?\b)")
_WEB_DATE_RE = re.compile(
    r"\(\s*(?:19|20)\d{2}\s*,\s*(?:\d{1,2}\s+[A-Za-zÀ-ÖØ-öø-ÿ]+|[A-Za-zÀ-ÖØ-öø-ÿ]+\s+\d{1,2})\s*\)",
    re.IGNORECASE,
)
_STRONG_MARK_RE = re.compile(
    r"(doi:\s*\S+|https?://\S+|www\.\S+|\bissn\b|\bisbn\b|\b10\.\d{4,9}/\S+)",
    re.IGNORECASE,
)
_AUTHOR_SEP_AT_END_RE = re.compile(r"(?:,|&|\band\b)\s*$", re.IGNORECASE)
_ENDS_WITH_YEARLINE_RE = re.compile(r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)\.\s*$")
_NONAUTHOR_ORG_START_RE = re.compile(r"^\s*[A-Z0-9][^\n]{4,}$")
_NONAUTHOR_FLEX_ORG_START_RE = re.compile(r"^\s*[A-Za-z0-9][^\n]{4,}$")
_ORG_YEAR_START_RE = re.compile(
    r"^\s*[A-Z0-9][^\n]{0,80}\(\s*(?:19|20)\d{2}[a-z]?(?:\s*,|\s*\))",
    re.IGNORECASE,
)
_ORG_YEAR_FLEX_START_RE = re.compile(
    r"^\s*[A-Za-z0-9][^\n]{0,80}\(\s*(?:19|20)\d{2}[a-z]?(?:\s*,|\s*\))",
    re.IGNORECASE,
)
_URL_ONLY_RE = re.compile(r"^\s*(?:https?://|www\.)\S+\s*$", re.IGNORECASE)
_DOI_ONLY_RE = re.compile(r"^\s*(?:doi:\s*)?(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/\S+\s*$", re.IGNORECASE)
_RETRIEVAL_PREFIX_RE = re.compile(
    r"^\s*(?:retrieved|accessed|opgehaald|geraadpleegd)\b", re.IGNORECASE
)
_JOURNALISH_RE = re.compile(
    r"\b(?:\d+\(\d+\)|pp?\.\s*\d|vol\.?|volume|issue|journal|press|publisher|https?://|doi:|10\.)",
    re.IGNORECASE,
)
_TITLE_LED_START_RE = re.compile(
    r"^\s*(?:[\"“”']?[A-Z][^.!?]{8,120}\.)\s+[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+",
    re.UNICODE,
)
_NUMERIC_START_RE = re.compile(
    r"^\s*(?:\[(?:\d+|[ivxlcdm]+)\]|(?:\d+|[ivxlcdm]+)[\.\)])\s+",
    re.IGNORECASE,
)
_LOWERCASE_CONTINUATION_RE = re.compile(
    r"^\s*(?:and|or|en|of|van|von|der|den|de|het|the)\b",
)
_JOURNAL_METADATA_START_RE = re.compile(
    r"^\s*[A-Z][A-Za-zÀ-ÖØ-öø-ÿ0-9&'`\-:/ ]{3,120},\s*\d",
    re.UNICODE,
)
_VOLUME_PAGES_START_RE = re.compile(
    r"^\s*(?:vol\.?\s*\d+|\d+\s*(?:\(\d+\))?[,;:]\s*(?:\d+|[A-Z]?\d+|Article\b))",
    re.IGNORECASE,
)
_ARTICLE_NUMBER_RE = re.compile(r"\b(?:article|e)\s*[A-Za-z]?\d+\b", re.IGNORECASE)
_REPORT_TAIL_WITH_URL_RE = re.compile(
    r"^\s*(?:final\s+report|report|part\s+\d+)\b.*(?:https?://|www\.)",
    re.IGNORECASE,
)


def collapse_spaces(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u00a0", " ")).strip()


def normalize_line(text: str) -> str:
    return collapse_spaces((text or "").replace("\u00ad", ""))


def strip_list_marker(line: str) -> tuple[str, bool]:
    stripped = line.lstrip()
    patterns = (
        r"^\[(?:\d+|[ivxlcdm]+)\]\s+",
        r"^(?:\d+|[ivxlcdm]+)[\.\)]\s+",
        r"^[*•·]\s+",
    )
    for pattern in patterns:
        match = re.match(pattern, stripped, re.IGNORECASE)
        if match:
            return stripped[match.end() :].lstrip(), True
    return stripped, False


def line_has_year(text: str) -> bool:
    return bool(_YEAR_RE.search(text))


def line_has_web_date(text: str) -> bool:
    return bool(_WEB_DATE_RE.search(text))


def line_has_strong_marker(text: str) -> bool:
    return bool(_STRONG_MARK_RE.search(text))


def looks_like_author_start(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _AUTHOR_COMMA_START_RE.match(stripped)
        or _AUTHOR_NO_COMMA_START_RE.match(stripped)
    )


def looks_like_org_website_start(line: str) -> bool:
    stripped = line.strip()
    if not (_NONAUTHOR_ORG_START_RE.match(stripped) or _NONAUTHOR_FLEX_ORG_START_RE.match(stripped)):
        return False
    if looks_like_author_start(stripped):
        return False
    if _RETRIEVAL_PREFIX_RE.match(stripped):
        return False
    return bool(
        line_has_web_date(stripped)
        or _ORG_YEAR_START_RE.match(stripped)
        or _ORG_YEAR_FLEX_START_RE.match(stripped)
        or "(n.d.)" in stripped.casefold()
        or "z.d." in stripped.casefold()
    )


def looks_like_title_led_start(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if looks_like_author_start(stripped) or looks_like_org_website_start(stripped):
        return False
    return bool(_TITLE_LED_START_RE.match(stripped))


def is_obvious_continuation_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    return bool(
        _URL_ONLY_RE.match(stripped)
        or _DOI_ONLY_RE.match(stripped)
        or _RETRIEVAL_PREFIX_RE.match(stripped)
        or _JOURNALISH_RE.search(stripped)
    )


def looks_like_tail_fragment_start(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if (
        _URL_ONLY_RE.match(stripped)
        or _DOI_ONLY_RE.match(stripped)
        or _RETRIEVAL_PREFIX_RE.match(stripped)
        or _JOURNAL_METADATA_START_RE.match(stripped)
        or _VOLUME_PAGES_START_RE.match(stripped)
        or _REPORT_TAIL_WITH_URL_RE.match(stripped)
    ):
        return True
    if _LOWERCASE_CONTINUATION_RE.match(stripped):
        return True
    if _ARTICLE_NUMBER_RE.search(stripped) and not looks_like_author_start(stripped):
        return True
    return False


def buffer_in_author_block(buffer: list[str]) -> bool:
    joined = " ".join(buffer).strip()
    return bool(buffer) and not line_has_year(joined) and not line_has_strong_marker(joined)


def buffer_ends_with_author_separator(buffer: list[str]) -> bool:
    return bool(_AUTHOR_SEP_AT_END_RE.search(" ".join(buffer).strip()))


def buffer_looks_finished(buffer: list[str]) -> bool:
    joined = " ".join(buffer).strip()
    if not joined:
        return False
    if _ENDS_WITH_YEARLINE_RE.search(joined) and not line_has_strong_marker(joined):
        return False
    if re.search(r"(doi:\s*\S+|https?://\S+|www\.\S+)\s*$", joined, re.IGNORECASE):
        return True
    if joined.endswith(".") and line_has_year(joined) and len(joined) >= 60:
        return True
    if joined.endswith(".") and (len(joined) >= 120 or line_has_strong_marker(joined)):
        return True
    if line_has_strong_marker(joined) and len(joined) >= 80:
        return True
    return False


def buffer_starts_like_reference_head(buffer: list[str]) -> bool:
    joined = " ".join(buffer).strip()
    if not joined:
        return False
    return looks_like_author_start(joined) or looks_like_org_website_start(joined)


def buffer_lacks_terminal_metadata(buffer: list[str]) -> bool:
    joined = " ".join(buffer).strip()
    if not joined:
        return True
    if line_has_strong_marker(joined):
        return False
    if _JOURNAL_METADATA_START_RE.search(joined):
        return False
    if _ARTICLE_NUMBER_RE.search(joined):
        return False
    if re.search(r"\b\d+\s*(?:\(\d+\))?[,;:]\s*\d", joined):
        return False
    return True


def candidate_looks_like_ref_start(
    lines: list[str], index: int, profile: SegmentationProfile
) -> bool:
    if index >= len(lines):
        return False
    line0 = normalize_line(lines[index])
    if not line0:
        return False
    if profile.prefer_numeric_starts and _NUMERIC_START_RE.match(lines[index].lstrip()):
        return True
    is_author_start = looks_like_author_start(line0)
    is_org_start = looks_like_org_website_start(line0)
    is_title_led_start = profile.allow_title_led_starts and looks_like_title_led_start(
        line0
    )
    if not (is_author_start or is_org_start or is_title_led_start):
        return False
    lookahead = [line0]
    for next_index in (index + 1, index + 2):
        if next_index >= len(lines):
            break
        candidate = normalize_line(lines[next_index])
        if not candidate:
            break
        lookahead.append(candidate)
    window = " ".join(lookahead)
    if is_author_start:
        if profile.prefer_author_year:
            return line_has_year(window) or line_has_strong_marker(window)
        return True
    if is_org_start:
        return bool(
            _ORG_YEAR_START_RE.match(line0)
            or _ORG_YEAR_FLEX_START_RE.match(line0)
            or line_has_web_date(line0)
            or line_has_year(window)
            or re.search(r"^(?:https?://|www\.)\S+", line0, re.IGNORECASE)
            or (
                len(lookahead) >= 2
                and re.search(r"^(?:https?://|www\.)\S+", lookahead[1], re.IGNORECASE)
            )
        )
    if line_has_web_date(line0):
        return True
    if re.search(r"(?:https?://|www\.)\S+", line0, re.IGNORECASE):
        return True
    if len(lookahead) >= 2 and re.search(r"^(?:https?://|www\.)\S+", lookahead[1], re.IGNORECASE):
        return True
    if is_title_led_start:
        return line_has_year(window) or line_has_strong_marker(window) or len(window) >= 80
    return False


def detect_marker_mode(lines: list[str], extraction: DocumentExtraction) -> str | None:
    numbered_count = 0
    bullet_count = 0
    for raw_line in lines:
        line = raw_line.lstrip()
        if re.match(r"^(?:\d+|[ivxlcdm]+)[\.\)]\s+", line, re.IGNORECASE) or re.match(
            r"^\[(?:\d+|[ivxlcdm]+)\]\s+", line, re.IGNORECASE
        ):
            numbered_count += 1
        elif re.match(r"^[*•·]\s+", line):
            bullet_count += 1
    threshold = 2 if extraction.source_kind in {"docx", "text"} else 3
    if numbered_count >= threshold:
        return "numbered"
    if bullet_count >= threshold:
        return "bulleted"
    return None
