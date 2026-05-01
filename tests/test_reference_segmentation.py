from __future__ import annotations

from pathlib import Path

import pytest

from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.bibliography_detection import detect_bibliography
from reference_gen2.extractors.docx_extractor import extract_docx_text
from reference_gen2.extractors.models import (
    DocumentExtraction,
    ExtractionStats,
    PdfLayoutHint,
    TextUnit,
)
from reference_gen2.extractors.pdf_extractor import extract_pdf_text
from reference_gen2.reference_segmentation import (
    ReferenceSegmentationError,
    normalize_reference_list_text,
    prepare_reference_text_input,
    segment_reference_text,
    segment_references,
    split_reference_items,
)
from reference_gen2.security.file_validation import validate_upload
from reference_gen2.security.temp_storage import store_temp_upload


def _make_extraction(
    source_kind: str,
    units: list[tuple[str | None, str]],
    *,
    pdf_layout_hints: list[PdfLayoutHint] | None = None,
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
        pdf_layout_hints=pdf_layout_hints,
        warnings=[],
        stats=ExtractionStats(
            input_bytes=0,
            units_emitted=len(text_units),
            chars_emitted=sum(len(unit.text) for unit in text_units),
            pages_seen=len(text_units) if source_kind == "pdf" else 0,
            paragraphs_seen=len(text_units) if source_kind == "docx" else 0,
        ),
    )


def _fixture_path(*parts: str) -> Path:
    root = Path(__file__).resolve().parents[3]
    candidates = [
        root / "Ref_Parser" / "tests" / "testfile" / Path(*parts),
        root.parent / "Ref_Parser" / "tests" / "testfile" / Path(*parts),
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    pytest.skip(f"Missing fixture: {'/'.join(parts)}")


def _relax_bibliography_limits(monkeypatch):
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 1)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_UNITS", 1)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_PDF_HEADING_SCAN_LINES", 6)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_PDF_HEADING_MIN_LINE_CHARS", 3)


def test_segment_references_splits_numbered_docx_list():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=1,
        end_unit_index=2,
        text=(
            "1. Alpha, A. (2020). Example reference.\n"
            "2. Beta, B. (2021). Another reference."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Heading 1", "References"), ("Normal", "x")])

    result = segment_references(bibliography, extraction)

    assert result.references == [
        "Alpha, A. (2020). Example reference.",
        "Beta, B. (2021). Another reference.",
    ]


def test_segment_references_splits_bulleted_pdf_list():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "• Alpha, A. (2020). Example reference.\n"
            "• Beta, B. (2021). Another reference.\n"
            "• Gamma, G. (2022). Third reference."
        ),
        warnings=[],
    )
    extraction = _make_extraction("pdf", [("page-1", "x")])

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 3
    assert result.references[0].startswith("Alpha, A.")
    assert result.references[2].startswith("Gamma, G.")


def test_segment_references_keeps_pdf_continuation_lines_attached():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=1,
        text=(
            "Alpha, A. (2020). Example article.\n"
            "Journal of Examples, 12(3), 10-20.\n"
            "https://doi.org/10.1000/example\n\n"
            "Beta, B. (2021). Another reference.\n"
            "Publisher."
        ),
        warnings=[],
    )
    extraction = _make_extraction("pdf", [("page-1", "x"), ("page-2", "y")])

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 2
    assert "Journal of Examples, 12(3), 10-20." in result.references[0]
    assert "https://doi.org/10.1000/example" in result.references[0]


def test_segment_references_pdf_splits_finished_article_before_new_org_block():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Duncan, G. (1993). Poverty Dynamics in Eight Countries.\n"
            "Journal of Population Economics 6, 215-234.\n"
            "Eurostat (1998) Recommendations from the Task Force.\n"
            "doc/E2/sep/5/98 Eurostat."
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "pdf",
        [("page-1", "x")],
        pdf_layout_hints=[
            PdfLayoutHint(
                text="Duncan, G. (1993). Poverty Dynamics in Eight Countries.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="Journal of Population Economics 6, 215-234.",
                unit_index=0,
                page_index=0,
                is_new_block=False,
                gap_before="small",
                indentation_change="indented",
            ),
            PdfLayoutHint(
                text="Eurostat (1998) Recommendations from the Task Force.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="outdented",
            ),
            PdfLayoutHint(
                text="doc/E2/sep/5/98 Eurostat.",
                unit_index=0,
                page_index=0,
                is_new_block=False,
                gap_before="small",
                indentation_change="indented",
            ),
        ],
    )

    result = segment_references(bibliography, extraction, style_hint="apa7_nl")

    assert len(result.references) == 2
    assert result.references[0].startswith("Duncan, G. (1993)")
    assert result.references[1].startswith("Eurostat (1998)")


def test_segment_references_pdf_keeps_wrapped_reference_with_small_gap_attached():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Hellendoorn, M. en J. de Bruijn (1999) Overheidsbeleid en Armoederisico’s van Vrouwen.\n"
            "In: G. Engbersen, C. Vrooman en E. Snel (red.) Armoede en Verzorgingsstaat.\n"
            "Amsterdam: aup, 109-127"
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "pdf",
        [("page-1", "x")],
        pdf_layout_hints=[
            PdfLayoutHint(
                text="Hellendoorn, M. en J. de Bruijn (1999) Overheidsbeleid en Armoederisico’s van Vrouwen.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="In: G. Engbersen, C. Vrooman en E. Snel (red.) Armoede en Verzorgingsstaat.",
                unit_index=0,
                page_index=0,
                is_new_block=False,
                gap_before="small",
                indentation_change="indented",
            ),
            PdfLayoutHint(
                text="Amsterdam: aup, 109-127",
                unit_index=0,
                page_index=0,
                is_new_block=False,
                gap_before="small",
                indentation_change="indented",
            ),
        ],
    )

    result = segment_references(bibliography, extraction, style_hint="apa7_nl")

    assert len(result.references) == 1
    assert "In: G. Engbersen" in result.references[0]


def test_segment_references_pdf_keeps_journal_metadata_tail_after_blank_line_attached():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Agyapong-Opoku, N., Agyapong-Opoku, F., & Greenshaw, A. J. (2025). "
            "Effects of social media use on youth and adolescent mental health: A scoping review of reviews.\n\n"
            "Behavioral Sciences, 15(5), 574. https://doi.org/10.3390/bs15050574\n\n"
            "Beck, U. (1992) Risk Society: Towards a New Modernity. Londen: Sage."
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "pdf",
        [("page-1", "x")],
        pdf_layout_hints=[
            PdfLayoutHint(
                text=(
                    "Agyapong-Opoku, N., Agyapong-Opoku, F., & Greenshaw, A. J. (2025). "
                    "Effects of social media use on youth and adolescent mental health: A scoping review of reviews."
                ),
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="Behavioral Sciences, 15(5), 574. https://doi.org/10.3390/bs15050574",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="medium",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="Beck, U. (1992) Risk Society: Towards a New Modernity. Londen: Sage.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="outdented",
            ),
        ],
    )

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 2
    assert "Behavioral Sciences, 15(5), 574." in result.references[0]
    assert "https://doi.org/10.3390/bs15050574" in result.references[0]
    assert result.references[1].startswith("Beck, U. (1992)")
    assert "segmentation_pdf_tail_continuation_attached" in result.warnings


def test_segment_references_pdf_keeps_lowercase_tail_fragment_attached():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Li, Y., Li, X., & Liu, P. L. (2023). Associations of social media use with stress, anxiety,\n"
            "and depression among adolescents: Evidence from a cross-sectional study. "
            "Psychology Research and Behavior Management, 16, Article S447067. "
            "https://doi.org/10.2147/PRBM.S447067\n\n"
            "Liu, P. L., & Li, X. (2023). Problematic social media use and mental health outcomes."
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "pdf",
        [("page-1", "x")],
        pdf_layout_hints=[
            PdfLayoutHint(
                text=(
                    "Li, Y., Li, X., & Liu, P. L. (2023). Associations of social media use with stress, anxiety,"
                ),
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text=(
                    "and depression among adolescents: Evidence from a cross-sectional study. "
                    "Psychology Research and Behavior Management, 16, Article S447067. "
                    "https://doi.org/10.2147/PRBM.S447067"
                ),
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="medium",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="Liu, P. L., & Li, X. (2023). Problematic social media use and mental health outcomes.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="outdented",
            ),
        ],
    )

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 2
    assert result.references[0].startswith("Li, Y., Li, X., & Liu, P. L. (2023).")
    assert "and depression among adolescents" in result.references[0]
    assert "Psychology Research and Behavior Management" in result.references[0]
    assert result.references[1].startswith("Liu, P. L., & Li, X. (2023).")


def test_segment_references_pdf_keeps_doi_only_tail_attached():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Alpha, A. (2020). Example article. Journal of Examples, 12(3), 10-20.\n\n"
            "https://doi.org/10.1000/example\n\n"
            "Beta, B. (2021). Another reference. Publisher."
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "pdf",
        [("page-1", "x")],
        pdf_layout_hints=[
            PdfLayoutHint(
                text="Alpha, A. (2020). Example article. Journal of Examples, 12(3), 10-20.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="https://doi.org/10.1000/example",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="medium",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="Beta, B. (2021). Another reference. Publisher.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="outdented",
            ),
        ],
    )

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 2
    assert result.references[0].endswith("https://doi.org/10.1000/example")
    assert result.references[1].startswith("Beta, B. (2021).")


def test_segment_references_pdf_still_splits_new_author_after_complete_reference():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Alpha, A. (2020). Example article. Journal of Examples, 12(3), 10-20. "
            "https://doi.org/10.1000/example\n\n"
            "Beta, B. (2021). Another article. Other Journal, 9(2), 21-30."
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "pdf",
        [("page-1", "x")],
        pdf_layout_hints=[
            PdfLayoutHint(
                text=(
                    "Alpha, A. (2020). Example article. Journal of Examples, 12(3), 10-20. "
                    "https://doi.org/10.1000/example"
                ),
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="same",
            ),
            PdfLayoutHint(
                text="Beta, B. (2021). Another article. Other Journal, 9(2), 21-30.",
                unit_index=0,
                page_index=0,
                is_new_block=True,
                gap_before="large",
                indentation_change="outdented",
            ),
        ],
    )

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 2
    assert result.references[0].startswith("Alpha, A. (2020).")
    assert result.references[1].startswith("Beta, B. (2021).")


def test_segment_references_splits_no_newline_word_paste():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article "
            "Doe, J. (2021). Another title. Other Journal, 3(1), 5-10. doi:10.5678/other"
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 2
    assert result.references[0].startswith("Smith, J.")
    assert result.references[1].startswith("Doe, J.")


def test_segment_reference_text_splits_pasted_reference_list():
    result = segment_reference_text(
        "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20.\n\n"
        "Doe, J. (2021). Another title. Other Journal, 3(1), 5-10.",
        style_hint="apa7_en",
    )

    assert len(result.references) == 2
    assert result.references[0].startswith("Smith, J.")
    assert result.references[1].startswith("Doe, J.")
    assert result.style_hint_used == "apa7_en"


def test_segment_reference_text_splits_numbered_and_bulleted_paste():
    numbered = segment_reference_text(
        "1. Alpha, A. (2020). Example reference.\n"
        "2. Beta, B. (2021). Another reference."
    )
    bulleted = segment_reference_text(
        "• Alpha, A. (2020). Example reference.\n"
        "• Beta, B. (2021). Another reference.\n"
        "• Gamma, G. (2022). Third reference."
    )

    assert [item[:8] for item in numbered.references] == ["Alpha, A", "Beta, B."]
    assert len(bulleted.references) == 3


def test_prepare_reference_text_input_rejects_empty_and_oversized_text():
    with pytest.raises(ReferenceSegmentationError) as empty_exc:
        prepare_reference_text_input("   \n\t ")
    with pytest.raises(ReferenceSegmentationError) as large_exc:
        prepare_reference_text_input("a" * 11, max_chars=10)

    assert empty_exc.value.code == "empty_reference_text"
    assert empty_exc.value.http_status == 400
    assert large_exc.value.code == "reference_text_too_large"
    assert large_exc.value.http_status == 413


def test_prepare_reference_text_input_rejects_unsafe_control_characters():
    with pytest.raises(ReferenceSegmentationError) as exc_info:
        prepare_reference_text_input("Smith, J. (2020). Title.\x00")

    assert exc_info.value.code == "reference_text_invalid_characters"
    assert exc_info.value.http_status == 400


def test_prepare_reference_text_input_strips_html_as_plain_text():
    prepared = prepare_reference_text_input(
        "<script>alert('x')</script><p>Smith, J. (2020). &amp; Title.</p>"
    )

    assert "<script" not in prepared
    assert "<p>" not in prepared
    assert "&amp;" not in prepared
    assert "Smith, J. (2020). & Title." in prepared


def test_segment_references_keeps_org_author_website_reference_intact():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Movisie (z.d.) racisme.\n"
            "Geraadpleegd op 14 januari 2026, van www.movisie.nl/racisme"
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction)

    assert result.references == [
        "Movisie (z.d.) racisme. Geraadpleegd op 14 januari 2026, van www.movisie.nl/racisme"
    ]


def test_segment_references_docx_blank_paragraph_hard_splits_references():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Alpha, A. (2020). Example reference.\n\n"
            "Beta, B. (2021). Another reference."
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "docx",
        [
            ("Normal", "Alpha, A. (2020). Example reference."),
            ("Normal", ""),
            ("Normal", "Beta, B. (2021). Another reference."),
        ],
    )

    result = segment_references(bibliography, extraction)

    assert result.references == [
        "Alpha, A. (2020). Example reference.",
        "Beta, B. (2021). Another reference.",
    ]


def test_segment_references_docx_keeps_continuation_paragraph_attached():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Movisie. (2025, 26 augustus). Ervaringsdeskundige bestaansonzekerheid.\n"
            "Geraadpleegd op 6 april 2026, van https://www.movisie.nl/artikel/example"
        ),
        warnings=[],
    )
    extraction = _make_extraction(
        "docx",
        [
            ("Normal", "Movisie. (2025, 26 augustus). Ervaringsdeskundige bestaansonzekerheid."),
            (
                "Normal",
                "Geraadpleegd op 6 april 2026, van https://www.movisie.nl/artikel/example",
            ),
        ],
    )

    result = segment_references(bibliography, extraction)

    assert result.references == [
        "Movisie. (2025, 26 augustus). Ervaringsdeskundige bestaansonzekerheid. "
        "Geraadpleegd op 6 april 2026, van https://www.movisie.nl/artikel/example"
    ]


def test_segment_references_does_not_split_ellipsis_author_list():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Han, S. H., Ockerman, K., Furnas, H., Mars, P., Klenke, A., Ching, J., . . .\n"
            "Sorice-Virk, S. (2024). Practice Patterns. Aesthetic Surgery Journal(44)."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction)

    assert len(result.references) == 1
    assert "Sorice-Virk" in result.references[0]


def test_normalize_reference_list_text_strips_narrow_pdf_glue_only():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Use of Literary Texts in Science Teaching: A Scoping Review Beach, R., Share, J., & Webb, A. (2017). "
            "Teaching climate change to adolescents: Reading, writing, and making a difference. Routledge."
        ),
        warnings=[],
    )
    extraction = _make_extraction("pdf", [("page-1", "x")])

    normalized = normalize_reference_list_text(bibliography, extraction)

    assert normalized.startswith("Beach, R., Share, J., & Webb, A. (2017).")
    assert "Use of Literary Texts in Science Teaching" not in normalized


def test_normalize_reference_list_text_splits_broken_pdf_doi_glued_to_author_start():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Karadeniz, E., & Değirmençay, Ş. A. (2020). Example article. Journal of Turkish Science Education, "
            "17(2), 225−241. https://doi.org/10.36681/Klassen, S., & Froese Klassen, C. (2014). "
            "Science teaching with stories: Theoretical and practical perspectives."
        ),
        warnings=[],
    )
    extraction = _make_extraction("pdf", [("page-1", "x")])

    normalized = normalize_reference_list_text(bibliography, extraction)

    assert "https://doi.org/10.36681/\nKlassen, S., & Froese Klassen, C. (2014)." in normalized


def test_segment_references_pdf_splits_general_url_glued_to_next_author_and_keeps_report_tail():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Sjøberg, S., & Schreiner, C. (2019). ROSE (The relevance of science education). "
            "The development, key findings and impacts of an international low cost comparative project.\n"
            "Final Report, Part 1 (of 2). https://www.miun.se/en/Research/researchgroups/roses/publications/"
            "Sjöström, J., & Eilks, I. (2018). Reconsidering different visions of scientific literacy and "
            "science education based on the concept of bildung. In Y. J. Dori, Z. R. Mevarech & D. R. Baker "
            "(Eds.), Cognition, metacognition, and culture in STEM education (pp. 65-88). Springer."
        ),
        warnings=[],
    )
    extraction = _make_extraction("pdf", [("page-1", "x")])

    result = segment_references(bibliography, extraction, style_hint="apa7_nl")

    assert result.references == [
        (
            "Sjøberg, S., & Schreiner, C. (2019). ROSE (The relevance of science education). "
            "The development, key findings and impacts of an international low cost comparative project. "
            "Final Report, Part 1 (of 2). https://www.miun.se/en/Research/researchgroups/roses/publications/"
        ),
        (
            "Sjöström, J., & Eilks, I. (2018). Reconsidering different visions of scientific literacy and "
            "science education based on the concept of bildung. In Y. J. Dori, Z. R. Mevarech & D. R. Baker "
            "(Eds.), Cognition, metacognition, and culture in STEM education (pp. 65-88). Springer."
        ),
    ]


def test_split_reference_items_raises_on_empty_text():
    extraction = _make_extraction("docx", [("Normal", "x")])

    with pytest.raises(ReferenceSegmentationError) as exc:
        split_reference_items("   ", extraction)

    assert exc.value.code == "empty_reference_list_text"


def test_segment_references_exposes_style_hint_and_profile_metadata():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Smith, J. (2020). Some title.\n"
            "Doe, J. (2021). Another title."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction, style_hint="apa7_en")

    assert result.style_hint_used == "apa7_en"
    assert result.profile_used == "author_year_profile"


def test_segment_references_unknown_profile_remains_conservative():
    bibliography = BibliographySection(
        heading="Works Cited",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Example of Reading. Smith, John. Chicago Press, 2020.\n"
            "Another Work in Context. Doe, Jane. MLA House, 2021."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction, style_hint="unknown")

    assert result.profile_used == "unknown_profile"
    assert len(result.references) == 1


def test_segment_references_unknown_auto_infers_numeric_boundary_profile():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "[1] Smith J. Example title. Journal Name. 2020;5(2):10-20.\n"
            "[2] Doe J. Another title. Other Journal. 2021;3(1):5-10."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction, style_hint="unknown")

    assert result.style_hint_used == "unknown"
    assert result.profile_used == "numeric_profile"
    assert len(result.references) == 2


def test_segment_references_supports_notes_bibliography_profile():
    bibliography = BibliographySection(
        heading="Works Cited",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Example of Reading. Smith, John. Chicago Press, 2020.\n"
            "Another Work in Context. Doe, Jane. MLA House, 2021."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction, style_hint="mla")

    assert result.profile_used == "notes_bibliography_profile"
    assert len(result.references) == 2
    assert result.references[0].startswith("Example of Reading.")


def test_segment_references_supports_vancouver_numeric_profile():
    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "[1] Smith J. Example title. Journal Name. 2020;5(2):10-20.\n"
            "[2] Doe J. Another title. Other Journal. 2021;3(1):5-10."
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    result = segment_references(bibliography, extraction, style_hint="vancouver")

    assert result.profile_used == "numeric_profile"
    assert len(result.references) == 2
    assert result.references[0].startswith("Smith J.")


def test_real_fixture_pdftest1_keeps_article_metadata_with_first_reference(
    monkeypatch, local_tmp_dir
):
    _relax_bibliography_limits(monkeypatch)
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    sample_path = _fixture_path("pdftest1.pdf")

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
        result = segment_references(section, extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert result.references[0].startswith(
        "Agyapong-Opoku, N., Agyapong-Opoku, F., & Greenshaw, A. J. (2025)."
    )
    assert "Behavioral Sciences, 15(5), 574." in result.references[0]
    assert "https://doi.org/10.3390/bs15050574" in result.references[0]
    assert any("Psychology Research and Behavior Management, 16, Article S447067." in ref for ref in result.references)
    assert any(
        ref.startswith("Li, Y., Li, X., & Liu, P. L. (2023).")
        and "and depression among adolescents" in ref
        for ref in result.references
    )
    assert any(
        ref.startswith("Rutledge, S. A., Bunn, S., Paul, M., Dennen, V., & Park-Gaghan, T. (2025).")
        and "International Journal of Adolescence and Youth, 30(1), Article 2447464." in ref
        for ref in result.references
    )


def test_real_fixture_pdftest1_does_not_merge_eurostat_and_keles(
    monkeypatch, local_tmp_dir
):
    _relax_bibliography_limits(monkeypatch)
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    sample_path = _fixture_path("pdftest1.pdf")

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
        result = segment_references(section, extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert any(ref.startswith("Eurostat (1998)") for ref in result.references)
    assert any(ref.startswith("Keles, B., McCrae, N., & Grealish, A. (2020).") for ref in result.references)
    assert not any(
        ref.startswith("Eurostat (1998)") and "Keles, B., McCrae, N." in ref
        for ref in result.references
    )


def test_real_fixture_docx_does_not_merge_boers_and_eurostat(
    monkeypatch, local_tmp_dir
):
    _relax_bibliography_limits(monkeypatch)
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    sample_path = _fixture_path("testbestand_APA_WORD.docx")

    validated = validate_upload(
        sample_path.name,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        sample_path.read_bytes(),
    )
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_docx_text(stored)
        section = detect_bibliography(extraction)
        result = segment_references(section, extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert any(ref.startswith("Boers, E., Afzali, M. H.") for ref in result.references)
    assert any(ref.startswith("Eurostat (1998)") for ref in result.references)


def test_real_fixture_docx_does_not_merge_movisie_and_openai(
    monkeypatch, local_tmp_dir
):
    _relax_bibliography_limits(monkeypatch)
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    sample_path = _fixture_path("testbestand_APA_WORD.docx")

    validated = validate_upload(
        sample_path.name,
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        sample_path.read_bytes(),
    )
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_docx_text(stored)
        section = detect_bibliography(extraction)
        result = segment_references(section, extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert any(ref.startswith("Movisie. (2025, 26 augustus).") for ref in result.references)
    assert any(ref.startswith("OpenAI. (2026). ChatGPT") for ref in result.references)


def test_real_fixture_pdftest2_splits_known_pdf_merged_pairs(
    monkeypatch, local_tmp_dir
):
    _relax_bibliography_limits(monkeypatch)
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    sample_path = Path(__file__).resolve().parents[1] / "manual_tests" / "input" / "pdftest2.pdf"
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest2.pdf")

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
        result = segment_references(section, extraction, style_hint="apa7_nl")
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert any(ref.startswith("Duncan, G., B. Gustafsson") for ref in result.references)
    assert any(ref.startswith("Eurostat (1998)") for ref in result.references)
    assert not any(
        ref.startswith("Duncan, G., B. Gustafsson") and "Eurostat (1998)" in ref
        for ref in result.references
    )

    assert any(ref.startswith("Pelleriaux, K. (1998)") for ref in result.references)
    assert any(ref.startswith("Rosanvallon, P. (2000)") for ref in result.references)
    assert any(ref.startswith("Saunders, P. (2002)") for ref in result.references)
    assert any(ref.startswith("scp/cbs (1999)") for ref in result.references)
    assert any(ref.startswith("Snel, E., en S. Karyotis (1998)") for ref in result.references)
    assert any(ref.startswith("Van de Werfhorst, H.G. (2007)") for ref in result.references)


def test_real_fixture_pdftest3_keeps_true_split_article_pairs_attached(
    monkeypatch, local_tmp_dir
):
    _relax_bibliography_limits(monkeypatch)
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    sample_path = Path(__file__).resolve().parents[1] / "manual_tests" / "input" / "pdftest3.pdf"
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest3.pdf")

    validated = validate_upload(sample_path.name, "application/pdf", sample_path.read_bytes())
    stored = store_temp_upload(validated, sample_path.read_bytes())
    try:
        extraction = extract_pdf_text(stored)
        section = detect_bibliography(extraction)
        result = segment_references(section, extraction)
    finally:
        stored.temp_path.unlink(missing_ok=True)

    assert any(
        ref.startswith("Abrori, F. M., Lavicza, Z., & Anđić, B. (2023).")
        and "Science Activities, 61(1), 25–43." in ref
        for ref in result.references
    )
    assert any(
        ref.startswith("de Oliveira Moraes, I., Aires, R. M., & de Souza Góes, A. C. (2021).")
        and "International Journal of Science Education, 43(15), 2501–2515." in ref
        for ref in result.references
    )
