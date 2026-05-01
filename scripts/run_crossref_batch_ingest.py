#!/usr/bin/env python3
"""Batch-ingest Crossref shards into an existing Reference_Gen2 SQLite DB.

This Crossref-only continuation tool never creates, deletes, or changes the DB
schema. Use scripts/ingest_with_initials.py for full rebuilds.
"""

from __future__ import annotations

import argparse
import gzip
import importlib.util
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

try:
    from scripts import ingest_with_initials as ingest
except ModuleNotFoundError:
    ingest = None
    for candidate in (
        Path(__file__).with_name("ingest.py"),
        Path(__file__).with_name("ingest_with_initials.py"),
    ):
        if not candidate.exists():
            continue
        spec = importlib.util.spec_from_file_location("ingest_with_initials", candidate)
        if spec and spec.loader:
            ingest = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(ingest)
            break
    if ingest is None:
        raise


SEARCH_TABLES = (
    "search_journal",
    "search_book",
    "search_book_chapter",
    "search_conference",
)

EXPECTED_COLUMNS: dict[str, set[str]] = {
    "search_journal": {
        "id", "doi", "title", "journal", "volume", "issue", "pages",
        "title_norm", "container_text", "year", "author_surnames_json",
        "author_initials_text", "author_text", "author_surnames_text",
        "source", "source_id",
    },
    "search_book": {
        "id", "doi", "isbn", "ol_key", "title", "publisher", "title_norm",
        "year", "publisher_norm", "container_text", "author_surnames_json",
        "author_initials_text", "author_text", "author_surnames_text",
        "source", "source_id",
    },
    "search_book_chapter": {
        "id", "doi", "title", "book_title", "publisher", "title_norm",
        "year", "book_title_norm", "container_text", "author_surnames_json",
        "author_initials_text", "author_text", "editor_surnames_json",
        "editor_initials_text", "editor_text", "author_surnames_text",
        "editor_surnames_text", "source", "source_id",
    },
    "search_conference": {
        "id", "doi", "title", "container", "volume", "pages", "title_norm",
        "year", "container_norm", "container_text", "author_surnames_json",
        "author_initials_text", "author_text", "author_surnames_text",
        "source", "source_id",
    },
}

INSERT_SQL: dict[str, str] = {
    "search_journal": """
        INSERT OR IGNORE INTO search_journal
        (doi, title, journal, volume, issue, pages, title_norm, container_text, year,
         author_surnames_json, author_initials_text, author_text,
         author_surnames_text, source, source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    "search_book": """
        INSERT OR IGNORE INTO search_book
        (doi, isbn, title, publisher, title_norm, year, publisher_norm,
         container_text, author_surnames_json, author_initials_text,
         author_text, author_surnames_text, source, source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    "search_book_chapter": """
        INSERT OR IGNORE INTO search_book_chapter
        (doi, title, book_title, publisher, title_norm, year, book_title_norm,
         container_text, author_surnames_json, author_initials_text, author_text,
         editor_surnames_json, editor_initials_text, editor_text,
         author_surnames_text, editor_surnames_text, source, source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    "search_conference": """
        INSERT OR IGNORE INTO search_conference
        (doi, title, container, volume, pages, title_norm, year, container_norm,
         container_text, author_surnames_json, author_initials_text, author_text,
         author_surnames_text, source, source_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
}

FTS_SELECT_SQL: dict[str, str] = {
    "search_journal": """
        SELECT id, title_norm, author_text, container_text
        FROM search_journal
        WHERE id > ? AND source = 'crossref'
    """,
    "search_book": """
        SELECT id, title_norm, author_text, container_text
        FROM search_book
        WHERE id > ? AND source = 'crossref'
    """,
    "search_book_chapter": """
        SELECT id, title_norm, author_text, editor_text, container_text
        FROM search_book_chapter
        WHERE id > ? AND source = 'crossref'
    """,
    "search_conference": """
        SELECT id, title_norm, author_text, container_text
        FROM search_conference
        WHERE id > ? AND source = 'crossref'
    """,
}

FTS_INSERT_SQL: dict[str, str] = {
    "search_journal": """
        INSERT INTO search_journal_fts(rowid, title_norm, author_text, container_text)
        VALUES (?, ?, ?, ?)
    """,
    "search_book": """
        INSERT INTO search_book_fts(rowid, title_norm, author_text, container_text)
        VALUES (?, ?, ?, ?)
    """,
    "search_book_chapter": """
        INSERT INTO search_book_chapter_fts(rowid, title_norm, author_text, editor_text, container_text)
        VALUES (?, ?, ?, ?, ?)
    """,
    "search_conference": """
        INSERT INTO search_conference_fts(rowid, title_norm, author_text, container_text)
        VALUES (?, ?, ?, ?)
    """,
}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def validate_existing_schema(conn: sqlite3.Connection) -> None:
    missing: list[str] = []
    for table in SEARCH_TABLES:
        if not _table_columns(conn, table):
            missing.append(table)
            continue
        if not _table_columns(conn, f"{table}_fts"):
            missing.append(f"{table}_fts")
            continue
        absent = EXPECTED_COLUMNS[table] - _table_columns(conn, table)
        if absent:
            missing.append(f"{table}: {', '.join(sorted(absent))}")
    if not _table_columns(conn, "crossref_works"):
        missing.append("crossref_works")
    if missing:
        raise RuntimeError("DB does not match expected ingest schema: " + "; ".join(missing))


def has_fts_triggers(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'trigger'
          AND name IN (
            'search_journal_ai', 'search_book_ai',
            'search_book_chapter_ai', 'search_conference_ai'
          )
        LIMIT 1
        """
    ).fetchone()
    return row is not None


def _read_resume_file(path: Path) -> list[str]:
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _load_completed_shards(path: Path | None) -> set[str]:
    if path is None or not path.exists():
        return set()
    completed: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            completed.add(line)
            continue
        shard = str(payload.get("shard") or "").strip()
        if shard:
            completed.add(shard)
    return completed


def _append_completed_shard(path: Path | None, shard_name: str, counts: dict[str, int]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"shard": shard_name, "counts": counts}, sort_keys=True) + "\n")


def _first_title(item: dict[str, Any]) -> str:
    title = item.get("title")
    if isinstance(title, list) and title:
        return str(title[0]).strip()
    if isinstance(title, str):
        return title.strip()
    return ""


def _year_from_item(item: dict[str, Any]) -> int | None:
    for date_key in ("published-print", "published-online", "created", "issued"):
        parts = ((item.get(date_key) or {}).get("date-parts") or [])
        if parts and isinstance(parts[0], list) and parts[0]:
            try:
                return int(parts[0][0])
            except (TypeError, ValueError):
                continue
    return None


def _container_from_item(item: dict[str, Any]) -> str:
    container = item.get("container-title")
    if isinstance(container, list) and container:
        return str(container[0]).strip()
    if isinstance(container, str):
        return container.strip()
    return ""


def _parse_item(
    item: dict[str, Any],
    *,
    shard_name: str,
    line_number: int,
) -> tuple[str, tuple[Any, ...], tuple[str, str] | None] | None:
    title_raw = _first_title(item)
    if not title_raw:
        return None

    typ = item.get("type") or None
    table = ingest._crossref_table(typ)
    if not table:
        return None

    doi = str(item.get("DOI") or "").strip() or None
    source_id = doi or f"crossref:{shard_name}:{line_number}"
    year = _year_from_item(item)
    container = _container_from_item(item)
    publisher = str(item.get("publisher") or "").strip() or None
    volume = str(item.get("volume") or "").strip() or None
    issue = str(item.get("issue") or "").strip() or None
    pages = str(item.get("page") or "").strip() or None

    author_surnames, author_initials = ingest._crossref_authors(item, "author")
    editor_surnames, editor_initials = ingest._crossref_authors(item, "editor")
    if not author_surnames and editor_surnames:
        author_surnames = editor_surnames
        author_initials = editor_initials

    author_text = " ".join(ingest.norm_text(s) for s in author_surnames if s)
    author_initials_text = "|".join(author_initials)
    author_surnames_text = "|".join(author_surnames)
    author_surnames_json = json.dumps(author_surnames, ensure_ascii=False)
    editor_text = " ".join(ingest.norm_text(s) for s in editor_surnames if s)
    editor_initials_text = "|".join(editor_initials)
    editor_surnames_text = "|".join(editor_surnames)
    editor_surnames_json = json.dumps(editor_surnames, ensure_ascii=False)

    title_norm = ingest.norm_text(title_raw)
    container_norm = ingest.norm_text(container) if container else ""
    publisher_norm = ingest.norm_text(publisher) if publisher else ""
    work_row = (doi, str(typ)) if doi else None

    if table == "search_journal":
        return table, (
            doi, title_raw, container or None, volume, issue, pages,
            title_norm, container_norm, year,
            author_surnames_json, author_initials_text,
            author_text, author_surnames_text, "crossref", source_id,
        ), work_row

    if table == "search_book":
        isbn = ingest._first_value(item.get("ISBN"))
        return table, (
            doi, isbn, title_raw, publisher, title_norm, year,
            publisher_norm or None, publisher_norm,
            author_surnames_json, author_initials_text,
            author_text, author_surnames_text, "crossref", source_id,
        ), work_row

    if table == "search_book_chapter":
        book_title = container or None
        book_title_norm = ingest.norm_text(book_title) if book_title else ""
        chapter_container_text = " ".join(
            part for part in (book_title_norm, publisher_norm) if part
        )
        return table, (
            doi, title_raw, book_title, publisher, title_norm, year,
            book_title_norm or None, chapter_container_text,
            author_surnames_json, author_initials_text, author_text,
            editor_surnames_json, editor_initials_text, editor_text,
            author_surnames_text, editor_surnames_text, "crossref", source_id,
        ), work_row

    return table, (
        doi, title_raw, container or None, volume, pages, title_norm, year,
        container_norm or None, container_norm,
        author_surnames_json, author_initials_text,
        author_text, author_surnames_text, "crossref", source_id,
    ), work_row


def _existing_dois(conn: sqlite3.Connection, table: str, rows: list[tuple[Any, ...]]) -> set[str]:
    dois = sorted({str(row[0]) for row in rows if row[0]})
    existing: set[str] = set()
    for start in range(0, len(dois), 500):
        chunk = dois[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in conn.execute(f"SELECT doi FROM {table} WHERE doi IN ({placeholders})", chunk):
            if row[0]:
                existing.add(str(row[0]))
    return existing


def _max_id(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()[0])


def _insert_table_batch(
    conn: sqlite3.Connection,
    table: str,
    rows: list[tuple[Any, ...]],
) -> int:
    if not rows:
        return 0

    existing_dois = _existing_dois(conn, table, rows)
    seen_dois: set[str] = set()
    filtered: list[tuple[Any, ...]] = []
    for row in rows:
        doi = str(row[0]) if row[0] else ""
        if doi:
            if doi in existing_dois or doi in seen_dois:
                continue
            seen_dois.add(doi)
        filtered.append(row)
    if not filtered:
        return 0

    before_id = _max_id(conn, table)
    conn.executemany(INSERT_SQL[table], filtered)
    fts_rows = conn.execute(FTS_SELECT_SQL[table], (before_id,)).fetchall()
    conn.executemany(FTS_INSERT_SQL[table], [tuple(row) for row in fts_rows])
    return len(fts_rows)


def _flush_batches(
    conn: sqlite3.Connection,
    rows_by_table: dict[str, list[tuple[Any, ...]]],
    work_rows: list[tuple[str, str]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in SEARCH_TABLES:
        counts[table] = _insert_table_batch(conn, table, rows_by_table.get(table, []))
        rows_by_table[table] = []
    if work_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO crossref_works (doi, work_type) VALUES (?, ?)",
            work_rows,
        )
        work_rows.clear()
    return counts


def _merge_counts(total: dict[str, int], incoming: dict[str, int]) -> None:
    for table, count in incoming.items():
        total[table] = total.get(table, 0) + count


def ingest_crossref_batches(
    conn: sqlite3.Connection,
    *,
    resume_file: Path,
    crossref_dir: Path,
    progress_file: Path | None = None,
    batch_size: int = 10_000,
    progress_every: int = 50,
    skip_completed: bool = True,
    allow_existing_triggers: bool = False,
) -> int:
    validate_existing_schema(conn)
    if has_fts_triggers(conn) and not allow_existing_triggers:
        raise RuntimeError(
            "FTS triggers already exist; explicit FTS inserts would double-index rows. "
            "Use --allow-existing-triggers only when this is intentional."
        )

    shard_names = _read_resume_file(resume_file)
    completed = _load_completed_shards(progress_file) if skip_completed else set()
    total_inserted = 0
    total_counts = {table: 0 for table in SEARCH_TABLES}
    processed_shards = 0
    skipped_shards = 0

    for shard_name in shard_names:
        if shard_name in completed:
            skipped_shards += 1
            continue

        shard_path = crossref_dir / shard_name
        if not shard_path.is_file():
            print(f"  skip missing shard: {shard_path}", flush=True)
            continue

        shard_counts = {table: 0 for table in SEARCH_TABLES}
        rows_by_table: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        work_rows: list[tuple[str, str]] = []
        buffered = 0

        try:
            with gzip.open(shard_path, "rt", encoding="utf-8", errors="replace") as fh:
                for line_number, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        item = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    parsed = _parse_item(item, shard_name=shard_name, line_number=line_number)
                    if not parsed:
                        continue
                    table, row, work_row = parsed
                    rows_by_table[table].append(row)
                    if work_row:
                        work_rows.append(work_row)
                    buffered += 1
                    if buffered >= batch_size:
                        counts = _flush_batches(conn, rows_by_table, work_rows)
                        _merge_counts(shard_counts, counts)
                        buffered = 0

            counts = _flush_batches(conn, rows_by_table, work_rows)
            _merge_counts(shard_counts, counts)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        processed_shards += 1
        _append_completed_shard(progress_file, shard_name, shard_counts)
        _merge_counts(total_counts, shard_counts)
        inserted = sum(shard_counts.values())
        total_inserted += inserted

        if processed_shards % progress_every == 0:
            remaining_total = len(shard_names) - skipped_shards
            print(
                f"  crossref batch shards: {processed_shards}/{remaining_total} "
                f"({total_inserted:,} inserted; last={inserted:,})",
                flush=True,
            )

    print(f"crossref batch inserted: {total_inserted:,}", flush=True)
    for table in SEARCH_TABLES:
        print(f"  {table}: {total_counts[table]:,}", flush=True)
    if skipped_shards:
        print(f"  skipped completed shards: {skipped_shards:,}", flush=True)
    return total_inserted


def finalize_db(conn: sqlite3.Connection) -> None:
    validate_existing_schema(conn)
    if not has_fts_triggers(conn):
        print("Adding FTS triggers ...", flush=True)
        conn.executescript(ingest._FTS_TRIGGERS_SQL)
        conn.commit()
    else:
        print("FTS triggers already exist; skipping trigger creation.", flush=True)
    print("Running ANALYZE ...", flush=True)
    conn.execute("ANALYZE")
    conn.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True, help="Existing SQLite DB path")
    parser.add_argument("--crossref-resume", type=Path, help="Shard list file")
    parser.add_argument("--crossref-dir", type=Path, help="Directory containing Crossref shards")
    parser.add_argument("--progress-file", type=Path, help="External JSONL progress file")
    parser.add_argument("--batch-size", type=int, default=10_000)
    parser.add_argument("--progress-every", type=int, default=50)
    parser.add_argument("--no-progress-skip", action="store_true")
    parser.add_argument("--allow-existing-triggers", action="store_true")
    parser.add_argument("--finalize", action="store_true", help="Add FTS triggers and ANALYZE only")
    args = parser.parse_args()

    if not args.db.exists():
        raise SystemExit(f"DB does not exist: {args.db}")
    if not args.finalize and (not args.crossref_resume or not args.crossref_dir):
        raise SystemExit("--crossref-resume and --crossref-dir are required unless --finalize is used")

    conn = sqlite3.connect(str(args.db))
    conn.row_factory = sqlite3.Row
    try:
        if args.finalize:
            finalize_db(conn)
        else:
            ingest_crossref_batches(
                conn,
                resume_file=args.crossref_resume,
                crossref_dir=args.crossref_dir,
                progress_file=args.progress_file,
                batch_size=max(args.batch_size, 1),
                progress_every=max(args.progress_every, 1),
                skip_completed=not args.no_progress_skip,
                allow_existing_triggers=args.allow_existing_triggers,
            )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
