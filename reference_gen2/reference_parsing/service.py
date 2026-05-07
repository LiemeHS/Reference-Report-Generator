from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from typing import Any

from reference_gen2.reference_parsing.anystyle_adapter import (
    parse_reference_tags,
    parse_reference_tags_batch,
)
from reference_gen2.reference_parsing.classification import (
    classify_reference_post_parse,
    classify_reference_pre_parse,
    normalize_reference_for_apa7_nl,
    profile_for_ctype,
    repair_parsed_reference_for_ctype,
)
from reference_gen2.reference_parsing.models import (
    AccessMetadata,
    MatchPreparation,
    ParsedName,
    ParsedReferenceData,
    ParsedReferenceResult,
    ReportBasis,
    ReferenceParsingError,
)
from reference_gen2.reference_parsing.rules_apa7_nl import apply_apa7_nl_rules
from reference_gen2.reference_styles import profile_for_reference_style
from reference_gen2.reference_segmentation.models import ReferenceStyleHint

_URL_SPACE_PATTERN = re.compile(r"(https?://)\s+")
_DOI_SPACE_PATTERN = re.compile(r"(doi:\s*)\s+", re.IGNORECASE)
_EARLY_YEAR_PATTERN = re.compile(r"^(?:(?!https?://).){0,90}\b(?:19|20)\d{2}[a-z]?\b", re.IGNORECASE)
_PHASE3_AUTHOR_TOKEN = r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+"
_PHASE3_AUTHOR_PARTICLE = r"(?:de|den|der|van|von|la|le|del|della|di|du|ten|ter)"
_PHASE3_PARTICLE_FAMILY = (
    rf"(?:{_PHASE3_AUTHOR_PARTICLE}\s+{_PHASE3_AUTHOR_TOKEN}(?:\s+{_PHASE3_AUTHOR_TOKEN})?"
    rf"|{_PHASE3_AUTHOR_TOKEN}\s+{_PHASE3_AUTHOR_PARTICLE}\s+{_PHASE3_AUTHOR_TOKEN}(?:\s+{_PHASE3_AUTHOR_TOKEN})?)"
)
_PHASE3_AUTHOR_FAMILY = rf"(?:{_PHASE3_AUTHOR_TOKEN}|{_PHASE3_PARTICLE_FAMILY})"
_PHASE3_INITIALS = r"(?:[A-Z]\.\s*){1,4}"
_PHASE3_AUTHOR_COMMA_START_RE = re.compile(
    rf"^\s*{_PHASE3_AUTHOR_FAMILY},\s*(?:{_PHASE3_INITIALS}|[A-Z][A-Za-z\-]+\s*,?\s*)",
    re.IGNORECASE,
)
_PHASE3_AUTHOR_NO_COMMA_START_RE = re.compile(
    rf"^\s*(?:{_PHASE3_AUTHOR_TOKEN}(?:\s+{_PHASE3_AUTHOR_TOKEN}){{0,2}})\s+[A-Z](?:[A-Z\-\. ]{{0,18}})"
)
_PHASE3_NONAUTHOR_ORG_START_RE = re.compile(r"^\s*[A-Z0-9][^\n]{4,}$")
_PHASE3_NONAUTHOR_FLEX_ORG_START_RE = re.compile(r"^\s*[A-Za-z0-9][^\n]{4,}$")
_PHASE3_ORG_YEAR_START_RE = re.compile(
    r"^\s*[A-Z0-9][^\n]{0,80}\(\s*(?:19|20)\d{2}[a-z]?(?:\s*,|\s*\))",
    re.IGNORECASE,
)
_PHASE3_ORG_YEAR_FLEX_START_RE = re.compile(
    r"^\s*[A-Za-z0-9][^\n]{0,80}\(\s*(?:19|20)\d{2}[a-z]?(?:\s*,|\s*\))",
    re.IGNORECASE,
)
_PHASE3_WEB_DATE_RE = re.compile(
    r"\(\s*(?:19|20)\d{2}\s*,\s*(?:\d{1,2}\s+[A-Za-zÀ-ÖØ-öø-ÿ]+|[A-Za-zÀ-ÖØ-öø-ÿ]+\s+\d{1,2})\s*\)",
    re.IGNORECASE,
)
_PHASE3_RETRIEVAL_PREFIX_RE = re.compile(
    r"^\s*(?:retrieved|accessed|opgehaald|geraadpleegd)\b",
    re.IGNORECASE,
)
_LITERAL_SURNAME_INITIALS_RE = re.compile(r"^(?P<family>[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+)\s+(?P<initials>[A-Z](?:[A-Z]+|(?:-[A-Z])+)?)+$")
_INITIALS_TOKEN_RE = re.compile(r"^[A-Z](?:[A-Z]+|(?:-[A-Z])+)?$")


def parse_reference(
    raw_reference: str,
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> ParsedReferenceResult:
    if not raw_reference.strip():
        normalized_reference = _cleanup_reference(raw_reference)
        return ParsedReferenceResult(
            reference_id=_reference_id_for(normalized_reference),
            raw_reference=raw_reference,
            normalized_reference=normalized_reference,
            parsed_data=None,
            warnings=["parser_unparseable_reference"],
            style_hint_used=style_hint,
            match_preparation=_build_match_preparation("unknown", None),
            report_basis=_build_report_basis(
                "unknown",
                None,
                None,
                None,
                ["parser_unparseable_reference"],
            ),
        )

    cleaned_reference = _cleanup_reference_for_style(raw_reference, style_hint)
    pre_classification = classify_reference_pre_parse(cleaned_reference)
    parse_profile = _parse_profile_name(style_hint, pre_classification.ctype)
    raw_tags = parse_reference_tags(cleaned_reference)
    parsed = _map_anystyle_tags(raw_tags)
    parsed = _apply_raw_year_fallback(parsed, cleaned_reference)

    if style_hint == "apa7_nl":
        parsed = apply_apa7_nl_rules(parsed, cleaned_reference)
        parsed = repair_parsed_reference_for_ctype(
            parsed,
            cleaned_reference,
            pre_classification.ctype,
        )

    post_classification, classifier_warnings = classify_reference_post_parse(
        cleaned_reference,
        parsed,
        pre_classification,
    )
    if style_hint == "apa7_nl":
        parsed = repair_parsed_reference_for_ctype(
            parsed,
            cleaned_reference,
            post_classification.ctype,
        )

    warnings = _warnings_for_result(parsed, raw_tags)
    warnings.extend(
        warning for warning in classifier_warnings if warning not in warnings
    )
    match_preparation = _build_match_preparation(post_classification.ctype, parsed)
    report_basis = _build_report_basis(
        post_classification.ctype,
        pre_classification,
        post_classification,
        match_preparation,
        warnings,
    )
    return ParsedReferenceResult(
        reference_id=_reference_id_for(cleaned_reference),
        raw_reference=raw_reference,
        normalized_reference=cleaned_reference,
        parsed_data=parsed,
        warnings=warnings,
        style_hint_used=style_hint,
        ctype=post_classification.ctype,
        classification_trace=post_classification.trace,
        pre_classification=pre_classification,
        post_classification=post_classification,
        parse_profile_used=parse_profile,
        repair_profile_used=_repair_profile_name(style_hint, post_classification.ctype),
        match_preparation=match_preparation,
        report_basis=report_basis,
    )


def parse_references(
    references: list[str],
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> list[ParsedReferenceResult]:
    """
    Parse multiple references using batch processing for better performance.
    
    This function uses the batch AnyStyle adapter to parse all references in a single
    subprocess call, which is significantly faster than parsing them one at a time.
    """
    if not references:
        return []
    
    # Prepare cleaned references for batch parsing
    cleaned_references: list[str] = []
    for raw_ref in references:
        if not raw_ref.strip():
            cleaned_references.append(_cleanup_reference(raw_ref))
        else:
            cleaned = _cleanup_reference_for_style(raw_ref, style_hint)
            cleaned_references.append(cleaned)
    
    raw_tags_batch = _parse_reference_tags_batch(cleaned_references)
    
    # Process each parsed result
    results: list[ParsedReferenceResult] = []
    for i, raw_reference in enumerate(references):
        cleaned_reference = cleaned_references[i]
        raw_tags = raw_tags_batch[i] if i < len(raw_tags_batch) else None
        
        # Handle empty references
        if not raw_reference.strip():
            results.append(ParsedReferenceResult(
                reference_id=_reference_id_for(cleaned_reference),
                raw_reference=raw_reference,
                normalized_reference=cleaned_reference,
                parsed_data=None,
                warnings=["parser_unparseable_reference"],
                style_hint_used=style_hint,
                match_preparation=_build_match_preparation("unknown", None),
                report_basis=_build_report_basis(
                    "unknown",
                    None,
                    None,
                    None,
                    ["parser_unparseable_reference"],
                ),
            ))
            continue
        
        # Classify and parse
        pre_classification = classify_reference_pre_parse(cleaned_reference)
        parse_profile = _parse_profile_name(style_hint, pre_classification.ctype)
        parsed = _map_anystyle_tags(raw_tags)
        parsed = _apply_raw_year_fallback(parsed, cleaned_reference)
        
        if style_hint == "apa7_nl":
            parsed = apply_apa7_nl_rules(parsed, cleaned_reference)
            parsed = repair_parsed_reference_for_ctype(
                parsed,
                cleaned_reference,
                pre_classification.ctype,
            )
        
        post_classification, classifier_warnings = classify_reference_post_parse(
            cleaned_reference,
            parsed,
            pre_classification,
        )
        
        if style_hint == "apa7_nl":
            parsed = repair_parsed_reference_for_ctype(
                parsed,
                cleaned_reference,
                post_classification.ctype,
            )
        
        warnings = _warnings_for_result(parsed, raw_tags)
        warnings.extend(
            warning for warning in classifier_warnings if warning not in warnings
        )
        match_preparation = _build_match_preparation(post_classification.ctype, parsed)
        report_basis = _build_report_basis(
            post_classification.ctype,
            pre_classification,
            post_classification,
            match_preparation,
            warnings,
        )
        
        results.append(ParsedReferenceResult(
            reference_id=_reference_id_for(cleaned_reference),
            raw_reference=raw_reference,
            normalized_reference=cleaned_reference,
            parsed_data=parsed,
            warnings=warnings,
            style_hint_used=style_hint,
            ctype=post_classification.ctype,
            classification_trace=post_classification.trace,
            pre_classification=pre_classification,
            post_classification=post_classification,
            parse_profile_used=parse_profile,
            repair_profile_used=_repair_profile_name(style_hint, post_classification.ctype),
            match_preparation=match_preparation,
            report_basis=report_basis,
        ))
    
    return results


def recover_parsed_references(
    parsed_results: list[ParsedReferenceResult],
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> list[ParsedReferenceResult]:
    recovered: list[ParsedReferenceResult] = []
    index = 0
    while index < len(parsed_results):
        left = parsed_results[index]
        if index + 1 >= len(parsed_results):
            recovered.append(left)
            break
        right = parsed_results[index + 1]
        attach_reasons = _adjacent_attach_reasons(left, right)
        if attach_reasons:
            merged_raw = _merge_raw_references(left.raw_reference, right.raw_reference)
            merged = parse_reference(merged_raw, style_hint=style_hint)
            if _merged_candidate_is_better(left, right, merged):
                recovered.append(
                    replace(
                        merged,
                        recovery_status="attached_backward",
                        recovery_trace=attach_reasons + ["phase3b_attach_accepted"],
                        recovery_source_indices=[index, index + 1],
                        absorbed_reference_ids=[right.reference_id],
                    )
                )
                index += 2
                continue
            recovered.append(
                replace(
                    left,
                    recovery_status="blocked",
                    recovery_trace=attach_reasons + ["phase3b_attach_rejected"],
                    recovery_source_indices=[index, index + 1],
                )
            )
            recovered.append(right)
            index += 2
            continue
        recovered.append(left)
        index += 1
    return recovered


def parse_references_with_recovery(
    references: list[str],
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> tuple[list[ParsedReferenceResult], list[ParsedReferenceResult]]:
    phase3_results = parse_references(references, style_hint=style_hint)
    phase3b_results = recover_parsed_references(phase3_results, style_hint=style_hint)
    return phase3_results, phase3b_results


def _parse_reference_tags_batch(
    cleaned_references: list[str],
) -> list[dict[str, Any] | None]:
    try:
        return parse_reference_tags_batch(cleaned_references)
    except ReferenceParsingError as exc:
        if exc.code != "anystyle_execution_failed":
            raise
        return [parse_reference_tags(reference) for reference in cleaned_references]


def _cleanup_reference(raw_reference: str) -> str:
    cleaned = " ".join(raw_reference.split())
    cleaned = _URL_SPACE_PATTERN.sub(r"\1", cleaned)
    cleaned = _DOI_SPACE_PATTERN.sub(r"\1", cleaned)
    return cleaned.strip(" \t\r\n;")


def _cleanup_reference_for_style(
    raw_reference: str,
    style_hint: ReferenceStyleHint,
) -> str:
    cleaned = _cleanup_reference(raw_reference)
    if style_hint == "apa7_nl":
        return normalize_reference_for_apa7_nl(cleaned)
    if style_hint == "vancouver":
        return _strip_vancouver_marker(cleaned)
    if style_hint == "apa7_en":
        return _normalize_apa7_en_reference(cleaned)
    return cleaned


def _strip_vancouver_marker(reference: str) -> str:
    return re.sub(r"^\s*(?:\[\d+\]|\d{1,3}[\.)])\s+", "", reference).strip()


def _normalize_apa7_en_reference(reference: str) -> str:
    cleaned = re.sub(r"\bRetrieved\s+from\s+", "Retrieved from ", reference, flags=re.IGNORECASE)
    cleaned = re.sub(r"\(\s*n\.?\s*d\.?\s*\)", "(n.d.)", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _parse_profile_name(style_hint: ReferenceStyleHint, ctype: str) -> str:
    profile = profile_for_reference_style(style_hint)
    return f"{profile.parser_profile}:{ctype}"


def _repair_profile_name(style_hint: ReferenceStyleHint, ctype: str) -> str:
    base = profile_for_ctype(ctype).ctype
    parser_profile = profile_for_reference_style(style_hint).parser_profile
    return f"{parser_profile}:{base}"


def _merge_raw_references(left: str, right: str) -> str:
    return f"{left.rstrip()} {right.lstrip()}".strip()


def _adjacent_attach_reasons(
    left: ParsedReferenceResult,
    right: ParsedReferenceResult,
) -> list[str]:
    return _chapter_tail_attach_reasons(left, right) or _metadata_tail_attach_reasons(left, right)


def _chapter_tail_attach_reasons(
    left: ParsedReferenceResult,
    right: ParsedReferenceResult,
) -> list[str]:
    reasons: list[str] = []
    right_raw = right.raw_reference.strip()
    left_parsed = left.parsed_data
    right_parsed = right.parsed_data
    if not right_raw.lower().startswith("in "):
        return []
    reasons.append("right_starts_with_in_tail")
    if "parser_partial_output" not in right.warnings:
        return []
    reasons.append("right_partial_output")
    if "parser_missing_author" not in right.warnings:
        return []
    reasons.append("right_missing_author")
    if "parser_missing_date" not in right.warnings:
        return []
    reasons.append("right_missing_date")
    if left_parsed is None or right_parsed is None:
        return []
    if not left_parsed.author or not left_parsed.title:
        return []
    reasons.append("left_has_author_title_head")
    if not (left_parsed.date or left_parsed.issued_year):
        return []
    reasons.append("left_has_date")
    if left_parsed.doi or left_parsed.url or left_parsed.identifier:
        return []
    if left_parsed.container_title or left_parsed.editor or left_parsed.pages:
        return []
    reasons.append("left_lacks_terminal_container_metadata")
    if not (
        right_parsed.editor
        or right_parsed.container_title
        or right_parsed.publisher
        or right_parsed.pages
    ):
        return []
    reasons.append("right_has_container_tail_fields")
    return reasons


def _metadata_tail_attach_reasons(
    left: ParsedReferenceResult,
    right: ParsedReferenceResult,
) -> list[str]:
    reasons: list[str] = []
    left_parsed = left.parsed_data
    right_parsed = right.parsed_data
    right_raw = right.raw_reference.strip()
    if left_parsed is None or right_parsed is None:
        return []
    if not left_parsed.author or not left_parsed.title:
        return []
    reasons.append("left_has_author_title_head")
    if not (left_parsed.date or left_parsed.issued_year):
        return []
    reasons.append("left_has_date")
    if not _left_looks_undercomplete(left):
        return []
    reasons.append("left_looks_undercomplete")
    if not _right_looks_like_metadata_tail(right):
        return []
    reasons.append("right_looks_like_metadata_tail")
    if _looks_like_standalone_reference_head(right_raw):
        return []
    reasons.append("right_not_standalone_head")
    return reasons


def _looks_like_standalone_reference_head(raw_reference: str) -> bool:
    stripped = raw_reference.strip()
    if not stripped:
        return False
    if _phase3_looks_like_org_website_start(stripped):
        return True
    if _phase3_looks_like_author_start(stripped) and _EARLY_YEAR_PATTERN.search(stripped):
        return True
    return False


def _phase3_looks_like_author_start(line: str) -> bool:
    stripped = line.strip()
    return bool(
        _PHASE3_AUTHOR_COMMA_START_RE.match(stripped)
        or _PHASE3_AUTHOR_NO_COMMA_START_RE.match(stripped)
    )


def _phase3_looks_like_org_website_start(line: str) -> bool:
    stripped = line.strip()
    if not (
        _PHASE3_NONAUTHOR_ORG_START_RE.match(stripped)
        or _PHASE3_NONAUTHOR_FLEX_ORG_START_RE.match(stripped)
    ):
        return False
    if _phase3_looks_like_author_start(stripped):
        return False
    if _PHASE3_RETRIEVAL_PREFIX_RE.match(stripped):
        return False
    lowered = stripped.casefold()
    return bool(
        _PHASE3_WEB_DATE_RE.search(stripped)
        or _PHASE3_ORG_YEAR_START_RE.match(stripped)
        or _PHASE3_ORG_YEAR_FLEX_START_RE.match(stripped)
        or "(n.d.)" in lowered
        or "z.d." in lowered
    )


def _left_looks_undercomplete(result: ParsedReferenceResult) -> bool:
    parsed = result.parsed_data
    if parsed is None:
        return False
    missing_terminal_metadata = not any(
        [
            parsed.container_title,
            parsed.editor,
            parsed.publisher,
            parsed.pages,
            parsed.doi,
            parsed.url,
            parsed.identifier,
        ]
    )
    has_undercomplete_warning = any(
        warning in result.warnings
        for warning in [
            "parser_partial_output",
            "parser_missing_identifier",
        ]
    )
    return missing_terminal_metadata or has_undercomplete_warning


def _right_looks_like_metadata_tail(result: ParsedReferenceResult) -> bool:
    parsed = result.parsed_data
    if parsed is None:
        return False
    if "parser_partial_output" not in result.warnings:
        return False
    if "parser_missing_author" not in result.warnings:
        return False
    if "parser_missing_date" not in result.warnings:
        return False
    metadata_fields = any(
        [
            parsed.container_title,
            parsed.editor,
            parsed.publisher,
            parsed.pages,
            parsed.doi,
            parsed.url,
            parsed.volume,
            parsed.issue,
        ]
    )
    return metadata_fields


def _merged_candidate_is_better(
    left: ParsedReferenceResult,
    right: ParsedReferenceResult,
    merged: ParsedReferenceResult,
) -> bool:
    parsed = merged.parsed_data
    if parsed is None:
        return False
    if not parsed.author or not parsed.title:
        return False
    critical_flags = {"parser_partial_output", "parser_missing_author", "parser_missing_title", "parser_missing_date"}
    original_critical = sum(flag in critical_flags for flag in left.warnings + right.warnings)
    merged_critical = sum(flag in critical_flags for flag in merged.warnings)
    if merged_critical >= original_critical:
        return False
    if merged.ctype == "book_chapter":
        if not (parsed.editor or parsed.container_title):
            return False
        if not (parsed.pages or parsed.publisher):
            return False
        return True
    if merged.ctype == "journal_article":
        if not parsed.container_title:
            return False
        if not (parsed.doi or parsed.url or parsed.pages or parsed.volume or parsed.issue):
            return False
        return True
    if merged.ctype in {"book", "report"}:
        if not (parsed.publisher or parsed.organization or parsed.institution):
            return False
        return True
    if not (parsed.editor or parsed.container_title or parsed.publisher or parsed.doi or parsed.url):
        return False
    return True


def _map_anystyle_tags(raw_tags: dict[str, Any] | None) -> ParsedReferenceData | None:
    if not raw_tags:
        return None

    data = ParsedReferenceData(
        type=_first_string(raw_tags.get("type")),
        author=_name_list(raw_tags.get("author")),
        editor=_name_list(raw_tags.get("editor")),
        title=_string_list(raw_tags.get("title")),
        container_title=_string_list(raw_tags.get("container-title")),
        publisher=_string_list(raw_tags.get("publisher")),
        institution=_string_list(raw_tags.get("institution")),
        organization=_string_list(raw_tags.get("organization")),
        collection_title=_string_list(raw_tags.get("collection-title")),
        date=_string_list(raw_tags.get("date")),
        issued_year=_issued_year(raw_tags.get("date")),
        volume=_string_list(raw_tags.get("volume")),
        issue=_string_list(raw_tags.get("issue")),
        pages=_string_list(raw_tags.get("pages")),
        doi=_string_list(raw_tags.get("doi")),
        url=_string_list(raw_tags.get("url")),
        identifier=_string_list(raw_tags.get("identifier")),
        location=_string_list(raw_tags.get("location")),
        genre=_string_list(raw_tags.get("genre")),
        note=_string_list(raw_tags.get("note")),
        access=_access_metadata(raw_tags),
        raw_tags=_trace_tags(raw_tags),
    )

    if _is_effectively_empty(data):
        return None
    return data


def _name_list(value: Any) -> list[ParsedName]:
    items: list[ParsedName] = []
    if not isinstance(value, list):
        return items

    for entry in value:
        if isinstance(entry, dict):
            items.append(_parsed_name_from_mapping(entry))
        elif isinstance(entry, str):
            text = entry.strip()
            if text:
                items.append(_parsed_name_from_literal(text))
    return items


def _parsed_name_from_mapping(entry: dict[str, Any]) -> ParsedName:
    parsed = ParsedName(
        family=_first_string(entry.get("family")),
        given=_first_string(entry.get("given")),
        literal=_first_string(entry.get("literal")),
    )
    repaired = _repair_swapped_surname_initials_name(parsed)
    if repaired is not None:
        return repaired
    return parsed


def _parsed_name_from_literal(text: str) -> ParsedName:
    repaired = _repair_literal_surname_initials_name(text)
    if repaired is not None:
        return repaired
    return ParsedName(literal=text)


def _repair_literal_surname_initials_name(text: str) -> ParsedName | None:
    stripped = text.strip()
    if len(stripped.split()) != 2:
        return None
    match = _LITERAL_SURNAME_INITIALS_RE.fullmatch(stripped)
    if not match:
        return None
    family = match.group("family")
    initials = match.group("initials")
    if not _INITIALS_TOKEN_RE.fullmatch(initials):
        return None
    return ParsedName(
        family=family,
        given=_normalize_compact_initials(initials),
    )


def _repair_swapped_surname_initials_name(name: ParsedName) -> ParsedName | None:
    family = (name.family or "").strip()
    given = (name.given or "").strip()
    if name.literal or not family or not given:
        return None
    if not _INITIALS_TOKEN_RE.fullmatch(family):
        return None
    if not re.fullmatch(r"[A-Z][A-Za-zÀ-ÖØ-öø-ÿ'`\-]+", given):
        return None
    return ParsedName(
        family=given,
        given=_normalize_compact_initials(family),
    )


def _normalize_compact_initials(initials: str) -> str:
    if "-" in initials:
        parts = [part for part in initials.split("-") if part]
        return "-".join(f"{part}." for part in parts)
    return "".join(f"{char}." for char in initials)


def _access_metadata(raw_tags: dict[str, Any]) -> AccessMetadata | None:
    source_url = _first_string(raw_tags.get("url"))
    note_values = _string_list(raw_tags.get("note"))
    source_text = note_values[0] if note_values else None
    if not source_url and not source_text:
        return None
    return AccessMetadata(
        source_url=source_url,
        source_text=source_text,
    )


def _trace_tags(raw_tags: dict[str, Any]) -> dict[str, list[str]] | None:
    trace: dict[str, list[str]] = {}
    for key, value in raw_tags.items():
        if key in {"type"}:
            continue
        values = _flatten_trace_value(value)
        if values:
            trace[key] = values
    return trace or None


def _flatten_trace_value(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        flattened: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    flattened.append(stripped)
            elif isinstance(item, dict):
                text = " ".join(
                    str(piece).strip()
                    for piece in [item.get("literal"), item.get("given"), item.get("family")]
                    if isinstance(piece, str) and piece.strip()
                ).strip()
                if text:
                    flattened.append(text)
        return flattened
    return []


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        return [stripped] if stripped else []
    if isinstance(value, list):
        output: list[str] = []
        for item in value:
            if isinstance(item, str):
                stripped = item.strip()
                if stripped:
                    output.append(stripped)
        return output
    return []


def _first_string(value: Any) -> str | None:
    values = _string_list(value)
    if values:
        return values[0]
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _issued_year(value: Any) -> str | None:
    for entry in _string_list(value):
        match = re.search(r"\b(1[0-9]{3}|2[0-9]{3})\b", entry)
        if match:
            return match.group(1)
    return None


def _apply_raw_year_fallback(
    parsed: ParsedReferenceData | None,
    raw_reference: str,
) -> ParsedReferenceData | None:
    if parsed is None or parsed.issued_year or parsed.date or not parsed.title:
        return parsed
    match = re.search(r"\b(19[0-9]{2}|20[0-9]{2})\b", raw_reference)
    if not match:
        return parsed
    year = match.group(1)
    return replace(parsed, date=[year], issued_year=year)


def _is_effectively_empty(data: ParsedReferenceData) -> bool:
    return not any(
        [
            data.author,
            data.editor,
            data.title,
            data.container_title,
            data.publisher,
            data.institution,
            data.organization,
            data.collection_title,
            data.date,
            data.volume,
            data.issue,
            data.pages,
            data.doi,
            data.url,
            data.identifier,
            data.location,
            data.genre,
            data.note,
            data.type,
        ]
    )


def _warnings_for_result(
    parsed: ParsedReferenceData | None,
    raw_tags: dict[str, Any] | None,
) -> list[str]:
    if raw_tags is None or parsed is None:
        return ["parser_empty_output", "parser_unparseable_reference"]

    warnings: list[str] = []
    if not parsed.title:
        warnings.append("parser_missing_title")
    if not parsed.author and not parsed.organization and not parsed.institution:
        warnings.append("parser_missing_author")
    if not parsed.date and parsed.issued_year is None:
        warnings.append("parser_missing_date")
    if not parsed.doi and not parsed.url and not parsed.identifier:
        warnings.append("parser_missing_identifier")
    if parsed.access is None and "gereadpleegd op" in " ".join(parsed.note).lower():
        warnings.append("parser_access_metadata_unparsed")

    core_missing = sum(
        int(flag in warnings)
        for flag in ["parser_missing_title", "parser_missing_author", "parser_missing_date"]
    )
    if core_missing >= 2:
        warnings.insert(0, "parser_partial_output")

    return warnings


def _reference_id_for(normalized_reference: str) -> str:
    digest = hashlib.sha1(normalized_reference.encode("utf-8")).hexdigest()[:12]
    return f"ref_{digest}"


def _build_match_preparation(
    ctype: str,
    parsed: ParsedReferenceData | None,
) -> MatchPreparation:
    if parsed is None:
        return MatchPreparation(
            eligible_for_db_match=False,
            match_target="none",
            lookup_confidence_basis=["no_parsed_data"],
        )

    if ctype == "journal_article":
        lookup_key_fields = {
            "doi": parsed.doi,
            "title": parsed.title,
            "author": _name_strings(parsed.author),
            "issued_year": _single_or_empty(parsed.issued_year),
            "container_title": parsed.container_title,
            "volume": parsed.volume,
            "issue": parsed.issue,
            "pages": parsed.pages,
        }
        return MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields=_drop_empty_fields(lookup_key_fields),
            lookup_query_fields=_drop_empty_fields(
                {
                    "title": parsed.title,
                    "author": _name_strings(parsed.author),
                    "container_title": parsed.container_title,
                    "issued_year": _single_or_empty(parsed.issued_year),
                }
            ),
            lookup_confidence_basis=_confidence_basis(
                parsed.doi,
                parsed.container_title,
                parsed.volume,
                parsed.issue,
                parsed.pages,
            ),
        )

    if ctype == "book":
        lookup_key_fields = {
            "doi": parsed.doi,
            "identifier": parsed.identifier,
            "title": parsed.title,
            "author": _name_strings(parsed.author),
            "organization": parsed.organization,
            "issued_year": _single_or_empty(parsed.issued_year),
            "publisher": parsed.publisher,
        }
        return MatchPreparation(
            eligible_for_db_match=True,
            match_target="openlibrary",
            lookup_key_fields=_drop_empty_fields(lookup_key_fields),
            lookup_query_fields=_drop_empty_fields(
                {
                    "title": parsed.title,
                    "author": _name_strings(parsed.author),
                    "organization": parsed.organization,
                    "issued_year": _single_or_empty(parsed.issued_year),
                    "publisher": parsed.publisher,
                }
            ),
            lookup_confidence_basis=_confidence_basis(
                parsed.doi,
                parsed.identifier,
                parsed.publisher,
                _single_or_empty(parsed.issued_year),
            ),
        )

    if ctype == "book_chapter":
        lookup_key_fields = {
            "chapter_title": parsed.title,
            "book_title": parsed.container_title or parsed.collection_title,
            "author": _name_strings(parsed.author),
            "editor": _name_strings(parsed.editor),
            "pages": parsed.pages,
            "issued_year": _single_or_empty(parsed.issued_year),
        }
        return MatchPreparation(
            eligible_for_db_match=True,
            match_target="openlibrary",
            lookup_key_fields=_drop_empty_fields(lookup_key_fields),
            lookup_query_fields=_drop_empty_fields(
                {
                    "chapter_title": parsed.title,
                    "book_title": parsed.container_title or parsed.collection_title,
                    "author": _name_strings(parsed.author),
                    "editor": _name_strings(parsed.editor),
                    "issued_year": _single_or_empty(parsed.issued_year),
                }
            ),
            lookup_confidence_basis=_confidence_basis(
                parsed.container_title or parsed.collection_title,
                parsed.pages,
                _single_or_empty(parsed.issued_year),
            ),
        )

    if ctype == "thesis":
        return MatchPreparation(
            eligible_for_db_match=False,
            match_target="none",
            lookup_key_fields=_drop_empty_fields(
                {
                    "title": parsed.title,
                    "author": _name_strings(parsed.author),
                    "institution": parsed.institution,
                    "issued_year": _single_or_empty(parsed.issued_year),
                }
            ),
            lookup_query_fields=_drop_empty_fields(
                {
                    "title": parsed.title,
                    "author": _name_strings(parsed.author),
                    "institution": parsed.institution,
                }
            ),
            lookup_confidence_basis=["phase4_target_not_enabled:thesis"],
        )

    return MatchPreparation(
        eligible_for_db_match=False,
        match_target="none",
        lookup_key_fields=_drop_empty_fields(_generic_lookup_fields(parsed)),
        lookup_query_fields=_drop_empty_fields(_generic_lookup_fields(parsed)),
        lookup_confidence_basis=[f"phase4_target_not_enabled:{ctype}"],
    )


def _build_report_basis(
    ctype: str,
    pre_classification: Any,
    post_classification: Any,
    match_preparation: MatchPreparation | None,
    warnings: list[str],
) -> ReportBasis:
    why_this_type: list[str] = []
    if pre_classification is not None:
        why_this_type.append(f"pre_classification:{pre_classification.ctype}")
    if post_classification is not None and post_classification is not pre_classification:
        why_this_type.append(f"post_classification:{post_classification.ctype}")
    if post_classification is not None:
        why_this_type.extend(post_classification.trace[-3:])
    if not why_this_type:
        why_this_type.append(f"default_ctype:{ctype}")

    missing_fields = []
    if match_preparation is not None:
        missing_fields = _missing_fields_for_match(ctype, match_preparation.lookup_key_fields)

    why_matchable_or_not: list[str] = []
    if match_preparation is None:
        why_matchable_or_not.append("match_preparation_unavailable")
    elif match_preparation.eligible_for_db_match:
        why_matchable_or_not.append(f"eligible_for_db_match:{match_preparation.match_target}")
        if missing_fields:
            why_matchable_or_not.append("eligible_but_partial_lookup_fields")
    else:
        why_matchable_or_not.append(f"not_match_eligible:{match_preparation.match_target}")
    why_matchable_or_not.extend(match_preparation.lookup_confidence_basis if match_preparation else [])
    if any(warning.startswith("classifier_") for warning in warnings):
        why_matchable_or_not.extend(
            warning for warning in warnings if warning.startswith("classifier_")
        )

    return ReportBasis(
        why_this_type=_dedupe_preserve_order(why_this_type),
        why_matchable_or_not=_dedupe_preserve_order(why_matchable_or_not),
        missing_fields_for_match=missing_fields,
    )


def _generic_lookup_fields(parsed: ParsedReferenceData) -> dict[str, list[str]]:
    return {
        "title": parsed.title,
        "author": _name_strings(parsed.author),
        "organization": parsed.organization,
        "institution": parsed.institution,
        "issued_year": _single_or_empty(parsed.issued_year),
        "url": parsed.url,
        "doi": parsed.doi,
    }


def _missing_fields_for_match(
    ctype: str,
    lookup_key_fields: dict[str, list[str]],
) -> list[str]:
    required_by_type = {
        "journal_article": ["title", "issued_year"],
        "book": ["title", "issued_year"],
        "book_chapter": ["chapter_title", "book_title", "issued_year"],
        "thesis": ["title", "issued_year"],
    }
    required_fields = required_by_type.get(ctype, [])
    return [
        field_name
        for field_name in required_fields
        if not lookup_key_fields.get(field_name)
    ]


def _name_strings(names: list[ParsedName]) -> list[str]:
    values: list[str] = []
    for name in names:
        literal = (name.literal or "").strip()
        if literal:
            values.append(literal)
            continue
        combined = ", ".join(
            piece for piece in [name.family, name.given] if piece and piece.strip()
        ).strip(", ")
        if combined:
            values.append(combined)
    return values


def _single_or_empty(value: str | None) -> list[str]:
    if value is None:
        return []
    stripped = value.strip()
    return [stripped] if stripped else []


def _drop_empty_fields(fields: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: value
        for key, value in fields.items()
        if any(item.strip() for item in value if isinstance(item, str))
    }


def _confidence_basis(*field_groups: list[str]) -> list[str]:
    labels: list[str] = []
    for index, values in enumerate(field_groups, start=1):
        if values:
            labels.append(f"signal_group_{index}_present")
    return labels or ["low_structural_confidence"]


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            output.append(item)
    return output
