from __future__ import annotations

from types import SimpleNamespace

import pytest

from reference_gen2.extractors.models import ExtractionError
from reference_gen2.security.file_validation import UploadValidationError
from reference_gen2.services import hosted_report_pipeline as pipeline
from reference_gen2.reference_segmentation import ReferenceSegmentationError


def test_hosted_report_pipeline_returns_sanitized_html_and_aggregate_counts(monkeypatch):
    phase1 = SimpleNamespace(bibliography=object(), extraction=object())
    segmented = SimpleNamespace(
        references=["raw secret reference"],
        profile_used="unknown_profile",
    )
    parsed = [SimpleNamespace(reference_id="raw_ref")]
    recovered = [SimpleNamespace(reference_id="recovered_ref")]
    matched = SimpleNamespace(reference_id="recovered_ref")
    evaluated = SimpleNamespace(reference_id="recovered_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda *_args, **_kwargs: (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **_kwargs: report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="private-filename.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="apa7_nl",
    )

    assert result.status == "ok"
    assert result.reference_count == 1
    assert result.final_status_counts == {"accepted": 1}
    assert result.html.startswith("<!doctype html>")
    assert "total" in result.timings_ms


def test_hosted_report_pipeline_rejects_invalid_style_hint():
    with pytest.raises(pipeline.HostedReportPipelineError) as exc_info:
        pipeline.run_hosted_report_pipeline(
            filename="paper.pdf",
            declared_mime="application/pdf",
            content=b"%PDF-1.4",
            db_path="/private/db.sqlite",
            style_hint="../apa",
        )

    assert exc_info.value.phase == "request"
    assert exc_info.value.code == "invalid_style_hint"
    assert exc_info.value.http_status == 400


def test_hosted_report_pipeline_auto_style_infers_vancouver_from_numeric_profile(monkeypatch):
    phase1 = SimpleNamespace(
        bibliography=SimpleNamespace(heading="References"),
        extraction=object(),
    )
    segmented = SimpleNamespace(references=["raw secret reference"], profile_used="numeric_profile")
    parsed = [SimpleNamespace(reference_id="recovered_ref")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="recovered_ref")
    evaluated = SimpleNamespace(reference_id="recovered_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")
    captured: dict[str, str] = {}

    def parse_with_style(references: list[str], *, style_hint: str) -> tuple:
        captured["parse_style_hint"] = style_hint
        assert references == segmented.references
        return parsed, recovered

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        parse_with_style,
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="private-filename.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="unknown",
    )

    assert captured["parse_style_hint"] == "vancouver"
    assert captured["style_hint"] == "vancouver"
    assert result.style_detection.detected_style == "vancouver"
    assert result.style_detection.confidence == "high"
    assert result.reference_count == 1
    assert result.final_status_counts == {"accepted": 1}


def test_hosted_report_pipeline_auto_style_uses_bibliography_heading_for_apa_locale(monkeypatch):
    phase1 = SimpleNamespace(
        bibliography=SimpleNamespace(heading="Literatuurlijst"),
        extraction=object(),
    )
    segmented = SimpleNamespace(
        references=[
            "Smit, J. A. (2020). Voorbeeldtitel. Tijdschrift Naam, 1(2), 3-4.",
            "Jansen, R. B. (2021). Nog een titel. Uitgever.",
            "Bakker, T. (2022). Derde titel. https://doi.org/10.1234/example",
        ],
        profile_used="author_year_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_ref")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_ref")
    evaluated = SimpleNamespace(reference_id="parsed_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")
    captured: dict[str, str] = {}

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="private-filename.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="unknown",
    )

    assert captured["parse_style_hint"] == "apa7_nl"
    assert captured["style_hint"] == "apa7_nl"
    assert result.style_detection.detected_style == "apa7_nl"
    assert result.style_detection.confidence == "high"


def test_hosted_report_pipeline_auto_style_uses_dutch_heading_for_weaker_author_year(monkeypatch):
    phase1 = SimpleNamespace(
        bibliography=SimpleNamespace(heading="Literatuurlijst"),
        extraction=object(),
    )
    segmented = SimpleNamespace(
        references=[
            "Smit, J. 2020. Voorbeeldtitel. Tijdschrift Naam, 1, 3-4.",
            "Losse titelregel zonder duidelijke APA-haakjes.",
            "Jansen, R. 2021. Nog een titel. Uitgever.",
        ],
        profile_used="author_year_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_ref")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_ref")
    evaluated = SimpleNamespace(reference_id="parsed_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")
    captured: dict[str, str] = {}

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="pdftest2.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="unknown",
    )

    assert captured["parse_style_hint"] == "apa7_nl"
    assert captured["style_hint"] == "apa7_nl"
    assert captured["requested_style_hint"] == "unknown"
    assert result.style_detection.detected_style == "apa7_nl"
    assert result.style_detection.confidence == "high"


def test_hosted_report_pipeline_auto_style_uses_literatuur_heading(monkeypatch):
    phase1 = SimpleNamespace(
        bibliography=SimpleNamespace(heading="Literatuur"),
        extraction=object(),
    )
    segmented = SimpleNamespace(
        references=[
            "Smit, J. A. (2020). Voorbeeldtitel. Tijdschrift Naam, 1(2), 3-4.",
            "Jansen, R. B. (2021). Nog een titel. Uitgever.",
            "Bakker, T. (2022). Derde titel. https://doi.org/10.1234/example",
        ],
        profile_used="author_year_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_ref")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_ref")
    evaluated = SimpleNamespace(reference_id="parsed_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")
    captured: dict[str, str] = {}

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="pdftest2.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="unknown",
    )

    assert captured["parse_style_hint"] == "apa7_nl"
    assert captured["style_hint"] == "apa7_nl"
    assert captured["requested_style_hint"] == "unknown"
    assert result.style_detection.detected_style == "apa7_nl"
    assert result.style_detection.confidence == "high"


def test_hosted_report_pipeline_auto_style_uses_dutch_heading_when_profile_unknown(monkeypatch):
    phase1 = SimpleNamespace(
        bibliography=SimpleNamespace(heading="Literatuurlijst"),
        extraction=object(),
    )
    segmented = SimpleNamespace(
        references=[
            "Smit J. Voorbeeldtitel. Tijdschrift Naam.",
            "Jansen R. Nog een titel. Uitgever.",
        ],
        profile_used="unknown_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_ref")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_ref")
    evaluated = SimpleNamespace(reference_id="parsed_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")
    captured: dict[str, str] = {}

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="pdftest2.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="unknown",
    )

    assert captured["parse_style_hint"] == "apa7_nl"
    assert captured["style_hint"] == "apa7_nl"
    assert captured["requested_style_hint"] == "unknown"
    assert result.style_detection.detected_style == "apa7_nl"
    assert result.style_detection.confidence == "high"


def test_hosted_report_pipeline_explicit_style_wins_over_auto_detection(monkeypatch):
    phase1 = SimpleNamespace(
        bibliography=SimpleNamespace(heading="Literatuurlijst"),
        extraction=object(),
    )
    segmented = SimpleNamespace(
        references=[
            "Smith, J. A. (2020). Example title. Journal Name, 1(2), 3-4.",
            "Doe, R. B. (2021). Another title. Publisher.",
            "Nguyen, T. (2022). Third title. https://doi.org/10.1234/example",
        ],
        profile_used="author_year_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_ref")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_ref")
    evaluated = SimpleNamespace(reference_id="parsed_ref", final_status="accepted")
    report = SimpleNamespace(status="ok")
    captured: dict[str, str] = {}

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", lambda *_args: phase1)
    monkeypatch.setattr(
        pipeline,
        "segment_references",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_hosted_report_pipeline(
        filename="private-filename.pdf",
        declared_mime="application/pdf",
        content=b"%PDF-1.4",
        db_path="/private/db.sqlite",
        style_hint="harvard",
    )

    assert captured["parse_style_hint"] == "harvard"
    assert captured["style_hint"] == "harvard"
    assert captured["requested_style_hint"] == "harvard"
    assert result.style_detection.detected_style == "harvard"
    assert result.style_detection.source == "user"


def test_text_report_pipeline_starts_at_phase2_and_omits_phase1(monkeypatch):
    captured = {}
    segmented = SimpleNamespace(
        references=["raw secret reference"],
        profile_used="unknown_profile",
    )
    parsed = [SimpleNamespace(reference_id="raw_ref")]
    recovered = [SimpleNamespace(reference_id="recovered_ref")]
    matched = SimpleNamespace(reference_id="recovered_ref")
    evaluated = SimpleNamespace(reference_id="recovered_ref", final_status="needs_review")
    report = SimpleNamespace(status="ok")

    monkeypatch.setattr(
        pipeline,
        "segment_reference_text",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda *_args, **_kwargs: (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_text_report_pipeline(
        reference_list_text="Raw Reference Title doi:10.1234/private",
        db_path="/private/db.sqlite",
        style_hint="apa7_nl",
        max_chars=1000,
    )

    assert result.status == "ok"
    assert result.reference_count == 1
    assert result.final_status_counts == {"needs_review": 1}
    assert captured["phase1"] is None
    assert captured["source_mode"] == "text"
    assert captured["phase2"] is segmented


def test_text_report_pipeline_auto_style_infers_vancouver_from_numeric_profile(monkeypatch):
    captured = {}
    segmented = SimpleNamespace(
        references=["raw secret reference", "another raw reference"],
        profile_used="numeric_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_1"), SimpleNamespace(reference_id="parsed_2")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_1")
    evaluated = SimpleNamespace(reference_id="parsed_1", final_status="needs_review")
    report = SimpleNamespace(status="ok")

    monkeypatch.setattr(
        pipeline,
        "segment_reference_text",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint) or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_text_report_pipeline(
        reference_list_text="1. Example reference",
        db_path="/private/db.sqlite",
        style_hint="unknown",
        max_chars=1000,
    )

    assert captured["style_hint"] == "vancouver"
    assert captured["requested_style_hint"] == "unknown"
    assert "total" in captured["timings_ms"]
    assert captured["parse_style_hint"] == "vancouver"
    assert result.style_detection.detected_style == "vancouver"
    assert result.style_detection.confidence == "high"
    assert result.reference_count == 2
    assert result.final_status_counts == {"needs_review": 2}


def test_text_report_pipeline_auto_style_uses_high_confidence_apa(monkeypatch):
    captured = {}
    segmented = SimpleNamespace(
        references=[
            "Smith, J. A. (2020). Example title. Journal Name, 1(2), 3-4.",
            "Doe, R. B. (2021). Another title. Publisher.",
            "Nguyen, T. (2022). Third title. https://doi.org/10.1234/example",
        ],
        profile_used="author_year_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_1")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_1")
    evaluated = SimpleNamespace(reference_id="parsed_1", final_status="needs_review")
    report = SimpleNamespace(status="ok")

    monkeypatch.setattr(
        pipeline,
        "segment_reference_text",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_text_report_pipeline(
        reference_list_text="pasted references",
        db_path="/private/db.sqlite",
        style_hint="unknown",
        max_chars=1000,
    )

    assert captured["style_hint"] == "apa7_en"
    assert captured["parse_style_hint"] == "apa7_en"
    assert result.style_detection.detected_style == "apa7_en"
    assert result.style_detection.confidence == "high"


def test_text_report_pipeline_auto_style_falls_back_to_regular_apa(monkeypatch):
    captured = {}
    segmented = SimpleNamespace(
        references=[
            "Smith, J. 2020. Harvard-like example. Publisher.",
            "Loose title-led line without a style cue.",
            "Another ambiguous reference.",
        ],
        profile_used="author_year_profile",
    )
    parsed = [SimpleNamespace(reference_id="parsed_1")]
    recovered: list[SimpleNamespace] = []
    matched = SimpleNamespace(reference_id="parsed_1")
    evaluated = SimpleNamespace(reference_id="parsed_1", final_status="needs_review")
    report = SimpleNamespace(status="ok")

    monkeypatch.setattr(
        pipeline,
        "segment_reference_text",
        lambda *_args, **_kwargs: segmented,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_references_with_recovery",
        lambda _refs, *, style_hint: captured.update(parse_style_hint=style_hint)
        or (parsed, recovered),
    )
    monkeypatch.setattr(
        pipeline,
        "match_reference",
        lambda parsed_result, *, config: matched,
    )
    monkeypatch.setattr(
        pipeline,
        "evaluate_reference",
        lambda parsed_result, phase4_result, *, config: evaluated,
    )
    monkeypatch.setattr(
        pipeline,
        "finalize_cycle_report",
        lambda **kwargs: captured.update(kwargs) or report,
    )
    monkeypatch.setattr(
        pipeline,
        "render_html_report",
        lambda _report: "<!doctype html><title>Reference Report</title>",
    )

    result = pipeline.run_text_report_pipeline(
        reference_list_text="pasted references",
        db_path="/private/db.sqlite",
        style_hint="unknown",
        max_chars=1000,
    )

    assert captured["style_hint"] == "apa7_en"
    assert captured["parse_style_hint"] == "apa7_en"
    assert result.style_detection.detected_style == "apa7_en"
    assert result.style_detection.confidence == "high"


def test_text_report_pipeline_wraps_phase_errors_without_user_content(monkeypatch):
    def reject_text(*_args, **_kwargs):
        raise ReferenceSegmentationError(
            "reference_text_invalid_characters",
            "private pasted text contains unsupported characters",
            http_status=400,
        )

    monkeypatch.setattr(pipeline, "segment_reference_text", reject_text)

    with pytest.raises(pipeline.HostedReportPipelineError) as exc_info:
        pipeline.run_text_report_pipeline(
            reference_list_text="Raw Reference Title doi:10.1234/private",
            db_path="/private/db.sqlite",
        )

    assert exc_info.value.phase == "phase2"
    assert exc_info.value.code == "reference_text_invalid_characters"
    assert exc_info.value.http_status == 400
    assert exc_info.value.message == "The reference text contains unsupported control characters."
    assert "Raw Reference Title" not in exc_info.value.message
    assert "/private/db.sqlite" not in exc_info.value.message


def test_hosted_report_pipeline_wraps_phase_errors_without_user_content(monkeypatch):
    def reject_upload(*_args):
        raise UploadValidationError(
            "invalid_signature",
            "private-filename.pdf contains unsupported content",
            http_status=422,
        )

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", reject_upload)

    with pytest.raises(pipeline.HostedReportPipelineError) as exc_info:
        pipeline.run_hosted_report_pipeline(
            filename="private-filename.pdf",
            declared_mime="application/pdf",
            content=b"not a pdf",
            db_path="/private/db.sqlite",
        )

    assert exc_info.value.phase == "phase1"
    assert exc_info.value.code == "invalid_signature"
    assert exc_info.value.http_status == 422
    assert exc_info.value.message == "The uploaded file does not appear to be a valid PDF or DOCX file."
    assert "private-filename" not in exc_info.value.message
    assert "/private/db.sqlite" not in exc_info.value.message


def test_hosted_report_pipeline_returns_specific_safe_page_limit_message(monkeypatch):
    def reject_upload(*_args):
        raise ExtractionError(
            "page_limit_exceeded",
            "PDF has 230 pages, exceeding the limit of 150.",
            http_status=422,
        )

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", reject_upload)

    with pytest.raises(pipeline.HostedReportPipelineError) as exc_info:
        pipeline.run_hosted_report_pipeline(
            filename="private-filename.pdf",
            declared_mime="application/pdf",
            content=b"%PDF-1.4",
            db_path="/private/db.sqlite",
        )

    assert exc_info.value.phase == "phase1"
    assert exc_info.value.code == "page_limit_exceeded"
    assert exc_info.value.http_status == 422
    assert "too many pages" in exc_info.value.message
    assert "private-filename" not in exc_info.value.message
    assert "/private/db.sqlite" not in exc_info.value.message


def test_hosted_report_pipeline_returns_specific_safe_ocr_message(monkeypatch):
    def reject_upload(*_args):
        raise ExtractionError(
            "no_extractable_text",
            "PDF contains no extractable text.",
            http_status=422,
        )

    monkeypatch.setattr(pipeline, "run_phase1_pipeline", reject_upload)

    with pytest.raises(pipeline.HostedReportPipelineError) as exc_info:
        pipeline.run_hosted_report_pipeline(
            filename="private-scan.pdf",
            declared_mime="application/pdf",
            content=b"%PDF-1.4",
            db_path="/private/db.sqlite",
        )

    assert exc_info.value.code == "no_extractable_text"
    assert "OCR" in exc_info.value.message
    assert "private-scan" not in exc_info.value.message
