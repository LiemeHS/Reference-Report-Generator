from __future__ import annotations

import gzip
import json
import sqlite3

from scripts import ingest_with_initials


SEARCH_TABLES = (
    "search_journal",
    "search_book",
    "search_book_chapter",
    "search_conference",
)


def _columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table_name})")}


def test_ingest_schema_adds_container_text_to_search_and_fts_tables():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ingest_with_initials._SCHEMA_SQL)

        for table_name in SEARCH_TABLES:
            assert "container_text" in _columns(conn, table_name)
            assert "container_text" in _columns(conn, f"{table_name}_fts")
    finally:
        conn.close()


def test_ingest_fts_triggers_keep_container_text_indexed():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ingest_with_initials._SCHEMA_SQL)
        conn.executescript(ingest_with_initials._FTS_TRIGGERS_SQL)

        conn.execute(
            """
            INSERT INTO search_journal (
                doi, title, journal, title_norm, container_text, year,
                author_text, author_surnames_text, source_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "10.1234/container",
                "Container indexed title",
                "Example Journal",
                "container indexed title",
                "example journal",
                2024,
                "doe",
                "Doe",
                "10.1234/container",
            ),
        )

        row = conn.execute(
            "SELECT rowid FROM search_journal_fts WHERE search_journal_fts MATCH ?",
            ("container_text:example",),
        ).fetchone()

        assert row is not None
        assert row[0] == 1
    finally:
        conn.close()


def test_openlibrary_editions_tolerate_string_author_entries(tmp_path):
    editions_path = tmp_path / "ol_editions.txt.gz"
    payload = {
        "title": "Mixed Author Shape Book",
        "publish_date": "2026",
        "publishers": ["Example Press"],
        "authors": [
            {"key": "/authors/OL1A"},
            "/authors/OL2A",
            "not-an-author-key",
        ],
    }
    with gzip.open(editions_path, "wt", encoding="utf-8") as fh:
        fh.write(
            "\t".join(
                [
                    "/type/edition",
                    "/books/OL1M",
                    "0",
                    "2026-02-28T00:00:00Z",
                    json.dumps(payload),
                ]
            )
            + "\n"
        )

    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(ingest_with_initials._SCHEMA_SQL)
        conn.executemany(
            "INSERT INTO ol_authors (ol_key, name, name_norm) VALUES (?, ?, ?)",
            [
                ("/authors/OL1A", "Jane Doe", "jane doe"),
                ("/authors/OL2A", "Max Smith", "max smith"),
            ],
        )

        count = ingest_with_initials._ingest_ol_editions(conn, editions_path)

        row = conn.execute(
            "SELECT author_surnames_text, author_text, container_text FROM search_book"
        ).fetchone()
        assert count == 1
        assert row == ("Doe|Smith", "doe smith", "example press")
    finally:
        conn.close()
