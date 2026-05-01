from __future__ import annotations

import sqlite3

from scripts.create_mock_localdb import create_mock_db


def test_create_mock_localdb_builds_final_search_schema(tmp_path):
    db_path = tmp_path / "mock_refs.db"

    create_mock_db(db_path, rows_per_search_table=3, overwrite=False)

    conn = sqlite3.connect(db_path)
    try:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

        assert "ol_authors" not in tables
        assert "ol_editions" not in tables
        assert "crossref_works" in tables

        for table_name in (
            "search_journal",
            "search_book",
            "search_book_chapter",
            "search_conference",
        ):
            assert table_name in tables
            assert f"{table_name}_fts" in tables
            assert conn.execute(f"SELECT count(*) FROM {table_name}").fetchone()[0] == 3
            assert conn.execute(f"SELECT count(*) FROM {table_name}_fts").fetchone()[0] == 3

        row = conn.execute(
            """
            SELECT rowid FROM search_journal_fts
            WHERE search_journal_fts MATCH ?
            LIMIT 1
            """,
            ("container_text:mock",),
        ).fetchone()
        assert row is not None
    finally:
        conn.close()
