from __future__ import annotations

import gzip
import json
import sqlite3

import pytest

from scripts import ingest_with_initials
from scripts import run_crossref_batch_ingest as batch_ingest


def _create_db(path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(ingest_with_initials._SCHEMA_SQL)
    conn.commit()
    return conn


def _write_shard(path, items: list[dict]) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item) + "\n")


def _write_resume(path, shard_names: list[str]) -> None:
    path.write_text("\n".join(shard_names) + "\n", encoding="utf-8")


def _sample_items() -> list[dict]:
    return [
        {
            "DOI": "10.1234/journal",
            "type": "journal-article",
            "title": ["Journal Batch Title"],
            "container-title": ["Journal of Tests"],
            "author": [{"family": "Doe", "given": "Jane"}],
            "published-print": {"date-parts": [[2024]]},
            "volume": "1",
            "issue": "2",
            "page": "3-4",
        },
        {
            "DOI": "10.1234/book",
            "type": "book",
            "title": ["Book Batch Title"],
            "publisher": "Example Press",
            "author": [{"family": "Smith", "given": "Max"}],
            "ISBN": ["9781234567890"],
            "issued": {"date-parts": [[2023]]},
        },
        {
            "DOI": "10.1234/chapter",
            "type": "book-chapter",
            "title": ["Chapter Batch Title"],
            "container-title": ["Handbook Examples"],
            "publisher": "Example Press",
            "author": [{"family": "Jones", "given": "Alex"}],
            "editor": [{"family": "Editor", "given": "Eve"}],
            "issued": {"date-parts": [[2022]]},
        },
        {
            "DOI": "10.1234/conf",
            "type": "proceedings-article",
            "title": ["Conference Batch Title"],
            "container-title": ["Proceedings Testing"],
            "author": [{"family": "Nguyen", "given": "Linh"}],
            "published-online": {"date-parts": [[2021]]},
            "page": "10-12",
        },
    ]


def test_crossref_batch_ingest_inserts_supported_types_and_fts(tmp_path):
    db_path = tmp_path / "refs.db"
    shard_dir = tmp_path / "crossref"
    shard_dir.mkdir()
    shard_name = "1.jsonl.gz"
    resume = tmp_path / "crossref.resume"
    progress = tmp_path / "progress.jsonl"
    _write_shard(shard_dir / shard_name, _sample_items())
    _write_resume(resume, [shard_name])

    conn = _create_db(db_path)
    try:
        inserted = batch_ingest.ingest_crossref_batches(
            conn,
            resume_file=resume,
            crossref_dir=shard_dir,
            progress_file=progress,
            batch_size=2,
            progress_every=1,
        )

        assert inserted == 4
        assert conn.execute("SELECT container_text FROM search_journal").fetchone()[0] == "journal of tests"
        assert conn.execute("SELECT container_text FROM search_book").fetchone()[0] == "example press"
        assert (
            conn.execute("SELECT container_text FROM search_book_chapter").fetchone()[0]
            == "handbook examples example press"
        )
        assert conn.execute("SELECT container_text FROM search_conference").fetchone()[0] == "proceedings testing"
        assert conn.execute("SELECT count(*) FROM crossref_works").fetchone()[0] == 4
        assert conn.execute(
            "SELECT count(*) FROM search_journal_fts WHERE search_journal_fts MATCH ?",
            ("container_text:journal",),
        ).fetchone()[0] == 1
        assert progress.exists()
    finally:
        conn.close()


def test_crossref_batch_ingest_duplicate_rerun_does_not_duplicate_rows_or_fts(tmp_path):
    db_path = tmp_path / "refs.db"
    shard_dir = tmp_path / "crossref"
    shard_dir.mkdir()
    shard_name = "1.jsonl.gz"
    resume = tmp_path / "crossref.resume"
    _write_shard(shard_dir / shard_name, [_sample_items()[0]])
    _write_resume(resume, [shard_name])

    conn = _create_db(db_path)
    try:
        first = batch_ingest.ingest_crossref_batches(
            conn,
            resume_file=resume,
            crossref_dir=shard_dir,
            skip_completed=False,
        )
        second = batch_ingest.ingest_crossref_batches(
            conn,
            resume_file=resume,
            crossref_dir=shard_dir,
            skip_completed=False,
        )

        assert first == 1
        assert second == 0
        assert conn.execute("SELECT count(*) FROM search_journal").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM search_journal_fts").fetchone()[0] == 1
    finally:
        conn.close()


def test_crossref_batch_ingest_skips_completed_progress_file_shards(tmp_path):
    db_path = tmp_path / "refs.db"
    shard_dir = tmp_path / "crossref"
    shard_dir.mkdir()
    shard_name = "1.jsonl.gz"
    resume = tmp_path / "crossref.resume"
    progress = tmp_path / "progress.jsonl"
    _write_shard(shard_dir / shard_name, [_sample_items()[0]])
    _write_resume(resume, [shard_name])
    progress.write_text(json.dumps({"shard": shard_name}) + "\n", encoding="utf-8")

    conn = _create_db(db_path)
    try:
        inserted = batch_ingest.ingest_crossref_batches(
            conn,
            resume_file=resume,
            crossref_dir=shard_dir,
            progress_file=progress,
        )

        assert inserted == 0
        assert conn.execute("SELECT count(*) FROM search_journal").fetchone()[0] == 0
    finally:
        conn.close()


def test_crossref_batch_ingest_refuses_existing_fts_triggers(tmp_path):
    db_path = tmp_path / "refs.db"
    shard_dir = tmp_path / "crossref"
    shard_dir.mkdir()
    shard_name = "1.jsonl.gz"
    resume = tmp_path / "crossref.resume"
    _write_shard(shard_dir / shard_name, [_sample_items()[0]])
    _write_resume(resume, [shard_name])

    conn = _create_db(db_path)
    try:
        conn.executescript(ingest_with_initials._FTS_TRIGGERS_SQL)
        conn.commit()

        with pytest.raises(RuntimeError, match="FTS triggers already exist"):
            batch_ingest.ingest_crossref_batches(
                conn,
                resume_file=resume,
                crossref_dir=shard_dir,
            )
    finally:
        conn.close()


def test_crossref_batch_ingest_does_not_change_schema(tmp_path):
    db_path = tmp_path / "refs.db"
    shard_dir = tmp_path / "crossref"
    shard_dir.mkdir()
    shard_name = "1.jsonl.gz"
    resume = tmp_path / "crossref.resume"
    _write_shard(shard_dir / shard_name, [_sample_items()[0]])
    _write_resume(resume, [shard_name])

    conn = _create_db(db_path)
    try:
        before = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        batch_ingest.ingest_crossref_batches(
            conn,
            resume_file=resume,
            crossref_dir=shard_dir,
        )
        after = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()

        assert [tuple(row) for row in after] == [tuple(row) for row in before]
    finally:
        conn.close()
