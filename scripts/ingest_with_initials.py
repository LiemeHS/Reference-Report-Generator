#!/usr/bin/env python3
"""Build an optimized SQLite reference database with author initials.

Produces the v2 schema (see docs/database_ingest_v2.md) which adds
``author_initials_text`` to every search table so that citation rendering
can include initials (e.g. "Smith, J. A." instead of just "Smith").

This script is self-contained — it does NOT import from the old
ref_parser package.  All helpers are inlined so it can be run on any
machine that has Python 3.9+ and no extra dependencies.

Usage:
  python scripts/ingest_with_initials.py \\
    --ol-authors  /data/ol_dump_authors.txt.gz \\
    --ol-editions /data/ol_dump_editions.txt.gz \\
    --crossref-resume /data/crossref.resume \\
    --crossref-dir    /data/crossref_dumps \\
    --output          /data/refs_2025_v2.db

Performance notes (SURF / temporary compute):
  - WAL is disabled during ingest (journal_mode=OFF) for maximum write speed.
  - FTS tables are populated via explicit INSERT after each batch rather than
    relying on triggers, so the trigger overhead is zero during the bulk load.
    Triggers are added at the very end so the live DB stays consistent.
  - A single large transaction per source (OL authors, OL editions, Crossref
    shard) keeps fsync overhead minimal.
  - cache_size=-512000 (~512 MB page cache) reduces I/O on large datasets.
  - ANALYZE + VACUUM are run once at the end.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Inlined helpers (no external imports needed)
# ---------------------------------------------------------------------------

_ASCII_FOLD_TRANSLATION = str.maketrans(
    {
        "Æ": "AE",
        "æ": "ae",
        "Ð": "D",
        "ð": "d",
        "Đ": "D",
        "đ": "d",
        "Ħ": "H",
        "ħ": "h",
        "ı": "i",
        "Ł": "L",
        "ł": "l",
        "Ø": "O",
        "ø": "o",
        "Œ": "OE",
        "œ": "oe",
        "Þ": "Th",
        "þ": "th",
        "Ŋ": "N",
        "ŋ": "n",
        "Ŧ": "T",
        "ŧ": "t",
        "ẞ": "SS",
        "ß": "ss",
    }
)


def _ascii_fold(s: str) -> str:
    """Return a best-effort ASCII representation for matching/indexing."""
    t = s.translate(_ASCII_FOLD_TRANSLATION)
    t = unicodedata.normalize("NFKD", t)
    return t.encode("ascii", "ignore").decode("ascii")


def norm_text(s: str | None) -> str:
    """Lowercase, strip diacritics, keep only alphanumeric + spaces."""
    if not s:
        return ""
    t = _ascii_fold(s).lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def extract_initials(given_name: str | None) -> str:
    """Return dot-separated initials from a given name.

    Examples:
        'John'        → 'J.'
        'John Andrew' → 'J. A.'
        'Mary-Kate'   → 'M.'   (hyphenated first part only)
        ''            → ''
    """
    if not given_name:
        return ""
    parts = re.split(r"[\s\-]+", given_name.strip())
    initials = [p[0].upper() + "." for p in parts if p and p[0].isalpha()]
    return " ".join(initials)


def parse_author_name(full_name: str) -> tuple[str, str]:
    """Split a full name into (family, given).

    Handles:
        'Smith, John A.'  → ('Smith', 'John A.')
        'John A. Smith'   → ('Smith', 'John A.')
        'Smith'           → ('Smith', '')
    """
    if not full_name:
        return "", ""
    full_name = full_name.strip()
    if "," in full_name:
        parts = full_name.split(",", 1)
        return parts[0].strip(), parts[1].strip()
    parts = full_name.split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return full_name, ""


# Surname particles used for right-edge walk (mirrors parsing_service.py)
_PARTICLES: frozenset[str] = frozenset({
    "van", "de", "den", "der", "ten", "ter", "te",
    "von", "vom", "zu", "zur",
    "le", "la", "les", "du", "des",
    "del", "da", "das", "dos", "di",
    "della", "degli", "delle", "dal", "dalla", "dai", "dagli",
    "el", "al",
})


def _norm_token(s: str) -> str:
    s = _ascii_fold(s).lower().replace("'", "").replace("-", "")
    return re.sub(r"[^a-z]", "", s)


def surname_candidates(raw_author: str) -> list[str]:
    """Return normalized surname aliases (mirrors parsing_service.surname_candidates)."""
    a = raw_author.strip()
    a_clean = re.sub(r"[\(\)\[\]\{\}]", " ", a)
    a_clean = re.sub(r"\s+", " ", a_clean).strip()

    if "," in a_clean:
        primary_raw = a_clean.split(",", 1)[0].strip()
    else:
        toks = [t for t in a_clean.split() if t]
        if not toks:
            return []
        if len(toks) == 1:
            primary_raw = toks[0]
        else:
            i = len(toks) - 1
            while i > 1 and _norm_token(toks[i - 1]) in _PARTICLES:
                i -= 1
            if i == 1 and _norm_token(toks[0]) in _PARTICLES:
                i = 0
            primary_raw = " ".join(toks[i:])

    primary_spaced = " ".join(_norm_token(t) for t in primary_raw.split() if _norm_token(t))

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(c: str) -> None:
        if c and len(c) >= 3 and not re.fullmatch(r"[a-z]{1,2}", c) and c not in seen:
            seen.add(c)
            candidates.append(c)

    _add(primary_spaced)
    collapsed = primary_spaced.replace(" ", "")
    if collapsed != primary_spaced:
        _add(collapsed)
    spaced_tokens = [t for t in primary_spaced.split() if t]
    if spaced_tokens:
        bare = spaced_tokens[-1]
        if bare != primary_spaced:
            _add(bare)
    all_toks_norm = [_norm_token(t) for t in a_clean.replace(",", " ").split() if _norm_token(t)]
    if all_toks_norm:
        _add(all_toks_norm[-1])
    return candidates


def _normalize_year(text: str | None) -> int | None:
    if not text:
        return None
    for token in str(text).replace("/", " ").split():
        if len(token) == 4 and token.isdigit():
            return int(token)
    return None


def _first_value(items: Any) -> str | None:
    if not isinstance(items, list) or not items:
        return None
    value = items[0]
    return str(value).strip() if value else None


# ---------------------------------------------------------------------------
# Crossref type → table mapping
# ---------------------------------------------------------------------------

def _crossref_table(item_type: str | None) -> str | None:
    typ = (item_type or "").lower()
    if typ in {"journal-article", "article-journal", "journal", "posted-content", "dataset"}:
        return "search_journal"
    if typ in {"book-chapter", "chapter", "reference-entry"}:
        return "search_book_chapter"
    if typ in {"conference-paper", "proceedings-article", "paper-conference"}:
        return "search_conference"
    if typ in {"book", "monograph", "edited-book", "reference-book", "report"}:
        return "search_book"
    return None


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

_SCHEMA_SQL = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA temp_store=MEMORY;
PRAGMA locking_mode=EXCLUSIVE;
PRAGMA cache_size=-512000;
PRAGMA foreign_keys=OFF;

-- Staging tables (dropped after ingest)
CREATE TABLE ol_authors (
    ol_key   TEXT PRIMARY KEY,
    name     TEXT NOT NULL,
    name_norm TEXT NOT NULL
);

CREATE TABLE ol_editions (
    ol_key               TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    publish_date         TEXT,
    publisher            TEXT,
    isbn_13              TEXT,
    isbn_10              TEXT,
    author_surnames_text TEXT NOT NULL DEFAULT '',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text          TEXT NOT NULL DEFAULT ''
);

-- search_journal
CREATE TABLE search_journal (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    doi                  TEXT UNIQUE,
    title                TEXT NOT NULL,
    journal              TEXT,
    volume               TEXT,
    issue                TEXT,
    pages                TEXT,
    title_norm           TEXT NOT NULL,
    container_text       TEXT NOT NULL DEFAULT '',
    year                 INTEGER,
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text          TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'crossref',
    source_id            TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_sj_doi  ON search_journal(doi) WHERE doi IS NOT NULL;
CREATE INDEX        idx_sj_year ON search_journal(year) WHERE year IS NOT NULL;
CREATE INDEX        idx_sj_sid  ON search_journal(source_id);

CREATE VIRTUAL TABLE search_journal_fts USING fts5(
    title_norm,
    author_text,
    container_text,
    content='search_journal',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- search_book
CREATE TABLE search_book (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    doi                  TEXT,
    isbn                 TEXT,
    ol_key               TEXT,
    title                TEXT NOT NULL,
    publisher            TEXT,
    title_norm           TEXT NOT NULL,
    year                 INTEGER,
    publisher_norm       TEXT,
    container_text       TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text          TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL,
    source_id            TEXT NOT NULL
);
CREATE UNIQUE INDEX uidx_sb_doi        ON search_book(doi)         WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX uidx_sb_isbn_src   ON search_book(isbn, source) WHERE isbn IS NOT NULL;
CREATE INDEX        idx_sb_isbn        ON search_book(isbn)         WHERE isbn IS NOT NULL;
CREATE INDEX        idx_sb_year        ON search_book(year)         WHERE year IS NOT NULL;
CREATE INDEX        idx_sb_sid         ON search_book(source_id);

CREATE VIRTUAL TABLE search_book_fts USING fts5(
    title_norm,
    author_text,
    container_text,
    content='search_book',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- search_book_chapter
CREATE TABLE search_book_chapter (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    doi                   TEXT,
    title                 TEXT NOT NULL,
    book_title            TEXT,
    publisher             TEXT,
    title_norm            TEXT NOT NULL,
    year                  INTEGER,
    book_title_norm       TEXT,
    container_text        TEXT NOT NULL DEFAULT '',
    author_surnames_json  TEXT NOT NULL DEFAULT '[]',
    author_initials_text  TEXT NOT NULL DEFAULT '',
    author_text           TEXT NOT NULL DEFAULT '',
    editor_surnames_json  TEXT NOT NULL DEFAULT '[]',
    editor_initials_text  TEXT NOT NULL DEFAULT '',
    editor_text           TEXT NOT NULL DEFAULT '',
    author_surnames_text  TEXT NOT NULL DEFAULT '',
    editor_surnames_text  TEXT NOT NULL DEFAULT '',
    source                TEXT NOT NULL DEFAULT 'crossref',
    source_id             TEXT NOT NULL
);
CREATE UNIQUE INDEX uidx_sbc_doi  ON search_book_chapter(doi)  WHERE doi IS NOT NULL;
CREATE INDEX        idx_sbc_year  ON search_book_chapter(year) WHERE year IS NOT NULL;
CREATE INDEX        idx_sbc_sid   ON search_book_chapter(source_id);

CREATE VIRTUAL TABLE search_book_chapter_fts USING fts5(
    title_norm,
    author_text,
    editor_text,
    container_text,
    content='search_book_chapter',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- search_conference
CREATE TABLE search_conference (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    doi                  TEXT,
    title                TEXT NOT NULL,
    container            TEXT,
    volume               TEXT,
    pages                TEXT,
    title_norm           TEXT NOT NULL,
    year                 INTEGER,
    container_norm       TEXT,
    container_text       TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text          TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source               TEXT NOT NULL DEFAULT 'crossref',
    source_id            TEXT NOT NULL
);
CREATE UNIQUE INDEX uidx_sc_doi  ON search_conference(doi)  WHERE doi IS NOT NULL;
CREATE INDEX        idx_sc_year  ON search_conference(year) WHERE year IS NOT NULL;
CREATE INDEX        idx_sc_sid   ON search_conference(source_id);

CREATE VIRTUAL TABLE search_conference_fts USING fts5(
    title_norm,
    author_text,
    container_text,
    content='search_conference',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- Crossref tracking table
CREATE TABLE crossref_works (
    doi         TEXT PRIMARY KEY,
    work_type   TEXT,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""

# FTS triggers added AFTER bulk ingest so they don't fire during INSERT
_FTS_TRIGGERS_SQL = """
-- search_journal triggers
CREATE TRIGGER search_journal_ai AFTER INSERT ON search_journal BEGIN
    INSERT INTO search_journal_fts(rowid, title_norm, author_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.container_text);
END;
CREATE TRIGGER search_journal_ad AFTER DELETE ON search_journal BEGIN
    INSERT INTO search_journal_fts(search_journal_fts, rowid, title_norm, author_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.container_text);
END;
CREATE TRIGGER search_journal_au AFTER UPDATE ON search_journal BEGIN
    INSERT INTO search_journal_fts(search_journal_fts, rowid, title_norm, author_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.container_text);
    INSERT INTO search_journal_fts(rowid, title_norm, author_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.container_text);
END;

-- search_book triggers
CREATE TRIGGER search_book_ai AFTER INSERT ON search_book BEGIN
    INSERT INTO search_book_fts(rowid, title_norm, author_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.container_text);
END;
CREATE TRIGGER search_book_ad AFTER DELETE ON search_book BEGIN
    INSERT INTO search_book_fts(search_book_fts, rowid, title_norm, author_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.container_text);
END;
CREATE TRIGGER search_book_au AFTER UPDATE ON search_book BEGIN
    INSERT INTO search_book_fts(search_book_fts, rowid, title_norm, author_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.container_text);
    INSERT INTO search_book_fts(rowid, title_norm, author_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.container_text);
END;

-- search_book_chapter triggers
CREATE TRIGGER search_book_chapter_ai AFTER INSERT ON search_book_chapter BEGIN
    INSERT INTO search_book_chapter_fts(rowid, title_norm, author_text, editor_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.editor_text, new.container_text);
END;
CREATE TRIGGER search_book_chapter_ad AFTER DELETE ON search_book_chapter BEGIN
    INSERT INTO search_book_chapter_fts(search_book_chapter_fts, rowid, title_norm, author_text, editor_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.editor_text, old.container_text);
END;
CREATE TRIGGER search_book_chapter_au AFTER UPDATE ON search_book_chapter BEGIN
    INSERT INTO search_book_chapter_fts(search_book_chapter_fts, rowid, title_norm, author_text, editor_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.editor_text, old.container_text);
    INSERT INTO search_book_chapter_fts(rowid, title_norm, author_text, editor_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.editor_text, new.container_text);
END;

-- search_conference triggers
CREATE TRIGGER search_conference_ai AFTER INSERT ON search_conference BEGIN
    INSERT INTO search_conference_fts(rowid, title_norm, author_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.container_text);
END;
CREATE TRIGGER search_conference_ad AFTER DELETE ON search_conference BEGIN
    INSERT INTO search_conference_fts(search_conference_fts, rowid, title_norm, author_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.container_text);
END;
CREATE TRIGGER search_conference_au AFTER UPDATE ON search_conference BEGIN
    INSERT INTO search_conference_fts(search_conference_fts, rowid, title_norm, author_text, container_text)
    VALUES ('delete', old.id, old.title_norm, old.author_text, old.container_text);
    INSERT INTO search_conference_fts(rowid, title_norm, author_text, container_text)
    VALUES (new.id, new.title_norm, new.author_text, new.container_text);
END;
"""


# ---------------------------------------------------------------------------
# OpenLibrary ingest
# ---------------------------------------------------------------------------

def _iter_gz_lines(path: Path) -> Iterable[str]:
    with gzip.open(path, "rt", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            yield line.rstrip("\n")


def _ol_parse_line(line: str) -> tuple[str, str, dict[str, Any]] | None:
    parts = line.split("\t", 4)
    if len(parts) != 5:
        return None
    try:
        payload = json.loads(parts[4])
    except json.JSONDecodeError:
        return None
    return parts[1], parts[3], payload


def _ol_author_key(author_entry: Any) -> str | None:
    """Return an OpenLibrary author key from common dump entry shapes."""
    if isinstance(author_entry, dict):
        key = author_entry.get("key")
        if not key and isinstance(author_entry.get("author"), dict):
            key = author_entry["author"].get("key")
    elif isinstance(author_entry, str):
        key = author_entry
    else:
        return None
    key_text = str(key or "").strip()
    return key_text or None


def _ingest_ol_authors(conn: sqlite3.Connection, authors_path: Path) -> int:
    """Load OL authors into staging table."""
    rows: list[tuple[str, str, str]] = []
    total = 0
    for line in _iter_gz_lines(authors_path):
        parsed = _ol_parse_line(line)
        if not parsed:
            continue
        ol_key, _ts, payload = parsed
        name = str(payload.get("name") or "").strip()
        if not name:
            continue
        rows.append((ol_key, name, norm_text(name)))
        total += 1
        if len(rows) >= 10_000:
            conn.executemany(
                "INSERT OR IGNORE INTO ol_authors (ol_key, name, name_norm) VALUES (?, ?, ?)",
                rows,
            )
            conn.commit()
            rows.clear()
            if total % 500_000 == 0:
                print(f"  ol_authors streamed: {total:,}", flush=True)
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO ol_authors (ol_key, name, name_norm) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    print(f"ol_authors: {total:,}", flush=True)
    return total


def _insert_ol_book_batch(conn: sqlite3.Connection, book_rows: list[tuple]) -> None:
    """Insert an OL book batch and index only rows that actually landed."""
    conn.executemany(
        """INSERT OR IGNORE INTO search_book
           (doi, isbn, ol_key, title, publisher, title_norm, year,
            publisher_norm, container_text, author_surnames_json, author_initials_text,
            author_text, author_surnames_text, source, source_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        book_rows,
    )

    source_ids = [str(row[14]) for row in book_rows if row[14]]
    for start in range(0, len(source_ids), 500):
        chunk = source_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        fts_rows = conn.execute(
            f"""SELECT id, title_norm, author_text, container_text FROM search_book
                WHERE source='openlibrary' AND source_id IN ({placeholders})""",
            chunk,
        ).fetchall()
        conn.executemany(
            "INSERT INTO search_book_fts(rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
            [(row[0], row[1], row[2], row[3]) for row in fts_rows],
        )


def _ingest_ol_editions(conn: sqlite3.Connection, editions_path: Path) -> int:
    """Load OL editions into staging table, then copy to search_book."""

    @lru_cache(maxsize=500_000)
    def author_name(ol_key: str) -> str | None:
        row = conn.execute(
            "SELECT name FROM ol_authors WHERE ol_key = ?", (ol_key,)
        ).fetchone()
        return row[0] if row else None

    # --- Stage 1: populate ol_editions ---
    edition_rows: list[tuple] = []
    total_editions = 0

    for line in _iter_gz_lines(editions_path):
        parsed = _ol_parse_line(line)
        if not parsed:
            continue
        ol_key, _ts, payload = parsed
        title = str(payload.get("title") or "").strip()
        if not title:
            continue

        authors_raw = payload.get("authors") or []
        if not isinstance(authors_raw, list):
            authors_raw = []
        author_names = [
            author_name(key)
            for a in authors_raw
            if (key := _ol_author_key(a))
        ]
        author_names = [n for n in author_names if n]

        # Build per-author (surname, initials) pairs
        surnames: list[str] = []
        initials_list: list[str] = []
        for name in author_names:
            family, given = parse_author_name(name)
            if not family:
                # fallback: use surname_candidates
                cands = surname_candidates(name)
                family = cands[0] if cands else ""
            if family:
                surnames.append(family)
                initials_list.append(extract_initials(given))

        # Deduplicate while preserving order
        seen_s: set[str] = set()
        deduped_surnames: list[str] = []
        deduped_initials: list[str] = []
        for s, ini in zip(surnames, initials_list):
            key = norm_text(s)
            if key and key not in seen_s:
                seen_s.add(key)
                deduped_surnames.append(s)
                deduped_initials.append(ini)

        isbn_13 = _first_value(payload.get("isbn_13"))
        isbn_10 = _first_value(payload.get("isbn_10"))
        publisher = _first_value(payload.get("publishers"))
        publish_date = str(payload.get("publish_date") or "").strip() or None

        author_text = " ".join(norm_text(s) for s in deduped_surnames if s)

        edition_rows.append((
            ol_key,
            title,
            publish_date,
            publisher,
            isbn_13,
            isbn_10,
            "|".join(deduped_surnames),
            "|".join(deduped_initials),
            author_text,
        ))
        total_editions += 1

        if len(edition_rows) >= 10_000:
            conn.executemany(
                """INSERT OR IGNORE INTO ol_editions
                   (ol_key, title, publish_date, publisher, isbn_13, isbn_10,
                    author_surnames_text, author_initials_text, author_text)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                edition_rows,
            )
            conn.commit()
            edition_rows.clear()
            if total_editions % 500_000 == 0:
                print(f"  ol_editions streamed: {total_editions:,}", flush=True)

    if edition_rows:
        conn.executemany(
            """INSERT OR IGNORE INTO ol_editions
               (ol_key, title, publish_date, publisher, isbn_13, isbn_10,
                author_surnames_text, author_initials_text, author_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            edition_rows,
        )
        conn.commit()
    print(f"ol_editions staged: {total_editions:,}", flush=True)

    # --- Stage 2: copy ol_editions → search_book ---
    book_rows: list[tuple] = []
    total_books = 0

    cur = conn.execute(
        """SELECT ol_key, title, publish_date, publisher, isbn_13, isbn_10,
                  author_surnames_text, author_initials_text, author_text
           FROM ol_editions"""
    )
    for row in cur:
        surnames_raw = row[6] or ""
        initials_raw = row[7] or ""
        author_text  = row[8] or ""
        surnames_list = [s for s in surnames_raw.split("|") if s]
        title_norm = norm_text(row[1])
        year = _normalize_year(row[2])
        isbn = row[4] or row[5] or None
        publisher = row[3]
        publisher_norm = norm_text(publisher) if publisher else None
        container_text = publisher_norm or ""

        book_rows.append((
            None,           # doi
            isbn,           # isbn
            row[0],         # ol_key
            row[1],         # title
            publisher,
            title_norm,
            year,
            publisher_norm,
            container_text,
            json.dumps(surnames_list, ensure_ascii=False),
            initials_raw,
            author_text,
            surnames_raw,
            "openlibrary",
            row[0],         # source_id = ol_key
        ))
        total_books += 1

        if len(book_rows) >= 10_000:
            _insert_ol_book_batch(conn, book_rows)
            conn.commit()
            book_rows.clear()
            if total_books % 500_000 == 0:
                print(f"  search_book(ol) streamed: {total_books:,}", flush=True)

    if book_rows:
        _insert_ol_book_batch(conn, book_rows)
        conn.commit()

    print(f"search_book(openlibrary): {total_books:,}", flush=True)
    return total_books


# ---------------------------------------------------------------------------
# Crossref ingest
# ---------------------------------------------------------------------------

def _crossref_authors(item: dict[str, Any], key: str = "author") -> tuple[list[str], list[str]]:
    """Return (surnames, initials_list) from a Crossref author/editor array."""
    surnames: list[str] = []
    initials_list: list[str] = []
    for a in item.get(key) or []:
        family = str(a.get("family") or "").strip()
        given  = str(a.get("given")  or "").strip()
        if family:
            surnames.append(family)
            initials_list.append(extract_initials(given))
    return surnames, initials_list


def _ingest_crossref(
    conn: sqlite3.Connection,
    resume_path: Path,
    crossref_dir: Path,
) -> int:
    shard_names = [
        line.strip()
        for line in resume_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    total_works = 0
    insert_counts: dict[str, int] = {
        "search_journal": 0,
        "search_book": 0,
        "search_book_chapter": 0,
        "search_conference": 0,
    }

    for shard_idx, shard_name in enumerate(shard_names, start=1):
        shard_path = crossref_dir / shard_name
        if not shard_path.is_file():
            print(f"  skip missing shard: {shard_path}", flush=True)
            continue

        with gzip.open(shard_path, "rt", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # --- Extract fields ---
                title_raw = ""
                t_list = item.get("title")
                if isinstance(t_list, list) and t_list:
                    title_raw = str(t_list[0]).strip()
                elif isinstance(t_list, str):
                    title_raw = t_list.strip()
                if not title_raw:
                    continue

                doi = str(item.get("DOI") or "").strip() or None
                typ = item.get("type") or None
                table = _crossref_table(typ)
                if not table:
                    continue

                # Year
                year: int | None = None
                for date_key in ("published-print", "published-online", "created", "issued"):
                    parts = ((item.get(date_key) or {}).get("date-parts") or [])
                    if parts and isinstance(parts[0], list) and parts[0]:
                        y = parts[0][0]
                        if y:
                            try:
                                year = int(y)
                            except (ValueError, TypeError):
                                pass
                            break

                # Container title
                ct_list = item.get("container-title")
                container = ""
                if isinstance(ct_list, list) and ct_list:
                    container = str(ct_list[0]).strip()
                elif isinstance(ct_list, str):
                    container = ct_list.strip()

                publisher = str(item.get("publisher") or "").strip() or None
                volume = str(item.get("volume") or "").strip() or None
                issue  = str(item.get("issue")  or "").strip() or None
                pages  = str(item.get("page")   or "").strip() or None

                # Authors & editors
                author_surnames, author_initials = _crossref_authors(item, "author")
                editor_surnames, editor_initials = _crossref_authors(item, "editor")

                # If no authors, fall back to editors (e.g. edited books)
                if not author_surnames and editor_surnames:
                    author_surnames = editor_surnames
                    author_initials = editor_initials

                author_text          = " ".join(norm_text(s) for s in author_surnames if s)
                author_initials_text = "|".join(author_initials)
                author_surnames_text = "|".join(author_surnames)
                author_surnames_json = json.dumps(author_surnames, ensure_ascii=False)

                editor_text          = " ".join(norm_text(s) for s in editor_surnames if s)
                editor_initials_text = "|".join(editor_initials)
                editor_surnames_text = "|".join(editor_surnames)
                editor_surnames_json = json.dumps(editor_surnames, ensure_ascii=False)

                title_norm = norm_text(title_raw)
                source_id  = doi or f"crossref:{total_works}"
                container_norm = norm_text(container) if container else ""
                publisher_norm = norm_text(publisher) if publisher else ""

                # Book title for chapters
                book_title: str | None = None
                if table == "search_book_chapter":
                    book_title = container or None
                book_title_norm = norm_text(book_title) if book_title else ""
                chapter_container_text = " ".join(
                    part for part in (book_title_norm, publisher_norm) if part
                )

                # --- Insert ---
                try:
                    if table == "search_journal":
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO search_journal
                               (doi, title, journal, volume, issue, pages,
                                title_norm, container_text, year,
                                author_surnames_json, author_initials_text,
                                author_text, author_surnames_text,
                                source, source_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (doi, title_raw, container or None, volume, issue, pages,
                             title_norm, container_norm, year,
                             author_surnames_json, author_initials_text,
                             author_text, author_surnames_text,
                             "crossref", source_id),
                        )
                    elif table == "search_book":
                        isbn = _first_value(item.get("ISBN"))
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO search_book
                               (doi, isbn, title, publisher, title_norm, year,
                                publisher_norm, container_text,
                                author_surnames_json, author_initials_text,
                                author_text, author_surnames_text,
                                source, source_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (doi, isbn, title_raw, publisher, title_norm, year,
                             publisher_norm or None, publisher_norm,
                             author_surnames_json, author_initials_text,
                             author_text, author_surnames_text,
                             "crossref", source_id),
                        )
                    elif table == "search_book_chapter":
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO search_book_chapter
                               (doi, title, book_title, publisher,
                                title_norm, year, book_title_norm, container_text,
                                author_surnames_json, author_initials_text,
                                author_text,
                                editor_surnames_json, editor_initials_text,
                                editor_text,
                                author_surnames_text, editor_surnames_text,
                                source, source_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (doi, title_raw, book_title, publisher,
                             title_norm, year,
                             book_title_norm or None, chapter_container_text,
                             author_surnames_json, author_initials_text,
                             author_text,
                             editor_surnames_json, editor_initials_text,
                             editor_text,
                             author_surnames_text, editor_surnames_text,
                             "crossref", source_id),
                        )
                    else:  # search_conference
                        cursor = conn.execute(
                            """INSERT OR IGNORE INTO search_conference
                               (doi, title, container, volume, pages,
                                title_norm, year, container_norm, container_text,
                                author_surnames_json, author_initials_text,
                                author_text, author_surnames_text,
                                source, source_id)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (doi, title_raw, container or None, volume, pages,
                             title_norm, year,
                             container_norm or None, container_norm,
                             author_surnames_json, author_initials_text,
                             author_text, author_surnames_text,
                             "crossref", source_id),
                        )
                except sqlite3.IntegrityError:
                    # Duplicate DOI — skip silently
                    continue

                if cursor.rowcount <= 0:
                    continue

                rowid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

                # Populate FTS immediately (no trigger overhead during bulk load)
                if table == "search_book_chapter":
                    conn.execute(
                        "INSERT INTO search_book_chapter_fts(rowid, title_norm, author_text, editor_text, container_text) VALUES (?, ?, ?, ?, ?)",
                        (rowid, title_norm, author_text, editor_text, chapter_container_text),
                    )
                else:
                    fts_container_text = (
                        publisher_norm if table == "search_book" else container_norm
                    )
                    conn.execute(
                        f"INSERT INTO {table}_fts(rowid, title_norm, author_text, container_text) VALUES (?, ?, ?, ?)",
                        (rowid, title_norm, author_text, fts_container_text),
                    )

                if doi:
                    conn.execute(
                        "INSERT OR REPLACE INTO crossref_works (doi, work_type) VALUES (?, ?)",
                        (doi, typ),
                    )

                insert_counts[table] += 1
                total_works += 1

        conn.commit()
        if shard_idx % 50 == 0:
            print(
                f"  crossref shards: {shard_idx}/{len(shard_names)} "
                f"({total_works:,} works)",
                flush=True,
            )

    print(f"crossref_works total: {total_works:,}", flush=True)
    for tbl, cnt in insert_counts.items():
        print(f"  {tbl}: {cnt:,}", flush=True)
    return total_works


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--ol-authors",      type=Path, required=True,
                    help="OpenLibrary authors dump (.txt.gz)")
    ap.add_argument("--ol-editions",     type=Path, required=True,
                    help="OpenLibrary editions dump (.txt.gz)")
    ap.add_argument("--crossref-resume", type=Path, required=True,
                    help="Crossref resume file (one shard filename per line)")
    ap.add_argument("--crossref-dir",    type=Path, required=True,
                    help="Directory containing Crossref shard files")
    ap.add_argument("--output",          type=Path, required=True,
                    help="Output SQLite database path")
    ap.add_argument("--skip-ol",         action="store_true",
                    help="Skip OpenLibrary ingest (useful for Crossref-only rebuild)")
    ap.add_argument("--skip-crossref",   action="store_true",
                    help="Skip Crossref ingest (useful for OL-only rebuild)")
    args = ap.parse_args()

    if args.output.exists():
        print(f"Removing existing database: {args.output}", flush=True)
        args.output.unlink()

    print(f"Creating database: {args.output}", flush=True)
    conn = sqlite3.connect(str(args.output))
    conn.row_factory = sqlite3.Row

    try:
        print("Creating schema …", flush=True)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        if not args.skip_ol:
            print("\n[1/3] Ingesting OpenLibrary authors …", flush=True)
            _ingest_ol_authors(conn, args.ol_authors)

            print("\n[2/3] Ingesting OpenLibrary editions → search_book …", flush=True)
            _ingest_ol_editions(conn, args.ol_editions)
        else:
            print("\n[1-2/3] Skipping OpenLibrary (--skip-ol)", flush=True)

        if not args.skip_crossref:
            print("\n[3/3] Ingesting Crossref …", flush=True)
            _ingest_crossref(conn, args.crossref_resume, args.crossref_dir)
        else:
            print("\n[3/3] Skipping Crossref (--skip-crossref)", flush=True)

        print("\nAdding FTS triggers …", flush=True)
        conn.executescript(_FTS_TRIGGERS_SQL)
        conn.commit()

        print("Dropping staging tables …", flush=True)
        conn.executescript("""
            DROP TABLE IF EXISTS ol_authors;
            DROP TABLE IF EXISTS ol_editions;
        """)
        conn.commit()

        print("Running ANALYZE …", flush=True)
        conn.execute("ANALYZE")
        conn.commit()

        print("Running VACUUM …", flush=True)
        conn.execute("VACUUM")
        conn.commit()

    finally:
        conn.close()

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"\nDone. Database written to: {args.output}  ({size_mb:.1f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
