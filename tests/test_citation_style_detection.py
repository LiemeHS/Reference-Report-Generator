from __future__ import annotations

from reference_gen2.services.citation_style_detection import detect_citation_style


def test_detect_citation_style_preserves_explicit_user_style():
    result = detect_citation_style(
        requested_style_hint="harvard",
        segmentation_profile="numeric_profile",
        references=[
            "Smith, J. (2020). Example title. Example Journal, 1(2), 3-4.",
        ],
    )

    assert result.detected_style == "harvard"
    assert result.confidence == "high"
    assert result.source == "user"
    assert result.signals == ["explicit_user_style"]


def test_detect_citation_style_keeps_numeric_profile_as_vancouver():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="numeric_profile",
        references=["1. Smith J. Example title. Example Journal. 2020;1:3-4."],
    )

    assert result.detected_style == "vancouver"
    assert result.confidence == "high"
    assert result.source == "segmentation_profile"
    assert "decision=numeric_profile" in result.signals


def test_detect_citation_style_detects_numbered_references_as_vancouver():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="unknown_profile",
        references=[
            "[1] Smith J. Example title. Journal Name. 2020;1:3-4.",
            "2. Doe J. Another title. Other Journal. 2021;4:5-6.",
        ],
    )

    assert result.detected_style == "vancouver"
    assert result.confidence == "high"
    assert "decision=numbered_references" in result.signals


def test_detect_citation_style_detects_strong_apa_author_year_list_as_english_by_default():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        references=[
            "Smith, J. A. (2020). Example title. Journal Name, 1(2), 3-4.",
            "Doe, R. B. (2021). Another title. Publisher.",
            "Nguyen, T. (2022). Third title. https://doi.org/10.1234/example",
            "Loose title-led line without a style cue.",
        ],
    )

    assert result.detected_style == "apa7_en"
    assert result.confidence == "high"
    assert result.source == "reference_list"
    assert "apa_author_year_starts=3/4" in result.signals


def test_detect_citation_style_detects_dutch_apa_from_retrieval_cues():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        references=[
            "Smit, J. A. (2020). Voorbeeldtitel. Tijdschrift Naam, 1(2), 3-4.",
            "Jansen, R. B. (2021). Nog een titel. Uitgever.",
            "Bakker, T. (2022). Webpagina. Geraadpleegd op 1 januari 2024, van https://example.test",
            "Losse titelregel zonder stijlcue.",
        ],
    )

    assert result.detected_style == "apa7_nl"
    assert result.confidence == "high"
    assert "apa_nl_cues=1/4" in result.signals


def test_detect_citation_style_uses_dutch_heading_as_apa_locale_tie_breaker():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        bibliography_heading="Literatuurlijst",
        references=[
            "Smit, J. A. (2020). Voorbeeldtitel. Tijdschrift Naam, 1(2), 3-4.",
            "Jansen, R. B. (2021). Nog een titel. Uitgever.",
            "Bakker, T. (2022). Derde titel. https://doi.org/10.1234/example",
            "Losse titelregel zonder stijlcue.",
        ],
    )

    assert result.detected_style == "apa7_nl"
    assert result.confidence == "high"
    assert "bibliography_heading=literatuurlijst" in result.signals


def test_detect_citation_style_uses_literatuur_heading_as_apa_nl():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        bibliography_heading="Literatuur",
        references=[
            "Smit, J. A. (2020). Voorbeeldtitel. Tijdschrift Naam, 1(2), 3-4.",
            "Jansen, R. B. (2021). Nog een titel. Uitgever.",
            "Bakker, T. (2022). Derde titel. https://doi.org/10.1234/example",
        ],
    )

    assert result.detected_style == "apa7_nl"
    assert result.confidence == "high"
    assert "bibliography_heading=literatuur" in result.signals
    assert "decision=dutch_heading_apa_nl" in result.signals


def test_detect_citation_style_uses_dutch_heading_with_weaker_author_year_cues():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        bibliography_heading="Literatuurlijst",
        references=[
            "Smit, J. 2020. Voorbeeldtitel. Tijdschrift Naam, 1, 3-4.",
            "Losse titelregel zonder duidelijke APA-haakjes.",
            "Jansen, R. 2021. Nog een titel. Uitgever.",
        ],
    )

    assert result.detected_style == "apa7_nl"
    assert result.confidence == "high"
    assert "bibliography_heading=literatuurlijst" in result.signals
    assert "decision=dutch_heading_apa_nl" in result.signals


def test_detect_citation_style_uses_dutch_heading_even_when_profile_is_unknown():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="unknown_profile",
        bibliography_heading="Literatuurlijst",
        references=[
            "Smit J. Voorbeeldtitel. Tijdschrift Naam.",
            "Jansen R. Nog een titel. Uitgever.",
        ],
    )

    assert result.detected_style == "apa7_nl"
    assert result.confidence == "high"
    assert "bibliography_heading=literatuurlijst" in result.signals
    assert "decision=dutch_heading_apa_nl" in result.signals


def test_detect_citation_style_detects_harvard_from_single_quoted_title():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        references=[
            "Smith, J. 2020, 'Harvard article title', Journal Name, 1(2), pp. 3-4.",
            "Doe, R. 2021, 'Another title', Publisher.",
        ],
    )

    assert result.detected_style == "harvard"
    assert result.confidence == "high"
    assert "decision=harvard_single_quoted_title" in result.signals


def test_detect_citation_style_detects_mla_from_double_quoted_title():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="notes_bibliography_profile",
        references=[
            'Smith, John. "MLA Article Title." Journal Name, vol. 1, no. 2, 2020, pp. 3-4.',
            'Doe, Jane. "Another Article." Magazine Name, 2021.',
        ],
    )

    assert result.detected_style == "mla"
    assert result.confidence == "high"
    assert "decision=mla_double_quoted_title" in result.signals


def test_detect_citation_style_detects_chicago_from_book_shape():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="notes_bibliography_profile",
        references=[
            "Smith, John. Example Book Title. Chicago: University Press, 2020.",
            "Doe, Jane. Another Book. New York: Example Press, 2021.",
        ],
    )

    assert result.detected_style == "chicago"
    assert result.confidence == "high"
    assert "decision=chicago_notes_bibliography" in result.signals


def test_detect_citation_style_detects_harvard_from_unparenthesized_author_year():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="author_year_profile",
        references=[
            "Smith, J. 2020. Harvard-like example. Publisher.",
            "Doe, R. 2021. Another Harvard-like example. Journal Name.",
        ],
    )

    assert result.detected_style == "harvard"
    assert result.confidence == "high"
    assert "decision=harvard_unparenthesized_year" in result.signals


def test_detect_citation_style_falls_back_to_regular_apa_when_unclear():
    result = detect_citation_style(
        requested_style_hint="unknown",
        segmentation_profile="unknown_profile",
        references=[
            "Loose title-led line without a style cue.",
            "Another ambiguous reference.",
        ],
    )

    assert result.detected_style == "apa7_en"
    assert result.confidence == "high"
    assert result.source == "reference_list"
    assert "decision=fallback_apa7_en" in result.signals
