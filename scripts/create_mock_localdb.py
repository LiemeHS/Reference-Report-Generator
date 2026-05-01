#!/usr/bin/env python3
"""Create a small synthesized SQLite reference DB for local Phase 4 testing."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from scripts import ingest_with_initials


DEFAULT_OUTPUT = Path("local_mock_refs.db")

FIRST_NAMES = (
    "Alex",
    "Jordan",
    "Taylor",
    "Morgan",
    "Casey",
    "Riley",
    "Avery",
    "Quinn",
)
SURNAMES = (
    "Smith",
    "Johnson",
    "Williams",
    "Brown",
    "Jones",
    "Garcia",
    "Miller",
    "Davis",
)
TOPICS = (
    "adolescent mental health",
    "digital learning",
    "public policy",
    "clinical practice",
    "urban planning",
    "environmental systems",
    "social media use",
    "evidence synthesis",
)
JOURNALS = (
    "Journal of Applied Reference Testing",
    "International Review of Mock Data",
    "Open Methods Quarterly",
    "Local Database Studies",
)
PUBLISHERS = (
    "Example University Press",
    "Reference Testing Press",
    "Local Data Books",
    "Synthetic Academic Publishing",
)
PROCEEDINGS = (
    "Proceedings of the Local Test Conference",
    "Annual Symposium on Synthetic Data",
    "Mock Research Systems Conference",
    "Conference on Reference Matching",
)


def _person(index: int) -> tuple[str, str, str, str]:
    first = FIRST_NAMES[index % len(FIRST_NAMES)]
    middle = FIRST_NAMES[(index + 3) % len(FIRST_NAMES)]
    surname = SURNAMES[index % len(SURNAMES)]
    full_name = f"{first} {middle} {surname}"
    initials = ingest_with_initials.extract_initials(f"{first} {middle}")
    return first, surname, full_name, initials


def _authors(index: int) -> tuple[str, str, str]:
    _, surname_one, full_one, initials_one = _person(index)
    _, surname_two, full_two, initials_two = _person(index + 1)
    surnames = [surname_one, surname_two]
    return (
        json.dumps(surnames),
        f"{initials_one}|{initials_two}",
        "|".join(surnames),
    )


def _title(prefix: str, index: int) -> str:
    topic = TOPICS[index % len(TOPICS)]
    return f"{prefix} {index:03d}: {topic.title()}"


def _doi(kind: str, index: int) -> str:
    return f"10.5555/mock.{kind}.{index:04d}"


def _insert_journal_rows(conn: sqlite3.Connection, count: int) -> None:
    rows = []
    for index in range(1, count + 1):
        title = _title("Article", index)
        journal = JOURNALS[index % len(JOURNALS)]
        surnames_json, initials_text, surnames_text = _authors(index)
        doi = _doi("journal", index)
        rows.append(
            (
                doi,
                title,
                journal,
                str(1 + index % 12),
                str(1 + index % 4),
                f"{10 + index}-{20 + index}",
                ingest_with_initials.norm_text(title),
                ingest_with_initials.norm_text(journal),
                2010 + index % 16,
                surnames_json,
                initials_text,
                ingest_with_initials.norm_text(surnames_text.replace("|", " ")),
                surnames_text,
                "mock-crossref",
                doi,
            )
        )
    conn.executemany(
        """
        INSERT INTO search_journal (
            doi, title, journal, volume, issue, pages, title_norm,
            container_text, year, author_surnames_json, author_initials_text,
            author_text, author_surnames_text, source, source_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_book_rows(conn: sqlite3.Connection, count: int) -> None:
    rows = []
    for index in range(1, count + 1):
        title = _title("Book", index)
        publisher = PUBLISHERS[index % len(PUBLISHERS)]
        surnames_json, initials_text, surnames_text = _authors(index + 100)
        source = "openlibrary" if index % 2 == 0 else "mock-crossref"
        doi = None if source == "openlibrary" else _doi("book", index)
        isbn = f"978000{index:07d}"
        ol_key = f"/books/OL{index}M" if source == "openlibrary" else None
        rows.append(
            (
                doi,
                isbn,
                ol_key,
                title,
                publisher,
                ingest_with_initials.norm_text(title),
                2005 + index % 20,
                ingest_with_initials.norm_text(publisher),
                ingest_with_initials.norm_text(publisher),
                surnames_json,
                initials_text,
                ingest_with_initials.norm_text(surnames_text.replace("|", " ")),
                surnames_text,
                source,
                ol_key or doi,
            )
        )
    conn.executemany(
        """
        INSERT INTO search_book (
            doi, isbn, ol_key, title, publisher, title_norm, year,
            publisher_norm, container_text, author_surnames_json,
            author_initials_text, author_text, author_surnames_text, source,
            source_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_chapter_rows(conn: sqlite3.Connection, count: int) -> None:
    rows = []
    for index in range(1, count + 1):
        title = _title("Chapter", index)
        book_title = _title("Edited Volume", index)
        publisher = PUBLISHERS[(index + 1) % len(PUBLISHERS)]
        surnames_json, initials_text, surnames_text = _authors(index + 200)
        editor_json, editor_initials, editor_surnames = _authors(index + 300)
        doi = _doi("chapter", index)
        container_text = ingest_with_initials.norm_text(f"{book_title} {publisher}")
        rows.append(
            (
                doi,
                title,
                book_title,
                publisher,
                ingest_with_initials.norm_text(title),
                2012 + index % 12,
                ingest_with_initials.norm_text(book_title),
                container_text,
                surnames_json,
                initials_text,
                ingest_with_initials.norm_text(surnames_text.replace("|", " ")),
                editor_json,
                editor_initials,
                ingest_with_initials.norm_text(editor_surnames.replace("|", " ")),
                surnames_text,
                editor_surnames,
                "mock-crossref",
                doi,
            )
        )
    conn.executemany(
        """
        INSERT INTO search_book_chapter (
            doi, title, book_title, publisher, title_norm, year,
            book_title_norm, container_text, author_surnames_json,
            author_initials_text, author_text, editor_surnames_json,
            editor_initials_text, editor_text, author_surnames_text,
            editor_surnames_text, source, source_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _insert_conference_rows(conn: sqlite3.Connection, count: int) -> None:
    rows = []
    for index in range(1, count + 1):
        title = _title("Conference Paper", index)
        container = PROCEEDINGS[index % len(PROCEEDINGS)]
        surnames_json, initials_text, surnames_text = _authors(index + 400)
        doi = _doi("conference", index)
        rows.append(
            (
                doi,
                title,
                container,
                str(1 + index % 6),
                f"{30 + index}-{40 + index}",
                ingest_with_initials.norm_text(title),
                2014 + index % 10,
                ingest_with_initials.norm_text(container),
                ingest_with_initials.norm_text(container),
                surnames_json,
                initials_text,
                ingest_with_initials.norm_text(surnames_text.replace("|", " ")),
                surnames_text,
                "mock-crossref",
                doi,
            )
        )
    conn.executemany(
        """
        INSERT INTO search_conference (
            doi, title, container, volume, pages, title_norm, year,
            container_norm, container_text, author_surnames_json,
            author_initials_text, author_text, author_surnames_text, source,
            source_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def _populate_crossref_tracking(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO crossref_works (doi, work_type)
        SELECT doi, 'journal-article' FROM search_journal WHERE doi IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO crossref_works (doi, work_type)
        SELECT doi, 'book' FROM search_book WHERE doi IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO crossref_works (doi, work_type)
        SELECT doi, 'book-chapter' FROM search_book_chapter WHERE doi IS NOT NULL
        """
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO crossref_works (doi, work_type)
        SELECT doi, 'conference-paper' FROM search_conference WHERE doi IS NOT NULL
        """
    )


def create_mock_db(output: Path, *, rows_per_search_table: int, overwrite: bool) -> None:
    if rows_per_search_table < 1:
        raise ValueError("--rows-per-search-table must be at least 1")
    if output.exists():
        if not overwrite:
            raise FileExistsError(f"{output} already exists; pass --overwrite to replace it")
        output.unlink()

    output.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(output))
    try:
        conn.executescript(ingest_with_initials._SCHEMA_SQL)
        _insert_journal_rows(conn, rows_per_search_table)
        _insert_book_rows(conn, rows_per_search_table)
        _insert_chapter_rows(conn, rows_per_search_table)
        _insert_conference_rows(conn, rows_per_search_table)
        _populate_crossref_tracking(conn)
        conn.executescript(ingest_with_initials._FTS_TRIGGERS_SQL)

        for fts_table in (
            "search_journal_fts",
            "search_book_fts",
            "search_book_chapter_fts",
            "search_conference_fts",
        ):
            conn.execute(f"INSERT INTO {fts_table}({fts_table}) VALUES ('rebuild')")

        conn.executescript(
            """
            DROP TABLE IF EXISTS ol_authors;
            DROP TABLE IF EXISTS ol_editions;
            """
        )
        conn.execute("ANALYZE")
        conn.commit()
        conn.execute("VACUUM")
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output SQLite DB path. Defaults to {DEFAULT_OUTPUT}.",
    )
    parser.add_argument(
        "--rows-per-search-table",
        type=int,
        default=25,
        help="Rows to synthesize in each search table. Default gives 100 search rows total.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing output DB.",
    )
    args = parser.parse_args(argv)

    create_mock_db(
        args.output,
        rows_per_search_table=args.rows_per_search_table,
        overwrite=args.overwrite,
    )
    print(f"Wrote mock local DB: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
