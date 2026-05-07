"""Phase 5 service for final match evaluation and confidence scoring."""

from __future__ import annotations

from dataclasses import dataclass, replace

from reference_gen2.reference_evaluation.models import (
    Phase5ConfidenceName,
    Phase5EvidenceCheck,
    Phase5FieldComparison,
    Phase5FieldComparisonStatus,
    Phase5MatchEvaluation,
    Phase5ReportSignals,
    Phase5RuntimeConfig,
    Phase5ScoreBreakdown,
    Phase5StatusName,
)
from reference_gen2.reference_evaluation.policy import has_journal_title_author_tension
from reference_gen2.reference_evaluation.scoring import (
    compute_ambiguity_penalty,
    compute_author_score,
    compute_container_score,
    compute_doi_score,
    compute_final_score,
    compute_metadata_score,
    compute_structure_penalty,
    compute_title_score,
    compute_type_penalty,
    compute_year_score,
)
from reference_gen2.reference_matching.models import (
    LocalDbCandidate,
    Phase4MatchResult,
    Phase4MatchSignals,
)
from reference_gen2.reference_matching.journal_abbreviations import journal_abbreviation_match
from reference_gen2.reference_parsing.models import ParsedReferenceResult


@dataclass(frozen=True)
class _Phase5CandidateSelection:
    selected_candidate: LocalDbCandidate
    runner_up_candidate: LocalDbCandidate | None
    ordered_candidates: list[LocalDbCandidate]
    doi_conflict_override_applied: bool = False
    selection_reasons: list[str] | None = None


def map_confidence_score_to_name(score: float) -> Phase5ConfidenceName:
    """Map numeric confidence score to categorical confidence name."""
    if score >= 0.82:
        return "high"
    if score >= 0.65:
        return "medium"
    if score >= 0.55:
        return "low"
    return "none"


def _has_major_contradiction(
    match_signals: Phase4MatchSignals,
    penalties: dict[str, float],
    config: Phase5RuntimeConfig,
    *,
    ctype: str | None = None,
) -> bool:
    return (
        match_signals.doi_match_type == "mismatch"
        or match_signals.year_match_type == "mismatch"
        or (match_signals.container_match == "no" and ctype != "book")
        or penalties.get("structure", 0.0) >= config.structure_penalty_medium
        or penalties.get("type", 0.0) >= config.type_penalty_major
    )


def _has_doi_record_metadata_conflict(match_signals: Phase4MatchSignals) -> bool:
    if match_signals.doi_match_type not in {"exact", "equivalent"}:
        return False
    conflict_count = 0
    if match_signals.title_match_strength in {"none", "weak"}:
        conflict_count += 1
    if match_signals.author_match_strength in {"none", "weak"}:
        conflict_count += 1
    if match_signals.container_match == "no":
        conflict_count += 1
    if match_signals.year_match_type == "mismatch":
        conflict_count += 1
    return conflict_count >= 2


def _component_scores(
    match_signals: Phase4MatchSignals,
    parsed: ParsedReferenceResult,
) -> dict[str, float]:
    return {
        "doi": compute_doi_score(match_signals, source_has_doi=_parsed_has_doi(parsed)),
        "title": compute_title_score(match_signals),
        "author": compute_author_score(match_signals),
        "year": compute_year_score(match_signals),
        "container": compute_container_score(match_signals),
        "metadata": compute_metadata_score(match_signals, parsed.ctype),
    }


def _candidate_component_confidence(
    candidate: LocalDbCandidate,
    parsed: ParsedReferenceResult,
    config: Phase5RuntimeConfig,
) -> float:
    _, confidence_score = compute_final_score(
        _component_scores(candidate.match_signals, parsed),
        {"ambiguity": 0.0, "structure": 0.0, "type": 0.0},
        config,
    )
    return confidence_score


def _has_minimum_text_support(candidate: LocalDbCandidate) -> bool:
    signals = candidate.match_signals
    return (
        signals.doi_match_type in {"none", "mismatch"}
        and signals.title_match_strength in {"strong", "exact_or_near_exact"}
        and signals.author_match_strength in {"partial", "strong"}
    )


def _is_book_identity_match(
    parsed: ParsedReferenceResult,
    candidate: LocalDbCandidate | None,
    match_signals: Phase4MatchSignals | None,
) -> bool:
    if parsed.ctype != "book" or candidate is None or match_signals is None:
        return False
    return (
        match_signals.doi_match_type != "mismatch"
        and match_signals.title_match_strength == "exact_or_near_exact"
        and match_signals.year_match_type == "exact"
        and match_signals.author_match_strength == "strong"
    )


def _is_book_publisher_variant_ambiguity(
    parsed: ParsedReferenceResult,
    candidates: list[LocalDbCandidate],
) -> bool:
    if parsed.ctype not in {"book", "book_chapter"} or len(candidates) < 2:
        return False
    left, right = candidates[0], candidates[1]
    if parsed.ctype == "book_chapter":
        if left.record_granularity != "book" or right.record_granularity != "book":
            return False
        parsed_primary = _parsed_primary_editor_surname(parsed)
    else:
        parsed_primary = _parsed_primary_author_surname(parsed)
    candidate_primary = _candidate_primary_author_surname(left)
    if not parsed_primary or not candidate_primary or parsed_primary != candidate_primary:
        return False
    left_signals = left.match_signals
    right_signals = right.match_signals
    return (
        left_signals.title_match_strength == "exact_or_near_exact"
        and right_signals.title_match_strength == "exact_or_near_exact"
        and left_signals.year_match_type == "exact"
        and right_signals.year_match_type == "exact"
        and _candidate_primary_author_surname(right) == parsed_primary
        and _main_title_key(left.title) == _main_title_key(right.title)
        and left.record_id != right.record_id
    )


def _select_phase5_candidates(
    parsed: ParsedReferenceResult,
    phase4: Phase4MatchResult,
    config: Phase5RuntimeConfig,
) -> _Phase5CandidateSelection:
    incumbent = phase4.best_candidate
    if not incumbent:
        raise ValueError("Phase 5 candidate selection requires a Phase 4 best candidate.")

    candidates = phase4.top_candidates or phase4.candidates or [incumbent]
    default_selection = _Phase5CandidateSelection(
        selected_candidate=incumbent,
        runner_up_candidate=candidates[1] if len(candidates) >= 2 else None,
        ordered_candidates=list(candidates),
        selection_reasons=[],
    )
    if len(candidates) < 2 or not _has_doi_record_metadata_conflict(incumbent.match_signals):
        return default_selection

    incumbent_score = _candidate_component_confidence(incumbent, parsed, config)
    best_alternative: LocalDbCandidate | None = None
    best_alternative_score = incumbent_score
    for candidate in candidates:
        if candidate.record_id == incumbent.record_id:
            continue
        if not _has_minimum_text_support(candidate):
            continue
        candidate_score = _candidate_component_confidence(candidate, parsed, config)
        if candidate_score > best_alternative_score:
            best_alternative = candidate
            best_alternative_score = candidate_score

    if (
        best_alternative is None
        or best_alternative_score - incumbent_score
        < config.doi_conflict_override_min_confidence_gap
    ):
        return default_selection

    reordered_top_candidates = [
        best_alternative,
        incumbent,
        *[
            candidate
            for candidate in candidates
            if candidate.record_id not in {best_alternative.record_id, incumbent.record_id}
        ],
    ]
    return _Phase5CandidateSelection(
        selected_candidate=best_alternative,
        runner_up_candidate=incumbent,
        ordered_candidates=reordered_top_candidates,
        doi_conflict_override_applied=True,
        selection_reasons=["phase5_doi_conflict_candidate_override"],
    )


def determine_final_status(
    confidence_score: float,
    phase4_status: str,
    ambiguity_gap: float | None,
    parsed: ParsedReferenceResult,
    best_candidate: LocalDbCandidate | None,
    match_signals: Phase4MatchSignals | None,
    penalties: dict[str, float],
    component_scores: dict[str, float],
    config: Phase5RuntimeConfig,
) -> Phase5StatusName:
    """Determine final user-facing status from confidence score and context."""
    if phase4_status in ("skipped", "error"):
        return phase4_status  # type: ignore[return-value]
    if not best_candidate or phase4_status == "no_match" or match_signals is None:
        return "needs_review"

    major_contradiction = _has_major_contradiction(
        match_signals,
        penalties,
        config,
        ctype=parsed.ctype,
    )
    close_candidates = ambiguity_gap is not None and ambiguity_gap < config.ambiguity_gap_minor

    if _is_book_identity_match(parsed, best_candidate, match_signals) and not major_contradiction:
        return "verified"

    if confidence_score < config.needs_review_threshold:
        return "suspicious"
    if close_candidates and major_contradiction:
        return "suspicious"

    if (
        _is_supported_book_level_chapter_recovery(parsed, best_candidate, match_signals)
        and not major_contradiction
        and confidence_score >= config.needs_review_threshold
    ):
        return "verified"

    if parsed.ctype == "book_chapter" and best_candidate.record_granularity == "book":
        return "needs_review"

    if close_candidates and not major_contradiction and confidence_score < config.verified_threshold:
        return "needs_review"
    if confidence_score >= config.verified_threshold and not major_contradiction:
        return "verified"
    return "needs_review"


def build_evidence_checks(
    parsed: ParsedReferenceResult,
    phase4: Phase4MatchResult,
    penalties: dict[str, float],
    ambiguity_gap: float | None,
    component_scores: dict[str, float] | None = None,
) -> list[Phase5EvidenceCheck]:
    """Build a full evidence/check list for reporting."""
    candidate = phase4.best_candidate
    if not candidate:
        return [
            Phase5EvidenceCheck(
                code="NO_CANDIDATE_FOUND",
                status="fail",
                summary="No candidate was available for final evaluation.",
            )
        ]

    checks: list[Phase5EvidenceCheck] = []
    signals = candidate.match_signals

    def add(code: str, status: str, summary: str, label: str | None = None) -> None:
        checks.append(
            Phase5EvidenceCheck(
                code=code,
                status=status,
                summary=summary,
                label=label or _humanize_check_code(code),
            )
        )

    source_has_doi = _parsed_has_doi(parsed)
    doi_hit_count = phase4.lookup_trace.doi_hit_count
    title_label = (
        "Containing book title"
        if parsed.ctype == "book_chapter" and candidate.record_granularity == "book"
        else "Title"
    )
    if source_has_doi:
        add(
            "DOI_EXTRACTED_FROM_REFERENCE",
            "pass",
            "Yes. A DOI was extracted from the submitted reference.",
            "DOI extracted from reference",
        )
        if doi_hit_count > 0 or signals.doi_match_type in {"exact", "equivalent"}:
            add(
                "EXTRACTED_DOI_FOUND_IN_DB",
                "pass",
                "Yes. The extracted DOI was found in the database.",
                "Extracted DOI found in database",
            )
        elif phase4.lookup_trace.doi_attempted:
            add(
                "EXTRACTED_DOI_NOT_FOUND_IN_DB",
                "warning",
                "No. The extracted DOI was not found in the database.",
                "Extracted DOI found in database",
            )
        else:
            add(
                "EXTRACTED_DOI_LOOKUP_SKIPPED",
                "not_applicable",
                "Skipped. DOI lookup was not attempted.",
                "Extracted DOI found in database",
            )

        if signals.doi_match_type == "exact":
            add(
                "EXTRACTED_DOI_MATCHES_CANDIDATE",
                "pass",
                "Yes. The extracted DOI matches the selected candidate.",
                "Extracted DOI matches selected candidate",
            )
        elif signals.doi_match_type == "equivalent":
            add(
                "EXTRACTED_DOI_EQUIVALENT_TO_CANDIDATE",
                "pass",
                "Yes. The extracted DOI matches the selected candidate in equivalent form.",
                "Extracted DOI matches selected candidate",
            )
        elif signals.doi_match_type == "mismatch":
            add(
                "EXTRACTED_DOI_CONTRADICTS_CANDIDATE",
                "fail",
                "No. The extracted DOI contradicts the selected candidate.",
                "Extracted DOI matches selected candidate",
            )
        else:
            add(
                "EXTRACTED_DOI_NOT_CONFIRMED_AGAINST_CANDIDATE",
                "not_applicable",
                "Skipped. The selected candidate did not confirm the extracted DOI.",
                "Extracted DOI matches selected candidate",
            )
    else:
        add(
            "DOI_NOT_EXTRACTED_FROM_REFERENCE",
            "not_applicable",
            "No DOI was supplied in the submitted reference. DOI is optional and did not affect scoring.",
            "DOI extracted from reference",
        )
        add(
            "EXTRACTED_DOI_LOOKUP_SKIPPED",
            "not_applicable",
            "Skipped because no DOI was supplied in the submitted reference.",
            "Extracted DOI found in database",
        )
        add(
            "EXTRACTED_DOI_MATCH_SKIPPED",
            "not_applicable",
            "Skipped because no DOI was supplied in the submitted reference.",
            "Extracted DOI matches selected candidate",
        )

    if _has_doi_record_metadata_conflict(signals):
        add(
            "DOI_RECORD_METADATA_CONFLICT",
            "fail",
            "The DOI resolves, but the resolved record does not match the submitted title/authors well.",
        )

    if signals.title_match_strength == "exact_or_near_exact":
        add(
            "TITLE_EXACT_OR_NEAR",
            "pass",
            f"{title_label} matched exactly or near exactly.",
            title_label,
        )
    elif signals.title_match_strength == "strong":
        add("TITLE_STRONG_MATCH", "pass", f"{title_label} matched strongly.", title_label)
    elif signals.title_match_strength == "partial":
        add("TITLE_PARTIAL_MATCH", "warning", f"{title_label} matched only partially.", title_label)
    elif signals.title_match_strength == "weak":
        add("TITLE_WEAK_MATCH", "fail", f"{title_label} match was weak.", title_label)
    else:
        add("TITLE_NO_MATCH", "fail", f"{title_label} did not support the candidate.", title_label)

    if signals.author_match_strength == "strong":
        add("AUTHOR_STRONG_MATCH", "pass", "Author overlap was strong.")
    elif signals.author_match_strength == "partial":
        add("AUTHOR_PARTIAL_MATCH", "warning", "Author overlap was partial.")
    elif signals.author_match_strength == "weak":
        add("AUTHOR_WEAK_MATCH", "warning", "Author overlap was weak.")
    else:
        add("AUTHOR_NO_MATCH", "fail", "No author overlap was found.")

    if has_journal_title_author_tension(parsed, signals, component_scores):
        add(
            "JOURNAL_TITLE_AUTHOR_TENSION",
            "fail",
            "The journal, year, and metadata aligned, but the title was not exact and author support was weak.",
        )

    if signals.year_match_type == "exact":
        add("YEAR_EXACT_MATCH", "pass", "Year matched exactly.")
    elif signals.year_match_type == "near":
        add("YEAR_NEAR_MATCH", "warning", "Year was close but not exact.")
    elif signals.year_match_type == "mismatch":
        add("YEAR_MISMATCH", "fail", "Year contradicted the candidate.")
    else:
        add("YEAR_NOT_CONFIRMED", "warning", "Year could not be confirmed.")

    if signals.container_match == "yes":
        add("CONTAINER_CONFIRMED", "pass", "Journal or publisher matched.")
    elif signals.container_match == "no" and parsed.ctype == "book":
        add("PUBLISHER_VARIANT", "warning", "Publisher differed from the candidate edition.")
    elif signals.container_match == "no":
        add("CONTAINER_MISMATCH", "fail", "Journal or publisher contradicted the candidate.")
    else:
        add("CONTAINER_NOT_CONFIRMED", "warning", "Journal or publisher could not be confirmed.")

    if not (parsed.ctype == "book_chapter" and candidate.record_granularity == "book"):
        if signals.volume_issue_pages_match == "exact":
            add("METADATA_EXACT_MATCH", "pass", "Volume, issue, or pages matched exactly.")
        elif signals.volume_issue_pages_match == "partial":
            add("METADATA_PARTIAL_MATCH", "warning", "Volume, issue, or pages matched partially.")
        elif signals.volume_issue_pages_match == "mismatch":
            add("METADATA_MISMATCH", "fail", "Volume, issue, or pages contradicted the candidate.")
        else:
            add("METADATA_NOT_CONFIRMED", "warning", "Volume, issue, or pages could not be confirmed.")

    if ambiguity_gap is not None:
        add(
            "AMBIGUOUS_TOP_CANDIDATES",
            "warning" if ambiguity_gap < 0.08 else "pass",
            f"Top-candidate gap was {ambiguity_gap:.4f}.",
        )

    if penalties.get("structure", 0.0) >= 0.12:
        add(
            "STRUCTURAL_CONTRADICTION",
            "fail",
            "The candidate had structural contradictions despite some overlap.",
        )

    if penalties.get("type", 0.0) > 0.0:
        supported_book_recovery = _is_supported_book_level_chapter_recovery(
            parsed,
            candidate,
            signals,
        )
        add(
            "BOOK_LEVEL_RECOVERY" if parsed.ctype == "book_chapter" else "TYPE_GRANULARITY_MISMATCH",
            (
                "pass"
                if supported_book_recovery
                else "warning"
                if parsed.ctype == "book_chapter"
                else "fail"
            ),
            (
                "The containing book was verified from book-level evidence; chapter-level title confirmation was unavailable."
                if supported_book_recovery
                else "The candidate was matched at a different granularity level."
            ),
        )

    return checks


def build_field_comparisons(
    parsed: ParsedReferenceResult,
    candidate: LocalDbCandidate | None,
    component_scores: dict[str, float],
) -> list[Phase5FieldComparison]:
    """Build sanitized source-vs-found field comparisons for reports."""
    parsed_data = parsed.parsed_data
    if parsed_data is None or candidate is None:
        return []

    container_label = "Publisher" if parsed.ctype == "book" else "Journal"
    if parsed.ctype == "book_chapter":
        container_label = "Book"
    title_label = "Title"
    title_found = candidate.title
    title_score = component_scores.get("title")
    contributor_label = "Authors"
    source_contributors = "; ".join(_parsed_author_display(parsed)) or _raw_leading_surname_display(
        parsed.raw_reference
    )
    is_book_level_chapter = (
        parsed.ctype == "book_chapter" and candidate.record_granularity == "book"
    )
    if parsed.ctype == "book_chapter":
        title_label = "Chapter title"
        if is_book_level_chapter:
            contributor_label = "Editors"
            source_contributors = "; ".join(_parsed_editor_display(parsed))

    comparisons = []
    if not is_book_level_chapter:
        comparisons.append(
            _field_comparison(
                "title",
                title_label,
                _first_text(parsed_data.title),
                title_found,
                title_score,
            )
        )
    comparisons.extend(
        [
            _field_comparison(
                "authors",
                contributor_label,
                source_contributors,
                "; ".join(_candidate_author_display(candidate)),
                component_scores.get("author"),
            ),
            _field_comparison(
                "year",
                "Year",
                parsed_data.issued_year,
                candidate.issued_year,
                component_scores.get("year"),
            ),
            _field_comparison(
                "container",
                container_label,
                _source_container_text(parsed),
                _candidate_container_text(parsed, candidate),
                component_scores.get("container"),
            ),
        ]
    )
    if parsed.ctype != "book" or parsed_data.doi or candidate.doi:
        comparisons.append(
            _field_comparison(
                "doi",
                "DOI",
                _first_text(parsed_data.doi),
                candidate.doi,
                component_scores.get("doi"),
                status_override=(
                    "found"
                    if not _first_text(parsed_data.doi) and _display_value(candidate.doi)
                    else None
                ),
            )
        )
    include_metadata = not (
        parsed.ctype == "book_chapter" and candidate.record_granularity == "book"
    )
    if include_metadata and (
        parsed.ctype != "book"
        or parsed_data.volume
        or parsed_data.issue
        or parsed_data.pages
        or candidate.volume
        or candidate.issue
        or candidate.pages
    ):
        comparisons.append(
            _field_comparison(
                "metadata",
                "Metadata",
                _metadata_text(parsed_data.volume, parsed_data.issue, parsed_data.pages),
                _metadata_text([candidate.volume], [candidate.issue], [candidate.pages]),
                component_scores.get("metadata"),
            )
        )
    return comparisons


def _field_comparison(
    field_name: str,
    label: str,
    source_value: str | None,
    found_value: str | None,
    score: float | None,
    *,
    status_override: Phase5FieldComparisonStatus | None = None,
) -> Phase5FieldComparison:
    clean_score = round(float(score), 4) if score is not None else None
    return Phase5FieldComparison(
        field_name=field_name,
        label=label,
        source_value=_display_value(source_value),
        found_value=_display_value(found_value),
        score=clean_score,
        status=status_override or _field_status(source_value, found_value, clean_score),
    )


def _field_status(
    source_value: str | None,
    found_value: str | None,
    score: float | None,
) -> str:
    if not _display_value(source_value) or not _display_value(found_value):
        return "missing"
    if score is None:
        return "missing"
    if score >= 0.95:
        return "match"
    if score >= 0.50:
        return "partial"
    if score > 0:
        return "mismatch"
    return "mismatch"


def _source_container_text(parsed: ParsedReferenceResult) -> str:
    parsed_data = parsed.parsed_data
    if parsed_data is None:
        return ""
    if parsed.ctype == "journal_article":
        return _first_text(parsed_data.container_title)
    if parsed.ctype == "book":
        return _first_text(parsed_data.publisher)
    return _first_text(parsed_data.container_title or parsed_data.collection_title)


def _candidate_container_text(parsed: ParsedReferenceResult, candidate: LocalDbCandidate) -> str | None:
    if parsed.ctype == "book_chapter" and candidate.record_granularity == "book":
        return candidate.title
    return candidate.container_title or candidate.publisher


def _is_supported_book_level_chapter_recovery(
    parsed: ParsedReferenceResult,
    candidate: LocalDbCandidate | None,
    signals: Phase4MatchSignals | None,
) -> bool:
    if parsed.ctype != "book_chapter" or candidate is None or signals is None:
        return False
    if candidate.record_granularity != "book":
        return False
    source_editors = _normalized_author_values(_parsed_editor_display(parsed))
    found_authors = _normalized_author_values(_clean_candidate_authors(candidate.authors))
    editor_author_match = bool(source_editors and found_authors) and all(
        any(source == found or _edit_distance_leq_one(source, found) for found in found_authors)
        for source in source_editors
    )
    return (
        signals.title_match_strength in {"exact_or_near_exact", "strong"}
        and editor_author_match
        and signals.year_match_type in {"exact", "near"}
        and signals.container_match == "yes"
    )


def _parsed_has_doi(parsed: ParsedReferenceResult) -> bool:
    parsed_data = parsed.parsed_data
    return bool(parsed_data and _first_text(parsed_data.doi))


def _humanize_check_code(code: str) -> str:
    return " ".join(part.capitalize() for part in code.split("_") if part)


def _metadata_text(volume: list[str | None], issue: list[str | None], pages: list[str | None]) -> str:
    parts = []
    for label, values in (("vol", volume), ("issue", issue), ("pages", pages)):
        value = _first_text(values)
        if value:
            parts.append(f"{label}: {value}")
    return "; ".join(parts)


def _parsed_author_display(parsed: ParsedReferenceResult) -> list[str]:
    parsed_data = parsed.parsed_data
    if parsed_data is None:
        return []
    output: list[str] = []
    for author in parsed_data.author:
        if author.family:
            output.append(_surname_display_from_family(author.family))
        elif author.literal:
            output.append(_literal_surname_display(author.literal))
    return _dedupe_display_names(output)


def _parsed_editor_display(parsed: ParsedReferenceResult) -> list[str]:
    parsed_data = parsed.parsed_data
    if parsed_data is None:
        return []
    output: list[str] = []
    for editor in parsed_data.editor:
        if editor.family:
            output.append(_surname_display_from_family(editor.family))
        elif editor.literal:
            output.append(_literal_surname_display(editor.literal))
    return _dedupe_display_names(output)


def _candidate_author_display(candidate: LocalDbCandidate) -> list[str]:
    return [_title_case_name(author) for author in _clean_candidate_authors(candidate.authors)]


def _literal_surname_display(value: str) -> str:
    text = _display_value(value)
    if not text:
        return ""
    if "," in text:
        return _surname_display_from_family(text.split(",", maxsplit=1)[0])
    return _surname_display_from_family(text)


def _surname_display_from_family(value: str | None) -> str:
    text = _display_value(value)
    if not text:
        return ""
    tokens = text.split()
    while tokens and _looks_like_initial_token(tokens[0]):
        tokens.pop(0)
    return " ".join(tokens)


def _looks_like_initial_token(token: str) -> bool:
    normalized = _normalize_text(token).replace(" ", "")
    return bool(normalized) and len(normalized) <= 2


def _author_comparison_score(
    parsed: ParsedReferenceResult,
    candidate: LocalDbCandidate,
    *,
    fallback_score: float,
) -> float:
    if fallback_score <= 0.0:
        return fallback_score
    uses_book_editors = parsed.ctype == "book_chapter" and candidate.record_granularity == "book"
    if uses_book_editors:
        source_authors = _normalized_author_values(_parsed_editor_display(parsed))
    else:
        source_authors = _normalized_author_values(_parsed_author_display(parsed))
    if not source_authors and not uses_book_editors:
        source_authors = _normalized_author_values([_raw_leading_surname_display(parsed.raw_reference)])
    found_authors = _normalized_author_values(_clean_candidate_authors(candidate.authors))
    if not source_authors or not found_authors:
        return fallback_score

    total = 0.0
    matched = 0
    unmatched_found = list(found_authors)
    for source in source_authors:
        exact = next((found for found in unmatched_found if _author_values_match(source, found)), None)
        if exact is not None:
            total += 1.0
            matched += 1
            unmatched_found.remove(exact)
            continue
        near = next(
            (found for found in unmatched_found if _edit_distance_leq_one(_author_match_key(source), _author_match_key(found))),
            None,
        )
        if near is not None:
            total += 0.9
            matched += 1
            unmatched_found.remove(near)

    missing_source = len(source_authors) - matched
    extra_found = len(unmatched_found)
    score = (total / len(source_authors)) - (0.3 * missing_source) - (0.3 * extra_found)
    bounded_score = round(max(0.0, min(1.0, score)), 4)
    if uses_book_editors:
        return max(fallback_score, bounded_score)
    return round(max(0.0, min(fallback_score, score)), 4)


def _candidate_with_phase5_container_signal(
    parsed: ParsedReferenceResult,
    candidate: LocalDbCandidate,
) -> LocalDbCandidate:
    if parsed.ctype != "journal_article":
        return candidate
    signals = candidate.match_signals
    if signals.container_match == "yes":
        return candidate
    if not journal_abbreviation_match(_source_container_text(parsed), _candidate_container_text(parsed, candidate)):
        return candidate
    reasons = list(candidate.match_reasons)
    if "container_or_publisher_abbreviation_match" not in reasons:
        reasons.append("container_or_publisher_abbreviation_match")
    return replace(
        candidate,
        match_signals=replace(signals, container_match="yes"),
        match_reasons=reasons,
    )


def _author_values_match(left: str, right: str) -> bool:
    return _author_match_key(left) == _author_match_key(right)


def _author_match_key(value: str) -> str:
    particles = {"de", "den", "der", "van", "von", "la", "le", "del", "della", "di", "du", "ten", "ter"}
    tokens = _normalize_text(value).split()
    while len(tokens) > 1 and tokens[0] in particles:
        tokens.pop(0)
    return " ".join(tokens)


def _clean_candidate_authors(authors: list[str]) -> list[str]:
    values = _dedupe_display_names(authors)
    normalized_values = {value: _normalize_text(value) for value in values}
    cleaned: list[str] = []
    for value in values:
        normalized = normalized_values[value]
        if not normalized:
            continue
        other_values = [other for other in values if other != value and normalized_values[other]]
        contained = [
            other
            for other in other_values
            if _normalized_author_contained(normalized_values[other], normalized)
        ]
        if len(contained) >= 2:
            continue
        cleaned.append(value)
    return cleaned


def _normalized_author_contained(author: str, combined: str) -> bool:
    if author == combined:
        return True
    return f" {author} " in f" {combined} "


def _normalized_author_values(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        author = _normalize_text(value)
        if author and author not in normalized:
            normalized.append(author)
    return normalized


def _dedupe_display_names(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _display_value(value)
        key = _normalize_text(text)
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text)
    return output


def _title_case_name(author: str) -> str:
    text = _display_value(author)
    return " ".join(part[:1].upper() + part[1:] for part in text.split())


def _edit_distance_leq_one(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if abs(len(left) - len(right)) > 1:
        return False
    if left == right:
        return True
    if len(left) == len(right):
        return sum(1 for lchar, rchar in zip(left, right) if lchar != rchar) <= 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    i = j = edits = 0
    while i < len(shorter) and j < len(longer):
        if shorter[i] == longer[j]:
            i += 1
            j += 1
            continue
        edits += 1
        if edits > 1:
            return False
        j += 1
    return True


def _first_text(values: list[str | None] | None) -> str:
    for value in values or []:
        text = _display_value(value)
        if text:
            return text
    return ""


def _display_value(value: str | None) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())


def _normalize_text(value: str | None) -> str:
    chars = [char.lower() if char.isalnum() else " " for char in str(value or "")]
    return " ".join("".join(chars).split())


def _main_title_key(value: str | None) -> str:
    text = str(value or "").strip()
    for separator in (":", ";"):
        if separator in text:
            text = text.split(separator, maxsplit=1)[0]
            break
    return _normalize_text(text)


def _parsed_primary_author_surname(parsed: ParsedReferenceResult) -> str:
    parsed_data = parsed.parsed_data
    if parsed_data and parsed_data.author:
        first = parsed_data.author[0]
        if first.family:
            return _normalize_text(first.family)
        if first.literal:
            return _normalize_text(first.literal.split()[0])
    return _raw_leading_surname(parsed.raw_reference)


def _parsed_primary_editor_surname(parsed: ParsedReferenceResult) -> str:
    parsed_data = parsed.parsed_data
    if parsed_data and parsed_data.editor:
        first = parsed_data.editor[0]
        if first.family:
            return _normalize_text(first.family)
        if first.literal:
            return _normalize_text(first.literal.split()[0])
    return ""


def _candidate_primary_author_surname(candidate: LocalDbCandidate | None) -> str:
    if candidate is None:
        return ""
    for author in candidate.authors:
        normalized = _normalize_text(author)
        if normalized:
            return normalized
    return ""


def _raw_leading_surname(raw_reference: str | None) -> str:
    text = str(raw_reference or "").strip()
    if not text:
        return ""
    first = text.split(",", maxsplit=1)[0].split()[0]
    return _normalize_text(first)


def _raw_leading_surname_display(raw_reference: str | None) -> str:
    text = str(raw_reference or "").strip()
    if not text:
        return ""
    return _display_value(text.split(",", maxsplit=1)[0].split()[0])


def generate_strengths(evidence_checks: list[Phase5EvidenceCheck]) -> list[str]:
    """Generate concise strengths from passing checks."""
    conflict_codes = {check.code for check in evidence_checks if check.status in {"warning", "fail"}}
    labels = {
        "EXTRACTED_DOI_MATCHES_CANDIDATE": "Extracted DOI matched the selected candidate",
        "EXTRACTED_DOI_EQUIVALENT_TO_CANDIDATE": "Extracted DOI matched the selected candidate",
        "TITLE_EXACT_OR_NEAR": "Title matched exactly",
        "TITLE_STRONG_MATCH": "Title matched strongly",
        "AUTHOR_STRONG_MATCH": "Author overlap was strong",
        "YEAR_EXACT_MATCH": "Year matched exactly",
        "CONTAINER_CONFIRMED": "Journal or publisher matched",
        "METADATA_EXACT_MATCH": "Volume, issue, or pages matched exactly",
    }
    contextual_labels = {
        ("TITLE_EXACT_OR_NEAR", "Containing book title"): "Containing book title matched exactly",
        ("TITLE_STRONG_MATCH", "Containing book title"): "Containing book title matched strongly",
    }
    return [
        contextual_labels.get((check.code, check.label), labels[check.code])
        for check in evidence_checks
        if check.status == "pass"
        and check.code in labels
        and not (
            check.code in {"EXTRACTED_DOI_MATCHES_CANDIDATE", "EXTRACTED_DOI_EQUIVALENT_TO_CANDIDATE"}
            and "DOI_RECORD_METADATA_CONFLICT" in conflict_codes
        )
    ][:4]


def generate_concerns(evidence_checks: list[Phase5EvidenceCheck]) -> list[str]:
    """Generate concise concerns from warning and failing checks."""
    labels = {
        "DOI_RECORD_METADATA_CONFLICT": "DOI record appears to describe a different source",
        "EXTRACTED_DOI_CONTRADICTS_CANDIDATE": "Extracted DOI contradicted the selected candidate",
        "EXTRACTED_DOI_NOT_FOUND_IN_DB": "Extracted DOI was not found in the database",
        "TITLE_WEAK_MATCH": "Title evidence was weak",
        "TITLE_PARTIAL_MATCH": "Title evidence was only partial",
        "JOURNAL_TITLE_AUTHOR_TENSION": "Journal title and author evidence conflicted",
        "AUTHOR_NO_MATCH": "No author overlap found",
        "AUTHOR_WEAK_MATCH": "Author overlap was weak",
        "YEAR_MISMATCH": "Year mismatch detected",
        "YEAR_NEAR_MATCH": "Year was close but not exact",
        "CONTAINER_MISMATCH": "Journal or publisher mismatch detected",
        "PUBLISHER_VARIANT": "Publisher differed from the candidate edition",
        "AMBIGUOUS_TOP_CANDIDATES": "Top candidates were very close",
        "STRUCTURAL_CONTRADICTION": "Structural contradictions were detected",
        "BOOK_LEVEL_RECOVERY": "Only a book-level recovery was available",
    }
    contextual_labels = {
        ("TITLE_WEAK_MATCH", "Containing book title"): "Containing book title evidence was weak",
        ("TITLE_PARTIAL_MATCH", "Containing book title"): "Containing book title evidence was only partial",
    }
    return [
        contextual_labels.get((check.code, check.label), labels[check.code])
        for check in evidence_checks
        if check.status in {"warning", "fail"} and check.code in labels
    ][:5]


def generate_review_flags(evidence_checks: list[Phase5EvidenceCheck]) -> list[str]:
    """Generate sparse alert-style review flags only."""
    allowed = {
        "DOI_RECORD_METADATA_CONFLICT",
        "EXTRACTED_DOI_CONTRADICTS_CANDIDATE",
        "YEAR_MISMATCH",
        "AUTHOR_NO_MATCH",
        "JOURNAL_TITLE_AUTHOR_TENSION",
        "CONTAINER_MISMATCH",
        "AMBIGUOUS_TOP_CANDIDATES",
        "BOOK_LEVEL_RECOVERY",
        "TITLE_WEAK_MATCH",
        "STRUCTURAL_CONTRADICTION",
    }
    rename = {
        "DOI_RECORD_METADATA_CONFLICT": "DOI_METADATA_CONFLICT",
        "EXTRACTED_DOI_CONTRADICTS_CANDIDATE": "DOI_MISMATCH",
        "AUTHOR_NO_MATCH": "AUTHOR_MISMATCH",
        "JOURNAL_TITLE_AUTHOR_TENSION": "TITLE_AUTHOR_TENSION",
        "TITLE_WEAK_MATCH": "WEAK_TITLE_EVIDENCE",
        "STRUCTURAL_CONTRADICTION": "STRUCTURAL_CONCERN",
    }
    flags: list[str] = []
    for check in evidence_checks:
        if check.code not in allowed:
            continue
        if check.code == "AMBIGUOUS_TOP_CANDIDATES" and check.status != "warning":
            continue
        if check.code != "AMBIGUOUS_TOP_CANDIDATES" and check.status == "pass":
            continue
        flag = rename.get(check.code, check.code)
        if flag not in flags:
            flags.append(flag)
    return flags


def generate_final_evidence_summary(
    final_status: Phase5StatusName,
    strengths: list[str],
    concerns: list[str],
) -> list[str]:
    """Generate final evidence summary combining strengths and concerns."""
    summary = [f"Final status: {final_status}"]
    if strengths:
        summary.append(f"Strengths: {', '.join(strengths[:3])}")
    if concerns:
        summary.append(f"Concerns: {', '.join(concerns[:3])}")
    if len(summary) == 1:
        summary.append("Limited evidence available")
    return summary


def _empty_breakdown() -> Phase5ScoreBreakdown:
    return Phase5ScoreBreakdown(
        title_score=0.0,
        author_score=0.0,
        year_score=0.0,
        container_score=0.0,
        doi_score=0.0,
        metadata_score=0.0,
        raw_score=0.0,
        ambiguity_penalty=0.0,
        structure_penalty=0.0,
        type_penalty=0.0,
        confidence_score=0.0,
    )


def evaluate_reference(
    parsed: ParsedReferenceResult,
    phase4: Phase4MatchResult,
    *,
    config: Phase5RuntimeConfig | None = None,
) -> Phase5MatchEvaluation:
    """Evaluate one reference and compute final confidence and status."""
    if config is None:
        config = Phase5RuntimeConfig()

    if phase4.status in ("skipped", "error"):
        return Phase5MatchEvaluation(
            reference_id=parsed.reference_id,
            phase4_status=phase4.status,
            final_status=phase4.status,  # type: ignore[arg-type]
            final_confidence="none",
            confidence_score=0.0,
            accepted_candidate=None,
            runner_up_candidate=None,
            top_candidate_gap=None,
            score_breakdown=_empty_breakdown(),
            report_signals=Phase5ReportSignals(
                strengths=[],
                concerns=[],
                review_flags=[],
                evidence_checks=[],
                final_evidence_summary=["No final evaluation was possible."],
                top_candidate_gap=None,
            ),
            reasons=list(phase4.reasons),
            warnings=list(phase4.warnings),
        )

    best_candidate = phase4.best_candidate
    if not best_candidate or phase4.status == "no_match":
        evidence_checks = [
            Phase5EvidenceCheck(
                code="NO_CANDIDATE_FOUND",
                status="fail",
                summary="No candidate was available for final evaluation.",
            )
        ]
        return Phase5MatchEvaluation(
            reference_id=parsed.reference_id,
            phase4_status=phase4.status,
            final_status="needs_review",
            final_confidence="none",
            confidence_score=0.0,
            accepted_candidate=None,
            runner_up_candidate=None,
            top_candidate_gap=None,
            score_breakdown=_empty_breakdown(),
            report_signals=Phase5ReportSignals(
                strengths=[],
                concerns=["No supported candidate was found"],
                review_flags=[],
                evidence_checks=evidence_checks,
                final_evidence_summary=[
                    "Final status: needs_review",
                    "No supported candidate was found.",
                ],
                top_candidate_gap=None,
            ),
            reasons=list(phase4.reasons) + ["phase5_needs_review_no_candidate"],
            warnings=list(phase4.warnings),
        )

    selection = _select_phase5_candidates(parsed, phase4, config)
    best_candidate = _candidate_with_phase5_container_signal(parsed, selection.selected_candidate)
    # Phase 5 owns the selected-candidate decision, but existing scoring helpers
    # consume a Phase4MatchResult-shaped object. This local wrapper does not
    # mutate or redefine the upstream Phase 4 result.
    evaluation_phase4 = replace(
        phase4,
        best_candidate=best_candidate,
        top_candidates=selection.ordered_candidates,
    )
    match_signals = best_candidate.match_signals
    component_scores = _component_scores(match_signals, parsed)
    component_scores["author"] = _author_comparison_score(
        parsed,
        best_candidate,
        fallback_score=component_scores["author"],
    )

    ambiguity_candidates = selection.ordered_candidates
    if not ambiguity_candidates and len(phase4.candidates) >= 2:
        ambiguity_candidates = phase4.candidates[:2]
    ambiguity_penalty, ambiguity_gap = compute_ambiguity_penalty(ambiguity_candidates, config)
    status_ambiguity_gap = ambiguity_gap
    evidence_ambiguity_gap = ambiguity_gap
    publisher_variant_ambiguity = _is_book_publisher_variant_ambiguity(
        parsed,
        ambiguity_candidates,
    )
    if publisher_variant_ambiguity:
        ambiguity_penalty = 0.0
        status_ambiguity_gap = None
        evidence_ambiguity_gap = config.ambiguity_gap_safe
    structure_penalty = compute_structure_penalty(parsed, evaluation_phase4, config, component_scores)
    type_penalty = compute_type_penalty(parsed, best_candidate, config)
    
    penalties = {
        "ambiguity": ambiguity_penalty,
        "structure": structure_penalty,
        "type": type_penalty,
    }

    raw_score, confidence_score = compute_final_score(component_scores, penalties, config)
    score_breakdown = Phase5ScoreBreakdown(
        title_score=component_scores["title"],
        author_score=component_scores["author"],
        year_score=component_scores["year"],
        container_score=component_scores["container"],
        doi_score=component_scores["doi"],
        metadata_score=component_scores["metadata"],
        raw_score=raw_score,
        ambiguity_penalty=ambiguity_penalty,
        structure_penalty=structure_penalty,
        type_penalty=type_penalty,
        confidence_score=confidence_score,
    )

    final_status = determine_final_status(
        confidence_score,
        phase4.status,
        status_ambiguity_gap,
        parsed,
        best_candidate,
        match_signals,
        penalties,
        component_scores,
        config,
    )
    final_confidence = map_confidence_score_to_name(confidence_score)
    evidence_checks = build_evidence_checks(
        parsed,
        evaluation_phase4,
        penalties,
        evidence_ambiguity_gap,
        component_scores,
    )
    field_comparisons = build_field_comparisons(parsed, best_candidate, component_scores)
    strengths = generate_strengths(evidence_checks)
    concerns = generate_concerns(evidence_checks)
    review_flags = generate_review_flags(evidence_checks)
    final_evidence_summary = generate_final_evidence_summary(final_status, strengths, concerns)

    runner_up = None
    if selection.doi_conflict_override_applied:
        runner_up = selection.runner_up_candidate
    elif ambiguity_gap is not None and ambiguity_gap < config.runner_up_gap_threshold:
        candidates = selection.ordered_candidates or phase4.candidates
        if len(candidates) >= 2:
            runner_up = candidates[1]

    report_signals = Phase5ReportSignals(
        strengths=strengths,
        concerns=concerns,
        review_flags=review_flags,
        evidence_checks=evidence_checks,
        field_comparisons=field_comparisons,
        final_evidence_summary=final_evidence_summary,
        top_candidate_gap=ambiguity_gap,
    )

    reasons = list(phase4.reasons)
    reasons.extend(selection.selection_reasons or [])
    if publisher_variant_ambiguity:
        reasons.append("phase5_book_publisher_variant_ambiguity_ignored")
    reasons.append(f"phase5_final_status:{final_status}")
    reasons.extend(f"phase5_review_flag:{flag}" for flag in review_flags)

    return Phase5MatchEvaluation(
        reference_id=parsed.reference_id,
        phase4_status=phase4.status,
        final_status=final_status,
        final_confidence=final_confidence,
        confidence_score=confidence_score,
        accepted_candidate=best_candidate,
        runner_up_candidate=runner_up,
        top_candidate_gap=ambiguity_gap,
        score_breakdown=score_breakdown,
        report_signals=report_signals,
        reasons=reasons,
        warnings=list(phase4.warnings),
    )


def evaluate_references(
    parsed_results: list[ParsedReferenceResult],
    phase4_results: list[Phase4MatchResult],
    *,
    config: Phase5RuntimeConfig | None = None,
) -> list[Phase5MatchEvaluation]:
    """Evaluate multiple references in batch."""
    if len(parsed_results) != len(phase4_results):
        raise ValueError(
            f"parsed_results ({len(parsed_results)}) and "
            f"phase4_results ({len(phase4_results)}) must have same length"
        )
    
    if config is None:
        config = Phase5RuntimeConfig()
    
    return [
        evaluate_reference(parsed, phase4, config=config)
        for parsed, phase4 in zip(parsed_results, phase4_results)
    ]
