from __future__ import annotations

from reference_gen2.finalization import finalize_cycle_report, serialize_sanitized_report
from reference_gen2.reference_evaluation.models import (
    Phase5EvidenceCheck,
    Phase5FieldComparison,
    Phase5MatchEvaluation,
    Phase5ReportSignals,
    Phase5ScoreBreakdown,
)
from reference_gen2.pipeline_models import (
    Phase1DocumentReport,
    Phase1PipelineResult,
    Phase1ReportContext,
    UploadReceipt,
)
from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.extractors.models import DocumentExtraction, ExtractionStats, TextUnit
from reference_gen2.reference_matching.models import (
    LocalDbCandidate,
    Phase4InputSummary,
    Phase4LookupTrace,
    Phase4MatchResult,
)
from reference_gen2.reference_parsing.models import (
    MatchPreparation,
    ParsedName,
    ParsedReferenceData,
    ParsedReferenceResult,
    ReportBasis,
)
from reference_gen2.reference_segmentation.models import SegmentationResult


def _phase1_result() -> Phase1PipelineResult:
    extraction = DocumentExtraction(
        source_kind="docx",
        unit_count=2,
        text_units=[
            TextUnit(unit_index=0, kind="paragraph", label="Heading 1", text="References"),
            TextUnit(unit_index=1, kind="paragraph", label="Normal", text="Alpha, A. (2020). Example."),
        ],
        warnings=["extract_warning"],
        stats=ExtractionStats(
            input_bytes=123,
            units_emitted=2,
            chars_emitted=35,
            pages_seen=0,
            paragraphs_seen=2,
        ),
    )
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=1,
        end_unit_index=1,
        text="Alpha, A. (2020). Example.",
        warnings=["bib_warning"],
    )
    report_context = Phase1ReportContext(
        source_mode="upload",
        document=Phase1DocumentReport(
            original_filename="secret.docx",
            detected_kind="docx",
            file_size_bytes=123,
            extraction_time_ms=12.3,
            heading="References",
            heading_found=True,
            heading_unit_index=0,
            start_unit_index=1,
            end_unit_index=1,
            unit_count=1,
            bibliography_char_count=len(bibliography.text),
            warnings=["extract_warning", "bib_warning"],
        ),
        document_summary="Uploaded secret.docx",
        extraction_warnings=["extract_warning", "bib_warning"],
    )
    return Phase1PipelineResult(
        upload=UploadReceipt(
            original_filename="secret.docx",
            normalized_filename="secret.docx",
            detected_kind="docx",
            declared_mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            size_bytes=123,
        ),
        extraction=extraction,
        bibliography=bibliography,
        report_context=report_context,
    )


def _phase3_result(reference_id: str = "ref_sensitive") -> ParsedReferenceResult:
    return ParsedReferenceResult(
        reference_id=reference_id,
        raw_reference="Alpha, A. (2020). Example.",
        normalized_reference="Alpha, A. (2020). Example.",
        parsed_data=None,
        warnings=["parser_missing_identifier"],
        ctype="journal_article",
        recovery_status="unchanged",
        recovery_trace=["step"],
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={"title": ["Example"], "issued_year": ["2020"]},
            lookup_query_fields={"title": ["Example"], "issued_year": ["2020"]},
        ),
        report_basis=ReportBasis(
            missing_fields_for_match=["author"],
        ),
    )


def test_finalize_cycle_report_uses_opaque_ids_and_display_safe_reference_text():
    report = finalize_cycle_report(
        style_hint="apa7_nl",
        phase1=_phase1_result(),
        phase2=SegmentationResult(
            reference_list_text="Alpha, A. (2020). Example.",
            references=["Alpha, A. (2020). Example."],
            warnings=["seg_warning"],
            style_hint_used="apa7_nl",
            profile_used="default_profile",
        ),
        phase3=[_phase3_result("ref_hash_1")],
        phase3b=[_phase3_result("ref_hash_1")],
        source_mode="upload",
        requested_style_hint="unknown",
        timings_ms={"phase1": 12.345, "total": 98.765, "private-path": 1.0, "raw": "secret"},
    )

    payload = serialize_sanitized_report(report)
    encoded = str(payload)

    assert payload["cycle_id"].startswith("cycle_")
    assert payload["phase1"]["upload_kind"] == "docx"
    assert payload["phase2"]["reference_count"] == 1
    assert payload["style_hint"] == "apa7_nl"
    assert payload["requested_style_hint"] == "unknown"
    assert payload["timings_ms"] == {"phase1": 12.35, "total": 98.77}
    assert payload["phase3"][0]["opaque_reference_id"] == "ref_0001"
    assert payload["phase3"][0]["display_reference"] == "Alpha, A. (2020). Example."
    assert payload["phase3b"][0]["opaque_reference_id"] == "ref_0001"
    assert "secret.docx" not in encoded
    assert "ref_hash_1" not in encoded
    assert "reference_list_text" not in encoded


def test_finalize_cycle_report_includes_safe_parsed_field_summary():
    base = _phase3_result()
    parsed = ParsedReferenceResult(
        **{
            **base.__dict__,
            "parsed_data": ParsedReferenceData(
                type="article-journal",
                author=[ParsedName(family="Chen", given="L.")],
                title=["Clinical result"],
                container_title=["Med J"],
                date=["2022"],
                issued_year="2022",
                volume=["12"],
                issue=["2"],
                pages=["5-9"],
                doi=["10.1234/example"],
                raw_tags={"title": ["Clinical result"], "private": ["adapter payload"]},
            ),
        }
    )

    report = finalize_cycle_report(
        style_hint="vancouver",
        phase3=[parsed],
        phase5=[],
        source_mode="upload",
    )
    payload = serialize_sanitized_report(report)

    fields = payload["phase3"][0]["parsed_fields"]
    assert fields["Parsed type"] == "article-journal"
    assert fields["Authors"] == "Chen, L."
    assert fields["Year"] == "2022"
    assert fields["Title"] == "Clinical result"
    assert fields["Container"] == "Med J"
    assert fields["Volume"] == "12"
    assert fields["Issue"] == "2"
    assert fields["Pages"] == "5-9"
    assert fields["DOI"] == "10.1234/example"
    assert "raw_tags" not in fields
    assert "adapter payload" not in str(payload)


def test_finalize_cycle_report_sanitizes_phase4_content():
    phase4_result = Phase4MatchResult(
        reference_id="ref_sensitive",
        input_summary=Phase4InputSummary(
            reference_id="ref_sensitive",
            ctype="journal_article",
            match_target="crossref",
            normalized_doi="10.1234/example",
            normalized_title="example title",
            normalized_authors=["alpha"],
        ),
        attempted=True,
        strategy_used="doi_exact",
        lookup_trace=Phase4LookupTrace(candidate_count=1),
        candidates=[
            LocalDbCandidate(
                record_id="search_journal:1",
                record_type="search_journal",
                title="Example title",
                authors=["Alpha"],
                raw_adapter_data={"title": "Example title", "doi": "10.1234/example"},
            )
        ],
        best_candidate=LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="Example title",
            authors=["Alpha"],
            raw_adapter_data={"title": "Example title"},
        ),
        status="matched_provisional",
        reasons=["phase4_candidates_found"],
        warnings=[],
        timings_ms={"doi": 1.0, "fallback": 0.0, "total": 1.2},
    )

    report = finalize_cycle_report(
        style_hint="unknown",
        phase3=[_phase3_result("ref_sensitive")],
        phase4=[phase4_result],
        source_mode="upload",
    )
    payload = serialize_sanitized_report(report)
    encoded = str(payload)

    assert payload["phase4"][0]["opaque_reference_id"] == "ref_0001"
    assert payload["phase4"][0]["best_record_id"] == "search_journal:1"
    assert payload["phase4"][0]["best_candidate_display"] == "Alpha. Example title"
    assert "raw_adapter_data" not in encoded


def test_finalize_cycle_report_sanitizes_phase5_content():
    phase5_result = Phase5MatchEvaluation(
        reference_id="ref_sensitive",
        phase4_status="matched_provisional",
        final_status="verified",
        final_confidence="high",
        confidence_score=0.93,
        accepted_candidate=LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="Example title",
            authors=["Alpha"],
            author_initials=["A."],
            doi="10.1234/example",
        ),
        runner_up_candidate=LocalDbCandidate(
            record_id="search_journal:2",
            record_type="search_journal",
            title="Example title alt",
            authors=["Beta"],
            author_initials=["B."],
            doi="10.1234/example2",
        ),
        top_candidate_gap=0.15,
        score_breakdown=Phase5ScoreBreakdown(
            title_score=1.0,
            author_score=1.0,
            year_score=1.0,
            container_score=1.0,
            doi_score=1.0,
            metadata_score=1.0,
            raw_score=1.0,
            ambiguity_penalty=0.05,
            structure_penalty=0.0,
            type_penalty=0.0,
            confidence_score=0.95,
        ),
        report_signals=Phase5ReportSignals(
            strengths=["DOI matched exactly"],
            concerns=[],
            review_flags=["AMBIGUOUS_TOP_CANDIDATES"],
            evidence_checks=[
                Phase5EvidenceCheck(
                    code="DOI_EXACT_MATCH",
                    label="DOI matched candidate",
                    status="pass",
                    summary="DOI matched exactly",
                )
            ],
            field_comparisons=[
                Phase5FieldComparison(
                    field_name="title",
                    label="Title",
                    source_value="Example title",
                    found_value="Example title",
                    score=1.0,
                    status="match",
                )
            ],
            final_evidence_summary=["Strengths: DOI matched exactly"],
            top_candidate_gap=0.15,
        ),
        reasons=["phase5_verified_high_confidence"],
        warnings=[],
    )

    report = finalize_cycle_report(
        style_hint="unknown",
        phase3=[_phase3_result("ref_sensitive")],
        phase4=[],
        phase5=[phase5_result],
        source_mode="upload",
    )
    payload = serialize_sanitized_report(report)
    encoded = str(payload)

    assert payload["phase5"][0]["opaque_reference_id"] == "ref_0001"
    assert payload["phase5"][0]["accepted_record_id"] == "search_journal:1"
    assert payload["phase5"][0]["runner_up_record_id"] == "search_journal:2"
    assert payload["phase5"][0]["accepted_match_display"] == "Alpha, A. Example title. DOI: 10.1234/example"
    assert payload["phase5"][0]["accepted_match_render"]["style"] == "apa-standard"
    assert payload["phase5"][0]["accepted_match_render"]["locale"] == "nl-NL"
    assert "Alpha, A." in payload["phase5"][0]["accepted_match_render"]["text"]
    assert "Example title" in payload["phase5"][0]["accepted_match_render"]["text"]
    assert payload["phase5"][0]["runner_up_match_display"] == "Beta, B. Example title alt. DOI: 10.1234/example2"
    assert "Example title alt" in payload["phase5"][0]["runner_up_match_render"]["text"]
    assert payload["phase5"][0]["review_flags"] == ["AMBIGUOUS_TOP_CANDIDATES"]
    assert payload["phase5"][0]["evidence_checks"][0]["code"] == "DOI_EXACT_MATCH"
    assert payload["phase5"][0]["evidence_checks"][0]["label"] == "DOI matched candidate"
    assert payload["phase5"][0]["field_comparisons"][0]["field_name"] == "title"
    assert payload["phase5"][0]["field_comparisons"][0]["status"] == "match"


def test_finalize_cycle_report_displays_phase5_doi_override_candidate_as_accepted():
    phase5_result = Phase5MatchEvaluation(
        reference_id="ref_sensitive",
        phase4_status="matched_provisional",
        final_status="needs_review",
        final_confidence="medium",
        confidence_score=0.72,
        accepted_candidate=LocalDbCandidate(
            record_id="search_journal:2",
            record_type="search_journal",
            title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
            authors=["Boers", "Afzali", "Conrod"],
            author_initials=["E.", "M. H.", "P."],
            issued_year="2020",
        ),
        runner_up_candidate=LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="Verbal communication is preferred",
            authors=["Woodworth", "Farooq", "Gorelick"],
            author_initials=["M.", "U.", "A."],
            issued_year="2019",
            doi="10.2196/16104",
        ),
        top_candidate_gap=0.20,
        score_breakdown=Phase5ScoreBreakdown(
            title_score=0.82,
            author_score=0.65,
            year_score=1.0,
            container_score=1.0,
            doi_score=0.10,
            metadata_score=0.60,
            raw_score=0.72,
            ambiguity_penalty=0.0,
            structure_penalty=0.0,
            type_penalty=0.0,
            confidence_score=0.72,
        ),
        report_signals=Phase5ReportSignals(
            strengths=["Title matched strongly", "Journal or publisher matched"],
            concerns=[],
            review_flags=[],
            evidence_checks=[
                Phase5EvidenceCheck(
                    code="DOI_NOT_CONFIRMED",
                    status="warning",
                    summary="DOI did not confirm the candidate.",
                )
            ],
            final_evidence_summary=["Final status: needs_review"],
            top_candidate_gap=0.20,
        ),
        reasons=["phase5_doi_conflict_candidate_override"],
        warnings=[],
    )

    report = finalize_cycle_report(
        style_hint="unknown",
        phase3=[_phase3_result("ref_sensitive")],
        phase5=[phase5_result],
        source_mode="upload",
    )
    payload = serialize_sanitized_report(report)

    assert payload["phase5"][0]["accepted_record_id"] == "search_journal:2"
    assert payload["phase5"][0]["runner_up_record_id"] == "search_journal:1"
    assert payload["phase5"][0]["accepted_match_display"].startswith(
        "Boers, E., Afzali, M. H., Conrod, P. (2020). Temporal Associations"
    )
    assert "Verbal communication is preferred" not in payload["phase5"][0]["accepted_match_display"]
    assert "Temporal Associations" in payload["phase5"][0]["accepted_match_render"]["text"]


def test_finalize_cycle_report_formats_candidate_authors_without_submitted_initials():
    parsed = _phase3_result("ref_sensitive")
    parsed = ParsedReferenceResult(
        **{
            **parsed.__dict__,
            "parsed_data": ParsedReferenceData(
                author=[
                    ParsedName(family="Boers", given="U.en E."),
                    ParsedName(family="Afzali", given="M. H."),
                    ParsedName(family="Conrod", given="P."),
                ]
            ),
        }
    )
    phase5_result = Phase5MatchEvaluation(
        reference_id="ref_sensitive",
        phase4_status="matched_provisional",
        final_status="suspicious",
        final_confidence="none",
        confidence_score=0.27,
        accepted_candidate=LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="Verbal communication is preferred",
            authors=["woodworth", "farooq", "gorelick", "woodworth farooq gorelick"],
            author_initials=["M.", "U.", "A."],
            issued_year="2019",
            doi="10.2196/16104",
        ),
        runner_up_candidate=LocalDbCandidate(
            record_id="search_journal:2",
            record_type="search_journal",
            title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
            authors=["boers", "afzali", "conrod", "boers afzali conrod"],
            author_initials=["E.", "M. H.", "P."],
            issued_year="2021",
        ),
        top_candidate_gap=0.15,
        score_breakdown=Phase5ScoreBreakdown(
            title_score=0.0,
            author_score=0.0,
            year_score=1.0,
            container_score=0.0,
            doi_score=1.0,
            metadata_score=0.0,
            raw_score=0.0,
            ambiguity_penalty=0.0,
            structure_penalty=0.0,
            type_penalty=0.0,
            confidence_score=0.27,
        ),
        report_signals=Phase5ReportSignals(
            strengths=[],
            concerns=["DOI record appears to describe a different source"],
            review_flags=["DOI_METADATA_CONFLICT"],
            evidence_checks=[],
            final_evidence_summary=[],
            top_candidate_gap=0.15,
        ),
        reasons=[],
        warnings=[],
    )

    report = finalize_cycle_report(
        style_hint="unknown",
        phase3=[parsed],
        phase5=[phase5_result],
        source_mode="upload",
    )
    payload = serialize_sanitized_report(report)

    assert payload["phase5"][0]["accepted_match_display"].startswith(
        "Woodworth, M., Farooq, U., Gorelick, A. (2019)."
    )
    assert "woodworth farooq gorelick" not in payload["phase5"][0]["accepted_match_display"]
    assert payload["phase5"][0]["runner_up_match_display"].startswith(
        "Boers, E., Afzali, M. H., Conrod, P. (2021)."
    )
    assert "boers afzali conrod" not in payload["phase5"][0]["runner_up_match_display"]
    assert "U.en" not in payload["phase5"][0]["runner_up_match_display"]
    assert "U.en" not in payload["phase5"][0]["runner_up_match_render"]["text"]
    assert "Boers, E." in payload["phase5"][0]["runner_up_match_render"]["text"]


def test_finalize_cycle_report_passes_selected_style_to_citation_rendering():
    phase5_result = Phase5MatchEvaluation(
        reference_id="ref_style",
        phase4_status="matched_provisional",
        final_status="verified",
        final_confidence="high",
        confidence_score=0.91,
        accepted_candidate=LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="Clinical result",
            authors=["chen"],
            author_initials=["L."],
            issued_year="2022",
            container_title="Med J",
            volume="12",
            issue="2",
            pages="5-9",
        ),
        runner_up_candidate=None,
        top_candidate_gap=1.0,
        score_breakdown=Phase5ScoreBreakdown(
            title_score=1.0,
            author_score=1.0,
            year_score=1.0,
            container_score=1.0,
            doi_score=0.0,
            metadata_score=1.0,
            raw_score=1.0,
            ambiguity_penalty=0.0,
            structure_penalty=0.0,
            type_penalty=0.0,
            confidence_score=0.91,
        ),
        report_signals=Phase5ReportSignals(
            strengths=[],
            concerns=[],
            review_flags=[],
            top_candidate_gap=1.0,
        ),
        reasons=[],
        warnings=[],
    )

    report = finalize_cycle_report(
        style_hint="vancouver",
        phase3=[_phase3_result("ref_style")],
        phase5=[phase5_result],
        source_mode="upload",
    )
    payload = serialize_sanitized_report(report)

    assert payload["phase5"][0]["accepted_match_render"]["style"] == "vancouver"
    assert payload["phase5"][0]["accepted_match_render"]["locale"] == "en-US"
