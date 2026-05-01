from __future__ import annotations

from typing import Any

from reference_gen2.report_generation.service import render_html_report


def _report_for(*references: dict[str, Any]) -> dict[str, Any]:
    return {
        "cycle_id": "cycle_source_checks",
        "status": "ok",
        "source_mode": "text",
        "style_hint": "apa7_nl",
        "phase3": [
            {
                "opaque_reference_id": item["id"],
                "display_reference": item.get("display_reference") or item["fields"].get("Title", item["id"]),
                "ctype": item["ctype"],
                "parser_backend": "test",
                "match_target": item["ctype"],
                "match_eligible": True,
                "parsed_fields": item["fields"],
            }
            for item in references
        ],
        "phase5": [
            {
                "opaque_reference_id": item["id"],
                "phase4_status": "candidate_only",
                "final_status": item.get("status", "needs_review"),
                "final_confidence": "none",
                "confidence_score": 0.0,
            }
            for item in references
        ],
    }


def _reference(
    ref_id: str,
    ctype: str,
    fields: dict[str, str],
    *,
    status: str = "needs_review",
    display_reference: str | None = None,
) -> dict[str, Any]:
    return {
        "id": ref_id,
        "ctype": ctype,
        "fields": fields,
        "status": status,
        "display_reference": display_reference,
    }


def test_report_source_check_actions_render_book_and_book_chapter_openlibrary_links():
    html = render_html_report(
        _report_for(
            _reference(
                "r1",
                "book",
                {
                    "Title": "Deep learning: Adaptive computation and machine learning",
                    "Authors": "Goodfellow, Ian; Bengio, Yoshua",
                },
            ),
            _reference(
                "r2",
                "book_chapter",
                {
                    "Title": "The chapter title",
                    "Container": "Empirical Poverty Research in Comparative Perspective",
                    "Authors": "Kronauer, Martin",
                },
            ),
        )
    )

    assert "https://openlibrary.org/search?title=Deep+learning&amp;author=Goodfellow" in html
    assert "title=Deep+learning%3A+Adaptive+computation" not in html
    assert (
        "https://openlibrary.org/search?title=Empirical+Poverty+Research+in+Comparative+Perspective&amp;author=Kronauer"
        in html
    )
    assert "Zoek in OpenLibrary" in html


def test_book_chapter_openlibrary_link_uses_book_title_and_editor():
    html = render_html_report(
        _report_for(
            _reference(
                "r1",
                "book_chapter",
                {
                    "Title": "Social work and its search for meaning: Theories, narratives and practices",
                    "Container": "Transforming Social Work Prac-tice: Postmodern Critical Perspectives, St Leonards",
                    "Authors": "Camilleri, P.",
                    "Editors": "Pease, B.; Fook, J.",
                },
            )
        )
    )

    assert (
        "https://openlibrary.org/search?title=Transforming+Social+Work+Practice&amp;author=Pease"
        in html
    )
    assert "author=Camilleri" not in html
    assert "title=Transforming+Social+Work+Prac-tice" not in html
    assert "title=Transforming+Social+Work+Practice%3A+Postmodern" not in html


def test_book_chapter_openlibrary_link_recovers_editor_from_display_reference():
    html = render_html_report(
        _report_for(
            _reference(
                "r1",
                "book_chapter",
                {
                    "Title": "Social work and its search for meaning: Theories, narratives and practices",
                    "Container": "Transforming Social Work Prac-tice: Postmodern Critical Perspectives, St Leonards",
                    "Authors": "Camilleri, P.",
                },
                display_reference=(
                    "Camilleri, P. (1999) 'Social work and its search for meaning', "
                    "in Pease, B. and Fook, J. (eds), Transforming Social Work "
                    "Prac-tice: Postmodern Critical Perspectives."
                ),
            )
        )
    )

    assert (
        "https://openlibrary.org/search?title=Transforming+Social+Work+Practice&amp;author=Pease"
        in html
    )


def test_report_source_check_actions_render_scholar_links_for_articles_and_conferences():
    html = render_html_report(
        _report_for(
            _reference(
                "r1",
                "journal_article",
                {
                    "Title": "A Primer for Evaluating Large Language Models in Social-Science Research",
                    "Authors": "Bail, Christopher",
                },
            ),
            _reference(
                "r2",
                "journal_article",
                {
                    "Title": "Short title",
                    "Authors": "Smith, Jane",
                },
            ),
            _reference(
                "r3",
                "conference_paper",
                {
                    "Title": "Conference methods for source verification",
                    "Authors": "Nguyen, Linh",
                },
            ),
        )
    )

    assert (
        "https://scholar.google.com/scholar?hl=nl&amp;as_sdt=0%2C5&amp;q=A+Primer+for+Evaluating+Large+Language+Models+in+Social-Science+Research&amp;btnG="
        in html
    )
    assert (
        "https://scholar.google.com/scholar?hl=nl&amp;as_sdt=0%2C5&amp;q=Short+title+Smith&amp;btnG="
        in html
    )
    assert (
        "https://scholar.google.com/scholar?hl=nl&amp;as_sdt=0%2C5&amp;q=Conference+methods+for+source+verification&amp;btnG="
        in html
    )
    assert "Zoek in Google Scholar" in html


def test_report_source_check_actions_render_website_guidance_and_safe_links():
    html = render_html_report(
        _report_for(
            _reference(
                "r1",
                "webpage",
                {
                    "Title": "Example web page",
                    "URL": "https://example.org/page",
                },
                status="suspicious",
            )
        )
    )

    assert "Geen overeenkomst gevonden in de database." in html
    assert (
        '<a class="source-check-link" href="https://example.org/page" target="_blank" rel="noopener noreferrer">Website openen</a>'
        in html
    )


def test_report_source_vs_found_is_collapsed_by_default_without_long_help_text():
    report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {
                "Title": "Short title",
                "Authors": "Smith, Jane",
            },
        )
    )
    html = render_html_report(report)

    assert '<details class="field-comparison">' in html
    assert '<details open class="field-comparison">' not in html
    assert "Bron vs gevonden bron" in html
    assert "Dit overzicht vergelijkt wat in de aangeleverde bronvermelding staat" not in html
    assert "Voor extra informatie" not in html


def test_report_source_vs_found_help_text_only_renders_with_best_match():
    report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {
                "Title": "Short title",
                "Authors": "Smith, Jane",
            },
        )
    )
    report["phase5"][0]["accepted_record_id"] = "rec1"
    report["phase5"][0]["accepted_match_display"] = "Smith, J. (2020). Short title."

    html = render_html_report(report)

    assert "comparison-help" not in html
    assert "Voor extra informatie kijk hieronder bij <i>Bron vs gevonden bron</i>" in html
    assert html.index("Voor extra informatie kijk hieronder") < html.index(
        '<details class="field-comparison">'
    )


def test_report_guidance_for_doi_not_found_links_to_doi_org():
    report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {
                "Title": "Short title",
                "Authors": "Smith, Jane",
                "DOI": "10.1234/example.",
            },
        )
    )
    report["phase5"][0]["evidence_checks"] = [
        {
            "code": "EXTRACTED_DOI_NOT_FOUND_IN_DB",
            "label": "Extracted DOI found in database",
            "status": "fail",
            "summary": "No. The extracted DOI was not found in the database.",
        }
    ]

    html = render_html_report(report)

    assert "De DOI uit de bron is niet teruggevonden in de database." in html
    assert "Voor extra informatie kijk hieronder bij <i>Bron vs gevonden bron</i>" in html
    assert (
        '<a class="source-check-link" href="https://doi.org/10.1234/example" target="_blank" rel="noopener noreferrer">Open DOI in doi.org</a>'
        in html
    )
    assert "Zoek in Google Scholar" in html


def test_report_omits_reference_details_and_review_flag_pills():
    report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {
                "Title": "Short title",
                "Authors": "Smith, Jane",
            },
        )
    )
    report["phase3"][0]["missing_fields_for_match"] = ["DOI"]
    report["phase5"][0]["review_flags"] = ["AMBIGUOUS_TOP_CANDIDATES"]
    report["phase5"][0]["reasons"] = ["phase5_reason"]

    html = render_html_report(report)

    assert "Reference details" not in html
    assert "Ontbrekende velden" not in html
    assert "AMBIGUOUS_TOP_CANDIDATES" not in html


def test_report_style_label_shows_auto_only_for_auto_requests():
    auto_report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {"Title": "Short title", "Authors": "Smith, Jane"},
        )
    )
    auto_report["style_hint"] = "apa7_nl"
    auto_report["requested_style_hint"] = "unknown"

    fallback_auto_report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {"Title": "Short title", "Authors": "Smith, Jane"},
        )
    )
    fallback_auto_report["style_hint"] = "apa7_en"
    fallback_auto_report["requested_style_hint"] = "unknown"

    harvard_auto_report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {"Title": "Short title", "Authors": "Smith, Jane"},
        )
    )
    harvard_auto_report["style_hint"] = "harvard"
    harvard_auto_report["requested_style_hint"] = "unknown"

    mla_auto_report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {"Title": "Short title", "Authors": "Smith, Jane"},
        )
    )
    mla_auto_report["style_hint"] = "mla"
    mla_auto_report["requested_style_hint"] = "unknown"

    chicago_auto_report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {"Title": "Short title", "Authors": "Smith, Jane"},
        )
    )
    chicago_auto_report["style_hint"] = "chicago"
    chicago_auto_report["requested_style_hint"] = "unknown"

    explicit_report = _report_for(
        _reference(
            "r1",
            "journal_article",
            {"Title": "Short title", "Authors": "Smith, Jane"},
        )
    )
    explicit_report["style_hint"] = "vancouver"
    explicit_report["requested_style_hint"] = "vancouver"

    auto_html = render_html_report(auto_report)
    fallback_auto_html = render_html_report(fallback_auto_report)
    harvard_auto_html = render_html_report(harvard_auto_report)
    mla_auto_html = render_html_report(mla_auto_report)
    chicago_auto_html = render_html_report(chicago_auto_report)
    explicit_html = render_html_report(explicit_report)

    assert "Stijl: APA 7 Nederlands (auto)" in auto_html
    assert "Stijl: APA 7 English (auto)" in fallback_auto_html
    assert "Stijl: Harvard (auto)" in harvard_auto_html
    assert "Stijl: MLA (auto)" in mla_auto_html
    assert "Stijl: Chicago (auto)" in chicago_auto_html
    assert "Stijl: Vancouver (auto)" not in explicit_html
    assert "Stijl: Vancouver" in explicit_html
