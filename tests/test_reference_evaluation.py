from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from reference_gen2.reference_evaluation import evaluate_reference, evaluate_references
from reference_gen2.reference_evaluation.models import Phase5RuntimeConfig
from reference_gen2.reference_evaluation.policy import has_journal_title_author_tension
from reference_gen2.reference_matching.models import (
    LocalDbCandidate,
    Phase4InputSummary,
    Phase4LookupTrace,
    Phase4MatchResult,
    Phase4MatchSignals,
)
from reference_gen2.reference_parsing.models import (
    MatchPreparation,
    ParsedName,
    ParsedReferenceData,
    ParsedReferenceResult,
)


def _parsed_result(
    *,
    reference_id: str = "ref_1",
    ctype: str = "journal_article",
    has_container: bool = True,
    title: str = "Some title",
    has_doi: bool = True,
) -> ParsedReferenceResult:
    parsed_data = ParsedReferenceData(
        type=ctype,
        author=[ParsedName(family="Smith", given="J.")],
        title=[title],
        container_title=["Journal Name"] if has_container else [],
        collection_title=["Handbook of Examples"] if ctype == "book_chapter" else [],
        publisher=["Example Press"] if ctype == "book" else [],
        issued_year="2020",
        volume=["5"] if ctype == "journal_article" else [],
        issue=["2"] if ctype == "journal_article" else [],
        pages=["10-20"],
        doi=["10.1234/test.article"] if ctype == "journal_article" and has_doi else [],
    )
    return ParsedReferenceResult(
        reference_id=reference_id,
        raw_reference="Smith, J. (2020). Some title.",
        normalized_reference="Smith, J. (2020). Some title.",
        parsed_data=parsed_data,
        ctype=ctype,  # type: ignore[arg-type]
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref" if ctype == "journal_article" else "openlibrary",
            lookup_key_fields={
                "title": [title],
                "issued_year": ["2020"],
                "container_title": ["Journal Name"] if has_container else [],
                "book_title": ["Handbook of Examples"] if ctype == "book_chapter" else [],
            },
            lookup_query_fields={"title": [title], "issued_year": ["2020"]},
        ),
    )


def _phase4_result(
    *,
    reference_id: str = "ref_1",
    status: str = "matched_provisional",
    signals: Phase4MatchSignals | None = None,
    ordering_score: float = 0.92,
    second_signals: Phase4MatchSignals | None = None,
    second_ordering_score: float = 0.20,
    second_title: str = "Some title revised",
    strategy_used: str | None = "journal_title_year_exact",
    source_strategy: str | None = "journal_title_year_exact",
    source_table: str | None = "search_journal",
    record_granularity: str | None = None,
) -> Phase4MatchResult:
    if record_granularity is None:
        record_granularity = {
            "search_journal": "article",
            "search_conference": "article",
            "search_book": "book",
            "search_book_chapter": "chapter",
        }.get(source_table or "", "unknown")
    best_signals = signals or Phase4MatchSignals(
        doi_match_type="exact",
        title_match_strength="exact_or_near_exact",
        author_match_strength="strong",
        year_match_type="exact",
        container_match="yes",
        volume_issue_pages_match="exact",
    )
    best = LocalDbCandidate(
        record_id="search_journal:1",
        record_type="search_journal",
        record_granularity=record_granularity,  # type: ignore[arg-type]
        title="Some title",
        authors=["Smith"],
        issued_year="2020",
        doi="10.1234/test.article",
        container_title="Journal Name",
        source_table=source_table,
        source_strategy=source_strategy,
        match_signals=best_signals,
        ordering_score=ordering_score,
    )
    candidates = [best]
    top_candidates = [best]
    if second_signals is not None:
        second = LocalDbCandidate(
            record_id="search_journal:2",
            record_type="search_journal",
            record_granularity=record_granularity,  # type: ignore[arg-type]
            title=second_title,
            authors=["Smith"],
            issued_year="2020",
            doi="10.1234/test.other",
            container_title="Journal Name",
            source_table=source_table,
            source_strategy=source_strategy,
            match_signals=second_signals,
            ordering_score=second_ordering_score,
        )
        candidates.append(second)
        top_candidates.append(second)
    return Phase4MatchResult(
        reference_id=reference_id,
        input_summary=Phase4InputSummary(
            reference_id=reference_id,
            ctype="journal_article",
            match_target="crossref",
            normalized_title="some title",
            normalized_year="2020",
            normalized_authors=["smith"],
        ),
        attempted=True,
        strategy_used=strategy_used,
        lookup_trace=Phase4LookupTrace(candidate_count=len(candidates)),
        candidates=candidates,
        top_candidates=top_candidates,
        best_candidate=best if status != "no_match" else None,
        status=status,  # type: ignore[arg-type]
        reasons=["phase4_candidates_found"] if status != "no_match" else ["phase4_no_candidates"],
        warnings=[],
    )


def test_evaluate_reference_verified_for_strong_doi_backed_article():
    result = evaluate_reference(_parsed_result(), _phase4_result())

    assert result.final_status == "verified"
    assert result.final_confidence == "high"
    assert result.confidence_score >= 0.82
    assert "EXTRACTED_DOI_MATCHES_CANDIDATE" in [
        check.code for check in result.report_signals.evidence_checks
    ]
    assert "Yes. The extracted DOI matches the selected candidate." in [
        check.summary for check in result.report_signals.evidence_checks
    ]
    assert "DOI_MISMATCH" not in result.report_signals.review_flags
    assert "DOI_METADATA_CONFLICT" not in result.report_signals.review_flags


def test_evaluate_reference_strong_text_match_without_doi_is_not_suspicious():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="strong",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.78,
        ),
    )

    assert result.final_status in {"verified", "needs_review"}
    assert result.final_status != "suspicious"
    assert "EXTRACTED_DOI_NOT_CONFIRMED_AGAINST_CANDIDATE" in [
        check.code for check in result.report_signals.evidence_checks
    ]


def test_missing_source_doi_is_neutral_even_when_candidate_has_doi():
    result = evaluate_reference(
        _parsed_result(has_doi=False),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="strong",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.78,
        ),
    )

    evidence = {check.code: check for check in result.report_signals.evidence_checks}
    assert result.score_breakdown.doi_score == 1.0
    assert result.confidence_score >= 0.82
    assert result.final_status == "verified"
    assert evidence["DOI_NOT_EXTRACTED_FROM_REFERENCE"].status == "not_applicable"
    assert evidence["DOI_NOT_EXTRACTED_FROM_REFERENCE"].label == "DOI extracted from reference"
    assert "did not affect scoring" in evidence["DOI_NOT_EXTRACTED_FROM_REFERENCE"].summary
    assert evidence["EXTRACTED_DOI_LOOKUP_SKIPPED"].status == "not_applicable"
    assert evidence["EXTRACTED_DOI_MATCH_SKIPPED"].status == "not_applicable"
    doi_comparison = [
        comparison
        for comparison in result.report_signals.field_comparisons
        if comparison.field_name == "doi"
    ][0]
    assert doi_comparison.status == "found"
    assert doi_comparison.source_value == ""
    assert doi_comparison.found_value == "10.1234/test.article"


def test_evaluate_reference_low_confidence_candidate_is_suspicious():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            status="candidate_only",
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="partial",
                author_match_strength="weak",
                year_match_type="missing",
                container_match="unknown",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=0.35,
        ),
    )

    assert result.final_status == "suspicious"


def test_evaluate_reference_close_benign_tie_is_needs_review():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="strong",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.78,
            second_signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="strong",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            second_ordering_score=0.75,
        ),
    )

    assert result.final_status == "needs_review"
    assert result.runner_up_candidate is not None
    assert "AMBIGUOUS_TOP_CANDIDATES" in result.report_signals.review_flags


def test_evaluate_reference_close_contradictory_tie_is_suspicious():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="mismatch",
                title_match_strength="strong",
                author_match_strength="none",
                year_match_type="mismatch",
                container_match="no",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.82,
            second_signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="strong",
                author_match_strength="partial",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            second_ordering_score=0.80,
        ),
    )

    assert result.final_status == "suspicious"


def test_partial_title_with_other_support_requires_review():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="partial",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.74,
        ),
    )

    assert result.final_status == "needs_review"


def test_journal_strong_non_exact_title_with_author_mismatch_is_suspicious():
    parsed = replace(
        _parsed_result(ctype="journal_article", has_container=True),
        raw_reference=(
            "Breen, R. en J.H. Goldthorpe (2001) Class, Mobility and Merit: "
            "The Experience of Two British Cohorts. European Sociological Review 17, 81-101."
        ),
        parsed_data=ParsedReferenceData(
            type="article-journal",
            author=[ParsedName(family="Breen", given="R."), ParsedName(family="Goldthorpe", given="J.H.")],
            title=["Class, Mobility and Merit: The Experience of Two British Cohorts"],
            container_title=["European Sociological Review"],
            issued_year="2001",
            volume=["17"],
            pages=["81-101"],
        ),
    )
    phase4 = _phase4_result(
        signals=Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="strong",
            author_match_strength="partial",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="exact",
        ),
        ordering_score=0.84,
    )
    assert phase4.best_candidate is not None
    best = replace(
        phase4.best_candidate,
        title="Class, Mobility and Merit The Experience of Two British Birth Cohorts",
        authors=["Breen"],
        issued_year="2001",
        doi="10.1093/esr/17.2.81",
        container_title="European Sociological Review",
        volume="17",
        issue="2",
        pages="81-101",
    )
    phase4 = replace(phase4, best_candidate=best, candidates=[best], top_candidates=[best])

    result = evaluate_reference(parsed, phase4)

    assert result.final_status == "suspicious"
    assert result.score_breakdown.structure_penalty >= Phase5RuntimeConfig().structure_penalty_major
    assert "TITLE_AUTHOR_TENSION" in result.report_signals.review_flags


def test_journal_exact_title_with_missing_author_support_is_not_title_author_tension():
    result = evaluate_reference(
        _parsed_result(ctype="journal_article", has_container=True, has_doi=False),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="exact",
            ),
            ordering_score=0.90,
        ),
    )

    assert result.final_status == "needs_review"
    assert "TITLE_AUTHOR_TENSION" not in result.report_signals.review_flags


def test_journal_title_author_tension_policy_requires_non_exact_strong_title():
    parsed = _parsed_result(ctype="journal_article")

    assert has_journal_title_author_tension(
        parsed,
        Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="strong",
            author_match_strength="weak",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="exact",
        ),
        {"author": 0.20},
    )
    assert not has_journal_title_author_tension(
        parsed,
        Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="exact_or_near_exact",
            author_match_strength="weak",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="exact",
        ),
        {"author": 0.20},
    )


def test_book_chapter_book_level_recovery_can_be_verified():
    parsed = _parsed_result(ctype="book_chapter")
    assert parsed.parsed_data is not None
    parsed = replace(
        parsed,
        parsed_data=replace(parsed.parsed_data, editor=[ParsedName(family="Smith", given="J.")]),
    )

    result = evaluate_reference(
        parsed,
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=0.90,
            strategy_used="chapter_book_title_year_near",
            source_strategy="chapter_book_title_year_near",
            source_table="search_book",
        ),
    )

    assert result.final_status == "verified"
    assert "BOOK_LEVEL_RECOVERY" not in result.report_signals.review_flags


def test_book_chapter_book_level_recovery_does_not_verify_on_chapter_author_only():
    result = evaluate_reference(
        _parsed_result(ctype="book_chapter"),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=0.90,
            strategy_used="chapter_book_title_year_near",
            source_strategy="chapter_book_title_year_near",
            source_table="search_book",
        ),
    )

    assert result.final_status == "needs_review"


def test_book_chapter_chapter_level_candidate_is_not_book_level_capped():
    result = evaluate_reference(
        _parsed_result(ctype="book_chapter"),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.90,
            strategy_used="chapter_main_title_author_year_near",
            source_strategy="chapter_main_title_author_year_near",
            source_table="search_book_chapter",
            record_granularity="chapter",
        ),
    )

    assert result.final_status == "verified"
    assert "BOOK_LEVEL_RECOVERY" not in result.report_signals.review_flags


def test_book_chapter_book_level_report_compares_chapter_editors_and_book_title():
    parsed = ParsedReferenceResult(
        reference_id="ref_chapter_book_level",
        raw_reference="Roberts, D. A. (2007). Scientific literacy/science literacy. In S. K. Abell & N. G. Lederman (Eds.), Handbook of research on science education (pp. 729-780). Lawrence Erlbaum Associates.",
        normalized_reference="Roberts, D. A. (2007). Scientific literacy/science literacy.",
        parsed_data=ParsedReferenceData(
            type="book_chapter",
            author=[ParsedName(family="Roberts", given="D. A.")],
            editor=[ParsedName(family="Abell", given="S. K."), ParsedName(family="Lederman", given="N. G.")],
            title=["Scientific literacy/science literacy"],
            container_title=["Handbook of research on science education"],
            issued_year="2007",
            pages=["729-780"],
        ),
        ctype="book_chapter",
    )
    candidate = LocalDbCandidate(
        record_id="search_book:10",
        record_type="search_book",
        record_granularity="book",
        title="Handbook of Research on Science Education",
        authors=["Abell", "Lederman"],
        issued_year="2007",
        publisher="Lawrence Erlbaum Associates",
        source_table="search_book",
        source_strategy="chapter_book_title_year_near",
        match_signals=Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="exact_or_near_exact",
            author_match_strength="strong",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="unknown",
        ),
        ordering_score=0.90,
    )
    runner_up = replace(
        candidate,
        record_id="search_book:11",
        publisher="Erlbaum Associates, Incorporated, Lawrence",
        ordering_score=0.88,
    )
    result = evaluate_reference(
        parsed,
        Phase4MatchResult(
            reference_id=parsed.reference_id,
            input_summary=Phase4InputSummary(
                reference_id=parsed.reference_id,
                ctype="book_chapter",
                match_target="openlibrary",
                normalized_title="scientific literacy science literacy",
                normalized_year="2007",
                normalized_authors=["roberts"],
            ),
            attempted=True,
            strategy_used="chapter_book_title_year_near",
            lookup_trace=Phase4LookupTrace(candidate_count=2),
            candidates=[candidate, runner_up],
            top_candidates=[candidate, runner_up],
            best_candidate=candidate,
            status="matched_provisional",
            reasons=["phase4_candidates_found"],
        ),
    )

    comparisons = {comparison.field_name: comparison for comparison in result.report_signals.field_comparisons}
    assert "title" not in comparisons
    assert comparisons["authors"].label == "Editors"
    assert result.final_status == "verified"
    assert "AMBIGUOUS_TOP_CANDIDATES" not in result.report_signals.review_flags
    assert "BOOK_LEVEL_RECOVERY" not in result.report_signals.review_flags
    assert comparisons["authors"].source_value == "Abell; Lederman"
    assert comparisons["authors"].found_value == "Abell; Lederman"
    assert comparisons["authors"].status == "match"
    assert comparisons["container"].label == "Book"
    assert comparisons["container"].found_value == "Handbook of Research on Science Education"
    assert comparisons["container"].status == "match"
    assert "metadata" not in comparisons
    assert "Containing book title matched exactly" in result.report_signals.strengths
    assert "Title matched exactly" not in result.report_signals.strengths
    assert "METADATA_NOT_CONFIRMED" not in [
        check.code for check in result.report_signals.evidence_checks
    ]


def test_book_chapter_book_level_recovery_uses_corrected_editor_evidence():
    parsed = ParsedReferenceResult(
        reference_id="ref_working_poor",
        raw_reference=(
            "Snel, E., J. de Boom en G. Engbersen (2008) The Silent Transformation of the Dutch "
            "Wel-fare State and the Rise of In-Work Poverty. In: H-J. Andress en H. Lohmann "
            "(red.) The working poor in Europe. Cheltenham: Edward Elgar."
        ),
        normalized_reference="Snel, E., J. de Boom en G. Engbersen (2008) The Silent Transformation.",
        parsed_data=ParsedReferenceData(
            type="book_chapter",
            author=[
                ParsedName(family="Snel", given="E."),
                ParsedName(family="de Boom", given="J."),
                ParsedName(family="Engbersen", given="G."),
            ],
            editor=[
                ParsedName(family="Andress", given="H-J."),
                ParsedName(family="Lohmann", given="H."),
            ],
            title=["The Silent Transformation of the Dutch Wel-fare State and the Rise of In-Work Poverty"],
            container_title=["The working poor in Europe"],
            publisher=["Edward Elgar"],
            issued_year="2008",
        ),
        ctype="book_chapter",
    )
    candidate = LocalDbCandidate(
        record_id="search_book:26886783",
        record_type="search_book",
        record_granularity="book",
        title="The working poor in Europe",
        authors=["Andress", "Lohmann"],
        issued_year="2008",
        publisher="Edward Elgar",
        source_table="search_book",
        source_strategy="chapter_book_title_year_near",
        match_signals=Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="exact_or_near_exact",
            author_match_strength="weak",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="unknown",
        ),
        ordering_score=0.81,
    )

    result = evaluate_reference(
        parsed,
        Phase4MatchResult(
            reference_id=parsed.reference_id,
            input_summary=Phase4InputSummary(
                reference_id=parsed.reference_id,
                ctype="book_chapter",
                match_target="openlibrary",
                normalized_title="the silent transformation of the dutch welfare state",
                normalized_year="2008",
                normalized_authors=["snel", "de boom", "engbersen"],
            ),
            attempted=True,
            strategy_used="chapter_book_title_year_near",
            lookup_trace=Phase4LookupTrace(candidate_count=4),
            candidates=[candidate],
            top_candidates=[candidate],
            best_candidate=candidate,
            status="matched_provisional",
            reasons=["phase4_candidates_found"],
        ),
    )

    comparisons = {comparison.field_name: comparison for comparison in result.report_signals.field_comparisons}
    assert result.final_status == "verified"
    assert "BOOK_LEVEL_RECOVERY" not in result.report_signals.review_flags
    assert comparisons["authors"].label == "Editors"
    assert comparisons["authors"].source_value == "Andress; Lohmann"
    assert comparisons["authors"].found_value == "Andress; Lohmann"
    assert comparisons["authors"].status == "match"


def test_doi_mismatch_with_strong_title_triggers_review_flags():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="mismatch",
                title_match_strength="exact_or_near_exact",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            ordering_score=0.65,
        ),
    )

    assert "DOI_MISMATCH" in result.report_signals.review_flags
    assert "STRUCTURAL_CONCERN" in result.report_signals.review_flags
    assert result.final_status in {"needs_review", "suspicious"}


def test_exact_doi_with_conflicting_metadata_is_explained_as_resolved_but_conflicting():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="exact",
                title_match_strength="none",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=1.0,
        ),
    )

    evidence = {check.code: check for check in result.report_signals.evidence_checks}

    assert evidence["EXTRACTED_DOI_FOUND_IN_DB"].status == "pass"
    assert evidence["EXTRACTED_DOI_FOUND_IN_DB"].label == "Extracted DOI found in database"
    assert evidence["EXTRACTED_DOI_MATCHES_CANDIDATE"].status == "pass"
    assert (
        evidence["EXTRACTED_DOI_MATCHES_CANDIDATE"].label
        == "Extracted DOI matches selected candidate"
    )
    assert evidence["DOI_RECORD_METADATA_CONFLICT"].status == "fail"
    assert (
        evidence["DOI_RECORD_METADATA_CONFLICT"].summary
        == "The DOI resolves, but the resolved record does not match the submitted title/authors well."
    )
    assert "TITLE_NO_MATCH" in evidence
    assert "AUTHOR_NO_MATCH" in evidence
    assert "DOI_METADATA_CONFLICT" in result.report_signals.review_flags
    assert "DOI record appears to describe a different source" in result.report_signals.concerns
    assert "Extracted DOI matched the selected candidate" not in result.report_signals.strengths
    assert result.final_status == "suspicious"


def test_doi_conflict_can_be_overridden_by_stronger_text_candidate_with_different_doi():
    phase4 = _phase4_result(
        signals=Phase4MatchSignals(
            doi_match_type="exact",
            title_match_strength="none",
            author_match_strength="none",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="unknown",
        ),
        ordering_score=1.0,
        second_signals=Phase4MatchSignals(
            doi_match_type="mismatch",
            title_match_strength="strong",
            author_match_strength="partial",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="partial",
        ),
        second_ordering_score=0.80,
    )

    result = evaluate_reference(_parsed_result(), phase4)

    assert phase4.best_candidate is not None
    assert phase4.best_candidate.record_id == "search_journal:1"
    assert result.accepted_candidate is not None
    assert result.accepted_candidate.record_id == "search_journal:2"
    assert result.runner_up_candidate is not None
    assert result.runner_up_candidate.record_id == "search_journal:1"
    assert "phase5_doi_conflict_candidate_override" in result.reasons
    assert "DOI_METADATA_CONFLICT" not in result.report_signals.review_flags
    assert result.final_status == "suspicious"


def test_doi_conflict_override_handles_boers_jmir_wrong_doi_shape():
    wrong_doi_candidate = LocalDbCandidate(
        record_id="search_journal:14163269",
        record_type="search_journal",
        title="Verbal communication is preferred to coordinate neurological services in a primary care setting (Preprint)",
        authors=["Woodworth", "Farooq", "Gorelick"],
        issued_year="2019",
        doi="10.2196/16104",
        container_title="JMIR Medical Informatics",
        source_table="search_journal",
        source_strategy="doi_exact",
        match_signals=Phase4MatchSignals(
            doi_match_type="exact",
            title_match_strength="none",
            author_match_strength="none",
            year_match_type="exact",
            container_match="no",
            volume_issue_pages_match="unknown",
        ),
        ordering_score=1.0,
    )
    boers_candidate = LocalDbCandidate(
        record_id="search_journal:16376055",
        record_type="search_journal",
        title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
        authors=["Boers", "Afzali", "Conrod"],
        issued_year="2019",
        doi="10.1177/0706743719885486",
        container_title="The Canadian Journal of Psychiatry",
        source_table="search_journal",
        source_strategy="journal_title_author_year_exact",
        match_signals=Phase4MatchSignals(
            doi_match_type="mismatch",
            title_match_strength="exact_or_near_exact",
            author_match_strength="strong",
            year_match_type="exact",
            container_match="unknown",
            volume_issue_pages_match="partial",
        ),
        ordering_score=0.80,
    )
    phase4 = Phase4MatchResult(
        reference_id="ref_1",
        input_summary=Phase4InputSummary(
            reference_id="ref_1",
            ctype="journal_article",
            match_target="crossref",
            normalized_doi="10.2196/16104",
            normalized_title="temporal associations of screen time and anxiety symptoms among adolescents",
            normalized_year="2019",
            normalized_authors=["boers", "afzali", "newton", "carr", "conrod"],
        ),
        attempted=True,
        strategy_used="doi_exact",
        lookup_trace=Phase4LookupTrace(candidate_count=2),
        candidates=[wrong_doi_candidate, boers_candidate],
        top_candidates=[wrong_doi_candidate, boers_candidate],
        best_candidate=wrong_doi_candidate,
        status="matched_provisional",
        reasons=["phase4_candidates_found", "phase4_doi_hit_suspicious"],
        warnings=[],
    )

    result = evaluate_reference(_parsed_result(title=boers_candidate.title or "Temporal Associations"), phase4)

    assert phase4.best_candidate is wrong_doi_candidate
    assert phase4.top_candidates[0] is wrong_doi_candidate
    assert result.accepted_candidate is not None
    assert result.accepted_candidate.record_id == "search_journal:16376055"
    assert result.runner_up_candidate is not None
    assert result.runner_up_candidate.record_id == "search_journal:14163269"
    assert "phase5_doi_conflict_candidate_override" in result.reasons


def test_doi_conflict_override_requires_strong_alternative_title():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="exact",
                title_match_strength="none",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            second_signals=Phase4MatchSignals(
                doi_match_type="mismatch",
                title_match_strength="weak",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            second_ordering_score=0.80,
        ),
    )

    assert result.accepted_candidate is not None
    assert result.accepted_candidate.record_id == "search_journal:1"
    assert "phase5_doi_conflict_candidate_override" not in result.reasons
    assert "DOI_METADATA_CONFLICT" in result.report_signals.review_flags


def test_phase5_rescores_true_journal_abbreviation_container_match():
    parsed = _parsed_result()
    assert parsed.parsed_data is not None
    parsed = replace(
        parsed,
        parsed_data=replace(parsed.parsed_data, container_title=["Ann Intern Med"]),
    )
    stale_signals = Phase4MatchSignals(
        doi_match_type="none",
        title_match_strength="exact_or_near_exact",
        author_match_strength="strong",
        year_match_type="exact",
        container_match="no",
        volume_issue_pages_match="unknown",
    )
    phase4 = _phase4_result(signals=stale_signals)
    assert phase4.best_candidate is not None
    candidate = replace(
        phase4.best_candidate,
        container_title="Annals of Internal Medicine",
        match_signals=stale_signals,
    )
    phase4 = replace(phase4, best_candidate=candidate, candidates=[candidate], top_candidates=[candidate])

    result = evaluate_reference(parsed, phase4)

    comparisons = {comparison.field_name: comparison for comparison in result.report_signals.field_comparisons}
    assert comparisons["container"].score == 1.0
    assert comparisons["container"].status == "match"
    assert result.accepted_candidate is not None
    assert result.accepted_candidate.match_signals.container_match == "yes"


def test_doi_conflict_override_requires_alternative_author_support():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="exact",
                title_match_strength="none",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            second_signals=Phase4MatchSignals(
                doi_match_type="mismatch",
                title_match_strength="strong",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            second_ordering_score=0.80,
        ),
    )

    assert result.accepted_candidate is not None
    assert result.accepted_candidate.record_id == "search_journal:1"
    assert "phase5_doi_conflict_candidate_override" not in result.reasons
    assert "DOI_METADATA_CONFLICT" in result.report_signals.review_flags


def test_doi_conflict_override_respects_confidence_gap_threshold():
    result = evaluate_reference(
        _parsed_result(),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="exact",
                title_match_strength="none",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            second_signals=Phase4MatchSignals(
                doi_match_type="mismatch",
                title_match_strength="strong",
                author_match_strength="partial",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="partial",
            ),
            second_ordering_score=0.80,
        ),
        config=Phase5RuntimeConfig(doi_conflict_override_min_confidence_gap=0.50),
    )

    assert result.accepted_candidate is not None
    assert result.accepted_candidate.record_id == "search_journal:1"
    assert "phase5_doi_conflict_candidate_override" not in result.reasons
    assert "DOI_METADATA_CONFLICT" in result.report_signals.review_flags


def test_phase5_does_not_import_phase4_lookup_or_phase6_rendering_boundaries():
    package_dir = Path(__file__).resolve().parents[1] / "reference_gen2" / "reference_evaluation"
    source = "\n".join(path.read_text(encoding="utf-8") for path in package_dir.glob("*.py"))

    assert "reference_gen2.reference_matching.provider" not in source
    assert "reference_gen2.reference_matching.service" not in source
    assert "reference_gen2.report_generation" not in source
    assert "reference_gen2.citation_rendering" not in source


def test_inherited_phase4_statuses_map_correctly():
    parsed = _parsed_result()

    skipped = evaluate_reference(parsed, _phase4_result(status="skipped"))
    error = evaluate_reference(parsed, _phase4_result(status="error"))
    no_match = evaluate_reference(parsed, _phase4_result(status="no_match"))

    assert skipped.final_status == "skipped"
    assert error.final_status == "error"
    assert no_match.final_status == "needs_review"
    assert "phase5_needs_review_no_candidate" in no_match.reasons


def test_book_with_exact_title_year_publisher_but_missing_author_is_review_not_structural():
    result = evaluate_reference(
        _parsed_result(ctype="book", has_container=False),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="none",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=0.92,
            source_strategy="book_main_title_author_year_exact",
            source_table="search_book",
        ),
    )

    assert result.final_status == "needs_review"
    assert "AUTHOR_MISMATCH" in result.report_signals.review_flags
    assert "STRUCTURAL_CONCERN" not in result.report_signals.review_flags


def test_book_identity_with_different_publisher_can_be_verified():
    result = evaluate_reference(
        _parsed_result(ctype="book", has_container=False),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="no",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=0.88,
            source_strategy="book_main_title_author_year_exact",
            source_table="search_book",
        ),
    )

    assert result.final_status == "verified"
    assert "CONTAINER_MISMATCH" not in result.report_signals.review_flags
    assert any(check.code == "PUBLISHER_VARIANT" for check in result.report_signals.evidence_checks)


def test_field_comparison_authors_use_surnames_only():
    result = evaluate_reference(
        _parsed_result(ctype="book", has_container=False),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            source_strategy="book_main_title_author_year_exact",
            source_table="search_book",
        ),
    )

    authors = [
        comparison
        for comparison in result.report_signals.field_comparisons
        if comparison.field_name == "authors"
    ][0]
    assert authors.source_value == "Smith"
    assert "J." not in authors.source_value


def test_book_field_comparison_omits_absent_doi_and_metadata():
    parsed = _parsed_result(ctype="book", has_container=False)
    parsed = replace(
        parsed,
        parsed_data=ParsedReferenceData(
            type="book",
            author=[ParsedName(family="Smith", given="J.")],
            title=["Some title"],
            publisher=["Example Press"],
            issued_year="2020",
        ),
    )
    phase4 = _phase4_result(
        signals=Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="exact_or_near_exact",
            author_match_strength="strong",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="unknown",
        ),
        source_strategy="book_main_title_author_year_exact",
        source_table="search_book",
    )
    assert phase4.best_candidate is not None
    best = replace(
        phase4.best_candidate,
        record_type="search_book",
        doi=None,
        container_title=None,
        publisher="Example Press",
        volume=None,
        issue=None,
        pages=None,
    )
    phase4 = replace(phase4, best_candidate=best, candidates=[best], top_candidates=[best])

    result = evaluate_reference(
        parsed,
        phase4,
    )

    field_names = [comparison.field_name for comparison in result.report_signals.field_comparisons]
    assert "doi" not in field_names
    assert "metadata" not in field_names


def test_field_comparison_strips_initials_from_malformed_family():
    parsed = _parsed_result(ctype="book", has_container=False)
    parsed = replace(
        parsed,
        parsed_data=ParsedReferenceData(
            type="book",
            author=[ParsedName(family="Blau", given="P.M."), ParsedName(family="O.D. Duncan")],
            title=["Some title"],
            publisher=["Example Press"],
            issued_year="2020",
        ),
    )

    result = evaluate_reference(
        parsed,
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            source_strategy="book_main_title_author_year_exact",
            source_table="search_book",
        ),
    )

    authors = [
        comparison
        for comparison in result.report_signals.field_comparisons
        if comparison.field_name == "authors"
    ][0]
    assert authors.source_value == "Blau; Duncan"
    assert "O.D." not in authors.source_value


def test_author_field_scores_one_letter_surname_variant_below_exact():
    parsed = replace(
        _parsed_result(ctype="book", has_container=False),
        parsed_data=ParsedReferenceData(
            type="book",
            author=[ParsedName(family="Cantillion", given="B.")],
            title=["De Nieuwe Sociale Kwesties"],
            publisher=["Garant"],
            issued_year="2003",
        ),
    )
    phase4 = _phase4_result(
        signals=Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="exact_or_near_exact",
            author_match_strength="strong",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="unknown",
        ),
        source_strategy="book_main_title_year_exact",
        source_table="search_book",
    )
    assert phase4.best_candidate is not None
    best = replace(
        phase4.best_candidate,
        record_type="search_book",
        title="De nieuwe sociale kwesties",
        authors=["Cantillon"],
        issued_year="2003",
        doi=None,
        container_title=None,
        publisher="Garant",
        volume=None,
        issue=None,
        pages=None,
    )
    phase4 = replace(phase4, best_candidate=best, candidates=[best], top_candidates=[best])

    result = evaluate_reference(parsed, phase4)

    authors = [
        comparison
        for comparison in result.report_signals.field_comparisons
        if comparison.field_name == "authors"
    ][0]
    assert authors.score == 0.9
    assert authors.status == "partial"
    assert result.final_status == "verified"


def test_extra_found_author_penalizes_author_field_and_final_status():
    parsed = replace(
        _parsed_result(ctype="journal_article", has_container=True),
        parsed_data=ParsedReferenceData(
            type="article-journal",
            author=[ParsedName(family="Gallie", given="D."), ParsedName(family="Jacobs", given="S.")],
            title=["Unemployment, Poverty and Social Isolation"],
            container_title=["European Societies"],
                issued_year="2003",
                volume=["5"],
                pages=["1-32"],
                doi=["10.1080/1461669032000057668"],
            ),
        )
    phase4 = _phase4_result(
        signals=Phase4MatchSignals(
            doi_match_type="none",
            title_match_strength="exact_or_near_exact",
            author_match_strength="strong",
            year_match_type="exact",
            container_match="yes",
            volume_issue_pages_match="exact",
        ),
    )
    assert phase4.best_candidate is not None
    best = replace(
        phase4.best_candidate,
        title="UNEMPLOYMENT, POVERTY AND SOCIAL ISOLATION",
        authors=["gallie", "paugam", "jacobs", "gallie paugam jacobs"],
        issued_year="2003",
        doi="10.1080/1461669032000057668",
        container_title="European Societies",
        volume="5",
        issue="1",
        pages="1-32",
    )
    phase4 = replace(phase4, best_candidate=best, candidates=[best], top_candidates=[best])

    result = evaluate_reference(parsed, phase4)

    authors = [
        comparison
        for comparison in result.report_signals.field_comparisons
        if comparison.field_name == "authors"
    ][0]
    assert authors.source_value == "Gallie; Jacobs"
    assert authors.found_value == "Gallie; Paugam; Jacobs"
    assert authors.score == 0.7
    assert authors.status == "partial"
    assert result.final_status == "needs_review"


def test_same_book_publisher_variants_do_not_force_ambiguity_review():
    result = evaluate_reference(
        _parsed_result(ctype="book", has_container=False),
        _phase4_result(
            signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            ordering_score=0.88,
            second_signals=Phase4MatchSignals(
                doi_match_type="none",
                title_match_strength="exact_or_near_exact",
                author_match_strength="strong",
                year_match_type="exact",
                container_match="yes",
                volume_issue_pages_match="unknown",
            ),
            second_ordering_score=0.86,
            second_title="Some title",
            source_strategy="book_main_title_author_year_exact",
            source_table="search_book",
        ),
    )

    assert result.final_status == "verified"
    assert "AMBIGUOUS_TOP_CANDIDATES" not in result.report_signals.review_flags
    assert "phase5_book_publisher_variant_ambiguity_ignored" in result.reasons


def test_evaluate_references_rejects_misaligned_inputs():
    try:
        evaluate_references([_parsed_result()], [])
    except ValueError as exc:
        assert "must have same length" in str(exc)
    else:
        raise AssertionError("Expected ValueError for misaligned inputs")
