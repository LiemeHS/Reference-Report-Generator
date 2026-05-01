from __future__ import annotations

import gzip
import json
import sqlite3

from scripts import ingest_full_fast


def _write_ol_line(path, record_type: str, key: str, payload: dict) -> None:
    with gzip.open(path, "at", encoding="utf-8") as fh:
        fh.write(
            "\t".join(
                [
                    record_type,
                    key,
                    "0",
                    "2026-02-28T00:00:00Z",
                    json.dumps(payload),
                ]
            )
            + "\n"
        )


def test_full_fast_ingest_is_single_file_resilient_and_rebuilds_fts(tmp_path):
    ol_authors = tmp_path / "ol_authors.txt.gz"
    ol_editions = tmp_path / "ol_editions.txt.gz"
    crossref_dir = tmp_path / "crossref"
    crossref_dir.mkdir()
    crossref_resume = tmp_path / "crossref.resume"
    crossref_progress = tmp_path / "crossref.progress.jsonl"
    output = tmp_path / "refs.db"

    _write_ol_line(ol_authors, "/type/author", "/authors/OL1A", {"name": "Jane Doe"})
    _write_ol_line(ol_authors, "/type/author", "/authors/OL2A", {"name": "Max Smith"})
    _write_ol_line(
        ol_editions,
        "/type/edition",
        "/books/OL1M",
        {
            "title": "Open Library Fast Book",
            "publish_date": "2026",
            "publishers": ["Example Press"],
            "authors": [{"key": "/authors/OL1A"}, "/authors/OL2A"],
        },
    )
    with gzip.open(crossref_dir / "1.jsonl.gz", "wt", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "DOI": "10.1234/full-fast",
                    "type": "journal-article",
                    "title": ["Full Fast Journal"],
                    "container-title": ["Journal of Fast Tests"],
                    "author": [{"family": "Roe", "given": "Richard"}],
                    "issued": {"date-parts": [[2025]]},
                }
            )
            + "\n"
        )
    crossref_resume.write_text("1.jsonl.gz\n", encoding="utf-8")

    result = ingest_full_fast.main_from_args(
        [
            "--ol-authors", str(ol_authors),
            "--ol-editions", str(ol_editions),
            "--crossref-resume", str(crossref_resume),
            "--crossref-dir", str(crossref_dir),
            "--output", str(output),
            "--crossref-progress-file", str(crossref_progress),
            "--crossref-batch-size", "2",
            "--no-vacuum",
        ]
    )

    conn = sqlite3.connect(output)
    try:
        assert result == 0
        assert conn.execute("SELECT author_surnames_text FROM search_book").fetchone()[0] == "Doe|Smith"
        assert conn.execute("SELECT container_text FROM search_book").fetchone()[0] == "example press"
        assert conn.execute("SELECT container_text FROM search_journal").fetchone()[0] == "journal of fast tests"
        assert conn.execute("SELECT count(*) FROM search_book_fts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM search_journal_fts").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM crossref_works").fetchone()[0] == 1
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'search_journal_ai'"
        ).fetchone()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_sj_year'"
        ).fetchone()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index' AND name = 'idx_sb_sid'"
        ).fetchone()
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'ol_authors'"
        ).fetchone() is None
        assert crossref_progress.exists()
    finally:
        conn.close()
