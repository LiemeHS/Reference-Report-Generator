from __future__ import annotations

import sqlite3

from reference_gen2.reference_matching import service as matching_service
from reference_gen2.reference_matching import (
    LocalDbCandidate,
    Phase4BatchInput,
    Phase4RuntimeConfig,
    Phase4SearchConfig,
    SqliteLocalDbProvider,
    match_reference,
    match_references,
)
from reference_gen2.reference_matching.journal_abbreviations import journal_abbreviation_match
from reference_gen2.reference_matching.provider import normalize_text as provider_normalize_text
from reference_gen2.reference_parsing.models import (
    MatchPreparation,
    ParsedName,
    ParsedReferenceData,
    ParsedReferenceResult,
)


def _create_local_db(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE crossref_works (
                id INTEGER PRIMARY KEY,
                doi TEXT,
                title TEXT,
                issued_year INTEGER
            );
            CREATE TABLE ol_editions (
                id INTEGER PRIMARY KEY,
                ol_key TEXT,
                title TEXT,
                publish_year INTEGER
            );
            CREATE TABLE ol_authors (
                id INTEGER PRIMARY KEY,
                ol_key TEXT,
                display_name TEXT
            );

            CREATE TABLE search_journal (
                id INTEGER PRIMARY KEY,
                title TEXT,
                year TEXT,
                doi TEXT,
                journal TEXT,
                volume TEXT,
                issue TEXT,
                pages TEXT,
                author_surnames_text TEXT,
                author_text TEXT
            );
            CREATE UNIQUE INDEX idx_search_journal_doi ON search_journal(doi);
            CREATE VIRTUAL TABLE search_journal_fts USING fts5(
                title_norm,
                author_text,
                container_text
            );

            CREATE TABLE search_book (
                id INTEGER PRIMARY KEY,
                title TEXT,
                year TEXT,
                doi TEXT,
                publisher TEXT,
                author_surnames_text TEXT,
                author_text TEXT
            );
            CREATE INDEX idx_search_book_title ON search_book(title);
            CREATE VIRTUAL TABLE search_book_fts USING fts5(
                title_norm,
                author_text,
                container_text
            );

            CREATE TABLE search_book_chapter (
                id INTEGER PRIMARY KEY,
                title TEXT,
                year TEXT,
                doi TEXT,
                book_title TEXT,
                pages TEXT,
                author_surnames_text TEXT,
                author_text TEXT
            );
            CREATE VIRTUAL TABLE search_book_chapter_fts USING fts5(
                title_norm,
                author_text,
                container_text
            );
            """
        )

        conn.execute(
            """
            INSERT INTO crossref_works (id, doi, title, issued_year)
            VALUES (?, ?, ?, ?)
            """,
            (1001, "10.1234/test.article", "Raw Crossref Title", 2020),
        )
        conn.execute(
            """
            INSERT INTO ol_editions (id, ol_key, title, publish_year)
            VALUES (?, ?, ?, ?)
            """,
            (2001, "OL1M", "Raw OpenLibrary Title", 2021),
        )
        conn.execute(
            """
            INSERT INTO ol_authors (id, ol_key, display_name)
            VALUES (?, ?, ?)
            """,
            (3001, "OL1A", "Doe, Jane"),
        )

        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "Some title",
                "2020",
                "10.1234/test.article",
                "Journal Name",
                "5",
                "2",
                "10-20",
                "Smith",
                "smith",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (1, "some title", "smith", "journal name"),
        )
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "Some title revised",
                "2020",
                "10.1234/test.other",
                "Journal Name",
                "5",
                "2",
                "10-20",
                "Smith",
                "smith",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (2, "some title revised", "smith", "journal name"),
        )
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                7,
                "The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review",
                "2020",
                "10.1007/s11469-020-1111-1",
                "International Journal of Mental Health and Addiction",
                "18",
                "1",
                "79-93",
                "Keles;McCrae;Grealish",
                "keles mccrae grealish",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                7,
                "relationship social media use mental health disorders adolescents young adults scoping review",
                "keles mccrae grealish",
                "international journal mental health addiction",
            ),
        )
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                8,
                "The relationship between social media use and mental health outcomes in adolescents and young adults: A scoping review",
                "2020",
                "10.1007/s11469-020-1111-2",
                "International Journal of Mental Health and Addiction",
                "18",
                "1",
                "94-108",
                "Keles;McCrae;Grealish",
                "keles mccrae grealish",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                8,
                "relationship social media use mental health outcomes adolescents young adults scoping review",
                "keles mccrae grealish",
                "international journal mental health addiction",
            ),
        )
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9,
                "The relationship between social media use and mental health disorders in adolescents: A scoping review",
                "2020",
                "10.1007/s11469-020-1111-3",
                "International Journal of Mental Health and Addiction",
                "18",
                "1",
                "109-120",
                "Keles;McCrae;Grealish",
                "keles mccrae grealish",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                9,
                "relationship social media use mental health disorders adolescents scoping review",
                "keles mccrae grealish",
                "international journal mental health addiction",
            ),
        )

        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "Another title",
                "2021",
                None,
                "Example Press",
                "Doe",
                "doe",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (1, "another title", "doe", "example press"),
        )
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "Another title",
                "2020",
                None,
                "Example Press",
                "Smith",
                "smith",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (2, "another title", "smith", "example press"),
        )
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                3,
                "Risk Society",
                "1992",
                None,
                "Sage Publications",
                "Beck",
                "beck",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (3, "risk society", "beck", "sage publications"),
        )

        conn.execute(
            """
            INSERT INTO search_book_chapter (
                id, title, year, doi, book_title, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "Chapter title",
                "2024",
                None,
                "Handbook of Examples",
                "10-22",
                "Doe",
                "doe",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_chapter_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (1, "chapter title", "doe", "handbook examples"),
        )
        conn.execute(
            """
            INSERT INTO search_book_chapter (
                id, title, year, doi, book_title, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "Chapter Title",
                "2024",
                None,
                "Handbook of Examples",
                "30-40",
                "Doe",
                "doe",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_chapter_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (2, "chapter title", "doe", "handbook examples"),
        )
        conn.commit()
    finally:
        conn.close()


def _create_v2_local_db(path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE search_journal (
                id INTEGER PRIMARY KEY,
                title TEXT,
                year TEXT,
                doi TEXT,
                journal TEXT,
                volume TEXT,
                issue TEXT,
                pages TEXT,
                author_surnames_json TEXT,
                author_initials_text TEXT,
                author_text TEXT,
                author_surnames_text TEXT
            );
            CREATE UNIQUE INDEX idx_search_journal_doi ON search_journal(doi);
            CREATE TABLE search_book_chapter (
                id INTEGER PRIMARY KEY,
                title TEXT,
                year TEXT,
                doi TEXT,
                book_title TEXT,
                publisher TEXT,
                pages TEXT,
                author_surnames_json TEXT,
                author_initials_text TEXT,
                author_text TEXT,
                editor_surnames_json TEXT,
                editor_initials_text TEXT,
                editor_text TEXT,
                author_surnames_text TEXT,
                editor_surnames_text TEXT
            );
            CREATE VIRTUAL TABLE search_journal_fts USING fts5(
                title_norm,
                author_text,
                container_text
            );
            CREATE VIRTUAL TABLE search_book_chapter_fts USING fts5(
                title_norm,
                author_text,
                editor_text,
                container_text
            );
            """
        )
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages,
                author_surnames_json, author_initials_text, author_text, author_surnames_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "Über social systems",
                "2024",
                "10.1234/v2",
                "Journal",
                "1",
                "2",
                "3-10",
                '["Voß","García"]',
                "S.|N.",
                "voss garcia",
                "Voß|García",
            ),
        )
        conn.execute(
            """
            INSERT INTO search_book_chapter (
                id, title, year, doi, book_title, publisher, pages,
                author_surnames_json, author_initials_text, author_text,
                editor_surnames_json, editor_initials_text, editor_text,
                author_surnames_text, editor_surnames_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                2,
                "Chapter",
                "2023",
                None,
                "Handbook",
                "Press",
                "11-18",
                '["Möller"]',
                "H.",
                "moller",
                '["von Aichberger","Lundström"]',
                "S.|K.",
                "von aichberger lundstrom",
                "Möller",
                "von Aichberger|Lundström",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (1, "uber social systems", "voss garcia", "journal"),
        )
        conn.execute(
            "INSERT INTO search_book_chapter_fts (rowid, title_norm, author_text, editor_text, container_text) VALUES (?, ?, ?, ?, ?)",
            (2, "chapter", "moller", "von aichberger lundstrom", "handbook"),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_journal_candidate(
    db_path,
    *,
    record_id: int,
    title: str,
    title_norm: str,
    year: str,
    doi: str | None,
    journal: str,
    container_text: str,
    author_surnames_text: str,
    author_text: str,
) -> None:
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record_id,
                title,
                year,
                doi,
                journal,
                None,
                None,
                None,
                author_surnames_text,
                author_text,
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (record_id, title_norm, author_text, container_text),
        )
        conn.commit()
    finally:
        conn.close()


def _journal_result(*, doi: str | None = "10.1234/test.article") -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family="Smith", given="J.")],
        title=["Some title"],
        container_title=["Journal Name"],
        date=["2020"],
        issued_year="2020",
        volume=["5"],
        issue=["2"],
        pages=["10-20"],
        doi=[doi] if doi else [],
    )
    return ParsedReferenceResult(
        reference_id="ref_journal",
        raw_reference="Smith, J. (2020). Some title.",
        normalized_reference="Smith, J. (2020). Some title.",
        parsed_data=parsed,
        ctype="journal_article",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={
                "doi": [doi] if doi else [],
                "title": ["Some title"],
                "author": ["Smith, J."],
                "issued_year": ["2020"],
                "container_title": ["Journal Name"],
                "volume": ["5"],
                "issue": ["2"],
                "pages": ["10-20"],
            },
            lookup_query_fields={
                "title": ["Some title"],
                "author": ["Smith, J."],
                "container_title": ["Journal Name"],
                "issued_year": ["2020"],
            },
        ),
    )


def _journal_result_with_container(
    *,
    container: str,
    title: str = "Clinical treatment effects",
    year: str = "2022",
    doi: str | None = None,
) -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family="Chen", given="L.")],
        title=[title],
        container_title=[container],
        date=[year],
        issued_year=year,
        doi=[doi] if doi else [],
    )
    return ParsedReferenceResult(
        reference_id="ref_journal_abbrev",
        raw_reference=f"Chen L. {title}. {container}. {year}.",
        normalized_reference=f"Chen L. {title}. {container}. {year}.",
        parsed_data=parsed,
        ctype="journal_article",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={
                "title": [title],
                "author": ["Chen, L."],
                "issued_year": [year],
                "container_title": [container],
                "doi": [doi] if doi else [],
            },
            lookup_query_fields={
                "title": [title],
                "author": ["Chen, L."],
                "issued_year": [year],
                "container_title": [container],
            },
        ),
    )


def test_journal_abbreviation_match_accepts_prefix_tokens_and_initialisms():
    assert journal_abbreviation_match("Ann Intern Med", "Annals of Internal Medicine")
    assert journal_abbreviation_match("J Am Med Assoc", "Journal of the American Medical Association")
    assert journal_abbreviation_match("AEM", "Annals of Emergency Medicine")
    assert not journal_abbreviation_match("Ann Intern Med", "Annals of Emergency Medicine")


def test_provider_normalize_text_strips_possessives_for_fts_terms():
    assert provider_normalize_text("JAMA’s editor") == "jama editor"
    assert provider_normalize_text("JAMA's editor") == "jama editor"


def _werfhorst_result() -> ParsedReferenceResult:
    title = (
        "Scarcity and Abundance: Reconciling Trends in the Effects of Education on Social Class "
        "and Earnings in Great Britain 1972-2003"
    )
    container = "European Sociological Review"
    parsed = ParsedReferenceData(
        type="article-journal",
        author=[ParsedName(family="Werfhorst", given="H.G.")],
        title=[title],
        container_title=[container],
        date=["2007"],
        issued_year="2007",
        volume=["23"],
        pages=["239-261"],
    )
    return ParsedReferenceResult(
        reference_id="ref_werfhorst",
        raw_reference=(
            "Van de Werfhorst, H.G. (2007) Scarcity and Abundance: Reconciling Trends in the "
            "Effects of Education on Social Class and Earnings in Great Britain 1972-2003. "
            "European Sociologi-cal Review 23, 239-261."
        ),
        normalized_reference=(
            "Van de Werfhorst, H.G. (2007) Scarcity and Abundance: Reconciling Trends in the "
            "Effects of Education on Social Class and Earnings in Great Britain 1972-2003. "
            "European Sociologi-cal Review 23, 239-261."
        ),
        parsed_data=parsed,
        ctype="journal_article",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={
                "title": [title],
                "author": ["Werfhorst, H.G."],
                "issued_year": ["2007"],
                "container_title": [container],
                "volume": ["23"],
                "pages": ["239-261"],
            },
            lookup_query_fields={
                "title": [title],
                "author": ["Werfhorst, H.G."],
                "issued_year": ["2007"],
                "container_title": [container],
            },
            lookup_confidence_basis=[title, container, "2007"],
        ),
    )


def _book_result(*, year: str = "2021", author: str = "Doe") -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family=author, given="J.")],
        title=["Another title"],
        publisher=["Example Press"],
        date=[year],
        issued_year=year,
    )
    return ParsedReferenceResult(
        reference_id=f"ref_book_{year}_{author.lower()}",
        raw_reference="Doe, J. (2021). Another title.",
        normalized_reference="Doe, J. (2021). Another title.",
        parsed_data=parsed,
        ctype="book",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="openlibrary",
            lookup_key_fields={
                "title": ["Another title"],
                "author": [f"{author}, J."],
                "issued_year": [year],
                "publisher": ["Example Press"],
            },
            lookup_query_fields={
                "title": ["Another title"],
                "author": [f"{author}, J."],
                "issued_year": [year],
                "publisher": ["Example Press"],
            },
        ),
    )


def _book_with_subtitle_result(
    *,
    title: str = "Risk Society: Towards a New Modernity",
    year: str = "1992",
    author: str = "Beck",
    publisher: str = "Sage",
) -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family=author, given="U.")],
        title=[title],
        publisher=[publisher],
        date=[year],
        issued_year=year,
    )
    return ParsedReferenceResult(
        reference_id=f"ref_book_subtitle_{year}_{author.lower()}",
        raw_reference=f"{author}, U. ({year}). {title}.",
        normalized_reference=f"{author}, U. ({year}). {title}.",
        parsed_data=parsed,
        ctype="book",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="openlibrary",
            lookup_key_fields={
                "title": [title],
                "author": [f"{author}, U."],
                "issued_year": [year],
                "publisher": [publisher],
            },
            lookup_query_fields={
                "title": [title],
                "author": [f"{author}, U."],
                "issued_year": [year],
                "publisher": [publisher],
            },
        ),
    )


def _book_chapter_result() -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family="Doe", given="J.")],
        title=["Chapter title"],
        container_title=["Handbook of Examples"],
        date=["2024"],
        issued_year="2024",
        pages=["10-22"],
    )
    return ParsedReferenceResult(
        reference_id="ref_chapter",
        raw_reference="Doe, J. (2024). Chapter title.",
        normalized_reference="Doe, J. (2024). Chapter title.",
        parsed_data=parsed,
        ctype="book_chapter",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="openlibrary",
            lookup_key_fields={
                "chapter_title": ["Chapter title"],
                "book_title": ["Handbook of Examples"],
                "author": ["Doe, J."],
                "issued_year": ["2024"],
                "pages": ["10-22"],
            },
            lookup_query_fields={
                "chapter_title": ["Chapter title"],
                "book_title": ["Handbook of Examples"],
                "author": ["Doe, J."],
                "issued_year": ["2024"],
            },
        ),
    )


def _journal_broad_result() -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family="Smith", given="J.")],
        title=["Some title"],
        container_title=["Journal Name"],
    )
    return ParsedReferenceResult(
        reference_id="ref_journal_broad",
        raw_reference="Smith, J. Some title.",
        normalized_reference="Smith, J. Some title.",
        parsed_data=parsed,
        ctype="journal_article",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={
                "title": ["Some title"],
            },
            lookup_query_fields={
                "title": ["Some title"],
                "author": ["Smith, J."],
                "container_title": ["Journal Name"],
            },
        ),
    )


def _journal_reference7_style_result(*, doi: str = "10.1007/s11469-018-0004-9") -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[
            ParsedName(family="Keles", given="B."),
            ParsedName(family="McCrae", given="N."),
            ParsedName(family="Grealish", given="A."),
        ],
        title=[
            "The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review"
        ],
        container_title=["International Journal of Mental Health and Addiction"],
        date=["2020"],
        issued_year="2020",
        doi=[doi],
    )
    return ParsedReferenceResult(
        reference_id="ref_journal_reference7_style",
        raw_reference="Keles, B., McCrae, N., & Grealish, A. (2020). The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review.",
        normalized_reference="Keles, B., McCrae, N., & Grealish, A. (2020). The relationship between social media use and mental health disorders in adolescents and young adults: A scoping review.",
        parsed_data=parsed,
        ctype="journal_article",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={
                "doi": [doi],
                "title": parsed.title,
                "author": ["Keles, B.", "McCrae, N.", "Grealish, A."],
                "issued_year": ["2020"],
                "container_title": ["International Journal of Mental Health and Addiction"],
            },
            lookup_query_fields={
                "title": parsed.title,
                "author": ["Keles, B.", "McCrae, N.", "Grealish, A."],
                "issued_year": ["2020"],
                "container_title": ["International Journal of Mental Health and Addiction"],
            },
        ),
    )


def _journal_short_broad_doi_miss_result(*, doi: str = "10.1000/missing-short") -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=[ParsedName(family="Keles", given="B.")],
        title=["Social media review"],
        container_title=["International Journal of Mental Health and Addiction"],
        date=["2020"],
        issued_year="2020",
        doi=[doi],
    )
    return ParsedReferenceResult(
        reference_id="ref_journal_short_broad",
        raw_reference="Keles, B. (2020). Social media review.",
        normalized_reference="Keles, B. (2020). Social media review.",
        parsed_data=parsed,
        ctype="journal_article",
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref",
            lookup_key_fields={
                "doi": [doi],
                "title": parsed.title,
                "author": ["Keles, B."],
                "issued_year": ["2020"],
                "container_title": ["International Journal of Mental Health and Addiction"],
            },
            lookup_query_fields={
                "title": parsed.title,
                "author": ["Keles, B."],
                "issued_year": ["2020"],
                "container_title": ["International Journal of Mental Health and Addiction"],
            },
        ),
    )


def test_phase4_doi_exact_hit_returns_provisional_match(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.top_candidates[0] == result.best_candidate
    assert result.best_candidate.doi == "10.1234/test.article"
    assert result.strategy_used == "doi_exact"
    assert result.lookup_trace.doi_attempted is True
    assert result.lookup_trace.strategies_attempted == []
    assert result.lookup_trace.doi_hit_quality == "clean"
    assert result.lookup_trace.corroboration_triggered is False
    assert result.best_candidate.match_signals.doi_match_type == "exact"
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"
    assert "phase4_doi_hit_clean" in result.reasons


def test_provider_normalize_text_ascii_folds_special_characters():
    assert provider_normalize_text("Voß") == "voss"
    assert provider_normalize_text("Andreβ") == "andress"
    assert provider_normalize_text("García") == "garcia"
    assert provider_normalize_text("Lundström") == "lundstrom"
    assert provider_normalize_text("Möller") == "moller"
    assert provider_normalize_text("von Aichberger") == "von aichberger"


def test_provider_hydrates_v2_initials_and_editors(tmp_path):
    db_path = tmp_path / "local_v2.db"
    _create_v2_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    journal = provider.lookup_by_doi(
        ctype="journal_article",
        doi="10.1234/v2",
        max_candidates=3,
    )[0]
    chapter = provider.search_candidates(
        ctype="book_chapter",
        config=Phase4SearchConfig(
            name="chapter_probe",
            title_terms=["chapter"],
            author_terms=["moller"],
            container_terms=["handbook"],
        ),
        max_candidates=3,
    )[0]

    assert journal.authors == ["Voß", "García"]
    assert journal.author_initials == ["S.", "N."]
    assert chapter.authors == ["Möller"]
    assert chapter.author_initials == ["H."]
    assert chapter.editors == ["von Aichberger", "Lundström"]
    assert chapter.editor_initials == ["S.", "K."]


def test_provider_handles_older_schema_without_initials_columns(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    candidate = provider.lookup_by_doi(
        ctype="journal_article",
        doi="10.1234/test.article",
        max_candidates=3,
    )[0]

    assert candidate.authors == ["Smith"]
    assert candidate.author_initials == [""]
    assert candidate.editors == []
    assert candidate.editor_initials == []


def test_phase4_doi_miss_falls_back_to_title_search(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_result(doi="10.9999/notfound"),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.title == "Some title"
    assert result.lookup_trace.doi_attempted is True
    assert result.lookup_trace.doi_miss is True
    assert result.lookup_trace.strategies_attempted == ["journal_title_year_exact"]
    assert result.best_candidate.match_signals.doi_match_type == "mismatch"
    assert result.best_candidate.source_strategy == "journal_title_year_exact"
    assert "doi_miss_title_year_recovery" in result.reasons


def test_phase4_book_without_doi_uses_fallback_strategy(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _book_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_type == "search_book"
    assert result.best_candidate.issued_year == "2021"
    assert result.lookup_trace.strategies_attempted == ["book_main_title_author_year_exact"]
    assert result.strategy_used == "book_main_title_author_year_exact"
    assert result.top_candidates[0] == result.best_candidate


def test_phase4_book_can_recover_via_title_year_when_author_is_wrong(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_book_title_year",
            raw_reference="Wrong, J. (2021). Another title.",
            normalized_reference="Wrong, J. (2021). Another title.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Wrong", given="J.")],
                title=["Another title"],
                date=["2021"],
                issued_year="2021",
            ),
            ctype="book",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "title": ["Another title"],
                    "author": ["Wrong, J."],
                    "issued_year": ["2021"],
                },
                lookup_query_fields={
                    "title": ["Another title"],
                    "author": ["Wrong, J."],
                    "issued_year": ["2021"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:1"
    assert result.strategy_used == "book_main_title_year_exact"
    assert result.lookup_trace.strategies_attempted[0] == "book_main_title_author_year_exact"


def test_phase4_book_can_recover_via_near_year_fallback(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_book_near_year",
            raw_reference="Doe, J. (2022). Another title.",
            normalized_reference="Doe, J. (2022). Another title.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Doe", given="J.")],
                title=["Another title"],
                date=["2022"],
                issued_year="2022",
            ),
            ctype="book",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "title": ["Another title"],
                    "author": ["Doe, J."],
                    "issued_year": ["2022"],
                },
                lookup_query_fields={
                    "title": ["Another title"],
                    "author": ["Doe, J."],
                    "issued_year": ["2022"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:1"
    assert result.strategy_used == "book_title2_author_year_near"
    assert "book_title2_author_year_near" in result.lookup_trace.strategies_attempted
    assert result.best_candidate.match_signals.year_match_type == "near"


def test_phase4_book_subtitle_can_match_main_title_record(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _book_with_subtitle_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:3"
    assert result.strategy_used == "book_main_title_author_year_exact"
    assert result.lookup_trace.strategies_attempted == ["book_main_title_author_year_exact"]
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"


def test_phase4_book_main_title_can_match_candidate_with_subtitle(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                40,
                "Individualization: Institutionalized individualism and its social and political consequences",
                "2002",
                None,
                "Sage",
                "Beck;Beck-Gernsheim",
                "beck beck gernsheim",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                40,
                "individualization institutionalized individualism social political consequences",
                "beck beck gernsheim",
                "sage",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        _book_with_subtitle_result(
            title="Individualization",
            year="2002",
            author="Beck",
            publisher="Sage",
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:40"
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"


def test_phase4_book_publisher_match_ignores_location_prefix_and_imprint_words(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _book_with_subtitle_result(
            title="Risk Society",
            year="1992",
            author="Beck",
            publisher="London: Sage",
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:3"
    assert result.best_candidate.match_signals.container_match == "yes"
    assert "container_or_publisher_match" in result.best_candidate.match_reasons


def test_phase4_book_author_can_fall_back_to_raw_leading_surname(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_book_raw_author",
            raw_reference="Beck, U. (1992). Risk Society. London: Other Publisher.",
            normalized_reference="Beck, U. (1992). Risk Society. London: Other Publisher.",
            parsed_data=ParsedReferenceData(
                title=["Risk Society"],
                publisher=["Other Publisher"],
                date=["1992"],
                issued_year="1992",
            ),
            ctype="book",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "title": ["Risk Society"],
                    "issued_year": ["1992"],
                    "publisher": ["Other Publisher"],
                },
                lookup_query_fields={
                    "title": ["Risk Society"],
                    "issued_year": ["1992"],
                    "publisher": ["Other Publisher"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:3"
    assert result.best_candidate.match_signals.author_match_strength == "strong"
    assert result.best_candidate.match_signals.container_match == "no"
    assert "author_exact_overlap" in result.best_candidate.match_reasons


def test_phase4_book_author_surnames_allow_one_letter_variant(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                41,
                "De Nieuwe Sociale Kwesties",
                "2003",
                None,
                "Garant",
                "Cantillon",
                "cantillon",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (41, "de nieuwe sociale kwesties", "cantillon", "garant"),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_book_author_variant",
            raw_reference="Cantillion, B. (2003). De Nieuwe Sociale Kwesties. Antwerpen: Garant.",
            normalized_reference="Cantillion, B. (2003). De Nieuwe Sociale Kwesties. Antwerpen: Garant.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Cantillion", given="B.")],
                title=["De Nieuwe Sociale Kwesties"],
                publisher=["Garant"],
                date=["2003"],
                issued_year="2003",
            ),
            ctype="book",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "title": ["De Nieuwe Sociale Kwesties"],
                    "author": ["Cantillion, B."],
                    "issued_year": ["2003"],
                    "publisher": ["Garant"],
                },
                lookup_query_fields={
                    "title": ["De Nieuwe Sociale Kwesties"],
                    "author": ["Cantillion, B."],
                    "issued_year": ["2003"],
                    "publisher": ["Garant"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:41"
    assert result.best_candidate.match_signals.author_match_strength == "strong"


def test_phase4_book_chapter_uses_type_specific_strategy(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _book_chapter_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_type == "search_book_chapter"
    assert result.lookup_trace.strategies_attempted == ["chapter_main_title_author_year_near"]


def test_phase4_book_chapter_book_level_fallback_uses_editors_for_author_signal():
    class BookFallbackProvider:
        def lookup_by_doi(self, *, ctype, doi, max_candidates):
            return []

        def search_candidates(self, *, ctype, config, max_candidates):
            if "book_title" not in config.name:
                return []
            return [
                LocalDbCandidate(
                    record_id="search_book:10",
                    record_type="search_book",
                    record_granularity="book",
                    title="Handbook of Research on Science Education",
                    authors=["Abell", "Lederman"],
                    issued_year="2007",
                    publisher="Lawrence Erlbaum Associates",
                    source_table="search_book",
                    source_strategy=config.name,
                )
            ]

    parsed = ParsedReferenceData(
        author=[ParsedName(family="Roberts", given="D. A.")],
        editor=[ParsedName(family="Abell", given="S. K."), ParsedName(family="Lederman", given="N. G.")],
        title=["Scientific literacy/science literacy"],
        container_title=["Handbook of Research on Science Education"],
        issued_year="2007",
        pages=["729-780"],
    )
    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_chapter_book_fallback",
            raw_reference="Roberts, D. A. (2007). Scientific literacy/science literacy. In S. K. Abell & N. G. Lederman (Eds.), Handbook of research on science education (pp. 729-780). Lawrence Erlbaum Associates.",
            normalized_reference="Roberts, D. A. (2007). Scientific literacy/science literacy.",
            parsed_data=parsed,
            ctype="book_chapter",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "chapter_title": ["Scientific literacy/science literacy"],
                    "book_title": ["Handbook of Research on Science Education"],
                    "author": ["Roberts"],
                    "issued_year": ["2007"],
                },
                lookup_query_fields={
                    "chapter_title": ["Scientific literacy/science literacy"],
                    "book_title": ["Handbook of Research on Science Education"],
                    "author": ["Roberts"],
                    "issued_year": ["2007"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(provider=BookFallbackProvider()),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_granularity == "book"
    assert result.best_candidate.match_signals.author_match_strength == "strong"


def test_phase4_book_chapter_book_level_fallback_ignores_chapter_title_for_title_score():
    class BookFallbackProvider:
        def lookup_by_doi(self, *, ctype, doi, max_candidates):
            return []

        def search_candidates(self, *, ctype, config, max_candidates):
            if config.target_tables != ["search_book"]:
                return []
            return [
                LocalDbCandidate(
                    record_id="search_book:34",
                    record_type="search_book",
                    record_granularity="book",
                    title="Modernized Poverty: Individualization, Concentration and Embeddedness",
                    authors=["Berghman"],
                    issued_year="2000",
                    source_table="search_book",
                    source_strategy=config.name,
                )
            ]

    parsed = ParsedReferenceData(
        author=[
            ParsedName(family="Snel", given="E."),
            ParsedName(family="Engbersen", given="G."),
        ],
        editor=[ParsedName(family="Berghman", given="J.")],
        title=["Modernized Poverty: Individualization, Concentration and Embeddedness"],
        container_title=["Social Security in Transition"],
        issued_year="2000",
        pages=["63-76"],
    )
    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_chapter_title_named_like_book",
            raw_reference=(
                "Snel, E. en G. Engbersen (2000) Modernized Poverty: Individualization, "
                "Concentration and Embeddedness. In: J. Berghman (red.) Social Security in Transition."
            ),
            normalized_reference="Snel, E. en G. Engbersen (2000) Modernized Poverty.",
            parsed_data=parsed,
            ctype="book_chapter",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "chapter_title": [
                        "Modernized Poverty: Individualization, Concentration and Embeddedness"
                    ],
                    "book_title": ["Social Security in Transition"],
                    "author": ["Snel, E.", "Engbersen, G."],
                    "editor": ["Berghman, J."],
                    "issued_year": ["2000"],
                    "pages": ["63-76"],
                },
                lookup_query_fields={
                    "chapter_title": [
                        "Modernized Poverty: Individualization, Concentration and Embeddedness"
                    ],
                    "book_title": ["Social Security in Transition"],
                    "author": ["Snel, E.", "Engbersen, G."],
                    "editor": ["Berghman, J."],
                    "issued_year": ["2000"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(provider=BookFallbackProvider()),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_granularity == "book"
    assert result.best_candidate.match_signals.title_match_strength == "none"
    assert result.best_candidate.match_signals.container_match == "no"
    assert "title_exact_or_near_exact" not in result.best_candidate.match_reasons


def test_phase4_book_chapter_book_level_fallback_queries_book_title_and_editor(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                42,
                "Empirical Poverty Research in Comparative Perspective",
                "1998",
                None,
                "Ashgate",
                "Andreß",
                "andress",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                42,
                "empirical poverty research comparative perspective",
                "andress",
                "ashgate",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_kronauer_chapter_book_fallback",
            raw_reference=(
                "Kronauer, M. (1998) 'Social Exclusion' and 'Underclass' - New concepts for the "
                "analysis of poverty. In: H.J. Andreß (red.) Empirical Poverty Research in "
                "Comparative Perspective. Aldershot: Ashgate, 51-75."
            ),
            normalized_reference="Kronauer, M. (1998). Social Exclusion and Underclass.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Kronauer", given="M.")],
                editor=[ParsedName(family="Andreß", given="H.J.")],
                title=["Social Exclusion and Underclass - New concepts for the analysis of poverty"],
                container_title=["Empirical Poverty Research in Comparative Perspective"],
                date=["1998"],
                issued_year="1998",
                pages=["51-75"],
            ),
            ctype="book_chapter",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "chapter_title": [
                        "Social Exclusion and Underclass - New concepts for the analysis of poverty"
                    ],
                    "book_title": ["Empirical Poverty Research in Comparative Perspective"],
                    "author": ["Kronauer, M."],
                    "editor": ["Andreß, H.J."],
                    "issued_year": ["1998"],
                    "pages": ["51-75"],
                },
                lookup_query_fields={
                    "chapter_title": [
                        "Social Exclusion and Underclass - New concepts for the analysis of poverty"
                    ],
                    "book_title": ["Empirical Poverty Research in Comparative Perspective"],
                    "author": ["Kronauer, M."],
                    "editor": ["Andreß, H.J."],
                    "issued_year": ["1998"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:42"
    assert result.best_candidate.record_granularity == "book"
    assert result.strategy_used == "chapter_book_title_editor_year_near"
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"
    assert result.best_candidate.match_signals.author_match_strength == "strong"


def test_phase4_book_chapter_book_level_fallback_recovers_inline_editor_marker(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                43,
                "Empirical Poverty Research in Comparative Perspective",
                "1998",
                None,
                "Ashgate",
                "Andreß",
                "andress",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                43,
                "empirical poverty research comparative perspective",
                "andress",
                "ashgate",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_kronauer_chapter_contaminated_book_title",
            raw_reference=(
                "Kronauer, M. (1998) 'Social Exclusion' and 'Underclass' - New concepts for the "
                "analysis of poverty. In: H.J. Andreβ (red.) Empirical Poverty Research in "
                "Comparative Perspective. Aldershot: Ashgate, 51-75."
            ),
            normalized_reference="Kronauer, M. (1998). Social Exclusion and Underclass.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Kronauer", given="M.")],
                title=["Social Exclusion and Underclass - New concepts for the analysis of poverty"],
                container_title=[
                    "H.J. Andreβ (red.) Empirical Poverty Research in Comparative Perspective"
                ],
                date=["1998"],
                issued_year="1998",
                pages=["51-75"],
            ),
            ctype="book_chapter",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "chapter_title": [
                        "Social Exclusion and Underclass - New concepts for the analysis of poverty"
                    ],
                    "book_title": [
                        "H.J. Andreβ (red.) Empirical Poverty Research in Comparative Perspective"
                    ],
                    "author": ["Kronauer, M."],
                    "issued_year": ["1998"],
                    "pages": ["51-75"],
                },
                lookup_query_fields={
                    "chapter_title": [
                        "Social Exclusion and Underclass - New concepts for the analysis of poverty"
                    ],
                    "book_title": [
                        "H.J. Andreβ (red.) Empirical Poverty Research in Comparative Perspective"
                    ],
                    "author": ["Kronauer, M."],
                    "issued_year": ["1998"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:43"
    assert result.strategy_used == "chapter_book_title_editor_year_near"
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"
    assert result.best_candidate.match_signals.author_match_strength == "strong"


def test_phase4_book_chapter_subtitle_can_match_main_title_record(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_chapter_subtitle",
            raw_reference="Doe, J. (2024). Chapter Title: With Subtitle.",
            normalized_reference="Doe, J. (2024). Chapter Title: With Subtitle.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Doe", given="J.")],
                title=["Chapter Title: With Subtitle"],
                container_title=["Handbook of Examples"],
                date=["2024"],
                issued_year="2024",
            ),
            ctype="book_chapter",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "chapter_title": ["Chapter Title: With Subtitle"],
                    "book_title": ["Handbook of Examples"],
                    "author": ["Doe, J."],
                    "issued_year": ["2024"],
                },
                lookup_query_fields={
                    "chapter_title": ["Chapter Title: With Subtitle"],
                    "book_title": ["Handbook of Examples"],
                    "author": ["Doe, J."],
                    "issued_year": ["2024"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id in {"search_book_chapter:1", "search_book_chapter:2"}
    assert result.strategy_used == "chapter_main_title_author_year_near"


def test_metadata_match_type_treats_normalized_page_ranges_as_exact():
    parsed = ParsedReferenceData(
        volume=["18"],
        issue=["2"],
        pages=["121–32"],
    )
    candidate = LocalDbCandidate(
        record_id="search_journal:range",
        record_type="search_journal",
        volume="18",
        issue="2",
        pages="121-132",
    )

    assert matching_service._metadata_match_type(parsed, candidate) == "exact"
    assert matching_service._metadata_score(parsed, candidate) == 1.0


def test_metadata_match_type_accepts_page_range_extension_as_partial():
    parsed = ParsedReferenceData(
        volume=["112"],
        issue=["5"],
        pages=["930-938"],
    )
    candidate = LocalDbCandidate(
        record_id="search_journal:extended_pages",
        record_type="search_journal",
        volume="112",
        issue="5",
        pages="930-938.e1",
    )

    # Volume and issue match exactly; only the pages carry the trailing
    # electronic-continuation suffix. The metadata label should be "partial"
    # and the weighted score should reflect that volume/issue dominate.
    assert matching_service._metadata_match_type(parsed, candidate) == "partial"
    score = matching_service._metadata_score(parsed, candidate)
    assert 0.9 < score < 1.0


def test_metadata_match_type_downranks_full_mismatch():
    parsed = ParsedReferenceData(
        volume=["5"],
        issue=["2"],
        pages=["10-20"],
    )
    candidate = LocalDbCandidate(
        record_id="search_journal:mismatch",
        record_type="search_journal",
        volume="6",
        issue="3",
        pages="11-25",
    )

    assert matching_service._metadata_match_type(parsed, candidate) == "mismatch"
    assert matching_service._metadata_score(parsed, candidate) == 0.0


def test_doi_prefix_equivalent_matches_truncated_stem():
    from reference_gen2.reference_matching import doi_prefix_equivalent

    # Student reference only captured the DOI stem; database has the full DOI.
    assert doi_prefix_equivalent(
        "10.1111/1467-9566.",
        "10.1111/1467-9566.13038",
    )
    # Order of arguments does not matter.
    assert doi_prefix_equivalent(
        "10.1111/1467-9566.13038",
        "10.1111/1467-9566.",
    )
    # Extensions at a DOI separator boundary still match when the shorter
    # value does not itself end with a separator.
    assert doi_prefix_equivalent(
        "10.1234/abc.def",
        "10.1234/abc.def/extra",
    )
    # Arbitrary alphanumeric extension is NOT a prefix match.
    assert not doi_prefix_equivalent(
        "10.1234/abc",
        "10.1234/abcd",
    )
    # Short / malformed DOIs are rejected.
    assert not doi_prefix_equivalent("10.1/a", "10.1/a.b")
    # Identical DOIs are handled by the exact/equivalent branches, so the
    # prefix check returns False for them.
    assert not doi_prefix_equivalent("10.1234/same", "10.1234/same")


def test_phase4_doi_prefix_hit_still_promotes_match(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    # Candidate DOI in the local DB is the fully-qualified DOI; the parsed
    # reference only captured the truncated stem. Phase 4 should still
    # recognise them as equivalent and promote the match.
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages,
                author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                9101,
                "Some title",
                "2020",
                "10.1111/1467-9566.13038",
                "Journal Name",
                "5",
                "2",
                "10-20",
                "smith",
                "Smith",
            ),
        )
        conn.execute(
            "INSERT INTO search_journal_fts (rowid, title_norm, author_text, container_text)"
            " VALUES (?, ?, ?, ?)",
            (9101, "some title", "smith", "journal name"),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        _journal_result(doi="10.1111/1467-9566."),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.doi == "10.1111/1467-9566.13038"
    assert result.best_candidate.match_signals.doi_match_type == "equivalent"
    assert "doi_prefix_equivalent_match" in result.best_candidate.match_reasons



def test_phase4_book_chapter_book_container_matches_main_title_with_dot_delimiter(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                50,
                "Effecten van armoede",
                "1998",
                None,
                None,
                "Engbersen;Vrooman;Snel",
                "engbersen vrooman snel",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                50,
                "effecten van armoede",
                "engbersen vrooman snel",
                "",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_armoedecarrieres",
            raw_reference=(
                "Muffels, R., E. Snel, D. Fouarge en S. Karyotis (1998) Armoedecarrieres. "
                "Dynamiek en Determinanten van Armoede. In: G. Engbersen, C. Vrooman en E. Snel "
                "(red.) Effecten van Armoede. Derde Jaarrapport Armoede en Sociale Uitsluiting."
            ),
            normalized_reference="Muffels et al. (1998). Armoedecarrieres.",
            parsed_data=ParsedReferenceData(
                author=[
                    ParsedName(family="Muffels", given="R."),
                    ParsedName(family="Snel", given="E."),
                    ParsedName(family="Fouarge", given="D."),
                    ParsedName(family="Karyotis", given="S."),
                ],
                editor=[
                    ParsedName(family="Engbersen", given="G."),
                    ParsedName(family="Vrooman", given="C."),
                    ParsedName(family="Snel", given="E."),
                ],
                title=["Armoedecarrieres. Dynamiek en Determinanten van Armoede"],
                container_title=["Effecten van Armoede. Derde Jaarrapport Armoede en Sociale Uitsluiting"],
                date=["1998"],
                issued_year="1998",
                pages=["45-65"],
            ),
            ctype="book_chapter",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "chapter_title": ["Armoedecarrieres. Dynamiek en Determinanten van Armoede"],
                    "book_title": ["Effecten van Armoede. Derde Jaarrapport Armoede en Sociale Uitsluiting"],
                    "editor": ["Engbersen, G.", "Vrooman, C.", "Snel, E."],
                    "issued_year": ["1998"],
                },
                lookup_query_fields={
                    "chapter_title": ["Armoedecarrieres. Dynamiek en Determinanten van Armoede"],
                    "book_title": ["Effecten van Armoede. Derde Jaarrapport Armoede en Sociale Uitsluiting"],
                    "editor": ["Engbersen, G.", "Vrooman, C.", "Snel, E."],
                    "issued_year": ["1998"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:50"
    assert result.best_candidate.match_signals.container_match == "yes"
    assert "container_or_publisher_match" in result.best_candidate.match_reasons


def test_phase4_container_main_title_dot_rule_requires_uppercase_or_digit_after_dot():
    assert (
        matching_service._container_text_similarity(
            "Effecten van Armoede. Derde Jaarrapport Armoede en Sociale Uitsluiting",
            "Effecten van armoede",
            publisher_like=False,
        )
        == 1.0
    )
    assert (
        matching_service._container_text_similarity(
            "Effecten van armoede. derde jaarrapport",
            "Effecten van armoede",
            publisher_like=False,
        )
        < 1.0
    )


def test_phase4_ineligible_ctype_returns_skipped(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    parsed = ParsedReferenceData(title=["Voorbeeldpagina"])
    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_web",
            raw_reference="Movisie. Voorbeeldpagina.",
            normalized_reference="Movisie. Voorbeeldpagina.",
            parsed_data=parsed,
            ctype="webpage",
            match_preparation=MatchPreparation(
                eligible_for_db_match=False,
                match_target="none",
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "skipped"
    assert "phase4_ineligible_ctype" in result.reasons


def test_phase4_missing_required_fields_returns_structured_skip(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    parsed = ParsedReferenceData(
        author=[ParsedName(family="Doe", given="J.")],
        title=["Another title"],
    )
    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_partial_book",
            raw_reference="Doe. Another title.",
            normalized_reference="Doe. Another title.",
            parsed_data=parsed,
            ctype="book",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={"title": ["Another title"], "author": ["Doe, J."]},
                lookup_query_fields={"title": ["Another title"], "author": ["Doe, J."]},
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "skipped"
    assert "phase4_insufficient_lookup_fields" in result.reasons
    assert "phase4_missing_field:issued_year" in result.reasons


def test_phase4_multiple_candidates_are_ranked_deterministically(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_result(doi=None),
        config=Phase4RuntimeConfig(
            local_db_path=str(db_path),
            enable_relaxed_queries=True,
            max_fallback_strategies=3,
        ),
    )

    assert len(result.candidates) >= 2
    assert len(result.top_candidates) == 2
    assert result.best_candidate is not None
    assert result.top_candidates[0] == result.best_candidate
    assert result.best_candidate.record_id == "search_journal:1"
    assert result.candidates[0].ordering_score >= result.candidates[1].ordering_score
    assert result.top_candidates[1].record_id == "search_journal:2"
    assert result.best_candidate.match_signals.year_match_type == "exact"
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"
    assert result.lookup_trace.second_candidate_retained is True


def test_phase4_finds_werfhorst_after_soft_hyphen_container_repair(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    title = (
        "Scarcity and Abundance: Reconciling Trends in the Effects of Education on Social Class "
        "and Earnings in Great Britain 1972-2003"
    )
    _insert_journal_candidate(
        db_path,
        record_id=30,
        title=title,
        title_norm=(
            "scarcity abundance reconciling trends effects education social class earnings "
            "great britain 1972 2003"
        ),
        year="2007",
        doi="10.1093/esr/jcl024",
        journal="European Sociological Review",
        container_text="european sociological review",
        author_surnames_text="Werfhorst",
        author_text="werfhorst",
    )

    result = match_reference(
        _werfhorst_result(),
        config=Phase4RuntimeConfig(
            local_db_path=str(db_path),
            enable_relaxed_queries=True,
            max_fallback_strategies=6,
        ),
    )

    assert result.status == "matched_provisional"
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_journal:30"
    assert result.best_candidate.container_title == "European Sociological Review"
    assert result.best_candidate.match_signals.container_match == "yes"


def test_sqlite_provider_maps_rows_to_normalized_candidate(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    candidates = provider.lookup_by_doi(
        ctype="journal_article",
        doi="10.1234/test.article",
        max_candidates=5,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.record_id == "search_journal:1"
    assert candidate.record_granularity == "article"
    assert candidate.container_title == "Journal Name"
    assert candidate.authors == ["Smith"]
    assert candidate.raw_adapter_data["title"] == "Some title"


def test_sqlite_provider_uses_search_layer_not_raw_source_tables(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    candidates = provider.lookup_by_doi(
        ctype="journal_article",
        doi="10.1234/test.article",
        max_candidates=5,
    )

    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.record_id == "search_journal:1"
    assert candidate.title == "Some title"
    assert candidate.title != "Raw Crossref Title"


def test_sqlite_provider_exposes_book_and_chapter_granularity(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    book_candidates = provider.search_candidates(
        ctype="book",
        config=Phase4SearchConfig(
            name="book_title_year_exact",
            title_terms=["another", "title"],
            fielded_terms={"title_norm": ["another", "title"]},
            target_tables=["search_book"],
            year="2021",
            limit=5,
        ),
        max_candidates=5,
    )
    chapter_candidates = provider.search_candidates(
        ctype="book_chapter",
        config=Phase4SearchConfig(
            name="chapter_title_year_exact",
            title_terms=["chapter", "title"],
            fielded_terms={"title_norm": ["chapter", "title"]},
            target_tables=["search_book_chapter"],
            year="2024",
            limit=5,
        ),
        max_candidates=5,
    )

    assert book_candidates
    assert chapter_candidates
    assert book_candidates[0].record_granularity == "book"
    assert chapter_candidates[0].record_granularity == "chapter"


def test_sqlite_provider_normalizes_prefixed_doi_for_exact_lookup(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    candidates = provider.lookup_by_doi(
        ctype="journal_article",
        doi="https://doi.org/10.1234/test.article",
        max_candidates=5,
    )

    assert len(candidates) == 1
    assert candidates[0].record_id == "search_journal:1"


def test_sqlite_provider_ignores_missing_fts_fields_per_table(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    provider = SqliteLocalDbProvider(str(db_path))

    candidates = provider.search_candidates(
        ctype="book",
        config=Phase4SearchConfig(
            name="title_author_year",
            title_terms=["another", "title"],
            author_terms=["doe"],
            container_terms=["example", "press"],
            fielded_terms={
                "title_norm": ["another", "title"],
                "author_text": ["doe"],
                "container_text": ["example", "press"],
            },
            target_tables=["search_book"],
            year="2021",
        ),
        max_candidates=5,
    )

    assert len(candidates) == 1
    assert candidates[0].record_id == "search_book:1"


def test_sqlite_provider_applies_container_text_when_available(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    _insert_journal_candidate(
        db_path,
        record_id=20,
        title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
        title_norm="temporal associations screen time anxiety symptoms adolescents",
        year="2019",
        doi="10.1177/0706743719885486",
        journal="The Canadian Journal of Psychiatry",
        container_text="canadian journal psychiatry",
        author_surnames_text="Boers;Afzali;Conrod",
        author_text="boers afzali conrod",
    )
    _insert_journal_candidate(
        db_path,
        record_id=21,
        title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
        title_norm="temporal associations screen time anxiety symptoms adolescents",
        year="2019",
        doi="10.2196/16104",
        journal="JMIR Medical Informatics",
        container_text="jmir medical informatics",
        author_surnames_text="Woodworth;Farooq;Gorelick",
        author_text="woodworth farooq gorelick",
    )
    provider = SqliteLocalDbProvider(str(db_path))

    candidates = provider.search_candidates(
        ctype="journal_article",
        config=Phase4SearchConfig(
            name="journal_title_container_year_exact",
            title_terms=["temporal", "associations", "screen", "time"],
            container_terms=["canadian", "psychiatry"],
            fielded_terms={
                "title_norm": ["temporal", "associations", "screen", "time"],
                "container_text": ["canadian", "psychiatry"],
            },
            target_tables=["search_journal"],
            year="2019",
        ),
        max_candidates=5,
    )

    assert [candidate.record_id for candidate in candidates] == ["search_journal:20"]


def test_phase4_prefers_same_container_candidate_over_unrelated_title_hit(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    _insert_journal_candidate(
        db_path,
        record_id=20,
        title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
        title_norm="temporal associations screen time anxiety symptoms adolescents",
        year="2019",
        doi="10.1177/0706743719885486",
        journal="The Canadian Journal of Psychiatry",
        container_text="canadian journal psychiatry",
        author_surnames_text="Boers;Afzali;Conrod",
        author_text="boers afzali conrod",
    )
    _insert_journal_candidate(
        db_path,
        record_id=21,
        title="Temporal Associations of Screen Time and Anxiety Symptoms Among Adolescents",
        title_norm="temporal associations screen time anxiety symptoms adolescents",
        year="2019",
        doi="10.2196/16104",
        journal="JMIR Medical Informatics",
        container_text="jmir medical informatics",
        author_surnames_text="Woodworth;Farooq;Gorelick",
        author_text="woodworth farooq gorelick",
    )

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_same_container",
            raw_reference=(
                "Boers, E., Afzali, M. H., & Conrod, P. (2019). "
                "Temporal associations of screen time and anxiety symptoms among adolescents. "
                "The Canadian Journal of Psychiatry."
            ),
            normalized_reference=(
                "Boers, E., Afzali, M. H., & Conrod, P. (2019). "
                "Temporal associations of screen time and anxiety symptoms among adolescents. "
                "The Canadian Journal of Psychiatry."
            ),
            parsed_data=ParsedReferenceData(
                author=[
                    ParsedName(family="Boers", given="E."),
                    ParsedName(family="Afzali", given="M. H."),
                    ParsedName(family="Conrod", given="P."),
                ],
                title=["Temporal associations of screen time and anxiety symptoms among adolescents"],
                container_title=["The Canadian Journal of Psychiatry"],
                date=["2019"],
                issued_year="2019",
            ),
            ctype="journal_article",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="crossref",
                lookup_key_fields={
                    "title": ["Temporal associations of screen time and anxiety symptoms among adolescents"],
                    "author": ["Boers, E.", "Afzali, M. H.", "Conrod, P."],
                    "issued_year": ["2019"],
                    "container_title": ["The Canadian Journal of Psychiatry"],
                },
                lookup_query_fields={
                    "title": ["Temporal associations of screen time and anxiety symptoms among adolescents"],
                    "author": ["Boers, E.", "Afzali, M. H.", "Conrod, P."],
                    "issued_year": ["2019"],
                    "container_title": ["The Canadian Journal of Psychiatry"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path), max_candidates=5),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_journal:20"
    assert "container_or_publisher_match" in result.best_candidate.match_reasons


def test_phase4_scores_journal_abbreviation_as_container_match(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    _insert_journal_candidate(
        db_path,
        record_id=30,
        title="Clinical treatment effects",
        title_norm="clinical treatment effects",
        year="2022",
        doi=None,
        journal="Annals of Internal Medicine",
        container_text="annals internal medicine",
        author_surnames_text="Chen",
        author_text="chen",
    )

    result = match_reference(
        _journal_result_with_container(container="Ann Intern Med"),
        config=Phase4RuntimeConfig(local_db_path=str(db_path), max_candidates=5),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_journal:30"
    assert result.best_candidate.match_signals.container_match == "yes"
    assert "container_or_publisher_match" in result.best_candidate.match_reasons


def test_phase4_cleans_escaped_cdata_candidate_titles_before_scoring(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    title = "Authorship criteria for scientific papers: a polemic and delicate subject"
    _insert_journal_candidate(
        db_path,
        record_id=31,
        title="&lt;![CDATA[&lt;b&gt;Authorship criteria for scientific papers&lt;/b&gt;: &lt;b&gt;a polemic and delicate subject&lt;/b&gt;]]&gt;",
        title_norm="authorship criteria scientific papers polemic delicate subject",
        year="2004",
        doi="10.1590/s1678-97412004000400002",
        journal="Brazilian Journal of Cardiovascular Surgery",
        container_text="brazilian journal cardiovascular surgery",
        author_surnames_text="Monteiro",
        author_text="monteiro",
    )

    result = match_reference(
        _journal_result_with_container(
            container="Brazilian Journal of Cardiovascular Surgery",
            title=title,
            year="2004",
            doi=None,
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path), max_candidates=5),
    )

    assert result.best_candidate is not None
    assert result.best_candidate.title == title
    assert result.best_candidate.match_signals.title_match_strength == "exact_or_near_exact"


def test_phase4_top_candidates_are_capped_to_two(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_result(doi=None),
        config=Phase4RuntimeConfig(
            local_db_path=str(db_path),
            enable_relaxed_queries=True,
            max_fallback_strategies=3,
        ),
    )

    assert len(result.candidates) >= len(result.top_candidates)
    assert len(result.top_candidates) == 2
    assert [candidate.record_id for candidate in result.top_candidates] == [
        candidate.record_id for candidate in result.candidates[:2]
    ]


def test_phase4_invalid_db_returns_error(tmp_path):
    missing_path = tmp_path / "missing.db"

    result = match_reference(
        _journal_result(),
        config=Phase4RuntimeConfig(local_db_path=str(missing_path)),
    )

    assert result.status == "error"
    assert "phase4_lookup_failed" in result.reasons


def test_phase4_prefers_phase3b_input_when_available(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    phase3 = [_book_result(year="2020", author="Smith")]
    phase3b = [_book_result(year="2021", author="Doe")]

    results = match_references(
        Phase4BatchInput(phase3=phase3, phase3b=phase3b),
        config=Phase4RuntimeConfig(local_db_path=str(db_path), prefer_recovered=True),
    )

    assert len(results) == 1
    assert results[0].reference_id == "ref_book_2021_doe"
    assert results[0].best_candidate is not None
    assert results[0].best_candidate.record_id == "search_book:1"


def test_phase4_long_journal_doi_miss_uses_protected_recall_band(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_reference7_style_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status in {"matched_provisional", "candidate_only"}
    assert result.best_candidate is not None
    assert result.lookup_trace.doi_attempted is True
    assert result.lookup_trace.doi_miss is True
    assert "journal_title6_year_exact_doi_miss" in result.lookup_trace.strategies_attempted
    assert result.best_candidate.source_strategy == "journal_title6_year_exact_doi_miss"
    assert result.lookup_trace.candidate_count >= 1
    assert "phase4_doi_miss_recall_band_entered" in result.reasons
    assert "phase4_doi_miss_recall_band_candidates_found" in result.reasons


def test_phase4_doi_book_miss_falls_back_to_book_title_search(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            INSERT INTO search_book (
                id, title, year, doi, publisher, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                20,
                "Teaching climate change to adolescents: Reading, writing, and making a difference",
                "2017",
                None,
                "Routledge",
                "Beach|Share|Webb",
                "beach share webb",
            ),
        )
        conn.execute(
            "INSERT INTO search_book_fts (rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            (
                20,
                "teaching climate change adolescents reading writing making difference",
                "beach share webb",
                "routledge",
            ),
        )
        conn.commit()
    finally:
        conn.close()

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_doi_book",
            raw_reference=(
                "Beach, R., Share, J., & Webb, A. (2017). Teaching climate change to adolescents: "
                "Reading, writing, and making a difference. Routledge. https://doi.org/10.4324/9781315276304"
            ),
            normalized_reference=(
                "Beach, R., Share, J., & Webb, A. (2017). Teaching climate change to adolescents: "
                "Reading, writing, and making a difference. Routledge. https://doi.org/10.4324/9781315276304"
            ),
            parsed_data=ParsedReferenceData(
                author=[
                    ParsedName(family="Beach", given="R."),
                    ParsedName(family="Share", given="J."),
                    ParsedName(family="Webb", given="A."),
                ],
                title=["Teaching climate change to adolescents: Reading, writing, and making a difference"],
                publisher=["Routledge"],
                date=["2017"],
                issued_year="2017",
                doi=["10.4324/9781315276304"],
            ),
            ctype="book",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="openlibrary",
                lookup_key_fields={
                    "doi": ["10.4324/9781315276304"],
                    "title": ["Teaching climate change to adolescents: Reading, writing, and making a difference"],
                    "author": ["Beach, R.", "Share, J.", "Webb, A."],
                    "issued_year": ["2017"],
                    "publisher": ["Routledge"],
                },
                lookup_query_fields={
                    "title": ["Teaching climate change to adolescents: Reading, writing, and making a difference"],
                    "author": ["Beach, R.", "Share, J.", "Webb, A."],
                    "issued_year": ["2017"],
                    "publisher": ["Routledge"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "matched_provisional"
    assert result.lookup_trace.doi_attempted is True
    assert result.lookup_trace.doi_miss is True
    assert result.best_candidate is not None
    assert result.best_candidate.record_id == "search_book:20"
    assert result.best_candidate.source_strategy == "book_main_title_author_year_exact"
    assert "doi_miss_title_year_recovery" in result.reasons


def test_phase4_long_journal_doi_miss_retains_up_to_three_candidates(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_reference7_style_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path), max_candidates=3),
    )

    assert result.lookup_trace.doi_miss is True
    assert len(result.candidates) == 3
    assert result.lookup_trace.candidate_count >= 3
    assert result.candidates[0].ordering_score >= result.candidates[1].ordering_score
    assert result.candidates[1].ordering_score >= result.candidates[2].ordering_score
    assert all(candidate.source_strategy == "journal_title6_year_exact_doi_miss" for candidate in result.candidates)


def test_phase4_generic_short_journal_doi_miss_still_blocked_by_guard(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_short_broad_doi_miss_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.status == "no_match"
    assert result.lookup_trace.doi_attempted is True
    assert result.lookup_trace.doi_miss is True
    assert result.lookup_trace.strategies_attempted == []
    assert "journal_title_year_exact" in result.lookup_trace.strategies_skipped
    assert "phase4_doi_miss_no_selective_fallback" in result.lookup_trace.skipped_reasons
    assert "phase4_no_candidates" in result.reasons


def test_phase4_relaxed_queries_are_off_by_default(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        _journal_reference7_style_result(),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert "journal_title3_year_near" not in result.lookup_trace.strategies_attempted
    assert "journal_title6_year_exact_doi_miss" in result.lookup_trace.strategies_attempted


def test_phase4_suspicious_doi_hit_triggers_text_corroboration(tmp_path):
    db_path = tmp_path / "local.db"
    _create_local_db(db_path)

    result = match_reference(
        ParsedReferenceResult(
            reference_id="ref_suspicious_doi",
            raw_reference="Smith, J. (2021). Different title. Journal Name.",
            normalized_reference="Smith, J. (2021). Different title. Journal Name.",
            parsed_data=ParsedReferenceData(
                author=[ParsedName(family="Smith", given="J.")],
                title=["Different title for same doi"],
                container_title=["Journal Name"],
                date=["2021"],
                issued_year="2021",
                doi=["10.1234/test.article"],
            ),
            ctype="journal_article",
            match_preparation=MatchPreparation(
                eligible_for_db_match=True,
                match_target="crossref",
                lookup_key_fields={
                    "doi": ["10.1234/test.article"],
                    "title": ["Different title for same doi"],
                    "author": ["Smith, J."],
                    "issued_year": ["2021"],
                    "container_title": ["Journal Name"],
                },
                lookup_query_fields={
                    "title": ["Different title for same doi"],
                    "author": ["Smith, J."],
                    "issued_year": ["2021"],
                    "container_title": ["Journal Name"],
                },
            ),
        ),
        config=Phase4RuntimeConfig(local_db_path=str(db_path)),
    )

    assert result.lookup_trace.doi_hit_quality == "suspicious"
    assert result.lookup_trace.corroboration_triggered is True
    assert "phase4_doi_hit_suspicious" in result.reasons
    assert "phase4_doi_hit_text_corroboration_started" in result.reasons
    assert result.top_candidates[0].record_id == "search_journal:1"
