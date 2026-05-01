from __future__ import annotations

from reference_gen2.citation_rendering import render_candidate_citation
from reference_gen2.citation_rendering import service as citation_service
from reference_gen2.reference_matching.models import LocalDbCandidate


def test_journal_candidate_renders_db_initials_in_dutch_apa_output():
    result = render_candidate_citation(
        LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
            authors=["boers", "afzali", "conrod", "boers afzali conrod"],
            author_initials=["E.", "M. H.", "P."],
            issued_year="2019",
            doi="10.1177/0706743719885486",
            container_title="The Canadian Journal of Psychiatry",
            volume="65",
            issue="3",
            pages="123-130",
        ),
        reference_ctype="journal_article",
    )

    assert result is not None
    assert result.style == "apa-standard"
    assert result.locale == "nl-NL"
    assert "Temporal Associations" in result.text
    assert "Boers, E." in result.text
    assert "Afzali, M. H." in result.text
    assert "boers afzali conrod" not in result.text
    assert "10.1177/0706743719885486" in result.text
    assert "<script" not in result.html


def test_book_candidate_falls_back_to_surnames_when_initials_are_missing():
    result = render_candidate_citation(
        LocalDbCandidate(
            record_id="search_book:1",
            record_type="search_book",
            title="Individualization",
            authors=["beck", "beck-gernsheim", "beck beck-gernsheim"],
            issued_year="2002",
            publisher="SAGE",
        ),
        reference_ctype="book",
    )

    assert result is not None
    assert "Beck" in result.text
    assert "Beck-Gernsheim" in result.text
    assert "U.en" not in result.text
    assert "E. Beck-Gernsheim" not in result.text
    assert "beck beck-gernsheim" not in result.text.lower()


def test_no_author_book_candidate_does_not_render_title_as_author():
    result = render_candidate_citation(
        LocalDbCandidate(
            record_id="search_book:33731863",
            record_type="search_book",
            record_granularity="book",
            title="Empirical poverty research in a comparative perspective",
            issued_year="1998",
            publisher="Ashgate",
        ),
        reference_ctype="book_chapter",
    )

    assert result is not None
    assert (
        result.text
        == "Empirical poverty research in a comparative perspective. (1998). Ashgate."
    )
    assert result.text.count("Empirical poverty research in a comparative perspective") == 1
    assert "<i>Empirical poverty research in a comparative perspective</i>. (1998). Ashgate." in result.html
    assert "Missing author/organization" in result.warnings


def test_chapter_candidate_includes_editor_initials_in_fallback(monkeypatch):
    def fail_render(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(citation_service, "_render_with_citeproc", fail_render)

    result = render_candidate_citation(
        LocalDbCandidate(
            record_id="search_book_chapter:1",
            record_type="search_book_chapter",
            record_granularity="chapter",
            title="How systems work",
            authors=["Möller"],
            author_initials=["H."],
            editors=["von Aichberger", "Lundström"],
            editor_initials=["S.", "K."],
            issued_year="2024",
            container_title="Handbook of Examples",
            publisher="Example Press",
            pages="11-18",
        ),
        reference_ctype="book_chapter",
    )

    assert result is not None
    assert "Möller, H." in result.text
    assert "Von Aichberger, S." in result.text
    assert "Lundström, K." in result.text
    assert "Handbook of Examples" in result.text


def test_citeproc_failure_returns_sanitized_fallback(monkeypatch):
    def fail_render(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(citation_service, "_render_with_citeproc", fail_render)

    result = render_candidate_citation(
        LocalDbCandidate(
            record_id="search_journal:1",
            record_type="search_journal",
            title="<script>alert(1)</script> Safe title",
            authors=["alpha"],
            issued_year="2020",
            doi="10.1234/example",
        ),
        reference_ctype="journal_article",
    )

    assert result is not None
    assert result.partial is True
    assert "citeproc renderer unavailable" in " ".join(result.warnings)
    assert "<script" not in result.html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in result.html


def test_vancouver_style_uses_vancouver_fallback_shape(monkeypatch):
    def fail_render(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(citation_service, "_render_with_citeproc", fail_render)

    result = render_candidate_citation(
        LocalDbCandidate(
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
        reference_ctype="journal_article",
        style="vancouver",
        locale="en-US",
    )

    assert result is not None
    assert result.style == "vancouver"
    assert result.locale == "en-US"
    assert "Chen, L. Clinical result. Med J. 2022;12(2):5-9." in result.text
