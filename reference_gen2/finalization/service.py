from __future__ import annotations

from dataclasses import asdict, is_dataclass
import uuid
from typing import Any

from reference_gen2.citation_rendering import CitationRenderResult, render_candidate_citation
from reference_gen2.finalization.models import (
    SanitizedCitationRender,
    SanitizedCycleError,
    SanitizedCycleReport,
    SanitizedDocumentSummary,
    SanitizedPhase1Summary,
    SanitizedPhase2Summary,
    SanitizedPhase3ReferenceSummary,
    SanitizedPhase4Summary,
    SanitizedPhase5Summary,
)
from reference_gen2.pipeline_models import Phase1PipelineResult
from reference_gen2.reference_evaluation.models import Phase5MatchEvaluation
from reference_gen2.reference_matching.models import LocalDbCandidate, Phase4MatchResult
from reference_gen2.reference_parsing.models import ParsedReferenceResult
from reference_gen2.reference_segmentation.models import SegmentationResult
from reference_gen2.reference_styles import profile_for_reference_style


def finalize_cycle_report(
    *,
    style_hint: str,
    phase1: Phase1PipelineResult | None = None,
    phase2: SegmentationResult | None = None,
    phase3: list[ParsedReferenceResult] | None = None,
    phase3b: list[ParsedReferenceResult] | None = None,
    phase4: list[Phase4MatchResult] | None = None,
    phase5: list[Phase5MatchEvaluation] | None = None,
    error: Exception | dict[str, Any] | None = None,
    source_mode: str = "upload",
    cycle_id: str | None = None,
    requested_style_hint: str | None = None,
    timings_ms: dict[str, Any] | None = None,
) -> SanitizedCycleReport:
    report_id = cycle_id or _opaque_cycle_id()
    reference_id_map = _reference_id_map(phase3, phase3b)
    parsed_lookup = _parsed_result_map(phase3, phase3b)
    return SanitizedCycleReport(
        cycle_id=report_id,
        status="error" if error is not None else "ok",
        source_mode=source_mode,
        style_hint=style_hint,
        requested_style_hint=requested_style_hint or style_hint,
        timings_ms=_sanitize_timings_ms(timings_ms),
        phase1=_sanitize_phase1(phase1),
        phase2=_sanitize_phase2(phase2),
        phase3=_sanitize_phase3_results(phase3, reference_id_map),
        phase3b=_sanitize_phase3_results(phase3b, reference_id_map),
        phase4=_sanitize_phase4_results(phase4, reference_id_map),
        phase5=_sanitize_phase5_results(
            phase5,
            reference_id_map,
            parsed_lookup,
            style_hint=style_hint,
        ),
        error=_sanitize_error(error),
    )


def sanitize_error_payload(
    *,
    phase: str,
    exc: Exception,
) -> dict[str, Any]:
    return asdict(
        SanitizedCycleError(
            phase=phase,
            code=getattr(exc, "code", exc.__class__.__name__),
            message=getattr(exc, "message", str(exc)),
            details=_safe_error_details(getattr(exc, "details", {})),
        )
    )


def serialize_sanitized_report(report: SanitizedCycleReport) -> dict[str, Any]:
    return _serialize(asdict(report))


def _sanitize_phase1(phase1: Phase1PipelineResult | None) -> SanitizedPhase1Summary | None:
    if phase1 is None:
        return None
    return SanitizedPhase1Summary(
        upload_kind=phase1.upload.detected_kind,
        report=SanitizedDocumentSummary(
            source_kind=phase1.upload.detected_kind,
            size_bytes=phase1.upload.size_bytes,
            extraction_time_ms=phase1.report_context.document.extraction_time_ms,
            heading_found=phase1.report_context.document.heading_found,
            heading_unit_index=phase1.report_context.document.heading_unit_index,
            start_unit_index=phase1.report_context.document.start_unit_index,
            end_unit_index=phase1.report_context.document.end_unit_index,
            unit_count=phase1.report_context.document.unit_count,
            bibliography_char_count=phase1.report_context.document.bibliography_char_count,
            warnings=list(phase1.report_context.document.warnings),
        ),
        extraction_warnings=list(phase1.report_context.extraction_warnings),
    )


def _sanitize_phase2(phase2: SegmentationResult | None) -> SanitizedPhase2Summary | None:
    if phase2 is None:
        return None
    return SanitizedPhase2Summary(
        reference_count=len(phase2.references),
        warnings=list(phase2.warnings),
        style_hint_used=phase2.style_hint_used,
        profile_used=phase2.profile_used,
    )


def _sanitize_phase3_results(
    results: list[ParsedReferenceResult] | None,
    reference_id_map: dict[str, str],
) -> list[SanitizedPhase3ReferenceSummary]:
    if not results:
        return []
    sanitized: list[SanitizedPhase3ReferenceSummary] = []
    for result in results:
        match_preparation = result.match_preparation
        report_basis = result.report_basis
        sanitized.append(
            SanitizedPhase3ReferenceSummary(
                opaque_reference_id=reference_id_map[result.reference_id],
                ctype=result.ctype,
                parser_backend=result.parser_backend,
                parser_model_used=result.parser_model_used,
                display_reference=_safe_display_text(result.raw_reference),
                warnings=list(result.warnings),
                recovery_status=result.recovery_status,
                recovery_trace=list(result.recovery_trace),
                match_eligible=bool(
                    match_preparation is not None and match_preparation.eligible_for_db_match
                ),
                match_target=match_preparation.match_target if match_preparation else "none",
                missing_fields_for_match=list(
                    report_basis.missing_fields_for_match if report_basis else []
                ),
                parsed_fields=_parsed_field_summary(result.parsed_data),
            )
        )
    return sanitized


def _sanitize_phase4_results(
    results: list[Phase4MatchResult] | None,
    reference_id_map: dict[str, str],
) -> list[SanitizedPhase4Summary]:
    if not results:
        return []
    sanitized: list[SanitizedPhase4Summary] = []
    for result in results:
        sanitized.append(
            SanitizedPhase4Summary(
                opaque_reference_id=reference_id_map.get(
                    result.reference_id,
                    _opaque_reference_id(len(sanitized) + 1),
                ),
                attempted=result.attempted,
                status=result.status,
                strategy_used=result.strategy_used,
                candidate_count=len(result.candidates),
                best_record_id=result.best_candidate.record_id if result.best_candidate else None,
                best_candidate_display=_format_candidate_display(result.best_candidate),
                reasons=list(result.reasons),
                warnings=list(result.warnings),
                timings_ms=dict(result.timings_ms),
            )
        )
    return sanitized


def _sanitize_phase5_results(
    results: list[Phase5MatchEvaluation] | None,
    reference_id_map: dict[str, str],
    parsed_lookup: dict[str, ParsedReferenceResult],
    *,
    style_hint: str,
) -> list[SanitizedPhase5Summary]:
    """Sanitize Phase 5 evaluation results for finalization.
    
    Excludes raw text, DOI, and full candidate details to maintain privacy.
    Only includes opaque IDs and categorical outcomes.
    """
    if not results:
        return []
    sanitized: list[SanitizedPhase5Summary] = []
    for result in results:
        reference_ctype = _reference_ctype(parsed_lookup.get(result.reference_id))
        style_profile = profile_for_reference_style(style_hint)
        accepted_render = _serialize_citation_render(
            render_candidate_citation(
                result.accepted_candidate,
                reference_ctype=reference_ctype,
                style=style_profile.citation_style,
                locale=style_profile.citation_locale,
            )
        )
        runner_up_render = _serialize_citation_render(
            render_candidate_citation(
                result.runner_up_candidate,
                reference_ctype=reference_ctype,
                style=style_profile.citation_style,
                locale=style_profile.citation_locale,
            )
        )
        sanitized.append(
            SanitizedPhase5Summary(
                opaque_reference_id=reference_id_map.get(
                    result.reference_id,
                    _opaque_reference_id(len(sanitized) + 1),
                ),
                phase4_status=result.phase4_status,
                final_status=result.final_status,
                final_confidence=result.final_confidence,
                confidence_score=result.confidence_score,
                accepted_record_id=(
                    result.accepted_candidate.record_id if result.accepted_candidate else None
                ),
                runner_up_record_id=(
                    result.runner_up_candidate.record_id if result.runner_up_candidate else None
                ),
                accepted_match_display=_format_candidate_display(result.accepted_candidate),
                runner_up_match_display=_format_candidate_display(result.runner_up_candidate),
                accepted_match_render=accepted_render,
                runner_up_match_render=runner_up_render,
                reasons=list(result.reasons),
                review_flags=list(result.report_signals.review_flags),
                evidence_checks=[
                    {
                        "code": check.code,
                        "label": check.label,
                        "status": check.status,
                        "summary": check.summary,
                    }
                    for check in result.report_signals.evidence_checks
                ],
                field_comparisons=[
                    {
                        "field_name": comparison.field_name,
                        "label": comparison.label,
                        "source_value": comparison.source_value,
                        "found_value": comparison.found_value,
                        "score": comparison.score,
                        "status": comparison.status,
                    }
                    for comparison in result.report_signals.field_comparisons
                ],
                warnings=list(result.warnings),
            )
        )
    return sanitized


def _sanitize_error(error: Exception | dict[str, Any] | None) -> SanitizedCycleError | None:
    if error is None:
        return None
    if isinstance(error, dict):
        return SanitizedCycleError(
            phase=str(error.get("phase", "unknown")),
            code=str(error.get("code", "unknown_error")),
            message=str(error.get("message", "")),
            details=_safe_error_details(error.get("details", {})),
        )
    return SanitizedCycleError(
        phase="unknown",
        code=getattr(error, "code", error.__class__.__name__),
        message=getattr(error, "message", str(error)),
        details=_safe_error_details(getattr(error, "details", {})),
    )


def _safe_display_text(value: Any, *, max_chars: int = 1200) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\x00", "").split())
    if not text:
        return None
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _parsed_field_summary(parsed: Any) -> dict[str, str]:
    if parsed is None:
        return {}
    fields: dict[str, str] = {}

    def add(label: str, value: Any, *, max_chars: int = 360) -> None:
        text = _safe_display_text(value, max_chars=max_chars)
        if text:
            fields[label] = text

    add("Parsed type", getattr(parsed, "type", None), max_chars=80)
    add("Authors", _format_parsed_names(getattr(parsed, "author", [])), max_chars=500)
    add("Editors", _format_parsed_names(getattr(parsed, "editor", [])), max_chars=500)
    add("Year", getattr(parsed, "issued_year", None), max_chars=40)
    add("Date", _join_values(getattr(parsed, "date", [])), max_chars=160)
    add("Title", _join_values(getattr(parsed, "title", [])), max_chars=500)
    add("Container", _join_values(getattr(parsed, "container_title", [])), max_chars=360)
    add("Publisher", _join_values(getattr(parsed, "publisher", [])), max_chars=260)
    add("Institution", _join_values(getattr(parsed, "institution", [])), max_chars=260)
    add("Organization", _join_values(getattr(parsed, "organization", [])), max_chars=260)
    add("Volume", _join_values(getattr(parsed, "volume", [])), max_chars=80)
    add("Issue", _join_values(getattr(parsed, "issue", [])), max_chars=80)
    add("Pages", _join_values(getattr(parsed, "pages", [])), max_chars=120)
    add("DOI", _join_values(getattr(parsed, "doi", [])), max_chars=240)
    add("URL", _join_values(getattr(parsed, "url", [])), max_chars=360)
    access = getattr(parsed, "access", None)
    if access is not None:
        add("Accessed", getattr(access, "accessed_date_text", None), max_chars=160)
        add("Access URL", getattr(access, "source_url", None), max_chars=360)
    return fields


def _format_parsed_names(names: Any) -> str:
    if not isinstance(names, list):
        return ""
    rendered: list[str] = []
    for name in names:
        family = _safe_display_text(getattr(name, "family", None), max_chars=120)
        given = _safe_display_text(getattr(name, "given", None), max_chars=80)
        literal = _safe_display_text(getattr(name, "literal", None), max_chars=180)
        if literal:
            rendered.append(literal)
        elif family and given:
            rendered.append(f"{family}, {given}")
        elif family:
            rendered.append(family)
        elif given:
            rendered.append(given)
    return "; ".join(rendered)


def _join_values(values: Any) -> str:
    if not isinstance(values, list):
        return ""
    cleaned = [_safe_display_text(value, max_chars=240) for value in values]
    return "; ".join(value for value in cleaned if value)


def _format_candidate_display(
    candidate: LocalDbCandidate | None,
) -> str | None:
    if candidate is None:
        return None
    parts: list[str] = []
    authors = _safe_display_text(
        ", ".join(_display_author_names(candidate.authors, candidate.author_initials)),
        max_chars=240,
    )
    if authors:
        parts.append(authors.rstrip("."))
    year = _safe_display_text(candidate.issued_year, max_chars=40)
    if year:
        parts.append(f"({year})")
    title = _safe_display_text(candidate.title, max_chars=360)
    if title:
        parts.append(title)
    editors = _safe_display_text(
        ", ".join(_display_author_names(candidate.editors, candidate.editor_initials)),
        max_chars=240,
    )
    if editors:
        if candidate.record_granularity == "chapter":
            parts.append(f"In {editors} (Eds.)")
        elif not authors:
            parts.append(f"{editors} (Eds.)")
    container = _safe_display_text(candidate.container_title or candidate.publisher, max_chars=260)
    if container:
        parts.append(container)
    details = ", ".join(
        detail
        for detail in (
            _safe_display_text(candidate.volume, max_chars=40),
            _safe_display_text(candidate.issue, max_chars=40),
            _safe_display_text(candidate.pages, max_chars=80),
        )
        if detail
    )
    if details:
        parts.append(details)
    doi = _safe_display_text(candidate.doi, max_chars=160)
    if doi:
        parts.append(f"DOI: {doi}")
    if not parts:
        parts.append(candidate.record_id)
    return _safe_display_text(". ".join(parts))


def _display_author_names(
    authors: list[str],
    initials: list[str] | None = None,
) -> list[str]:
    normalized_authors = [_safe_display_text(author, max_chars=120) for author in authors]
    clean_authors = [author for author in normalized_authors if author]
    output: list[str] = []
    seen: set[str] = set()
    for index, author in enumerate(clean_authors):
        key = _author_display_key(author)
        if key in seen:
            continue
        if _looks_like_aggregate_author_text(author, clean_authors):
            continue
        seen.add(key)
        display_name = _title_case_author_name(author)
        display_initials = _safe_display_text(
            initials[index] if initials and index < len(initials) else None,
            max_chars=40,
        )
        if display_initials:
            display_name = f"{display_name}, {display_initials}"
        output.append(display_name)
    return output


def _looks_like_aggregate_author_text(author: str, authors: list[str]) -> bool:
    normalized = _author_display_key(author)
    if len(normalized.split()) < 2:
        return False
    contained_names = 0
    for other in authors:
        other_normalized = _author_display_key(other)
        if not other_normalized or other_normalized == normalized:
            continue
        if other_normalized in normalized:
            contained_names += 1
    return contained_names >= 2


def _title_case_author_name(author: str) -> str:
    particles = {"al", "bij", "da", "de", "del", "den", "der", "di", "du", "el", "la", "le", "ten", "ter", "van", "von"}
    words: list[str] = []
    for index, word in enumerate(author.split()):
        lower = word.casefold()
        if index > 0 and lower in particles:
            words.append(lower)
            continue
        words.append("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")))
    return " ".join(words)


def _author_display_key(author: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in author).split()
    )


def _sanitize_timings_ms(timings_ms: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(timings_ms, dict):
        return {}
    safe: dict[str, float] = {}
    for key, value in timings_ms.items():
        key_text = str(key)
        if not _is_safe_timing_key(key_text):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        safe[key_text] = round(float(value), 2)
    return safe


def _is_safe_timing_key(key: str) -> bool:
    if key == "total":
        return True
    return key.startswith("phase") and key[5:].isdigit()


def _safe_error_details(details: Any) -> dict[str, object]:
    if not isinstance(details, dict):
        return {}
    safe: dict[str, object] = {}
    for key, value in details.items():
        if isinstance(value, (int, float, bool)) or value is None:
            safe[str(key)] = value
        elif isinstance(value, str) and _is_safe_metric_key(str(key)):
            safe[str(key)] = value
    return safe


def _is_safe_metric_key(key: str) -> bool:
    return any(
        token in key
        for token in (
            "count",
            "index",
            "status",
            "kind",
            "mime",
            "bytes",
            "ms",
            "http_status",
        )
    )


def _reference_id_map(
    phase3: list[ParsedReferenceResult] | None,
    phase3b: list[ParsedReferenceResult] | None,
) -> dict[str, str]:
    ordered_ids: list[str] = []
    for result_list in (phase3b or [], phase3 or []):
        for result in result_list:
            if result.reference_id not in ordered_ids:
                ordered_ids.append(result.reference_id)
    return {
        reference_id: _opaque_reference_id(index)
        for index, reference_id in enumerate(ordered_ids, start=1)
    }


def _reference_ctype(parsed_result: ParsedReferenceResult | None) -> str | None:
    if parsed_result is None:
        return None
    return parsed_result.ctype


def _serialize_citation_render(
    render: CitationRenderResult | None,
) -> SanitizedCitationRender | None:
    if render is None:
        return None
    return SanitizedCitationRender(
        text=_safe_display_text(render.text, max_chars=1200) or "",
        html=_safe_display_text(render.html, max_chars=2400) or "",
        style=_safe_display_text(render.style, max_chars=120) or "apa-standard",
        locale=_safe_display_text(render.locale, max_chars=40) or "nl-NL",
        warnings=[_safe_display_text(warning, max_chars=240) or "" for warning in render.warnings],
        partial=bool(render.partial),
    )


def _parsed_result_map(
    phase3: list[ParsedReferenceResult] | None,
    phase3b: list[ParsedReferenceResult] | None,
) -> dict[str, ParsedReferenceResult]:
    lookup: dict[str, ParsedReferenceResult] = {}
    for result_list in (phase3 or [], phase3b or []):
        for result in result_list:
            lookup[result.reference_id] = result
    return lookup


def _opaque_cycle_id() -> str:
    return f"cycle_{uuid.uuid4().hex[:12]}"


def _opaque_reference_id(index: int) -> str:
    return f"ref_{index:04d}"


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    return value
