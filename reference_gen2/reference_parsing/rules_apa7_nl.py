from __future__ import annotations

import re
from dataclasses import replace

from reference_gen2.reference_parsing.classification import normalize_reference_for_apa7_nl
from reference_gen2.reference_parsing.models import (
    AccessMetadata,
    ParsedName,
    ParsedReferenceData,
)

_RETRIEVED_PATTERN = re.compile(
    r"(?P<phrase>Geraadpleegd op\s+(?P<date>.+?),\s+van\s+(?P<url>https?://\S+|www\.\S+))",
    re.IGNORECASE,
)
_GREY_LITERATURE_PATTERN = re.compile(
    r"\b(rapport|jaarverslag|brochure|persbericht|discussion paper)\b",
    re.IGNORECASE,
)
_YEAR_MARKER_PATTERN = re.compile(r"\(\s*(?:19|20)\d{2}[a-z]?\b", re.IGNORECASE)
_EDITOR_LIST_PATTERN = re.compile(
    r"\bIn:?\s+(?P<names>.+?)\s*\(\s*(?:red\.?|reds\.?)\s*\)",
    re.IGNORECASE,
)
_NAME_TOKEN = r"[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+"
_NAME_PARTICLES = {
    "de",
    "del",
    "della",
    "den",
    "der",
    "di",
    "du",
    "la",
    "le",
    "ten",
    "ter",
    "van",
    "von",
}
_NAME_PARTICLE = rf"(?:{'|'.join(sorted(_NAME_PARTICLES))})"
_FAMILY_PATTERN = (
    rf"(?:{_NAME_PARTICLE}\s+{_NAME_TOKEN}|{_NAME_TOKEN}(?:\s+{_NAME_PARTICLE}\s+{_NAME_TOKEN})?)"
)
_INITIALS_PATTERN = r"(?:[A-Z](?:-[A-Z])?\.\s*){1,4}"
_SURNAME_FIRST_NAME_PATTERN = re.compile(
    rf"(?<!\.\s)(?P<family>{_FAMILY_PATTERN}),\s*(?P<given>{_INITIALS_PATTERN})",
)
_INITIAL_FIRST_NAME_PATTERN = re.compile(
    rf"(?P<given>{_INITIALS_PATTERN})(?P<family>{_FAMILY_PATTERN})",
)
_SURNAME_INITIALS_FALLBACK_PATTERN = re.compile(
    rf"(?P<family>{_NAME_TOKEN})\s+(?P<given>[A-Z](?:[A-Z]+|(?:-[A-Z])+)?)(?=\s*(?:,|;|$|\b(?:en|EN|and|AND)\b))"
)
_SOFT_HYPHENATED_WORD_PATTERN = re.compile(r"\b([A-Za-zÀ-ÖØ-öø-ÿ]{4,})-([a-zà-öø-ÿ]{2,4})\b")
_PRESERVED_HYPHENATED_WORDS = {
    "cost-benefit",
    "cross-case",
    "e-mail",
    "full-time",
    "in-depth",
    "in-work",
    "long-term",
    "part-time",
    "self-help",
    "short-term",
    "well-being",
}


def apply_apa7_nl_rules(
    parsed: ParsedReferenceData | None,
    raw_reference: str,
) -> ParsedReferenceData | None:
    if parsed is None:
        return None

    raw_reference = normalize_reference_for_apa7_nl(raw_reference)
    updated = parsed
    updated = _apply_retrieval_clause(updated, raw_reference)
    updated = _apply_missing_date(updated, raw_reference)
    updated = _apply_organization_author(updated)
    updated = _apply_grey_literature_hints(updated, raw_reference)
    updated = _repair_dutch_contributor_lists(updated, raw_reference)
    updated = _repair_single_author_misfiled_as_editor(updated, raw_reference)
    updated = _repair_soft_hyphenated_fields(updated)
    return updated


def _apply_retrieval_clause(
    parsed: ParsedReferenceData,
    raw_reference: str,
) -> ParsedReferenceData:
    match = _RETRIEVED_PATTERN.search(raw_reference)
    if not match:
        return parsed

    url = match.group("url")
    if url.startswith("www."):
        url = f"https://{url}"

    access = parsed.access or AccessMetadata()
    access = replace(
        access,
        accessed_date_text=access.accessed_date_text or match.group("date").strip(),
        retrieval_phrase=access.retrieval_phrase or match.group("phrase").strip(),
        source_url=access.source_url or url,
        source_text=access.source_text or match.group("phrase").strip(),
    )

    urls = list(parsed.url)
    if url not in urls:
        urls.append(url)

    notes = list(parsed.note)
    if match.group("phrase").strip() not in notes:
        notes.append(match.group("phrase").strip())

    return replace(parsed, access=access, url=urls, note=notes)


def _apply_missing_date(
    parsed: ParsedReferenceData,
    raw_reference: str,
) -> ParsedReferenceData:
    if "z.d." not in raw_reference.lower():
        return parsed

    dates = list(parsed.date)
    if "z.d." not in dates:
        dates.append("z.d.")
    return replace(parsed, date=dates, issued_year=None)


def _apply_organization_author(parsed: ParsedReferenceData) -> ParsedReferenceData:
    if not parsed.author:
        return parsed
    if parsed.organization:
        return parsed

    first = parsed.author[0]
    if _looks_like_organization(first):
        org_values = list(parsed.organization)
        label = first.literal or " ".join(
            part for part in [first.given, first.family] if part
        ).strip()
        if label and label not in org_values:
            org_values.append(label)
        return replace(parsed, organization=org_values)

    return parsed


def _looks_like_organization(name: ParsedName) -> bool:
    if name.literal:
        return True

    family = (name.family or "").strip()
    given = (name.given or "").strip()
    combined = " ".join(part for part in [given, family] if part).strip()
    if not combined:
        return False
    if any(char.isdigit() for char in combined):
        return True
    return " " in family and not given


def _apply_grey_literature_hints(
    parsed: ParsedReferenceData,
    raw_reference: str,
) -> ParsedReferenceData:
    match = _GREY_LITERATURE_PATTERN.search(raw_reference)
    if not match:
        return parsed

    genres = list(parsed.genre)
    genre = "informele_publicatie"
    if genre not in genres:
        genres.append(genre)
    return replace(parsed, genre=genres)


def _repair_dutch_contributor_lists(
    parsed: ParsedReferenceData,
    raw_reference: str,
) -> ParsedReferenceData:
    author_names = _raw_author_names(raw_reference)
    editor_names = _raw_editor_names(raw_reference)
    updated = parsed
    if _raw_names_are_better(author_names, parsed.author):
        updated = replace(updated, author=author_names)
    if _raw_names_are_better(editor_names, parsed.editor):
        updated = replace(updated, editor=editor_names)
    return updated


def _repair_single_author_misfiled_as_editor(
    parsed: ParsedReferenceData,
    raw_reference: str,
) -> ParsedReferenceData:
    if parsed.author or len(parsed.editor) != 1:
        return parsed
    if parsed.type not in {"article-journal", "journal_article"}:
        return parsed
    raw_author = _raw_single_leading_author(raw_reference)
    if raw_author is None:
        return parsed
    editor = parsed.editor[0]
    if not _names_are_equivalent(raw_author, editor):
        return parsed
    return replace(parsed, author=[editor], editor=[])


def _repair_soft_hyphenated_fields(parsed: ParsedReferenceData) -> ParsedReferenceData:
    """Repair PDF line-break hyphens in parsed text fields only."""
    return replace(
        parsed,
        title=_repair_soft_hyphenated_values(parsed.title),
        container_title=_repair_soft_hyphenated_values(parsed.container_title),
        publisher=_repair_soft_hyphenated_values(parsed.publisher),
        institution=_repair_soft_hyphenated_values(parsed.institution),
        organization=_repair_soft_hyphenated_values(parsed.organization),
        collection_title=_repair_soft_hyphenated_values(parsed.collection_title),
        location=_repair_soft_hyphenated_values(parsed.location),
        genre=_repair_soft_hyphenated_values(parsed.genre),
        note=_repair_soft_hyphenated_values(parsed.note),
    )


def _repair_soft_hyphenated_values(values: list[str]) -> list[str]:
    return [_repair_soft_hyphenated_text(value) for value in values]


def _repair_soft_hyphenated_text(value: str) -> str:
    def replacement(match: re.Match[str]) -> str:
        token = match.group(0)
        if token.casefold() in _PRESERVED_HYPHENATED_WORDS:
            return token
        return f"{match.group(1)}{match.group(2)}"

    return _SOFT_HYPHENATED_WORD_PATTERN.sub(replacement, value)


def _raw_author_names(raw_reference: str) -> list[ParsedName]:
    match = _YEAR_MARKER_PATTERN.search(raw_reference)
    if not match:
        return []
    return _parse_contributor_list(raw_reference[: match.start()])


def _raw_single_leading_author(raw_reference: str) -> ParsedName | None:
    match = _YEAR_MARKER_PATTERN.search(raw_reference)
    if not match:
        return None
    prefix = raw_reference[: match.start()].strip()
    if re.search(r"\b(?:en|EN|and|AND)\b", prefix):
        return None
    match = re.match(r"(?P<family>.+?),\s*(?P<given>(?:[A-Z](?:-[A-Z])?\.?\s*){1,4})$", prefix)
    if not match:
        return None
    family = _surname_without_particles(_clean_name_part(match.group("family")))
    given = _clean_name_part(match.group("given"))
    if not family or not given:
        return None
    return ParsedName(family=family, given=given)


def _raw_editor_names(raw_reference: str) -> list[ParsedName]:
    match = _EDITOR_LIST_PATTERN.search(raw_reference)
    if not match:
        return []
    return _parse_contributor_list(match.group("names"))


def _parse_contributor_list(text: str) -> list[ParsedName]:
    if not _has_final_name_separator(text):
        return []

    matches: list[tuple[int, int, ParsedName]] = []
    occupied: list[tuple[int, int]] = []
    for pattern in (
        _SURNAME_FIRST_NAME_PATTERN,
        _INITIAL_FIRST_NAME_PATTERN,
        _SURNAME_INITIALS_FALLBACK_PATTERN,
    ):
        for match in pattern.finditer(text):
            start, end = match.span()
            if any(start < used_end and end > used_start for used_start, used_end in occupied):
                continue
            family = _clean_name_part(match.group("family"))
            given = _clean_name_part(match.group("given"))
            if not family or not given:
                continue
            if pattern is _SURNAME_INITIALS_FALLBACK_PATTERN:
                given = _normalize_compact_initials(given)
            matches.append((start, end, ParsedName(family=family, given=given)))
            occupied.append((start, end))

    matches.sort(key=lambda item: item[0])
    names = [item[2] for item in matches]
    return _dedupe_names(names)


def _has_final_name_separator(text: str) -> bool:
    return bool(
        re.search(
            rf"\s+(?:en|EN|and|AND)\s+(?={_INITIALS_PATTERN}|{_FAMILY_PATTERN},)",
            text,
        )
    )


def _raw_names_are_better(raw_names: list[ParsedName], parsed_names: list[ParsedName]) -> bool:
    if len(raw_names) < 2:
        return False
    if len(raw_names) > len(parsed_names):
        return True
    return _has_collapsed_contributor_name(parsed_names)


def _has_collapsed_contributor_name(names: list[ParsedName]) -> bool:
    for name in names:
        values = [name.family or "", name.given or "", name.literal or ""]
        combined = " ".join(values)
        if re.search(r"\b(?:en|EN|and)\b", combined) or ".en" in combined:
            return True
        if name.family and re.match(r"^\s*[A-Z](?:-[A-Z])?\.\s+", name.family):
            return True
        if name.given and re.search(r"\.[A-ZÀ-ÖØ-Þ][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+", name.given):
            return True
    return False


def _clean_name_part(value: str) -> str:
    return " ".join(value.strip(" .,\t\r\n").split())


def _normalize_compact_initials(value: str) -> str:
    cleaned = _clean_name_part(value)
    if "-" in cleaned:
        parts = [part for part in cleaned.split("-") if part]
        return "-".join(f"{part}." for part in parts)
    return "".join(f"{char}." for char in cleaned)


def _names_are_equivalent(left: ParsedName, right: ParsedName) -> bool:
    return (
        _surname_without_particles(left.family or "").casefold()
        == _surname_without_particles(right.family or "").casefold()
        and _clean_name_part(left.given or "").replace(" ", "").casefold()
        == _clean_name_part(right.given or "").replace(" ", "").casefold()
    )


def _surname_without_particles(value: str) -> str:
    text = _clean_name_part(value)
    tokens = text.split()
    while len(tokens) > 1 and tokens[0].casefold() in _NAME_PARTICLES:
        tokens.pop(0)
    return " ".join(tokens)


def _dedupe_names(names: list[ParsedName]) -> list[ParsedName]:
    output: list[ParsedName] = []
    seen: set[tuple[str, str]] = set()
    for name in names:
        key = ((name.family or "").casefold(), (name.given or "").casefold())
        if key in seen:
            continue
        seen.add(key)
        output.append(name)
    return output
