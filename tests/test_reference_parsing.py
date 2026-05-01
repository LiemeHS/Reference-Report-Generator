from __future__ import annotations

import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.extractors.models import DocumentExtraction, ExtractionStats, TextUnit
from reference_gen2.reference_parsing import (
    ReferenceParsingError,
    parse_reference,
    parse_references,
    parse_references_with_recovery,
)
from reference_gen2.reference_segmentation import segment_references
from reference_gen2.services.document_pipeline import run_phase1_pipeline


def _configure_anystyle(monkeypatch):
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_ENABLED", True)
    monkeypatch.setattr(
        "reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_EXECUTABLE",
        "anystyle",
    )
    monkeypatch.setattr(
        "reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_PARSE_ARGS",
        [],
    )
    monkeypatch.setattr(
        "reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_TIMEOUT_SEC",
        15,
    )


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


def test_parse_reference_maps_default_anystyle_output(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(command, **kwargs):
        assert command[:5] == ["anystyle", "--stdout", "-f", "json", "parse"]
        assert kwargs["shell"] is False
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Smith","given":"J."}],"date":["2020"],'
                '"title":["Some title"],"container-title":["Journal Name"],'
                '"volume":["5"],"issue":["2"],"pages":["10-20"],'
                '"doi":["10.1234/test.article"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Smith, J. (2020). Some title. Journal Name, 5(2), 10-20.")

    assert result.style_hint_used == "unknown"
    assert result.parser_model_used == "default"
    assert result.parsed_data is not None
    assert result.parsed_data.author[0].family == "Smith"
    assert result.parsed_data.title == ["Some title"]
    assert result.parsed_data.container_title == ["Journal Name"]
    assert result.parsed_data.doi == ["10.1234/test.article"]
    assert result.raw_reference.startswith("Smith, J.")
    assert result.reference_id.startswith("ref_")
    assert result.normalized_reference == "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20."
    assert result.match_preparation is not None
    assert result.report_basis is not None


def test_parse_reference_supports_harvard_like_output(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Doe","given":"J."}],"date":["2021"],'
                '"title":["Another title"],"publisher":["Example Press"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Doe, J. 2021. Another title. Example Press.", style_hint="harvard")

    assert result.style_hint_used == "harvard"
    assert result.parse_profile_used is not None
    assert result.parse_profile_used.startswith("harvard:")
    assert result.repair_profile_used is not None
    assert result.repair_profile_used.startswith("harvard:")
    assert result.parsed_data is not None
    assert result.parsed_data.publisher == ["Example Press"]


def test_parse_reference_supports_vancouver_like_output(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Chen","given":"L."}],"date":["2022"],'
                '"title":["Clinical result"],"container-title":["Med J"],'
                '"volume":["12"],"pages":["5-9"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("1. Chen L. Clinical result. Med J. 2022;12:5-9.", style_hint="vancouver")

    assert result.style_hint_used == "vancouver"
    assert result.normalized_reference.startswith("Chen L.")
    assert result.parse_profile_used is not None
    assert result.parse_profile_used.startswith("vancouver:")
    assert result.parsed_data is not None
    assert result.parsed_data.pages == ["5-9"]


def test_parse_reference_repairs_literal_surname_initials_authors_conservatively(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Kim JS","Franklin C"],"date":["2009"],'
                '"title":["Solution-Focused Brief Therapy in schools: A review of the outcome literature"],'
                '"container-title":["Children and Youth Services Review"],'
                '"volume":["31"],"issue":["4"],"pages":["464-470"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Kim JS and Franklin C (2009) Solution-Focused Brief Therapy in schools: A review of the outcome literature. "
        "Children and Youth Services Review 31(4): 464-470.",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        ("Kim", "J.S.", None),
        ("Franklin", "C.", None),
    ]


def test_parse_reference_repairs_literal_surname_initials_with_compact_and_hyphenated_initials(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Hsu W-S","Wang CDC"],"date":["2011"],'
                '"title":["Integrating Asian clients’ filial piety beliefs into Solution-Focused Brief Therapy"],'
                '"container-title":["International Journal for the Advancement of Counselling"],'
                '"volume":["33"],"issue":["4"],"pages":["322-334"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Hsu W-S and Wang CDC (2011) Integrating Asian clients’ filial piety beliefs into Solution-Focused Brief Therapy. "
        "International Journal for the Advancement of Counselling 33(4): 322-334.",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        ("Hsu", "W.-S.", None),
        ("Wang", "C.D.C.", None),
    ]


def test_parse_reference_repairs_pdftest7_style_literal_authors_without_touching_biography_tail(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Yokotani K","Tamura K"],"date":["2015"],'
                '"title":["Solution-focused group therapy for drug users in Japanese prison: Nonrandomized study",'
                '"Author biographies Luke Ho was a research intern with REACH Community Services Singapore."],'
                '"container-title":["International Journal of Brief Therapy and Family Science"],'
                '"volume":["5"],"issue":["2"],"pages":["42-61"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Yokotani K and Tamura K (2015) Solution-focused group therapy for drug users in Japanese prison: "
        "Nonrandomized study. International Journal of Brief Therapy and Family Science 5(2): 42-61. "
        "Author biographies Luke Ho was a research intern with REACH Community Services Singapore.",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        ("Yokotani", "K.", None),
        ("Tamura", "K.", None),
    ]
    assert any("Author biographies" in title for title in result.parsed_data.title)


def test_parse_reference_does_not_rewrite_ambiguous_or_non_initial_literal_names(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["McDonald Jane","Li","de Boom J","Smith J Q"],"date":["2020"],'
                '"title":["Some title"],"container-title":["Journal Name"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Placeholder reference.", style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        (None, None, "McDonald Jane"),
        (None, None, "Li"),
        (None, None, "de Boom J"),
        (None, None, "Smith J Q"),
    ]


def test_parse_reference_keeps_structured_names_unchanged_when_literal_repair_exists(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Kim","given":"J.S."},"Franklin C"],"date":["2009"],'
                '"title":["Some title"],"container-title":["Journal Name"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Placeholder reference.", style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        ("Kim", "J.S.", None),
        ("Franklin", "C.", None),
    ]


def test_parse_reference_repairs_swapped_structured_surname_initials_names(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"JS","given":"Kim"},{"family":"CDC","given":"Wang"}],'
                '"date":["2011"],"title":["Some title"],"container-title":["Journal Name"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Placeholder reference.", style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        ("Kim", "J.S.", None),
        ("Wang", "C.D.C.", None),
    ]


def test_parse_reference_does_not_rewrite_non_initial_structured_names(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Jane","given":"McDonald"},{"family":"de Boom","given":"J."}],'
                '"date":["2020"],"title":["Some title"],"container-title":["Journal Name"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Placeholder reference.", style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert [(name.family, name.given, name.literal) for name in result.parsed_data.author] == [
        ("Jane", "McDonald", None),
        ("de Boom", "J.", None),
    ]


def test_parse_reference_applies_apa7_nl_retrieval_rules(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Nederlands Jeugdinstituut"],"date":["2021"],'
                '"title":["De alledaagse uitdagingen bij opgroeien en opvoeden"],'
                '"url":["https://edu.nl/ktba3"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    raw = (
        "Nederlands Jeugdinstituut. (2021, 6 januari). De alledaagse uitdagingen bij "
        "opgroeien en opvoeden. Geraadpleegd op 17 januari 2021, van https://edu.nl/ktba3"
    )
    result = parse_reference(raw, style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert result.parsed_data.access is not None
    assert result.parsed_data.access.accessed_date_text == "17 januari 2021"
    assert result.parsed_data.access.source_url == "https://edu.nl/ktba3"
    assert result.parsed_data.organization == ["Nederlands Jeugdinstituut"]
    assert result.pre_classification is not None
    assert result.post_classification is not None
    assert result.ctype == "webpage"
    assert result.classification_trace


def test_parse_reference_preserves_zonder_datum_for_apa7_nl(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Movisie"],"title":["racisme"],"url":["https://www.movisie.nl/racisme"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Movisie (z.d.) racisme. Geraadpleegd op 14 januari 2026, van www.movisie.nl/racisme",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert "z.d." in result.parsed_data.date
    assert result.parsed_data.issued_year is None
    assert "https://www.movisie.nl/racisme" in result.parsed_data.url


def test_parse_reference_marks_grey_literature_for_apa7_nl(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Ministerie van Binnenlandse Zaken"],"date":["2023"],'
                '"title":["Jaarverslag integratie"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Ministerie van Binnenlandse Zaken. (2023). Jaarverslag integratie.",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert result.parsed_data.organization == ["Ministerie van Binnenlandse Zaken"]
    assert "informele_publicatie" in result.parsed_data.genre
    assert result.ctype == "report"


def test_parse_reference_apa7_nl_repairs_webpage_from_bad_anystyle_article(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","author":["Movisie"],'
                '"title":["Ervaringsdeskundige bestaansonzekerheid"],'
                '"container-title":["Geraadpleegd op"],'
                '"genre":["van"],'
                '"date":["2025-08-26","2026-04-06"],'
                '"url":["https://www.movisie.nl/artikel/example"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Movisie. (2025, 26 augustus). Ervaringsdeskundige bestaansonzekerheid. "
        "Geraadpleegd op 6 april 2026, van https://www.movisie.nl/artikel/example",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert result.pre_classification is not None
    assert result.post_classification is not None
    assert result.pre_classification.ctype == "webpage"
    assert result.ctype == "webpage"
    assert result.parsed_data.type == "webpage"
    assert result.parsed_data.container_title == []
    assert result.parsed_data.genre == []
    assert result.parsed_data.organization == ["Movisie"]
    assert any("webpage" in step for step in result.classification_trace)


def test_parse_reference_apa7_nl_classifies_software_with_trace(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["OpenAI"],"date":["2026","Versie 5.2"],'
                '"title":["ChatGPT"],"url":["https://chat.openai.com"],'
                '"note":["Generatieve AI]."]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "OpenAI. (2026). ChatGPT (Versie 5.2) [Generatieve AI]. https://chat.openai.com",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "software"
    assert result.ctype == "software"
    assert result.parsed_data.type == "software"
    assert result.parsed_data.organization == ["OpenAI"]
    assert result.classification_trace


def test_parse_reference_apa7_nl_preserves_book_chapter_ctype(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Doe","given":"J."}],"date":["2024"],'
                '"title":["Chapter title"],"editor":[{"family":"Smith","given":"A."}],'
                '"container-title":["Handbook of Examples"],"pages":["10-22"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Doe, J. (2024). Chapter title. In A. Smith (Ed.), Handbook of Examples (pp. 10-22). Example Press.",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "book_chapter"
    assert result.ctype == "book_chapter"


def test_parse_reference_apa7_nl_preserves_book_chapter_ctype_with_lowercase_in(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Fook","given":"J."}],"date":["2000"],'
                '"title":["Deconstructing and constructing professional expertise"],'
                '"editor":[{"family":"Fawcett","given":"B."},{"family":"Featherstone","given":"B."}],'
                '"container-title":["Practice and Research in Social Work"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Fook, J. (2000) 'Deconstructing and constructing professional expertise', "
        "in Fawcett, B. and Featherstone, B. (eds), Practice and Research in Social Work, London, Routledge.",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "book_chapter"
    assert result.ctype == "book_chapter"


def test_parse_reference_apa7_nl_does_not_treat_book_title_with_in_as_book_chapter(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Argyris","given":"C."},{"family":"Schon","given":"D."}],'
                '"date":["1976"],"title":["Theory in Practice: Increasing Professional Effectiveness"],'
                '"publisher":["Jossey-Bass"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Argyris, C. and Schon, D. (1976) Theory in Practice: Increasing Professional Effectiveness, "
        "San Francisco, Jossey-Bass.",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "book"
    assert result.ctype == "book"


def test_parse_reference_apa7_nl_recovers_book_publisher_from_location_tail(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Argyris","given":"C."},{"family":"Schon","given":"D."}],'
                '"date":["1976"],"title":["Theory in Practice: Increasing Professional Effectiveness"],'
                '"location":["San Francisco, Jossey-Bass"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Argyris, C. and Schon, D. (1976) Theory in Practice: Increasing Professional Effectiveness, "
        "San Francisco, Jossey-Bass.",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert result.parsed_data.publisher == ["Jossey-Bass"]
    assert result.parsed_data.location == ["San Francisco"]


def test_parse_reference_apa7_nl_recovers_book_chapter_publisher_from_location_tail(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Fook","given":"J."}],"date":["2000"],'
                '"title":["Deconstructing and constructing professional expertise"],'
                '"editor":[{"family":"Fawcett","given":"B."},{"family":"Featherstone","given":"B."}],'
                '"container-title":["Practice and Research in Social Work"],'
                '"location":["London, Routledge"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Fook, J. (2000) 'Deconstructing and constructing professional expertise', "
        "in Fawcett, B. and Featherstone, B. (eds), Practice and Research in Social Work, London, Routledge.",
        style_hint="apa7_nl",
    )

    assert result.parsed_data is not None
    assert result.ctype == "book_chapter"
    assert result.parsed_data.publisher == ["Routledge"]
    assert result.parsed_data.location == ["London"]


@pytest.mark.parametrize(
    ("raw_reference", "response_json", "expected_ctype"),
    [
        (
            "Kronauer, M. (1998) ‘Social Exclusion’ and ‘Underclass’ – New concepts for the analysis of poverty. "
            "In: H.J. Andreß (red.) Empirical Poverty Research in Comparative Perspective. Aldershot: Ashgate, 51-75.",
            (
                '[{"type":"article-journal","author":[{"family":"Kronauer","given":"M."}],"date":["1998"],'
                '"title":["Social Exclusion and Underclass – New concepts for the analysis of poverty"],'
                '"editor":[{"family":"Andreß","given":"H.J."}],"container-title":["Empirical Poverty Research in Comparative Perspective"],'
                '"publisher":["Ashgate"],"location":["Aldershot"],"pages":["51-75"]}]'
            ),
            "book_chapter",
        ),
        (
            "Snel, E. en G. Engbersen (2000) Modernized Poverty: Individualization, Concentration and Embeddedness. "
            "In: J. Berghman, A. Nagelkerke, K. Boos, R. Doesschot en G. Vonk (red.) Social Security in Transition. "
            "Den Haag: Kluwer Law International, 63-76.",
            (
                '[{"type":"article-journal","author":[{"family":"Snel","given":"E."},{"family":"Engbersen","given":"G."}],"date":["2000"],'
                '"title":["Modernized Poverty: Individualization, Concentration and Embeddedness"],'
                '"editor":[{"family":"Berghman","given":"J."}],"container-title":["Social Security in Transition"],'
                '"publisher":["Kluwer Law International"],"location":["Den Haag"],"pages":["63-76"]}]'
            ),
            "book_chapter",
        ),
        (
            "Snel, E., G. Engbersen en C. Vrooman (2000) Arm Nederland: Verandering en Bestendiging van Armoede. "
            "In: G. Engbersen, C. Vrooman en E. Snel (red.) Balans van het Armoedebeleid. "
            "Vijfde Jaarrapport Armoede en Sociale Uitsluiting. Amsterdam: aup, 13-52.",
            (
                '[{"type":"article-journal","author":[{"family":"Snel","given":"E."},{"family":"Engbersen","given":"G."},{"family":"Vrooman","given":"C."}],"date":["2000"],'
                '"title":["Arm Nederland: Verandering en Bestendiging van Armoede"],'
                '"editor":[{"family":"Engbersen","given":"G."}],"container-title":["Balans van het Armoedebeleid. Vijfde Jaarrapport Armoede en Sociale Uitsluiting"],'
                '"publisher":["aup"],"location":["Amsterdam"],"pages":["13-52"]}]'
            ),
            "book_chapter",
        ),
        (
            "Snel, E., J. de Boom en G. Engbersen (2008) The Silent Transformation of the Dutch Welfare State and the Rise of In-Work Poverty. "
            "In: H-J. Andress en H. Lohmann (red.) The working poor in Europe. Cheltenham: Edward Elgar.",
            (
                '[{"type":"chapter","author":[{"family":"Snel","given":"E."},{"family":"de Boom","given":"J."},{"family":"Engbersen","given":"G."}],"date":["2008"],'
                '"title":["The Silent Transformation of the Dutch Welfare State and the Rise of In-Work Poverty"],'
                '"editor":[{"family":"Andress","given":"H-J."},{"family":"Lohmann","given":"H."}],"container-title":["The working poor in Europe"],'
                '"publisher":["Edward Elgar"],"location":["Cheltenham"]}]'
            ),
            "book_chapter",
        ),
        (
            "Hellendoorn, M. en J. de Bruijn (1999) Overheidsbeleid en Armoederisico’s van Vrouwen. "
            "In: G. Engbersen, C. Vrooman en E. Snel (red.) Armoede en Verzorgingsstaat. "
            "Vierde Jaarrapport Armoede en Sociale Uitsluiting. Amsterdam: aup, 109-127",
            (
                '[{"type":"article-journal","author":[{"family":"Hellendoorn","given":"M."},{"family":"de Bruijn","given":"J."}],"date":["1999"],'
                '"title":["Overheidsbeleid en Armoederisico’s van Vrouwen"],'
                '"editor":[{"family":"Engbersen","given":"G."}],"container-title":["Armoede en Verzorgingsstaat. Vierde Jaarrapport Armoede en Sociale Uitsluiting"],'
                '"publisher":["aup"],"location":["Amsterdam"],"pages":["109-127"]}]'
            ),
            "book_chapter",
        ),
    ],
)
def test_parse_reference_apa7_nl_prefers_book_chapter_for_edited_volume_patterns(
    monkeypatch,
    raw_reference,
    response_json,
    expected_ctype,
):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=response_json, stderr="")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(raw_reference, style_hint="apa7_nl")

    assert result.pre_classification is not None
    assert result.ctype == expected_ctype
    assert result.post_classification is not None
    assert any("chapter" in step.lower() for step in result.classification_trace)


@pytest.mark.parametrize(
    ("raw_reference", "response_json", "expected_authors", "expected_editors"),
    [
        (
            "Vrooman, C. en S. Hoff (2004) The Feminisation of Poverty – Women as a Risk Group. "
            "In: C. Vrooman en S. Hoff (red.) The Poor Side of the Netherlands. "
            "Results from the Dutch poverty monitor 1997-2003. Den Haag: scp/cbs, 93-110.",
            (
                '[{"type":"article-journal","author":[{"family":"Vrooman","given":"C.en S.Hoff"}],"date":["2004"],'
                '"title":["The Feminisation of Poverty – Women as a Risk Group"],'
                '"editor":[{"family":"S. Hoff","given":"C.Vrooman"}],'
                '"container-title":["The Poor Side of the Netherlands. Results from the Dutch poverty monitor 1997-2003"],'
                '"publisher":["scp/cbs"],"location":["Den Haag"],"pages":["93-110"]}]'
            ),
            ["Vrooman", "Hoff"],
            ["Vrooman", "Hoff"],
        ),
        (
            "Snel, E. en G. Engbersen (2000) Modernized Poverty: Individualization, Concentration and Embeddedness. "
            "In: J. Berghman, A. Nagelkerke, K. Boos, R. Doesschot en G. Vonk (red.) Social Security in Transition. "
            "Den Haag: Kluwer Law International, 63-76.",
            (
                '[{"type":"article-journal","author":[{"family":"Snel","given":"E.en G.Engbersen"}],"date":["2000"],'
                '"title":["Modernized Poverty: Individualization, Concentration and Embeddedness"],'
                '"editor":[{"family":"Berghman","given":"J."}],'
                '"container-title":["Social Security in Transition"],'
                '"publisher":["Kluwer Law International"],"location":["Den Haag"],"pages":["63-76"]}]'
            ),
            ["Snel", "Engbersen"],
            ["Berghman", "Nagelkerke", "Boos", "Doesschot", "Vonk"],
        ),
        (
            "Doe, J., A. Smith en B. Jones (2020). Example article. Journal Name, 5(2), 10-20.",
            (
                '[{"type":"article-journal","author":[{"family":"Doe","given":"J."}],"date":["2020"],'
                '"title":["Example article"],"container-title":["Journal Name"],"volume":["5"],"issue":["2"],"pages":["10-20"]}]'
            ),
            ["Doe", "Smith", "Jones"],
            [],
        ),
        (
            "Snel, E. EN G. Engbersen (2000). Example book. Example Press.",
            (
                '[{"author":[{"family":"Snel","given":"E.EN G.Engbersen"}],"date":["2000"],'
                '"title":["Example book"],"publisher":["Example Press"]}]'
            ),
            ["Snel", "Engbersen"],
            [],
        ),
    ],
)
def test_parse_reference_apa7_nl_repairs_dutch_en_contributor_lists(
    monkeypatch,
    raw_reference,
    response_json,
    expected_authors,
    expected_editors,
):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=response_json, stderr="")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(raw_reference, style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert [name.family for name in result.parsed_data.author] == expected_authors
    assert [name.family for name in result.parsed_data.editor] == expected_editors


def test_parse_reference_does_not_repair_dutch_en_contributors_without_apa7_nl(
    monkeypatch,
):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","author":[{"family":"Vrooman","given":"C.en S.Hoff"}],'
                '"date":["2004"],"title":["Example article"],"container-title":["Journal Name"],"pages":["10-20"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Vrooman, C. en S. Hoff (2004). Example article. Journal Name, 5(2), 10-20."
    )

    assert result.parsed_data is not None
    assert [(name.family, name.given) for name in result.parsed_data.author] == [
        ("Vrooman", "C.en S.Hoff")
    ]


def test_parse_reference_apa7_nl_repairs_pdf_soft_hyphenated_fields(monkeypatch):
    _configure_anystyle(monkeypatch)

    raw_reference = (
        "Van de Werfhorst, H.G. (2007) Scarcity and Abundance: Reconciling Trends in the Effects of "
        "Education on Social Class and Earnings in Great Britain 1972-2003. European Sociologi-cal "
        "Review 23, 239-261."
    )

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","editor":[{"family":"Werfhorst","given":"H.G."}],'
                '"date":["2007"],'
                '"title":["Scarcity and Abundance: Reconciling Trends in the Effects of Education on Social Class and Earnings in Great Britain 1972-2003"],'
                '"container-title":["European Sociologi-cal Review"],"volume":["23"],"pages":["239-261"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(raw_reference, style_hint="apa7_nl")

    assert result.parsed_data is not None
    assert result.raw_reference == raw_reference
    assert [(name.family, name.given) for name in result.parsed_data.author] == [("Werfhorst", "H.G.")]
    assert result.parsed_data.editor == []
    assert result.parsed_data.container_title == ["European Sociological Review"]
    assert result.parsed_data.title == [
        "Scarcity and Abundance: Reconciling Trends in the Effects of Education on Social Class and Earnings in Great Britain 1972-2003"
    ]


def test_parse_reference_apa7_nl_preserves_conference_paper_ctype(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","author":[{"family":"Doe","given":"J."}],"date":["2024"],'
                '"title":["Conference title"],'
                '"container-title":["Proceedings of the 10th International Conference on Examples"],'
                '"url":["https://doi.org/10.1000/conf"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Doe, J. (2024). Conference title. Proceedings of the 10th International Conference on Examples. https://doi.org/10.1000/conf",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "conference_paper"
    assert result.ctype == "conference_paper"
    assert any("conference" in step.lower() for step in result.classification_trace)


def test_parse_reference_apa7_nl_classifies_thesis(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Jansen, P."],"date":["2023"],"title":["Leren classificeren"],"institution":["Universiteit Utrecht"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Jansen, P. (2023). Leren classificeren [Masterthesis, Universiteit Utrecht].",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "thesis"
    assert result.ctype == "thesis"


def test_parse_reference_apa7_nl_classifies_dataset(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["CBS"],"date":["2024"],"title":["Bevolkingsdataset"],"url":["https://zenodo.org/record/123"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "CBS. (2024). Bevolkingsdataset [Dataset]. https://zenodo.org/record/123",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "dataset"
    assert result.ctype == "dataset"


def test_parse_reference_apa7_nl_classifies_newspaper_article(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Doe, J."],"date":["2024-05-01"],"title":["Voorbeeldnieuws"],"container-title":["de Volkskrant"],"url":["https://www.volkskrant.nl/example"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Doe, J. (2024, 1 mei). Voorbeeldnieuws. de Volkskrant. https://www.volkskrant.nl/example",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "newspaper_article"
    assert result.ctype == "newspaper_article"


def test_parse_reference_apa7_nl_classifies_unknown_when_only_one_weak_signal(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"title":["Losse vermelding"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Losse vermelding.", style_hint="apa7_nl")

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "unknown"
    assert result.ctype == "unknown"
    assert "classifier_unknown_ctype" in result.warnings


def test_parse_reference_reclassifies_unknown_article_shape_for_crossref_match(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Sorinola O","Olufowobi O"],"date":["2004"],'
                '"title":["Instructions to authors for case reporting are limited: a review of a core journal list"],'
                '"container-title":["BMC Med Educ"],"volume":["4"],"pages":["4"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Sorinola O, Olufowobi O. Instructions to authors for case reporting are limited: a review of a core journal list. BMC Med Educ. 2004;4:4.",
        style_hint="vancouver",
    )

    assert result.ctype == "journal_article"
    assert result.match_preparation is not None
    assert result.match_preparation.eligible_for_db_match is True
    assert result.match_preparation.match_target == "crossref"


def test_parse_reference_recovers_missing_raw_publication_year_for_vancouver(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":["Monteiro R"],'
                '"title":["Authorship criteria for scientific papers: a polemic and delicate subject"],'
                '"container-title":["Rev Bras Cir Cardiovasc"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Monteiro R. Authorship criteria for scientific papers: a polemic and delicate subject. "
        "Rev Bras Cir Cardiovasc [periódico na Internet]. 2004 Dez [citado 2007 Out 31];",
        style_hint="vancouver",
    )

    assert result.parsed_data is not None
    assert result.parsed_data.issued_year == "2004"
    assert result.match_preparation is not None
    assert result.match_preparation.eligible_for_db_match is True


def test_parse_reference_apa7_nl_reclassifies_webpage_to_journal_article_on_strong_contradiction(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","author":["Example Org"],"date":["2024"],'
                '"title":["Strong article"],"container-title":["Journal of Examples"],'
                '"volume":["10"],"issue":["2"],"pages":["10-20"],'
                '"doi":["10.1234/example"],"url":["https://doi.org/10.1234/example"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Example Org. (2024). Strong article. Geraadpleegd op 1 mei 2024, van https://doi.org/10.1234/example",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "webpage"
    assert result.ctype == "journal_article"
    assert "classifier_reclassified_post_parse" in result.warnings
    assert any("RECLASSIFY post" in step for step in result.classification_trace)
    assert result.match_preparation is not None
    assert result.match_preparation.eligible_for_db_match is True
    assert result.match_preparation.match_target == "crossref"


def test_parse_reference_apa7_nl_report_outranks_webpage_when_report_signals_are_explicit(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Ministerie van Onderwijs"],"date":["2024"],"title":["Jaarverslag onderwijs"],"url":["https://www.rijksoverheid.nl/jaarverslag"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Ministerie van Onderwijs. (2024). Jaarverslag onderwijs. https://www.rijksoverheid.nl/jaarverslag",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "report"
    assert result.ctype == "report"


def test_parse_reference_apa7_nl_book_is_reclassified_to_report_when_report_signals_win(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Rijksinstituut voor Volksgezondheid"],"date":["2024"],"title":["Rapport volksgezondheid"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Rijksinstituut voor Volksgezondheid. (2024). Rapport volksgezondheid.",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "report"
    assert result.ctype == "report"


def test_parse_reference_apa7_nl_conference_outranks_journal_when_explicit(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","author":["Doe, J."],"date":["2024"],'
                '"title":["Proceedings paper"],"container-title":["Proceedings of the Example Conference"],'
                '"volume":["1"],"pages":["1-5"],"doi":["10.5555/conf"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Doe, J. (2024). Proceedings paper. Proceedings of the Example Conference. https://doi.org/10.5555/conf",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "conference_paper"
    assert result.ctype == "conference_paper"


def test_parse_reference_apa7_nl_doi_book_uses_book_target(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"type":"article-journal","author":["R. Beach","J. Share","A. Webb"],'
                '"date":["2017"],'
                '"title":["Teaching climate change to adolescents: Reading, writing, and making a difference"],'
                '"genre":["Routledge."],"url":["https://doi.org/10.4324/9781315276304"],'
                '"doi":["10.4324/9781315276304"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Beach, R., Share, J., & Webb, A. (2017). Teaching climate change to adolescents: "
        "Reading, writing, and making a difference. Routledge. https://doi.org/10.4324/9781315276304",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "journal_article"
    assert result.ctype == "book"
    assert result.parsed_data is not None
    assert result.parsed_data.publisher == ["Routledge"]
    assert result.match_preparation is not None
    assert result.match_preparation.match_target == "openlibrary"
    assert result.match_preparation.lookup_key_fields["doi"] == ["10.4324/9781315276304"]


def test_parse_reference_apa7_nl_software_outranks_webpage(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["OpenAI"],"date":["2026"],"title":["ChatGPT"],"url":["https://chat.openai.com"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "OpenAI. (2026). ChatGPT (Versie 5.2) [Generatieve AI]. https://chat.openai.com",
        style_hint="apa7_nl",
    )

    assert result.pre_classification is not None
    assert result.pre_classification.ctype == "software"
    assert result.ctype == "software"


def test_parse_reference_trace_contains_branch_and_post_resolution(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Movisie"],"title":["Voorbeeldpagina"],"url":["https://www.movisie.nl/example"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Movisie. (2025). Voorbeeldpagina. Geraadpleegd op 6 april 2026, van https://www.movisie.nl/example",
        style_hint="apa7_nl",
    )

    assert result.classification_trace
    assert any(step.startswith("BRANCH") for step in result.classification_trace)
    assert any(step.startswith("KEEP post") or step.startswith("RECLASSIFY post") for step in result.classification_trace)


def test_parse_reference_phase3_contract_includes_match_and_report_handoff(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Smith","given":"J."}],"date":["2020"],'
                '"title":["Some title"],"container-title":["Journal Name"],'
                '"volume":["5"],"issue":["2"],"pages":["10-20"],'
                '"doi":["10.1234/test.article"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article",
        style_hint="apa7_nl",
    )

    assert result.reference_id.startswith("ref_")
    assert result.normalized_reference
    assert result.pre_classification is not None
    assert result.post_classification is not None
    assert result.match_preparation is not None
    assert result.report_basis is not None
    assert result.match_preparation.match_target == "crossref"
    assert "title" in result.match_preparation.lookup_key_fields
    assert result.report_basis.why_this_type
    assert result.report_basis.why_matchable_or_not


@pytest.mark.parametrize(
    ("raw_reference", "response_json", "expected_ctype", "expected_target", "eligible"),
    [
        (
            "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article",
            '[{"author":[{"family":"Smith","given":"J."}],"date":["2020"],"title":["Some title"],"container-title":["Journal Name"],"volume":["5"],"issue":["2"],"pages":["10-20"],"doi":["10.1234/test.article"]}]',
            "journal_article",
            "crossref",
            True,
        ),
        (
            "Doe, J. (2021). Another title. Example Press.",
            '[{"author":[{"family":"Doe","given":"J."}],"date":["2021"],"title":["Another title"],"publisher":["Example Press"]}]',
            "book",
            "openlibrary",
            True,
        ),
        (
            "Doe, J. (2024). Chapter title. In A. Smith (Ed.), Handbook of Examples (pp. 10-22). Example Press.",
            '[{"author":[{"family":"Doe","given":"J."}],"date":["2024"],"title":["Chapter title"],"editor":[{"family":"Smith","given":"A."}],"container-title":["Handbook of Examples"],"pages":["10-22"]}]',
            "book_chapter",
            "openlibrary",
            True,
        ),
        (
            "Jansen, P. (2023). Leren classificeren [Masterthesis, Universiteit Utrecht].",
            '[{"author":["Jansen, P."],"date":["2023"],"title":["Leren classificeren"],"institution":["Universiteit Utrecht"]}]',
            "thesis",
            "none",
            False,
        ),
        (
            "Movisie. (2025). Voorbeeldpagina. Geraadpleegd op 6 april 2026, van https://www.movisie.nl/example",
            '[{"author":["Movisie"],"title":["Voorbeeldpagina"],"url":["https://www.movisie.nl/example"]}]',
            "webpage",
            "none",
            False,
        ),
        (
            "OpenAI. (2026). ChatGPT (Versie 5.2) [Generatieve AI]. https://chat.openai.com",
            '[{"author":["OpenAI"],"date":["2026"],"title":["ChatGPT"],"url":["https://chat.openai.com"]}]',
            "software",
            "none",
            False,
        ),
        (
            "Losse vermelding.",
            '[{"title":["Losse vermelding"]}]',
            "unknown",
            "none",
            False,
        ),
    ],
)
def test_parse_reference_match_eligibility_follows_final_ctype(
    monkeypatch,
    raw_reference,
    response_json,
    expected_ctype,
    expected_target,
    eligible,
):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout=response_json, stderr="")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(raw_reference, style_hint="apa7_nl")

    assert result.ctype == expected_ctype
    assert result.match_preparation is not None
    assert result.match_preparation.match_target == expected_target
    assert result.match_preparation.eligible_for_db_match is eligible
    assert result.report_basis is not None
    assert result.report_basis.why_matchable_or_not


def test_parse_reference_report_basis_tracks_missing_fields_for_match(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":[{"family":"Doe","given":"J."}],"title":["Another title"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Doe, J. Another title.", style_hint="apa7_nl")

    assert result.ctype == "book"
    assert result.match_preparation is not None
    assert result.match_preparation.match_target == "openlibrary"
    assert result.report_basis is not None
    assert "issued_year" in result.report_basis.missing_fields_for_match


def test_parse_reference_does_not_apply_apa7_nl_rules_for_other_styles(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"author":["Movisie"],"title":["racisme"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference(
        "Movisie (z.d.) racisme. Geraadpleegd op 14 januari 2026, van www.movisie.nl/racisme",
        style_hint="apa7_en",
    )

    assert result.parsed_data is not None
    assert result.parsed_data.access is None
    assert result.parsed_data.organization == []


def test_parse_reference_preserves_backend_trace_tags(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout=(
                '[{"author":[{"family":"Smith","given":"J."}],"title":["Some title"],'
                '"container-title":["Journal"],"note":["Original trailing note"]}]'
            ),
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Smith, J. Some title. Journal. Original trailing note.")

    assert result.parsed_data is not None
    assert result.parsed_data.raw_tags is not None
    assert result.parsed_data.raw_tags["note"] == ["Original trailing note"]


def test_parse_reference_returns_partial_parse_warnings(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(
            returncode=0,
            stdout='[{"title":["Untitled source"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_reference("Untitled source.")

    assert "parser_partial_output" in result.warnings
    assert "parser_missing_author" in result.warnings
    assert "parser_missing_date" in result.warnings
    assert "parser_missing_identifier" in result.warnings


def test_parse_references_preserves_batch_length(monkeypatch):
    _configure_anystyle(monkeypatch)

    calls = []

    def _fake_run(*args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(
            returncode=0,
            stdout='[{"title":["One"]},{"title":["Two"]}]',
            stderr="",
        )

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    result = parse_references(["Ref one", "Ref two"])

    assert len(result) == 2
    assert len(calls) == 1
    assert result[0].parsed_data is not None
    assert result[1].parsed_data is not None
    assert all(item.pre_classification is not None for item in result)


def test_parse_references_with_recovery_attaches_orphan_in_tail(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_parse_reference_tags(raw_reference: str):
        if raw_reference == "Klassen, S., & Froese Klassen, C. (2014). Science teaching with stories: Theoretical and practical perspectives.":
            return {
                "author": [
                    {"family": "Klassen", "given": "S."},
                    {"family": "Froese Klassen", "given": "C."},
                ],
                "date": ["2014"],
                "title": ["Science teaching with stories: Theoretical and practical perspectives"],
            }
        if raw_reference == "In M. R. Matthews (Ed.), International handbook of research in history, philosophy and science teaching (pp. 1503–1529). Springer.":
            return {
                "type": ["chapter"],
                "editor": [{"family": "Matthews", "given": "M. R."}],
                "container-title": ["International handbook of research in history, philosophy and science teaching"],
                "pages": ["1503–1529"],
                "publisher": ["Springer"],
            }
        if raw_reference == (
            "Klassen, S., & Froese Klassen, C. (2014). Science teaching with stories: Theoretical and practical perspectives. "
            "In M. R. Matthews (Ed.), International handbook of research in history, philosophy and science teaching (pp. 1503–1529). Springer."
        ):
            return {
                "type": ["chapter"],
                "author": [
                    {"family": "Klassen", "given": "S."},
                    {"family": "Froese Klassen", "given": "C."},
                ],
                "date": ["2014"],
                "title": ["Science teaching with stories: Theoretical and practical perspectives"],
                "editor": [{"family": "Matthews", "given": "M. R."}],
                "container-title": ["International handbook of research in history, philosophy and science teaching"],
                "pages": ["1503–1529"],
                "publisher": ["Springer"],
            }
        raise AssertionError(f"Unexpected raw reference: {raw_reference}")

    monkeypatch.setattr("reference_gen2.reference_parsing.service.parse_reference_tags", _fake_parse_reference_tags)

    phase3, phase3b = parse_references_with_recovery(
        [
            "Klassen, S., & Froese Klassen, C. (2014). Science teaching with stories: Theoretical and practical perspectives.",
            "In M. R. Matthews (Ed.), International handbook of research in history, philosophy and science teaching (pp. 1503–1529). Springer.",
        ]
    )

    assert len(phase3) == 2
    assert len(phase3b) == 1
    assert phase3b[0].ctype == "book_chapter"
    assert phase3b[0].recovery_status == "attached_backward"
    assert "right_starts_with_in_tail" in phase3b[0].recovery_trace
    assert phase3b[0].absorbed_reference_ids == [phase3[1].reference_id]


def test_parse_references_with_recovery_does_not_attach_healthy_adjacent_references(monkeypatch):
    _configure_anystyle(monkeypatch)

    responses = iter(
        [
            SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"author":[{"family":"Smith","given":"J."}],"date":["2020"],"title":["Some article"],'
                    '"container-title":["Journal"],"volume":["5"],"issue":["2"],"pages":["10-20"],"doi":["10.1234/test"]}]'
                ),
                stderr="",
            ),
            SimpleNamespace(
                returncode=0,
                stdout=(
                    '[{"type":"chapter","editor":[{"family":"Matthews","given":"M. R."}],"container-title":["Handbook"],'
                    '"pages":["1503–1529"],"publisher":["Springer"]}]'
                ),
                stderr="",
            ),
        ]
    )

    def _fake_run(*_args, **_kwargs):
        return next(responses)

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    phase3, phase3b = parse_references_with_recovery(
        [
            "Smith, J. (2020). Some article. Journal, 5(2), 10-20. doi:10.1234/test",
            "In M. R. Matthews (Ed.), Handbook (pp. 1503–1529). Springer.",
        ]
    )

    assert len(phase3b) == 2
    assert phase3b[0].recovery_status in {"unchanged", "blocked"}


def test_parse_references_with_recovery_attaches_split_article_metadata_tail(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_parse_reference_tags(raw_reference: str):
        if raw_reference == "Kim, Y., & Lee, M. (2025). Social media use and loneliness among adolescents: The moderating role of media literacy.":
            return {
                "author": [
                    {"family": "Kim", "given": "Y."},
                    {"family": "Lee", "given": "M."},
                ],
                "date": ["2025"],
                "title": ["Social media use and loneliness among adolescents: The moderating role of media literacy"],
            }
        if raw_reference == "Online Information Review, 49(3), 585–599. https://doi.org/10.1108/OIR-12-2023-0664":
            return {
                "type": ["article-journal"],
                "container-title": ["Online Information Review"],
                "volume": ["49"],
                "issue": ["3"],
                "pages": ["585–599"],
                "doi": ["10.1108/OIR-12-2023-0664"],
                "url": ["https://doi.org/10.1108/OIR-12-2023-0664"],
            }
        if raw_reference == (
            "Kim, Y., & Lee, M. (2025). Social media use and loneliness among adolescents: The moderating role of media literacy. "
            "Online Information Review, 49(3), 585–599. https://doi.org/10.1108/OIR-12-2023-0664"
        ):
            return {
                "type": ["article-journal"],
                "author": [
                    {"family": "Kim", "given": "Y."},
                    {"family": "Lee", "given": "M."},
                ],
                "date": ["2025"],
                "title": ["Social media use and loneliness among adolescents: The moderating role of media literacy"],
                "container-title": ["Online Information Review"],
                "volume": ["49"],
                "issue": ["3"],
                "pages": ["585–599"],
                "doi": ["10.1108/OIR-12-2023-0664"],
                "url": ["https://doi.org/10.1108/OIR-12-2023-0664"],
            }
        raise AssertionError(f"Unexpected raw reference: {raw_reference}")

    monkeypatch.setattr("reference_gen2.reference_parsing.service.parse_reference_tags", _fake_parse_reference_tags)

    phase3, phase3b = parse_references_with_recovery(
        [
            "Kim, Y., & Lee, M. (2025). Social media use and loneliness among adolescents: The moderating role of media literacy.",
            "Online Information Review, 49(3), 585–599. https://doi.org/10.1108/OIR-12-2023-0664",
        ]
    )

    assert len(phase3) == 2
    assert len(phase3b) == 1
    assert phase3b[0].ctype == "journal_article"
    assert phase3b[0].recovery_status == "attached_backward"
    assert "right_looks_like_metadata_tail" in phase3b[0].recovery_trace


def test_parse_references_with_recovery_blocks_metadata_tail_when_right_is_standalone_head(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_parse_reference_tags(raw_reference: str):
        mapping = {
            "Eurostat (1998) Recommendations from the Task Force, [rapport] Eurostat": {
                "organization": ["Eurostat"],
                "date": ["1998"],
                "title": ["Recommendations from the Task Force"],
            },
            "Keles, B., McCrae, N., & Grealish, A. (2020). The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review.": {
                "author": [
                    {"family": "Keles", "given": "B."},
                    {"family": "McCrae", "given": "N."},
                    {"family": "Grealish", "given": "A."},
                ],
                "date": ["2020"],
                "title": ["The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review"],
            },
        }
        if raw_reference not in mapping:
            raise AssertionError(f"Unexpected raw reference: {raw_reference}")
        return mapping[raw_reference]

    monkeypatch.setattr("reference_gen2.reference_parsing.service.parse_reference_tags", _fake_parse_reference_tags)

    phase3, phase3b = parse_references_with_recovery(
        [
            "Eurostat (1998) Recommendations from the Task Force, [rapport] Eurostat",
            "Keles, B., McCrae, N., & Grealish, A. (2020). The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review.",
        ]
    )

    assert len(phase3b) == 2
    assert all(item.recovery_status == "unchanged" for item in phase3b)


def test_parse_reference_empty_string_returns_warning_result():
    result = parse_reference("   ")

    assert result.parsed_data is None
    assert result.warnings == ["parser_unparseable_reference"]
    assert result.reference_id.startswith("ref_")
    assert result.normalized_reference == ""
    assert result.match_preparation is not None
    assert result.match_preparation.eligible_for_db_match is False


def test_parse_reference_raises_for_missing_executable(monkeypatch):
    _configure_anystyle(monkeypatch)
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_EXECUTABLE", "")

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_unconfigured"


def test_parse_reference_raises_when_anystyle_disabled(monkeypatch):
    _configure_anystyle(monkeypatch)
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_ENABLED", False)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_disabled"


def test_parse_reference_raises_for_timeout(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["anystyle"], timeout=15)

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_timeout"


def test_parse_reference_raises_for_execution_failure(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        raise OSError("exec format error")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_execution_failed"


def test_parse_reference_raises_for_non_zero_exit_and_preserves_stderr(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=2, stdout="", stderr="parser backend exploded")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_parse_failed"
    assert exc.value.details["returncode"] == 2
    assert exc.value.details["stderr"] == "parser backend exploded"


def test_parse_reference_raises_for_malformed_json(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="{not-json", stderr="")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_invalid_output"


def test_parse_reference_raises_for_unexpected_json_payload_type(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout='{"not":"a-list"}', stderr="")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_invalid_output"


def test_parse_reference_raises_for_unexpected_json_reference_payload(monkeypatch):
    _configure_anystyle(monkeypatch)

    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=0, stdout='["not-a-dict"]', stderr="")

    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.subprocess.run", _fake_run)

    with pytest.raises(ReferenceParsingError) as exc:
        parse_reference("Smith, J. (2020). Some title.")

    assert exc.value.code == "anystyle_invalid_output"


def test_parse_reference_real_cli_smoke_if_available(monkeypatch):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for live smoke testing.")

    _configure_anystyle(monkeypatch)
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_EXECUTABLE", executable)

    result = parse_reference(
        "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article"
    )

    assert result.parsed_data is not None
    assert result.parser_backend == "anystyle"
    assert result.parser_model_used == "default"
    assert result.parsed_data.title


def test_segmented_references_parse_with_real_cli_if_available(monkeypatch):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for live phase integration testing.")

    _configure_anystyle(monkeypatch)
    monkeypatch.setattr("reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_EXECUTABLE", executable)

    bibliography = BibliographySection(
        heading="References",
        heading_unit_index=0,
        start_unit_index=0,
        end_unit_index=0,
        text=(
            "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article\n"
            "Doe, J. (2021). Another title. Other Journal, 3(1), 5-10. doi:10.5678/other"
        ),
        warnings=[],
    )
    extraction = _make_extraction("docx", [("Normal", "x")])

    segmented = segment_references(bibliography, extraction)
    parsed = parse_references(segmented.references)

    assert len(segmented.references) == 2
    assert len(parsed) == len(segmented.references)
    assert all(item.parsed_data is not None for item in parsed)


def test_phase123_docx_pipeline_parses_stable_fields_if_real_cli_available(
    monkeypatch, local_tmp_dir, good_docx_bytes: bytes
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for live Phase 1 to 3 testing.")

    _configure_anystyle(monkeypatch)
    monkeypatch.setattr(
        "reference_gen2.reference_parsing.anystyle_adapter.ANYSTYLE_EXECUTABLE",
        executable,
    )
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    phase1 = run_phase1_pipeline(
        "paper.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        good_docx_bytes,
    )
    segmented = segment_references(phase1.bibliography, phase1.extraction)
    parsed = parse_references(segmented.references)

    assert phase1.upload.detected_kind == "docx"
    assert phase1.bibliography.heading == "References"
    assert len(segmented.references) == 3
    assert len(parsed) == len(segmented.references)

    first = parsed[0]
    assert first.parsed_data is not None
    assert first.parsed_data.title
    assert first.parsed_data.author or first.parsed_data.organization

    second = parsed[1]
    assert second.parsed_data is not None
    assert second.parsed_data.doi or second.parsed_data.url
