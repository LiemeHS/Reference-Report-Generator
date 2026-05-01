from __future__ import annotations

import logging
import multiprocessing
import os
from pathlib import Path
import shutil
import time

import pytest

from reference_gen2.bibliography.models import BibliographyDetectionError
from reference_gen2.bibliography_detection import detect_bibliography
from reference_gen2.document_intake import document_input_from_paste
from reference_gen2.document_extraction.service import _dispatch, extract_document_text
from reference_gen2.extractors.models import (
    DocumentExtraction,
    ExtractionError,
    ExtractionStats,
    TextUnit,
)
from reference_gen2.extractors.pdf_extractor import extract_pdf_text
from reference_gen2.security.file_validation import StoredUpload, UploadValidationError
from reference_gen2.security.file_validation import validate_upload
from reference_gen2.security.temp_storage import store_temp_upload
from reference_gen2.services.document_pipeline import run_phase1_pipeline
from reference_gen2.reference_parsing import parse_references_with_recovery
from reference_gen2.reference_segmentation import segment_references


def _make_stored_upload(kind: str) -> StoredUpload:
    return StoredUpload(
        original_filename=f"paper.{kind}",
        normalized_filename=f"paper.{kind}",
        detected_kind=kind,  # type: ignore[arg-type]
        declared_mime=None,
        size_bytes=10,
        temp_path=Path(f"paper.{kind}"),
    )


def _real_anystyle_executable() -> str | None:
    configured = os.getenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    if configured and shutil.which(configured):
        return configured
    discovered = shutil.which("anystyle")
    if discovered:
        return discovered
    candidate = os.path.expanduser("~/.local/share/gem/ruby/3.2.0/bin/anystyle")
    if os.path.isfile(candidate):
        return candidate
    return None


def _make_extraction(
    source_kind: str, units: list[tuple[str | None, str]]
) -> DocumentExtraction:
    kind = "page" if source_kind == "pdf" else "paragraph"
    text_units = [
        TextUnit(
            unit_index=index,
            kind=kind,
            label=label,
            text=text,
            layout=("blank" if source_kind == "docx" and not text.strip() else "normal"),
        )
        for index, (label, text) in enumerate(units)
    ]
    return DocumentExtraction(
        source_kind=source_kind,  # type: ignore[arg-type]
        unit_count=len(text_units),
        text_units=text_units,
        warnings=[],
        stats=ExtractionStats(
            input_bytes=0,
            units_emitted=len(text_units),
            chars_emitted=sum(len(unit.text) for unit in text_units),
            pages_seen=len(text_units) if source_kind == "pdf" else 0,
            paragraphs_seen=len(text_units) if source_kind == "docx" else 0,
        ),
    )


def _stuck_pdf_extractor(_upload: StoredUpload) -> DocumentExtraction:
    time.sleep(10)
    return _make_extraction("pdf", [("page-1", "late")])


def _crashing_pdf_extractor(_upload: StoredUpload) -> DocumentExtraction:
    os._exit(23)


def test_dispatch_selects_pdf_extractor():
    extractor = _dispatch(_make_stored_upload("pdf"))
    assert extractor.__name__ == "extract_pdf_text"


def test_dispatch_selects_docx_extractor():
    extractor = _dispatch(_make_stored_upload("docx"))
    assert extractor.__name__ == "extract_docx_text"


def test_extract_document_text_timeout_kills_stuck_extractor(monkeypatch):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("stuck extractor monkeypatch regression uses forked child process")

    monkeypatch.setattr(
        "reference_gen2.document_extraction.service.EXTRACT_TIMEOUT_SEC",
        1,
    )
    monkeypatch.setattr(
        "reference_gen2.document_extraction.service.extract_pdf_text",
        _stuck_pdf_extractor,
    )

    started_at = time.monotonic()
    with pytest.raises(ExtractionError) as exc:
        extract_document_text(_make_stored_upload("pdf"))
    elapsed = time.monotonic() - started_at

    assert exc.value.code == "extraction_timeout"
    assert elapsed < 3.0


def test_extract_document_text_reports_worker_exit_quickly(monkeypatch):
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("worker-exit monkeypatch regression uses forked child process")

    monkeypatch.setattr(
        "reference_gen2.document_extraction.service.EXTRACT_TIMEOUT_SEC",
        5,
    )
    monkeypatch.setattr(
        "reference_gen2.document_extraction.service.extract_pdf_text",
        _crashing_pdf_extractor,
    )

    started_at = time.monotonic()
    with pytest.raises(ExtractionError) as exc:
        extract_document_text(_make_stored_upload("pdf"))
    elapsed = time.monotonic() - started_at

    assert exc.value.code == "extraction_failed"
    assert elapsed < 2.0


def test_extract_pdf_text_orders_two_column_lines_left_then_right(monkeypatch):
    class _FakePdf:
        def __init__(self, pages):
            self.pages = pages

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class _FakePage:
        width = 600.0
        height = 800.0

        def extract_words(self, **_kwargs):
            return [
                {"text": "Right", "x0": 320.0, "x1": 350.0, "top": 100.0, "bottom": 112.0},
                {"text": "one", "x0": 353.0, "x1": 374.0, "top": 100.0, "bottom": 112.0},
                {"text": "Left", "x0": 40.0, "x1": 62.0, "top": 100.0, "bottom": 112.0},
                {"text": "one", "x0": 65.0, "x1": 86.0, "top": 100.0, "bottom": 112.0},
                {"text": "Right", "x0": 320.0, "x1": 350.0, "top": 118.0, "bottom": 130.0},
                {"text": "two", "x0": 353.0, "x1": 374.0, "top": 118.0, "bottom": 130.0},
                {"text": "Left", "x0": 40.0, "x1": 62.0, "top": 118.0, "bottom": 130.0},
                {"text": "two", "x0": 65.0, "x1": 86.0, "top": 118.0, "bottom": 130.0},
                {"text": "Right", "x0": 320.0, "x1": 350.0, "top": 136.0, "bottom": 148.0},
                {"text": "three", "x0": 353.0, "x1": 382.0, "top": 136.0, "bottom": 148.0},
                {"text": "Left", "x0": 40.0, "x1": 62.0, "top": 136.0, "bottom": 148.0},
                {"text": "three", "x0": 65.0, "x1": 94.0, "top": 136.0, "bottom": 148.0},
                {"text": "Right", "x0": 320.0, "x1": 350.0, "top": 154.0, "bottom": 166.0},
                {"text": "four", "x0": 353.0, "x1": 379.0, "top": 154.0, "bottom": 166.0},
                {"text": "Left", "x0": 40.0, "x1": 62.0, "top": 154.0, "bottom": 166.0},
                {"text": "four", "x0": 65.0, "x1": 91.0, "top": 154.0, "bottom": 166.0},
            ]

        def extract_text_lines(self, **_kwargs):
            return []

        def extract_text(self):
            return "Right one\nLeft one"

    monkeypatch.setattr(
        "reference_gen2.extractors.pdf_extractor.pdfplumber.open",
        lambda _path: _FakePdf([_FakePage()]),
    )

    extraction = extract_pdf_text(_make_stored_upload("pdf"))

    assert extraction.text_units[0].text.splitlines() == [
        "Left one",
        "Left two",
        "Left three",
        "Left four",
        "Right one",
        "Right two",
        "Right three",
        "Right four",
    ]
    assert [hint.text for hint in extraction.pdf_layout_hints[:4]] == [
        "Left one",
        "Left two",
        "Left three",
        "Left four",
    ]


def test_document_input_from_paste_is_helper_only():
    document_input = document_input_from_paste("Alpha, A. (2020). Example reference.")

    assert document_input.source_mode == "paste"
    assert "Alpha, A. (2020)." in document_input.reference_list


def test_run_phase1_pipeline_pdf_returns_public_metadata_only(
    monkeypatch, local_tmp_dir, good_pdf_bytes: bytes
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    result = run_phase1_pipeline("paper.pdf", "application/pdf", good_pdf_bytes)

    assert result.upload.detected_kind == "pdf"
    assert not hasattr(result.upload, "temp_path")
    assert result.bibliography.heading == "References"
    assert "Alpha, A. (2020)." in result.bibliography.text
    assert result.extraction.pdf_layout_hints
    assert result.report_context.document.bibliography_char_count == len(
        result.bibliography.text
    )
    assert list(local_tmp_dir.iterdir()) == []


def test_run_phase1_pipeline_docx_returns_public_metadata_only(
    monkeypatch, local_tmp_dir, good_docx_bytes: bytes
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    result = run_phase1_pipeline(
        "paper.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        good_docx_bytes,
    )

    assert result.upload.detected_kind == "docx"
    assert not hasattr(result.upload, "temp_path")
    assert result.bibliography.heading == "References"
    assert "Beta, B. (2021)." in result.bibliography.text
    assert result.extraction.pdf_layout_hints is None
    assert list(local_tmp_dir.iterdir()) == []


def test_run_phase1_pipeline_cleans_up_when_detection_fails(
    monkeypatch, local_tmp_dir, good_docx_bytes: bytes
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 1000000)

    with pytest.raises(BibliographyDetectionError):
        run_phase1_pipeline(
            "paper.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            good_docx_bytes,
        )

    assert list(local_tmp_dir.iterdir()) == []


def test_run_phase1_pipeline_stops_before_extraction_when_scanner_rejects(
    monkeypatch, local_tmp_dir, good_pdf_bytes: bytes
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)

    def _reject(_validated, _content):
        raise UploadValidationError(
            "security_scan_rejected",
            "scanner rejection short-circuits first",
        )

    monkeypatch.setattr("reference_gen2.document_intake.service.run_upload_security_scan", _reject)

    with pytest.raises(UploadValidationError) as exc:
        run_phase1_pipeline("paper.pdf", "application/pdf", good_pdf_bytes)

    assert exc.value.code == "security_scan_rejected"
    assert list(local_tmp_dir.iterdir()) == []


def test_detect_bibliography_result_is_phase1_only(monkeypatch):
    extraction = _make_extraction(
        "docx",
        [
            ("Heading 1", "References"),
            (
                "Normal",
                "Alpha, A. (2020). Example reference with enough detail for testing.",
            ),
            (
                "Normal",
                "Beta, B. (2021). Another reference with enough detail for testing.",
            ),
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert section.heading == "References"
    assert "Alpha, A. (2020)." in section.text


def test_detect_bibliography_accepts_prefixed_docx_heading(monkeypatch):
    extraction = _make_extraction(
        "docx",
        [
            ("Heading 1", "1. References"),
            (
                "Normal",
                "Alpha, A. (2020). Example reference with enough detail for testing.",
            ),
            (
                "Normal",
                "Beta, B. (2021). Another reference with enough detail for testing.",
            ),
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert section.heading == "1. References"
    assert section.start_unit_index == 1


def test_detect_bibliography_pdf_skips_sparse_intro_page(monkeypatch):
    extraction = _make_extraction(
        "pdf",
        [
            ("page-1", "1\nA"),
            (
                "page-2",
                "References\nAlpha, A. (2020). Example reference with enough detail.\n"
                "Beta, B. (2021). Another reference with enough detail.",
            ),
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert section.heading == "References"
    assert section.start_unit_index == 1
    assert "Alpha, A. (2020)." in section.text


def test_detect_bibliography_pdf_trims_heading_page_preface_to_first_reference(monkeypatch):
    extraction = _make_extraction(
        "pdf",
        [
            (
                "page-1",
                "Body text still on the page.\n"
                "Author biographies\n"
                "Jenny Edvardsson is a lecturer in educational sciences.\n"
                "References\n"
                "*=Reference to an article included in the scoping review.\n"
                "Abd-El-Khalick, F., Myers, J. Y., Summers, R., Brunner, J. (2017). "
                "A longitudinal analysis of representations of nature of science.\n"
                "Beach, R., Share, J., & Webb, A. (2017). Teaching climate change to adolescents.",
            )
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert section.heading == "References"
    assert section.text.startswith("Abd-El-Khalick, F., Myers, J. Y.")
    assert "Author biographies" not in section.text
    assert "scoping review" not in section.text.casefold()
    assert "pdf_bibliography_preface_trimmed" in section.warnings
    assert "pdf_bibliography_note_line_stripped" in section.warnings


def test_detect_bibliography_pdf_strips_running_headers_from_bibliography_pages(monkeypatch):
    extraction = _make_extraction(
        "pdf",
        [
            (
                "page-1",
                "References\n"
                "Alpha, A. (2020). Example reference with enough detail.\n"
                "Beta, B. (2021). Another reference with enough detail.",
            ),
            (
                "page-2",
                "Use of Literary Texts in Science Teaching: A Scoping Review\n"
                "Jenny Edvardsson, Lotta Leden & Kristina Juter\n"
                "Gamma, G. (2022). Third reference with enough detail.",
            ),
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert "Use of Literary Texts in Science Teaching: A Scoping Review" not in section.text
    assert "Jenny Edvardsson, Lotta Leden & Kristina Juter" not in section.text
    assert "Gamma, G. (2022)." in section.text
    assert "pdf_bibliography_running_header_stripped" in section.warnings


def test_detect_bibliography_pdf_strips_structured_footer_header_lines(monkeypatch):
    extraction = _make_extraction(
        "pdf",
        [
            (
                "page-1",
                "References\n"
                "1. Alpha, A. Example reference.\n"
                "2. Beta, B. Another reference.",
            ),
            (
                "page-2",
                "518 - Acta Cirúrgica Brasileira - Vol 22 (6) 2007 - 517\n"
                "3. Gamma, G. Third reference with enough detail.",
            ),
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert "Acta Cirúrgica Brasileira - Vol 22 (6) 2007 - 517" not in section.text
    assert "3. Gamma, G. Third reference" in section.text
    assert "pdf_bibliography_footer_header_stripped" in section.warnings


def test_detect_bibliography_pdf_prefers_reference_dense_heading_over_early_false_positive(
    monkeypatch,
):
    extraction = _make_extraction(
        "pdf",
        [
            (
                "page-1",
                "British Journal of Social Work (2007) 37, 73-90\n"
                "Reflexivity, its Meanings and Relevance for Social Work: A Critical Review of the\n"
                "Literature\n"
                "Heather D'Cruz, Philip Gillingham and Sebastian Melendez\n"
                "Summary\n"
                "This article discusses reflexivity in social work.",
            ),
            (
                "page-2",
                "Discussion\n"
                "More article body text continues here without references.",
            ),
            (
                "page-3",
                "Accepted: December 2005\n"
                "References\n"
                "Argyris, C. and Schon, D. (1976) Theory in Practice, San Francisco, Jossey-Bass.\n"
                "Beck, U. (1992) Risk Society: Towards a New Modernity, London, Sage.\n"
                "Biestek, F. P. (1961) The Casework Relationship, London, Allen and Unwin.",
            ),
        ],
    )
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    section = detect_bibliography(extraction)

    assert section.heading == "References"
    assert section.start_unit_index == 2
    assert section.text.startswith("Argyris, C. and Schon, D. (1976)")
    assert "Summary" not in section.text


def test_bibliography_detection_error_exposes_structured_details(monkeypatch):
    extraction = _make_extraction("docx", [("Heading 1", "References")])
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)

    with pytest.raises(BibliographyDetectionError) as exc:
        detect_bibliography(extraction)

    assert exc.value.code == "empty_bibliography_section"
    assert exc.value.details["start_unit_index"] == 1
    assert exc.value.details["heading_unit_index"] == 0


def test_detect_bibliography_real_fixture_pdftest3_trims_preface_and_headers(
    monkeypatch, local_tmp_dir
):
    sample_path = Path("manual_tests/input/pdftest3.pdf")
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest3.pdf")

    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 200000)

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert section.heading == "References"
    assert section.text.startswith("Abd-El-Khalick, F., Myers, J. Y.")
    assert "Author biographies" not in section.text
    assert "Jenny Edvardsson is a PhD student" not in section.text
    assert "Use of Literary Texts in Science Teaching: A Scoping Review" not in section.text
    assert "Jenny Edvardsson, Lotta Leden & Kristina Juter" not in section.text
    assert "*=Reference to an article included in the scoping review." not in section.text
    assert "pdf_bibliography_preface_trimmed" in section.warnings
    assert "pdf_bibliography_running_header_stripped" in section.warnings


def test_detect_bibliography_real_fixture_pdftest4_keeps_vancouver_references_column_ordered(
    monkeypatch, local_tmp_dir
):
    sample_path = Path("manual_tests/input/pdftest4_vancouver.pdf")
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest4_vancouver.pdf")

    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 200000)

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert not section.text.startswith("Revistas brasileiras publicadoras")
    assert section.text.startswith("1. Queluz")
    assert "518 - Acta Cirúrgica Brasileira - Vol 22 (6) 2007" not in section.text


def test_pdftest4_vancouver_explicit_style_reaches_segmentation_and_parsing(
    monkeypatch, local_tmp_dir
):
    sample_path = Path("manual_tests/input/pdftest4_vancouver.pdf")
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest4_vancouver.pdf")
    anystyle = _real_anystyle_executable()
    if not anystyle:
        pytest.skip("AnyStyle executable is not available")

    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 200000)
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_ENABLED", True)
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_EXECUTABLE", anystyle)

    phase1 = run_phase1_pipeline(
        sample_path.name,
        "application/pdf",
        sample_path.read_bytes(),
    )
    segmented = segment_references(
        phase1.bibliography,
        phase1.extraction,
        style_hint="vancouver",
    )
    parsed, recovered = parse_references_with_recovery(
        segmented.references[:3],
        style_hint="vancouver",
    )

    assert segmented.style_hint_used == "vancouver"
    assert segmented.profile_used == "numeric_profile"
    assert len(segmented.references) >= 20
    assert all(result.style_hint_used == "vancouver" for result in parsed)
    assert all(result.parse_profile_used.startswith("vancouver:") for result in parsed)
    assert all(result.style_hint_used == "vancouver" for result in recovered)


def test_detect_bibliography_real_fixture_pdftest8_prefers_references_heading(
    monkeypatch, local_tmp_dir
):
    sample_path = Path("manual_tests/input/pdftest8.pdf")
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest8.pdf")

    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 200000)

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert section.heading == "References"
    assert section.start_unit_index == 14
    assert section.text.startswith("Argyris, C. and Schon, D. (1976)")
    assert "Summary" not in section.text
    assert "The concept of 'reflexivity'" not in section.text


def test_run_phase1_pipeline_logs_metadata_only_on_success(
    monkeypatch, local_tmp_dir, good_pdf_bytes: bytes, caplog
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)
    monkeypatch.setattr("reference_gen2.services.document_pipeline.LOG_ENABLED", True)
    monkeypatch.setattr("reference_gen2.services.document_pipeline.LOG_PIPELINE_EVENTS", True)

    with caplog.at_level(logging.INFO, logger="reference_gen2.services.document_pipeline"):
        run_phase1_pipeline("paper.pdf", "application/pdf", good_pdf_bytes)

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "phase1.pipeline_success" in joined
    assert "size_bytes=" in joined
    assert "Alpha, A. (2020)." not in joined
    assert "https://doi.org/10.1000/test" not in joined


def test_run_phase1_pipeline_logging_can_be_disabled(
    monkeypatch, local_tmp_dir, good_pdf_bytes: bytes, caplog
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)
    monkeypatch.setattr("reference_gen2.services.document_pipeline.LOG_ENABLED", False)

    with caplog.at_level(logging.INFO, logger="reference_gen2.services.document_pipeline"):
        run_phase1_pipeline("paper.pdf", "application/pdf", good_pdf_bytes)

    assert caplog.records == []


def test_run_phase1_pipeline_logs_failure_code_without_content(
    monkeypatch, local_tmp_dir, good_docx_bytes: bytes, caplog
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 1000000)
    monkeypatch.setattr("reference_gen2.services.document_pipeline.LOG_ENABLED", True)
    monkeypatch.setattr("reference_gen2.services.document_pipeline.LOG_PIPELINE_EVENTS", True)

    with caplog.at_level(logging.WARNING, logger="reference_gen2.services.document_pipeline"):
        with pytest.raises(BibliographyDetectionError):
            run_phase1_pipeline(
                "paper.docx",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                good_docx_bytes,
            )

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "phase1.pipeline_detection_failed" in joined
    assert "bibliography_section_too_short" in joined
    assert "Alpha, A. (2020)." not in joined
