from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import replace

from reference_gen2.reference_matching.models import (
    LocalDbCandidate,
    Phase4BatchInput,
    Phase4InputSummary,
    Phase4LookupTrace,
    Phase4MatchSignals,
    Phase4MatchResult,
    Phase4RuntimeConfig,
    Phase4SearchConfig,
)
from reference_gen2.reference_matching.journal_abbreviations import journal_abbreviation_match
from reference_gen2.reference_matching.provider import (
    doi_equivalence_key,
    doi_prefix_equivalent,
    normalize_doi,
    normalize_text,
)
from reference_gen2.reference_parsing.models import ParsedName, ParsedReferenceData, ParsedReferenceResult

_SUPPORTED_CTYPES: set[str] = {"journal_article", "book", "book_chapter"}
_COMMON_TITLE_WORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "into",
    "onto",
    "between",
    "within",
    "without",
    "through",
    "article",
    "articles",
    "journal",
    "book",
    "chapter",
}
_BROAD_SCHOLARLY_TITLE_WORDS = {
    "relationship",
    "study",
    "review",
    "reviews",
    "scoping",
    "evidence",
    "among",
    "adolescents",
    "adolescent",
    "mental",
    "health",
    "social",
    "media",
    "young",
    "adults",
    "adult",
    "use",
    "uses",
    "disorders",
}
_PUBLISHER_GENERIC_WORDS = {
    "academic",
    "books",
    "inc",
    "incorporated",
    "limited",
    "press",
    "publisher",
    "publishers",
    "publishing",
    "publications",
    "university",
}
_BOOK_CHAPTER_EDITOR_PREFIX_RE = re.compile(
    r"^\s*(?:In:?\s*)?.+?\(\s*(?:red\.?|reds\.?|ed\.?|eds\.?|editor|editors)\s*\)\s*",
    re.IGNORECASE,
)
_BOOK_CHAPTER_EDITOR_NAMES_RE = re.compile(
    r"^\s*(?:In:?\s*)?(?P<names>.+?)\s*\(\s*(?:red\.?|reds\.?|ed\.?|eds\.?|editor|editors)\s*\)",
    re.IGNORECASE,
)
_EDITOR_NAME_NOISE_WORDS = {"and", "en", "ed", "eds", "editor", "editors", "in", "red", "reds"}
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


def match_reference(
    parsed_result: ParsedReferenceResult,
    *,
    config: Phase4RuntimeConfig,
) -> Phase4MatchResult:
    started_at = time.perf_counter()
    input_summary = _build_input_summary(parsed_result)
    warnings = list(parsed_result.warnings)
    reasons: list[str] = []
    trace = Phase4LookupTrace()
    match_preparation = parsed_result.match_preparation
    if match_preparation is None:
        return _finalize_result(
            Phase4MatchResult(
                reference_id=parsed_result.reference_id,
                input_summary=input_summary,
                attempted=False,
                status="skipped",
                reasons=["phase4_match_preparation_missing"],
                warnings=warnings,
            ),
            started_at=started_at,
            doi_ms=0.0,
            fallback_ms=0.0,
        )

    if parsed_result.ctype not in _SUPPORTED_CTYPES:
        skipped_reasons = [
            "phase4_ineligible_ctype",
            f"phase4_unsupported_ctype:{parsed_result.ctype}",
        ]
        return _finalize_result(
            Phase4MatchResult(
                reference_id=parsed_result.reference_id,
                input_summary=input_summary,
                attempted=False,
                status="skipped",
                reasons=skipped_reasons,
                warnings=warnings,
                lookup_trace=replace(trace, skipped_reasons=skipped_reasons),
            ),
            started_at=started_at,
            doi_ms=0.0,
            fallback_ms=0.0,
        )

    if not match_preparation.eligible_for_db_match:
        skipped_reasons = [
            "phase4_not_match_eligible",
            f"phase4_match_target:{match_preparation.match_target}",
        ]
        return _finalize_result(
            Phase4MatchResult(
                reference_id=parsed_result.reference_id,
                input_summary=input_summary,
                attempted=False,
                status="skipped",
                reasons=skipped_reasons,
                warnings=warnings,
                lookup_trace=replace(trace, skipped_reasons=skipped_reasons),
            ),
            started_at=started_at,
            doi_ms=0.0,
            fallback_ms=0.0,
        )

    try:
        provider = config.resolve_provider()
    except Exception as exc:
        return _finalize_result(
            Phase4MatchResult(
                reference_id=parsed_result.reference_id,
                input_summary=input_summary,
                attempted=False,
                status="error",
                reasons=["phase4_provider_unavailable"],
                warnings=warnings + [f"phase4_provider_error:{type(exc).__name__}"],
            ),
            started_at=started_at,
            doi_ms=0.0,
            fallback_ms=0.0,
        )

    ctype = parsed_result.ctype  # narrowed by guard above
    normalized_doi = input_summary.normalized_doi or ""
    search_configs = _build_search_configs(
        parsed_result,
        config=config,
        max_candidates=config.max_candidates,
    )
    missing_fields = _required_missing_fields(parsed_result)
    if not normalized_doi and not search_configs:
        reasons.extend(["phase4_insufficient_lookup_fields"])
        reasons.extend(f"phase4_missing_field:{field}" for field in missing_fields)
        return _finalize_result(
            Phase4MatchResult(
                reference_id=parsed_result.reference_id,
                input_summary=input_summary,
                attempted=False,
                status="skipped",
                reasons=reasons,
                warnings=warnings,
                lookup_trace=replace(trace, skipped_reasons=reasons),
            ),
            started_at=started_at,
            doi_ms=0.0,
            fallback_ms=0.0,
        )

    candidates: list[LocalDbCandidate] = []
    strategy_used: str | None = None
    doi_ms = 0.0
    fallback_ms = 0.0
    doi_candidate_was_suspicious = False

    try:
        if normalized_doi:
            doi_started = time.perf_counter()
            doi_candidates = provider.lookup_by_doi(
                ctype=ctype,  # type: ignore[arg-type]
                doi=normalized_doi,
                max_candidates=config.max_candidates,
            )
            doi_ms = round((time.perf_counter() - doi_started) * 1000, 2)
            candidates = _merge_ranked_candidates(
                candidates,
                doi_candidates,
                parsed_result=parsed_result,
            )
            trace = replace(
                trace,
                doi_attempted=True,
                doi_query_values=[normalized_doi],
                doi_hit_count=len(doi_candidates),
                doi_miss=len(doi_candidates) == 0,
            )
            if any("doi_exact_match" in candidate.match_reasons for candidate in candidates):
                strategy_used = "doi_exact"
                suspicious = _doi_hit_is_suspicious(candidates[0])
                trace = replace(
                    trace,
                    doi_hit_quality="suspicious" if suspicious else "clean",
                    corroboration_triggered=bool(
                        suspicious and config.allow_doi_corroboration_search
                    ),
                )
                if suspicious:
                    reasons.append("phase4_doi_hit_suspicious")
                    doi_candidate_was_suspicious = True
                else:
                    reasons.append("phase4_doi_hit_clean")
        if strategy_used is None or (
            doi_candidate_was_suspicious and config.allow_doi_corroboration_search
        ):
            fallback_started = time.perf_counter()
            attempted = list(trace.strategies_attempted)
            skipped = list(trace.strategies_skipped)
            selected_query_terms = dict(trace.selected_query_terms)
            query_profiles = dict(trace.query_profiles)
            year_profiles = dict(trace.year_profiles)
            skipped_reasons = list(trace.skipped_reasons)
            executed = 0
            stop_reason: str | None = None
            fallback_configs = search_configs
            doi_miss_recall_band_available = (
                trace.doi_miss
                and parsed_result.ctype == "journal_article"
                and any(_is_doi_miss_recall_config(config_item) for config_item in search_configs)
            )
            doi_miss_recall_band_entered = False
            doi_miss_recall_band_candidates_found = False
            max_fallback_strategies = _max_fallback_strategies_for_result(
                parsed_result,
                config=config,
            )
            if doi_candidate_was_suspicious:
                reasons.append("phase4_doi_hit_text_corroboration_started")
                fallback_configs = search_configs[: config.max_corroboration_strategies]
            for search_config in fallback_configs:
                selected_query_terms[search_config.name] = [
                    *search_config.title_terms,
                    *search_config.author_terms,
                    *search_config.container_terms,
                ]
                query_profiles[search_config.name] = search_config.strictness
                year_profiles[search_config.name] = search_config.year_mode
                skip_reason = _skip_search_config_reason(
                    parsed_result,
                    search_config,
                    config=config,
                    doi_missed=trace.doi_miss,
                )
                if skip_reason:
                    skipped.append(search_config.name)
                    skipped_reasons.append(skip_reason)
                    continue
                if _is_doi_miss_recall_config(search_config):
                    doi_miss_recall_band_entered = True
                attempted.append(search_config.name)
                executed += 1
                fallback_candidates = provider.search_candidates(
                    ctype=ctype,  # type: ignore[arg-type]
                    config=search_config,
                    max_candidates=config.max_candidates,
                )
                if _is_doi_miss_recall_config(search_config) and fallback_candidates:
                    doi_miss_recall_band_candidates_found = True
                candidates = _merge_ranked_candidates(
                    candidates,
                    fallback_candidates,
                    parsed_result=parsed_result,
                )
                stop_reason = _fallback_stop_reason(candidates, config=config)
                if stop_reason:
                    break
                if executed >= max_fallback_strategies:
                    stop_reason = "phase4_stop_max_steps_reached"
                    break
            fallback_ms = round((time.perf_counter() - fallback_started) * 1000, 2)
            trace = replace(
                trace,
                strategies_attempted=attempted,
                strategies_skipped=skipped,
                selected_query_terms=selected_query_terms,
                query_profiles=query_profiles,
                year_profiles=year_profiles,
                cascade_stop_reason=stop_reason,
                skipped_reasons=_dedupe_preserve_order(skipped_reasons),
            )
            if doi_miss_recall_band_available:
                if doi_miss_recall_band_entered:
                    reasons.append("phase4_doi_miss_recall_band_entered")
                    if doi_miss_recall_band_candidates_found:
                        reasons.append("phase4_doi_miss_recall_band_candidates_found")
                    else:
                        reasons.append("phase4_doi_miss_recall_band_exhausted")
                else:
                    reasons.append("phase4_doi_miss_recall_band_skipped")
    except Exception as exc:
        return _finalize_result(
            Phase4MatchResult(
                reference_id=parsed_result.reference_id,
                input_summary=input_summary,
                attempted=True,
                status="error",
                reasons=["phase4_lookup_failed"],
                warnings=warnings + [f"phase4_lookup_error:{type(exc).__name__}"],
                lookup_trace=replace(
                    trace,
                    timings_ms={"doi": doi_ms, "fallback": fallback_ms, "total": 0.0},
                ),
            ),
            started_at=started_at,
            doi_ms=doi_ms,
            fallback_ms=fallback_ms,
        )

    retained_candidates = candidates[: config.max_candidates]
    top_candidates, second_candidate_reason = _select_top_candidates(
        retained_candidates,
        config=config,
    )
    best_candidate = top_candidates[0] if top_candidates else None
    if best_candidate is None:
        if trace.doi_miss and trace.strategies_attempted:
            reasons.append("phase4_doi_miss_no_selective_fallback_match")
        reasons.append("phase4_no_candidates")
        status = "no_match"
    else:
        reasons.extend(best_candidate.match_reasons)
        reasons.append("phase4_candidates_found")
        if trace.doi_miss and best_candidate.source_strategy and best_candidate.source_strategy != "doi_exact":
            reasons.append("doi_miss_title_year_recovery")
        if len(top_candidates) >= 2:
            reasons.append("phase4_second_candidate_retained")
        elif second_candidate_reason:
            reasons.append(second_candidate_reason)
        if best_candidate.ordering_score >= 0.6 or "doi_exact_match" in best_candidate.match_reasons:
            status = "matched_provisional"
        else:
            status = "candidate_only"
        strategy_used = strategy_used or best_candidate.source_strategy

    trace = replace(
        trace,
        candidate_count=len(candidates),
        second_candidate_retained=len(top_candidates) >= 2,
        second_candidate_rejected_reason=second_candidate_reason,
    )
    return _finalize_result(
        Phase4MatchResult(
            reference_id=parsed_result.reference_id,
            input_summary=input_summary,
            attempted=bool(normalized_doi or search_configs),
            strategy_used=strategy_used,
            lookup_trace=trace,
            candidates=retained_candidates,
            top_candidates=top_candidates,
            best_candidate=best_candidate,
            status=status,
            reasons=_dedupe_preserve_order(reasons),
            warnings=warnings,
            timings_ms={"doi": doi_ms, "fallback": fallback_ms, "total": 0.0},
        ),
        started_at=started_at,
        doi_ms=doi_ms,
        fallback_ms=fallback_ms,
    )


def match_references(
    batch_input: Phase4BatchInput,
    *,
    config: Phase4RuntimeConfig,
) -> list[Phase4MatchResult]:
    source = batch_input.phase3b if (config.prefer_recovered and batch_input.phase3b) else batch_input.phase3
    return [match_reference(result, config=config) for result in source]


def _build_input_summary(parsed_result: ParsedReferenceResult) -> Phase4InputSummary:
    parsed = parsed_result.parsed_data
    doi = normalize_doi(_first_list_item(parsed.doi) if parsed else None)
    title = normalize_text(_first_list_item(parsed.title) if parsed else None)
    year = (parsed.issued_year or "").strip() if parsed else ""
    match_target = (
        parsed_result.match_preparation.match_target
        if parsed_result.match_preparation is not None
        else "none"
    )
    return Phase4InputSummary(
        reference_id=parsed_result.reference_id,
        ctype=parsed_result.ctype,
        match_target=match_target,
        normalized_doi=doi or None,
        normalized_title=title or None,
        normalized_year=year or None,
        normalized_authors=_parsed_author_surnames(parsed),
    )


def _required_missing_fields(parsed_result: ParsedReferenceResult) -> list[str]:
    match_preparation = parsed_result.match_preparation
    if match_preparation is None:
        return ["match_preparation"]
    required_by_type = {
        "journal_article": ["title", "issued_year"],
        "book": ["title", "issued_year"],
        "book_chapter": ["chapter_title", "book_title", "issued_year"],
    }
    required_fields = required_by_type.get(parsed_result.ctype, [])
    return [
        field_name
        for field_name in required_fields
        if not match_preparation.lookup_key_fields.get(field_name)
    ]


def _build_search_configs(
    parsed_result: ParsedReferenceResult,
    *,
    config: Phase4RuntimeConfig,
    max_candidates: int,
) -> list[Phase4SearchConfig]:
    parsed = parsed_result.parsed_data
    match_preparation = parsed_result.match_preparation
    if parsed is None or match_preparation is None:
        return []
    query_fields = match_preparation.lookup_query_fields
    issued_year = _first_list_item(query_fields.get("issued_year", []))
    authors = [_normalize_name_term(name) for name in query_fields.get("author", [])]
    authors = [author for author in authors if author]
    editors = [_normalize_name_term(name) for name in query_fields.get("editor", [])]
    editors = [editor for editor in editors if editor]
    title_source = query_fields.get("title", [])
    if parsed_result.ctype == "book_chapter":
        title_source = query_fields.get("chapter_title", []) or title_source
    title = _first_list_item(title_source)
    title_terms = _meaningful_title_terms(
        title,
        prefer_distinctive=config.prefer_distinctive_title_terms,
    )
    if parsed_result.ctype == "journal_article":
        if not title_terms or not issued_year:
            return []
        container_terms = _meaningful_title_terms(
            _first_list_item(query_fields.get("container_title", [])),
            prefer_distinctive=config.prefer_distinctive_title_terms,
        )
        near_year_enabled = config.enable_near_year_fallback and bool(issued_year)
        title_prefix_6 = title_terms[:6]
        configs = [
            Phase4SearchConfig(
                name="journal_title_year_exact",
                title_terms=title_terms[:4],
                fielded_terms={"title_norm": title_terms[:4]},
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max_candidates,
                strictness="strict",
            ),
            Phase4SearchConfig(
                name="journal_title_author_year_exact",
                title_terms=title_terms[:4],
                author_terms=authors[:1],
                fielded_terms={
                    "title_norm": title_terms[:4],
                    "author_text": authors[:1],
                },
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max_candidates,
                strictness="balanced",
            ),
            Phase4SearchConfig(
                name="journal_title3_year_exact",
                title_terms=title_terms[:3],
                fielded_terms={"title_norm": title_terms[:3]},
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max_candidates,
                strictness="balanced",
            ),
            Phase4SearchConfig(
                name="journal_title_container_year_exact",
                title_terms=title_terms[:4],
                container_terms=container_terms[:2],
                fielded_terms={
                    "title_norm": title_terms[:4],
                    "container_text": container_terms[:2],
                },
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max_candidates,
                strictness="balanced",
            ),
            Phase4SearchConfig(
                name="journal_title6_year_exact_doi_miss",
                title_terms=title_prefix_6,
                fielded_terms={"title_norm": title_prefix_6},
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max(max_candidates, 3),
                strictness="balanced",
            ),
            Phase4SearchConfig(
                name="journal_title6_author_year_exact_doi_miss",
                title_terms=title_prefix_6,
                author_terms=authors[:1],
                fielded_terms={
                    "title_norm": title_prefix_6,
                    "author_text": authors[:1],
                },
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max(max_candidates, 3),
                strictness="balanced",
            ),
            Phase4SearchConfig(
                name="journal_title6_container_year_exact_doi_miss",
                title_terms=title_prefix_6,
                container_terms=container_terms[:3],
                fielded_terms={
                    "title_norm": title_prefix_6,
                    "container_text": container_terms[:3],
                },
                target_tables=["search_journal", "search_conference"],
                year=issued_year,
                year_mode="exact",
                limit=max(max_candidates, 3),
                strictness="balanced",
            ),
        ]
        if near_year_enabled:
            configs.append(
                Phase4SearchConfig(
                    name="journal_title3_year_near",
                    title_terms=title_terms[:3],
                    fielded_terms={"title_norm": title_terms[:3]},
                    target_tables=["search_journal", "search_conference"],
                    year=issued_year,
                    year_mode="near",
                    year_window=config.near_year_distance,
                    limit=max_candidates * 2,
                    strictness="relaxed",
                    enabled_by_default=False,
                )
            )
            configs.append(
                Phase4SearchConfig(
                    name="journal_title6_year_near_doi_miss",
                    title_terms=title_prefix_6,
                    fielded_terms={"title_norm": title_prefix_6},
                    target_tables=["search_journal", "search_conference"],
                    year=issued_year,
                    year_mode="near",
                    year_window=config.near_year_distance,
                    limit=max(max_candidates, 3) * 2,
                    strictness="balanced",
                )
            )
        return _drop_empty_configs(_dedupe_search_configs(configs))
    if parsed_result.ctype == "book":
        if not title_terms or not issued_year:
            return []
        main_title_terms = _main_title_terms(
            title,
            prefer_distinctive=config.prefer_distinctive_title_terms,
        )
        main_title_terms = main_title_terms or title_terms
        title_prefix_2 = title_terms[:2]
        main_title_prefix_2 = main_title_terms[:2]
        near_year_enabled = config.enable_near_year_fallback and bool(issued_year)
        return _drop_empty_configs(
            _dedupe_search_configs(
                [
                Phase4SearchConfig(
                    name="book_main_title_author_year_exact",
                    title_terms=main_title_terms[:6],
                    author_terms=authors[:1],
                    fielded_terms={
                        "title_norm": main_title_terms[:6],
                        "author_text": authors[:1],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="exact",
                    limit=max_candidates,
                    strictness="strict",
                ),
                Phase4SearchConfig(
                    name="book_main_title_year_exact",
                    title_terms=main_title_terms[:6],
                    fielded_terms={
                        "title_norm": main_title_terms[:6],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="exact",
                    limit=max_candidates,
                    strictness="strict",
                ),
                Phase4SearchConfig(
                    name="book_title2_author_year_exact",
                    title_terms=title_prefix_2 or main_title_prefix_2,
                    author_terms=authors[:1],
                    fielded_terms={
                        "title_norm": title_prefix_2 or main_title_prefix_2,
                        "author_text": authors[:1],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="exact",
                    limit=max_candidates,
                    strictness="balanced",
                ),
                Phase4SearchConfig(
                    name="book_title2_author_year_near",
                    title_terms=title_prefix_2 or main_title_prefix_2,
                    author_terms=authors[:1],
                    fielded_terms={
                        "title_norm": title_prefix_2 or main_title_prefix_2,
                        "author_text": authors[:1],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="near",
                    year_window=config.near_year_distance if near_year_enabled else 0,
                    limit=max_candidates * 2,
                    strictness="relaxed",
                    enabled_by_default=near_year_enabled,
                ),
            ]
            )
        )
    if parsed_result.ctype == "book_chapter":
        chapter_title = _first_list_item(query_fields.get("chapter_title", [])) or title
        chapter_title_terms = _meaningful_title_terms(
            chapter_title,
            prefer_distinctive=config.prefer_distinctive_title_terms,
        )
        if not chapter_title_terms or not issued_year:
            return []
        chapter_main_title_terms = _main_title_terms(
            chapter_title,
            prefer_distinctive=config.prefer_distinctive_title_terms,
        )
        chapter_main_title_terms = chapter_main_title_terms or chapter_title_terms
        chapter_title_prefix_2 = chapter_title_terms[:2]
        
        # Book-chapter match preparation stores the containing book under
        # `book_title`; keep `container_title` as a backward-compatible fallback.
        book_title = _first_list_item(query_fields.get("book_title", [])) or _first_list_item(
            query_fields.get("container_title", [])
        )
        if not editors:
            editors = _book_chapter_editor_terms_from_book_title(book_title)
        book_title_for_query = _book_chapter_book_title_for_query(book_title)
        book_title_terms = _meaningful_title_terms(
            book_title_for_query,
            prefer_distinctive=config.prefer_distinctive_title_terms,
        )
        book_main_title_terms = _main_title_terms(
            book_title_for_query,
            prefer_distinctive=config.prefer_distinctive_title_terms,
        )
        book_main_title_terms = book_main_title_terms or book_title_terms
        
        near_year_enabled = config.enable_near_year_fallback and bool(issued_year)
        
        # Interleave chapter and book searches for better performance.
        # Use ±1 year tolerance on early configs to catch year mismatches faster.
        configs = [
            # Config 1: Chapter title + author + near year (Crossref)
            Phase4SearchConfig(
                name="chapter_main_title_author_year_near",
                title_terms=chapter_main_title_terms[:5],
                author_terms=authors[:1],
                fielded_terms={
                    "title_norm": chapter_main_title_terms[:5],
                    "author_text": authors[:1],
                },
                target_tables=["search_book_chapter"],
                year=issued_year,
                year_mode="near",
                year_window=1,  # ±1 year tolerance
                limit=max_candidates,
                strictness="strict",
            ),
            # Config 2: Chapter title + near year (Crossref)
            Phase4SearchConfig(
                name="chapter_main_title_year_near",
                title_terms=chapter_main_title_terms[:5],
                fielded_terms={
                    "title_norm": chapter_main_title_terms[:5],
                },
                target_tables=["search_book_chapter"],
                year=issued_year,
                year_mode="near",
                year_window=1,  # ±1 year tolerance
                limit=max_candidates,
                strictness="strict",
            ),
        ]
        
        # Add book title searches early (interleaved with chapter searches).
        # These help find chapters indexed only at the book level in OpenLibrary.
        if book_title_terms:
            if editors:
                configs.extend(
                    [
                        # Config 3: Book title + editor + near year (OpenLibrary)
                        # Edited volumes are stored as book records with editor
                        # surnames in the author index, so query those before the
                        # broader title-only book fallback.
                        Phase4SearchConfig(
                            name="chapter_book_title_editor_year_near",
                            title_terms=book_main_title_terms[:5],
                            author_terms=editors[:2],
                            fielded_terms={
                                "title_norm": book_main_title_terms[:5],
                                "author_text": editors[:2],
                            },
                            target_tables=["search_book"],
                            year=issued_year,
                            year_mode="near",
                            year_window=1,  # ±1 year tolerance
                            limit=max_candidates,
                            strictness="balanced",
                            enabled_by_default=True,
                        ),
                        # Config 4: Book title + editor + exact year (OpenLibrary)
                        Phase4SearchConfig(
                            name="chapter_book_title_editor_year_exact",
                            title_terms=book_main_title_terms[:5],
                            author_terms=editors[:2],
                            fielded_terms={
                                "title_norm": book_main_title_terms[:5],
                                "author_text": editors[:2],
                            },
                            target_tables=["search_book"],
                            year=issued_year,
                            year_mode="exact",
                            limit=max_candidates,
                            strictness="balanced",
                            enabled_by_default=True,
                        ),
                    ]
                )
            configs.extend([
                # Config 5: Book title + near year (OpenLibrary)
                Phase4SearchConfig(
                    name="chapter_book_title_year_near",
                    title_terms=book_main_title_terms[:5],
                    fielded_terms={
                        "title_norm": book_main_title_terms[:5],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="near",
                    year_window=1,  # ±1 year tolerance
                    limit=max_candidates,
                    strictness="balanced",
                    enabled_by_default=True,
                ),
                # Config 6: Book title + exact year (OpenLibrary)
                Phase4SearchConfig(
                    name="chapter_book_title_year_exact",
                    title_terms=book_main_title_terms[:5],
                    fielded_terms={
                        "title_norm": book_main_title_terms[:5],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="exact",
                    limit=max_candidates,
                    strictness="balanced",
                    enabled_by_default=True,
                ),
                Phase4SearchConfig(
                    name="chapter_book_title_year_near",
                    title_terms=book_main_title_terms[:5],
                    fielded_terms={
                        "title_norm": book_main_title_terms[:5],
                    },
                    target_tables=["search_book"],
                    year=issued_year,
                    year_mode="near",
                    year_window=config.near_year_distance if near_year_enabled else 0,
                    limit=max_candidates * 2,
                    strictness="relaxed",
                    enabled_by_default=near_year_enabled,
                ),
            ])
        
        return _drop_empty_configs(_dedupe_search_configs(configs))
    return []


def _drop_empty_configs(configs: list[Phase4SearchConfig]) -> list[Phase4SearchConfig]:
    output: list[Phase4SearchConfig] = []
    for config in configs:
        if config.title_terms or config.author_terms or config.container_terms:
            output.append(config)
    return output


def _dedupe_search_configs(configs: list[Phase4SearchConfig]) -> list[Phase4SearchConfig]:
    output: list[Phase4SearchConfig] = []
    seen: set[
        tuple[
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            tuple[str, ...],
            str | None,
            str,
            int,
        ]
    ] = set()
    for config in configs:
        key = (
            tuple(config.title_terms),
            tuple(config.author_terms),
            tuple(config.container_terms),
            tuple(config.target_tables),
            config.year,
            config.year_mode,
            config.year_window,
        )
        if key in seen:
            continue
        seen.add(key)
        output.append(config)
    return output


def _doi_hit_is_suspicious(candidate: LocalDbCandidate) -> bool:
    signals = candidate.match_signals
    if signals.title_match_strength in {"none", "weak", "partial"}:
        return True
    if signals.year_match_type == "mismatch":
        return True
    if signals.author_match_strength in {"none", "weak"}:
        return True
    if signals.container_match == "no":
        return True
    if signals.volume_issue_pages_match == "mismatch":
        return True
    mismatch_count = 0
    if signals.title_match_strength == "strong":
        mismatch_count += 0
    if signals.year_match_type == "near":
        mismatch_count += 1
    if signals.author_match_strength == "partial":
        mismatch_count += 1
    if signals.container_match == "unknown":
        mismatch_count += 1
    if signals.volume_issue_pages_match == "partial":
        mismatch_count += 1
    return mismatch_count >= 2


def _select_top_candidates(
    retained_candidates: list[LocalDbCandidate],
    *,
    config: Phase4RuntimeConfig,
) -> tuple[list[LocalDbCandidate], str | None]:
    if not retained_candidates:
        return [], None
    top = [retained_candidates[0]]
    if config.max_top_candidates <= 1 or len(retained_candidates) == 1:
        return top, None
    second = retained_candidates[1]
    if second.record_id == retained_candidates[0].record_id:
        return top, "phase4_second_candidate_not_credible"
    if second.ordering_score < config.second_candidate_min_ordering_score:
        return top, "phase4_second_candidate_not_credible"
    if (
        second.doi
        and retained_candidates[0].doi
        and normalize_doi(second.doi) == normalize_doi(retained_candidates[0].doi)
    ):
        return top, "phase4_second_candidate_not_credible"
    top.append(second)
    return top, None


def _skip_search_config_reason(
    parsed_result: ParsedReferenceResult,
    search_config: Phase4SearchConfig,
    *,
    config: Phase4RuntimeConfig,
    doi_missed: bool,
) -> str | None:
    if _is_doi_miss_recall_config(search_config):
        if not doi_missed:
            return "phase4_doi_miss_recall_band_skipped"
        if not _allows_doi_miss_recall_config(search_config):
            return "phase4_doi_miss_recall_band_not_selective_enough"
    if not search_config.enabled_by_default and not config.enable_relaxed_queries:
        return "phase4_skipped_relaxed_query_default_off"
    if parsed_result.ctype == "journal_article" and not search_config.year:
        return "phase4_missing_year_for_journal_query"
    if not config.broad_query_guard_enabled:
        return None
    title_terms = search_config.title_terms
    if parsed_result.ctype == "journal_article" and search_config.strictness == "strict" and len(title_terms) < 2:
        return "phase4_skipped_broad_title_query"
    if (
        parsed_result.ctype == "journal_article"
        and title_terms
        and _is_broad_query_terms(title_terms)
        and not _is_doi_miss_recall_config(search_config)
    ):
        return (
            "phase4_doi_miss_no_selective_fallback"
            if doi_missed
            else "phase4_skipped_broad_title_query"
        )
    return None


def _max_fallback_strategies_for_result(
    parsed_result: ParsedReferenceResult,
    *,
    config: Phase4RuntimeConfig,
) -> int:
    if parsed_result.ctype == "book_chapter":
        return max(config.max_fallback_strategies, 6)
    if parsed_result.ctype == "book":
        return max(config.max_fallback_strategies, 4)
    if parsed_result.ctype == "journal_article":
        return max(config.max_fallback_strategies, 6)
    return config.max_fallback_strategies


def _is_doi_miss_recall_config(search_config: Phase4SearchConfig) -> bool:
    return "_doi_miss" in search_config.name


def _allows_doi_miss_recall_config(search_config: Phase4SearchConfig) -> bool:
    return bool(search_config.year) and len(search_config.title_terms) >= 5


def _merge_ranked_candidates(
    existing: list[LocalDbCandidate],
    incoming: list[LocalDbCandidate],
    *,
    parsed_result: ParsedReferenceResult,
) -> list[LocalDbCandidate]:
    ranked: dict[str, LocalDbCandidate] = {candidate.record_id: candidate for candidate in existing}
    for candidate in incoming:
        scored_candidate = _score_candidate(parsed_result, candidate)
        current = ranked.get(scored_candidate.record_id)
        if current is None or scored_candidate.ordering_score > current.ordering_score:
            ranked[scored_candidate.record_id] = scored_candidate
    return sorted(
        ranked.values(),
        key=lambda candidate: (candidate.ordering_score, candidate.record_id),
        reverse=True,
    )


def _score_candidate(
    parsed_result: ParsedReferenceResult,
    candidate: LocalDbCandidate,
) -> LocalDbCandidate:
    parsed = parsed_result.parsed_data or ParsedReferenceData()
    reasons: list[str] = []
    score = 0.0

    student_doi = normalize_doi(_first_list_item(parsed.doi))
    candidate_doi = normalize_doi(candidate.doi)
    doi_match_type = "none"
    if student_doi and candidate_doi:
        if student_doi == candidate_doi:
            score += 1.0
            doi_match_type = "exact"
            reasons.append("doi_exact_match")
        elif doi_equivalence_key(student_doi) == doi_equivalence_key(candidate_doi):
            score += 0.85
            doi_match_type = "equivalent"
            reasons.append("doi_equivalent_match")
        elif doi_prefix_equivalent(student_doi, candidate_doi):
            # Common case: the student captured only the DOI stem (e.g.
            # "10.1111/1467-9566.") while the database has the fully
            # qualified DOI ("10.1111/1467-9566.13038"), or vice versa.
            score += 0.85
            doi_match_type = "equivalent"
            reasons.append("doi_prefix_equivalent_match")
        else:
            doi_match_type = "mismatch"
            reasons.append("doi_mismatch")

    title_similarity = _title_similarity(parsed_result, candidate)
    title_match_strength = _title_match_strength(title_similarity)
    if title_similarity >= 0.95:
        reasons.append("title_exact_or_near_exact")
    elif title_similarity >= 0.6:
        reasons.append("title_strong_match")
    elif title_similarity >= 0.3:
        reasons.append("title_partial_match")
    elif title_similarity > 0:
        reasons.append("title_weak_match")
    score += title_similarity * 0.45

    author_overlap = _candidate_author_overlap(parsed_result, candidate)
    author_match_strength = _author_match_strength(author_overlap)
    if author_overlap >= 1.0:
        reasons.append("author_exact_overlap")
    elif author_overlap >= 0.5:
        reasons.append("author_partial_overlap")
    elif author_overlap > 0:
        reasons.append("author_weak_overlap")
    score += author_overlap * 0.2

    year_score = _year_score(parsed.issued_year, candidate.issued_year)
    year_match_type = _year_match_type(parsed.issued_year, candidate.issued_year)
    if year_score >= 1.0:
        reasons.append("year_exact_match")
    elif year_score > 0:
        reasons.append("year_near_match")
    elif parsed.issued_year and candidate.issued_year:
        reasons.append("year_mismatch")
    score += year_score * 0.15

    container_score = _container_score(parsed_result, candidate)
    container_match = _container_match_state(parsed_result, candidate, container_score)
    if container_score >= 1.0:
        reasons.append("container_or_publisher_match")
    elif container_score > 0:
        reasons.append("container_or_publisher_partial_match")
    score += container_score * 0.1

    metadata_score = _metadata_score(parsed, candidate)
    volume_issue_pages_match = _metadata_match_type(parsed, candidate)
    if metadata_score > 0:
        reasons.append("volume_issue_pages_signal")
    score += metadata_score * 0.1

    return replace(
        candidate,
        match_signals=Phase4MatchSignals(
            doi_match_type=doi_match_type,
            title_match_strength=title_match_strength,
            author_match_strength=author_match_strength,
            year_match_type=year_match_type,
            container_match=container_match,
            volume_issue_pages_match=volume_issue_pages_match,
        ),
        ordering_score=round(min(score, 1.0), 4),
        match_reasons=_dedupe_preserve_order(reasons),
    )


def _candidate_author_overlap(
    parsed_result: ParsedReferenceResult,
    candidate: LocalDbCandidate,
) -> float:
    parsed = parsed_result.parsed_data
    if parsed_result.ctype == "book_chapter" and candidate.record_granularity == "book":
        parsed_authors = _parsed_editor_surnames(parsed)
        if not parsed_authors and parsed is not None:
            book_title = _first_list_item(parsed.container_title or parsed.collection_title)
            parsed_authors = _book_chapter_editor_terms_from_book_title(book_title)
    else:
        parsed_authors = _parsed_author_surnames(parsed)
    overlap = _author_overlap(parsed_authors, candidate.authors)
    if parsed_result.ctype != "book":
        return overlap

    primary = _student_primary_author_surname(parsed_result)
    candidate_primary = _candidate_primary_author_surname(candidate)
    if primary and candidate_primary and primary == candidate_primary:
        return 1.0
    return overlap


def _container_score(
    parsed_result: ParsedReferenceResult,
    candidate: LocalDbCandidate,
) -> float:
    parsed = parsed_result.parsed_data
    if parsed is None:
        return 0.0
    if parsed_result.ctype == "journal_article":
        source_values = [_first_list_item(parsed.container_title)]
        publisher_like = False
    elif parsed_result.ctype == "book":
        source_values = _publisher_source_values(parsed)
        publisher_like = True
    else:
        source_values = [_first_list_item(parsed.container_title or parsed.collection_title)]
        if parsed_result.ctype == "book_chapter" and candidate.record_granularity == "book":
            source_values = [_book_chapter_book_title_for_query(value) for value in source_values]
        publisher_like = False
    source_texts = [value for value in source_values if normalize_text(value)]
    if not source_texts:
        return 0.0
    candidate_options = [candidate.container_title, candidate.publisher]
    if parsed_result.ctype == "book_chapter" and candidate.record_granularity == "book":
        candidate_options.insert(0, candidate.title)
    best_score = 0.0
    for option in candidate_options:
        if not normalize_text(option):
            continue
        for source_text in source_texts:
            score = _container_text_similarity(
                source_text,
                option,
                publisher_like=publisher_like,
            )
            if score > best_score:
                best_score = score
    return best_score


# Relative importance of each metadata field when scoring volume/issue/pages
# support.  Volume is the strongest signal (uniquely identifies a publication
# issue once combined with the container), issue narrows it further, and pages
# are often the first thing that drifts (e.g. ``930-938`` vs the published
# ``930-938.e1``).  The weights are used both for the numeric score component
# and for deciding whether the match label is ``exact``, ``partial``, or
# ``mismatch``.
_METADATA_FIELD_WEIGHTS: dict[str, float] = {
    "volume": 0.55,
    "issue": 0.30,
    "pages": 0.15,
}


def _metadata_field_credits(
    parsed: ParsedReferenceData,
    candidate: LocalDbCandidate,
) -> list[tuple[str, float]]:
    """Return ``(field, credit)`` pairs for every present metadata field.

    ``credit`` is ``1.0`` for an exact normalized match, ``0.8`` for a
    "start-of-string" match (common when one side has picked up a trailing
    suffix or continuation such as ``930-938`` vs ``930-938.e1``), and ``0.0``
    otherwise.  Fields that are not present on both sides are omitted so they
    do not drag the weighted score toward zero.
    """

    checks = [
        ("volume", _first_list_item(parsed.volume), candidate.volume),
        ("issue", _first_list_item(parsed.issue), candidate.issue),
        ("pages", _first_list_item(parsed.pages), candidate.pages),
    ]
    credits: list[tuple[str, float]] = []
    for field_name, left, right in checks:
        if not left or not right:
            continue
        credits.append((field_name, _metadata_field_credit(field_name, left, right)))
    return credits


def _metadata_score(parsed: ParsedReferenceData, candidate: LocalDbCandidate) -> float:
    credits = _metadata_field_credits(parsed, candidate)
    if not credits:
        return 0.0
    weighted_present = 0.0
    weighted_matched = 0.0
    for field_name, credit in credits:
        weight = _METADATA_FIELD_WEIGHTS.get(field_name, 0.0)
        weighted_present += weight
        weighted_matched += weight * credit
    if weighted_present <= 0.0:
        return 0.0
    return weighted_matched / weighted_present


def _metadata_match_type(
    parsed: ParsedReferenceData,
    candidate: LocalDbCandidate,
) -> str:
    credits = _metadata_field_credits(parsed, candidate)
    if not credits:
        return "unknown"
    weighted_present = 0.0
    weighted_matched = 0.0
    any_full_match = False
    any_partial_match = False
    for field_name, credit in credits:
        weight = _METADATA_FIELD_WEIGHTS.get(field_name, 0.0)
        weighted_present += weight
        weighted_matched += weight * credit
        if credit >= 1.0:
            any_full_match = True
        elif credit > 0.0:
            any_partial_match = True
    if weighted_present <= 0.0:
        return "unknown"
    if weighted_matched >= weighted_present:
        return "exact"
    if any_full_match or any_partial_match:
        return "partial"
    return "mismatch"


def _metadata_field_credit(field_name: str, left: str, right: str) -> float:
    """Score one metadata field's agreement on a ``0.0 - 1.0`` scale.

    A normalized exact match is ``1.0``.  A "start-of-string" match (one value
    is a meaningful prefix of the other after normalization, e.g. ``930-938``
    vs ``930-938.e1`` or ``112`` vs ``112A``) is ``0.8``.  Anything else is
    ``0.0``.  Prefix credit is deliberately limited to non-trivial shared
    stems so that, for example, ``1`` vs ``12`` is not treated as a match.
    """

    if _metadata_values_match(field_name, left, right):
        return 1.0
    if _metadata_values_prefix_match(field_name, left, right):
        return 0.8
    return 0.0


def _metadata_values_match(field_name: str, left: str, right: str) -> bool:
    if field_name == "pages":
        left_pages = _normalize_page_range(left)
        right_pages = _normalize_page_range(right)
        if left_pages and right_pages:
            return left_pages == right_pages
    if field_name == "issue":
        left_issue = _normalize_range_token(left)
        right_issue = _normalize_range_token(right)
        if left_issue and right_issue:
            return left_issue == right_issue
    return normalize_text(left) == normalize_text(right)


def _metadata_values_prefix_match(field_name: str, left: str, right: str) -> bool:
    """Return True when the shorter value is a credible prefix of the longer.

    The comparison runs against the same normalized form used by
    :func:`_metadata_values_match`, so it is tolerant of whitespace,
    punctuation, and (for pages) range-expansion differences.  To avoid
    trivial collisions (``1`` being a prefix of ``12``) we require the shorter
    value to contain at least one digit and be at least two characters long
    (or match the full start of the page range for the ``pages`` field).
    """

    if field_name == "pages":
        left_value = _normalize_page_range(left)
        right_value = _normalize_page_range(right)
    elif field_name == "issue":
        left_value = _normalize_range_token(left)
        right_value = _normalize_range_token(right)
    else:
        left_value = normalize_text(left).replace(" ", "")
        right_value = normalize_text(right).replace(" ", "")
    if not left_value or not right_value or left_value == right_value:
        return False
    short, long = (left_value, right_value) if len(left_value) <= len(right_value) else (right_value, left_value)
    if not long.startswith(short):
        return False
    if not any(char.isdigit() for char in short):
        return False
    if field_name == "pages":
        # ``930-938`` is a fair prefix of ``930-938.e1`` (the published
        # electronic continuation), but bare ``930`` should not match the
        # whole range ``930-938``.  Require that a hyphenated range be
        # preserved before we treat one value as a prefix of the other.
        if "-" in long and "-" not in short:
            return False
        return len(short) >= 3
    # volume / issue: require at least two characters so ``1`` does not
    # collide with ``12`` or ``18``.
    return len(short) >= 2


def _normalize_page_range(value: str | None) -> str:
    compact = _normalize_range_token(value, strip_prefix=True)
    if not compact:
        return ""
    match = re.fullmatch(r"(\d+)-(\d+)", compact)
    if not match:
        return compact
    start, end = match.groups()
    if len(end) >= len(start):
        return f"{start}-{end}"
    prefix = start[: len(start) - len(end)]
    expanded_end = f"{prefix}{end}"
    if int(expanded_end) < int(start):
        prefix_value = int(prefix or "0") + 1
        expanded_end = f"{prefix_value}{end}"
    return f"{start}-{expanded_end}"


def _normalize_range_token(value: str | None, *, strip_prefix: bool = False) -> str:
    text = unicodedata.normalize("NFKD", value or "").lower()
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[\u2010-\u2015]", "-", text)
    if strip_prefix:
        text = re.sub(r"\bpp?\.\s*", "", text)
    text = re.sub(r"\s+", "", text)
    return text


def _title_match_strength(title_similarity: float) -> str:
    if title_similarity >= 0.95:
        return "exact_or_near_exact"
    if title_similarity >= 0.6:
        return "strong"
    if title_similarity >= 0.3:
        return "partial"
    if title_similarity > 0:
        return "weak"
    return "none"


def _author_match_strength(author_overlap: float) -> str:
    if author_overlap >= 1.0:
        return "strong"
    if author_overlap >= 0.5:
        return "partial"
    if author_overlap > 0:
        return "weak"
    return "none"


def _year_match_type(student_year: str | None, candidate_year: str | None) -> str:
    if not student_year or not candidate_year:
        return "missing"
    if student_year == candidate_year:
        return "exact"
    try:
        distance = abs(int(student_year) - int(candidate_year))
    except ValueError:
        return "mismatch"
    return "near" if distance == 1 else "mismatch"


def _container_match_state(
    parsed_result: ParsedReferenceResult,
    candidate: LocalDbCandidate,
    container_score: float,
) -> str:
    parsed = parsed_result.parsed_data
    if parsed is None:
        return "unknown"
    if parsed_result.ctype == "journal_article":
        source_values = [_first_list_item(parsed.container_title)]
    elif parsed_result.ctype == "book":
        source_values = _publisher_source_values(parsed)
    else:
        source_values = [_first_list_item(parsed.container_title or parsed.collection_title)]
        if parsed_result.ctype == "book_chapter" and candidate.record_granularity == "book":
            source_values = [_book_chapter_book_title_for_query(value) for value in source_values]
    source_text = " ".join(value for value in source_values if normalize_text(value))
    candidate_text = candidate.container_title or candidate.publisher
    if parsed_result.ctype == "book_chapter" and candidate.record_granularity == "book":
        candidate_text = candidate.title or candidate_text
    candidate_text = normalize_text(candidate_text)
    if not source_text or not candidate_text:
        return "unknown"
    return "yes" if container_score > 0 else "no"


def _fallback_stop_reason(
    candidates: list[LocalDbCandidate],
    *,
    config: Phase4RuntimeConfig,
) -> str | None:
    if not candidates:
        return None
    best = candidates[0]
    if (
        len(candidates) >= config.max_top_candidates
        and candidates[1].ordering_score >= config.second_candidate_min_ordering_score
    ):
        return "phase4_stop_top2_filled"
    if best.match_signals.doi_match_type in {"exact", "equivalent"}:
        return "phase4_stop_doi_candidate_found"
    if best.ordering_score >= 0.75:
        return "phase4_stop_strong_candidate_found"
    if (
        best.match_signals.title_match_strength == "exact_or_near_exact"
        and best.match_signals.year_match_type in {"exact", "near"}
    ):
        return "phase4_stop_title_year_candidate_found"
    return None


def _author_overlap(student_authors: list[str], candidate_authors: list[str]) -> float:
    left = {normalize_text(author) for author in student_authors if normalize_text(author)}
    right = {normalize_text(author) for author in candidate_authors if normalize_text(author)}
    if not left or not right:
        return 0.0
    matched = 0
    unmatched_right = set(right)
    for surname in left:
        match = next(
            (
                candidate
                for candidate in unmatched_right
                if _surname_values_match(surname, candidate)
            ),
            None,
        )
        if match is None:
            continue
        matched += 1
        unmatched_right.remove(match)
    return matched / len(left)


def _surname_values_match(left: str, right: str) -> bool:
    left_key = _surname_match_key(left)
    right_key = _surname_match_key(right)
    return left_key == right_key or _surname_edit_distance_leq_one(left_key, right_key)


def _surname_match_key(value: str) -> str:
    tokens = normalize_text(value).split()
    while len(tokens) > 1 and tokens[0] in _NAME_PARTICLES:
        tokens.pop(0)
    return " ".join(tokens)


def _surname_edit_distance_leq_one(left: str, right: str) -> bool:
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


def _student_primary_author_surname(parsed_result: ParsedReferenceResult) -> str:
    parsed = parsed_result.parsed_data
    surnames = _parsed_author_surnames(parsed)
    if surnames:
        return normalize_text(surnames[0])
    return _raw_leading_surname(parsed_result.raw_reference)


def _candidate_primary_author_surname(candidate: LocalDbCandidate) -> str:
    for author in candidate.authors:
        normalized = normalize_text(author)
        if normalized:
            return normalized
    return ""


def _raw_leading_surname(raw_reference: str | None) -> str:
    text = (raw_reference or "").strip()
    if not text:
        return ""
    match = re.match(r"^\s*([^\W\d_][^\s,.;:]*)", text)
    if not match:
        return ""
    return normalize_text(match.group(1))


def _year_score(student_year: str | None, candidate_year: str | None) -> float:
    if not student_year or not candidate_year:
        return 0.0
    if student_year == candidate_year:
        return 1.0
    try:
        distance = abs(int(student_year) - int(candidate_year))
    except ValueError:
        return 0.0
    return 0.33 if distance == 1 else 0.0


def _token_similarity(left: str | None, right: str | None) -> float:
    left_tokens = set(_meaningful_title_terms(left))
    right_tokens = set(_meaningful_title_terms(right))
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    union = len(left_tokens | right_tokens)
    return overlap / union if union else 0.0


def _container_text_similarity(
    left: str | None,
    right: str | None,
    *,
    publisher_like: bool,
) -> float:
    left_text = normalize_text(left)
    right_text = normalize_text(right)
    if not left_text or not right_text:
        return 0.0
    if left_text == right_text:
        return 1.0
    if publisher_like and _publisher_names_equivalent(left_text, right_text):
        return 1.0
    if publisher_like:
        return _token_similarity(left_text, right_text)
    if journal_abbreviation_match(left_text, right_text):
        return 1.0
    return _title_variant_similarity(left, right)


def _publisher_names_equivalent(left: str, right: str) -> bool:
    left_tokens = set(_meaningful_title_terms(left))
    right_tokens = set(_meaningful_title_terms(right))
    if not left_tokens or not right_tokens:
        return False
    if not (left_tokens <= right_tokens or right_tokens <= left_tokens):
        return False
    left_distinctive = left_tokens - _PUBLISHER_GENERIC_WORDS
    right_distinctive = right_tokens - _PUBLISHER_GENERIC_WORDS
    return bool(left_distinctive and right_distinctive and left_distinctive & right_distinctive)


def _title_similarity(
    parsed_result: ParsedReferenceResult,
    candidate: LocalDbCandidate,
) -> float:
    parsed = parsed_result.parsed_data
    if parsed is None:
        return 0.0
    candidate_title = candidate.title
    if parsed_result.ctype == "book_chapter" and candidate.record_granularity == "book":
        book_title = _first_list_item(parsed.container_title or parsed.collection_title)
        book_title = _book_chapter_book_title_for_query(book_title)
        return _title_variant_similarity(book_title, candidate_title)
    raw_title = _first_list_item(parsed.title)
    return _title_variant_similarity(raw_title, candidate_title)


def _title_variant_similarity(left: str | None, right: str | None) -> float:
    similarities = [
        _token_similarity(left_variant, right_variant)
        for left_variant in _title_similarity_variants(left)
        for right_variant in _title_similarity_variants(right)
    ]
    return max(similarities) if similarities else 0.0


def _title_similarity_variants(value: str | None) -> list[str]:
    variants: list[str] = []
    raw = (value or "").strip()
    if raw:
        variants.append(raw)
    main_title = _main_title_text(raw)
    if main_title and main_title not in variants:
        variants.append(main_title)
    return variants


def _publisher_source_values(parsed: ParsedReferenceData) -> list[str]:
    values: list[str] = []
    publisher = _first_list_item(parsed.publisher)
    if publisher:
        values.append(publisher)
        if ":" in publisher:
            values.append(publisher.split(":", maxsplit=1)[1].strip())
        location_stripped = _strip_leading_location(publisher, parsed.location)
        if location_stripped:
            values.append(location_stripped)
    return _dedupe_preserve_order(values)


def _strip_leading_location(value: str, locations: list[str]) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    for location in locations:
        normalized_location = normalize_text(location)
        if normalized_location and normalized.startswith(normalized_location + " "):
            return normalized[len(normalized_location) :].strip()
    return ""


def _meaningful_title_terms(
    value: str | None,
    *,
    prefer_distinctive: bool = False,
) -> list[str]:
    normalized = normalize_text(value)
    if not normalized:
        return []
    tokens = [
        token
        for token in normalized.split()
        if len(token) >= 3 and token not in _COMMON_TITLE_WORDS
    ]
    tokens = tokens or [token for token in normalized.split() if len(token) >= 3]
    if not prefer_distinctive:
        return tokens
    distinctive = [token for token in tokens if token not in _BROAD_SCHOLARLY_TITLE_WORDS]
    if distinctive:
        return distinctive
    return tokens


def _main_title_terms(
    value: str | None,
    *,
    prefer_distinctive: bool = False,
) -> list[str]:
    main_title = _main_title_text(value)
    if not main_title:
        return []
    return _meaningful_title_terms(
        main_title,
        prefer_distinctive=prefer_distinctive,
    )


def _main_title_text(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    parts = re.split(r"[:;]|(?:\.\s+(?=[A-Z0-9]))", text, maxsplit=1)
    return parts[0].strip()


def _book_chapter_book_title_for_query(value: str | None) -> str | None:
    text = (value or "").strip()
    if not text:
        return value
    stripped = _BOOK_CHAPTER_EDITOR_PREFIX_RE.sub("", text, count=1).strip()
    return stripped or text


def _book_chapter_editor_terms_from_book_title(value: str | None) -> list[str]:
    text = (value or "").strip()
    if not text:
        return []
    match = _BOOK_CHAPTER_EDITOR_NAMES_RE.search(text)
    if not match:
        return []
    name_parts = re.split(r"\s*(?:,|/|;|\band\b|\ben\b|&)\s*", match.group("names"))
    tokens: list[str] = []
    for part in name_parts:
        part_tokens = [
            token
            for token in normalize_text(part).split()
            if len(token) >= 3 and token not in _EDITOR_NAME_NOISE_WORDS
        ]
        if part_tokens:
            tokens.append(part_tokens[-1])
    return _dedupe_preserve_order(tokens)


def _is_broad_query_terms(terms: list[str]) -> bool:
    if not terms:
        return True
    distinctive_count = sum(1 for term in terms if term not in _BROAD_SCHOLARLY_TITLE_WORDS)
    return distinctive_count < 2


def _parsed_author_surnames(parsed: ParsedReferenceData | None) -> list[str]:
    if parsed is None:
        return []
    values: list[str] = []
    for author in parsed.author:
        surname = _surname_from_name(author)
        if surname and surname not in values:
            values.append(surname)
    return values


def _parsed_editor_surnames(parsed: ParsedReferenceData | None) -> list[str]:
    if parsed is None:
        return []
    values: list[str] = []
    for editor in parsed.editor:
        surname = _surname_from_name(editor)
        if surname and surname not in values:
            values.append(surname)
    return values


def _surname_from_name(name: ParsedName) -> str:
    family = _surname_text_from_family(name.family)
    if family:
        return family
    literal = (name.literal or "").strip()
    if not literal:
        return ""
    if "," in literal:
        return _surname_text_from_family(literal.split(",", 1)[0])
    return _surname_text_from_family(literal)


def _surname_text_from_family(value: str | None) -> str:
    normalized = normalize_text(value)
    if not normalized:
        return ""
    tokens = normalized.split()
    while tokens and len(tokens[0]) <= 2:
        tokens.pop(0)
    return " ".join(tokens)


def _normalize_name_term(value: str) -> str:
    text = value.strip()
    if not text:
        return ""
    if "," in text:
        text = text.split(",", 1)[0]
    else:
        text = text.split()[-1]
    return normalize_text(text)


def _first_list_item(values: list[str] | None) -> str | None:
    if not values:
        return None
    for value in values:
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _finalize_result(
    result: Phase4MatchResult,
    *,
    started_at: float,
    doi_ms: float,
    fallback_ms: float,
) -> Phase4MatchResult:
    total_ms = round((time.perf_counter() - started_at) * 1000, 2)
    lookup_trace = replace(
        result.lookup_trace,
        timings_ms={"doi": doi_ms, "fallback": fallback_ms, "total": total_ms},
    )
    return replace(
        result,
        lookup_trace=lookup_trace,
        timings_ms={"doi": doi_ms, "fallback": fallback_ms, "total": total_ms},
    )
