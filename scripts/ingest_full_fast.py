#!/usr/bin/env python3
"""Single-file full DB rebuild with resilient OL ingest and batched Crossref.

The final DB schema matches ingest_with_initials.py, but this script delays FTS
population until after all base rows are loaded.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any


def _ts() -> str:
    """Return a short timestamp string for progress output."""
    return datetime.now().strftime("%H:%M:%S")


_ASCII_FOLD_TRANSLATION = str.maketrans(
    {
        "Æ": "AE", "æ": "ae", "Ð": "D", "ð": "d", "Đ": "D", "đ": "d",
        "Ħ": "H", "ħ": "h", "ı": "i", "Ł": "L", "ł": "l", "Ø": "O",
        "ø": "o", "Œ": "OE", "œ": "oe", "Þ": "Th", "þ": "th",
        "Ŋ": "N", "ŋ": "n", "Ŧ": "T", "ŧ": "t", "ẞ": "SS", "ß": "ss",
    }
)


def _ascii_fold(s: str) -> str:
    t = s.translate(_ASCII_FOLD_TRANSLATION)
    t = unicodedata.normalize("NFKD", t)
    return t.encode("ascii", "ignore").decode("ascii")


def norm_text(s: str | None) -> str:
    if not s:
        return ""
    t = _ascii_fold(s).lower()
    t = re.sub(r"[^a-z0-9\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def extract_initials(given_name: str | None) -> str:
    if not given_name:
        return ""
    parts = re.split(r"[\s\-]+", given_name.strip())
    return " ".join(p[0].upper() + "." for p in parts if p and p[0].isalpha())


def parse_author_name(full_name: str) -> tuple[str, str]:
    if not full_name:
        return "", ""
    full_name = full_name.strip()
    if "," in full_name:
        family, given = full_name.split(",", 1)
        return family.strip(), given.strip()
    parts = full_name.split()
    if len(parts) >= 2:
        return parts[-1], " ".join(parts[:-1])
    return full_name, ""


_PARTICLES: frozenset[str] = frozenset({
    "van", "de", "den", "der", "ten", "ter", "te", "von", "vom", "zu",
    "zur", "le", "la", "les", "du", "des", "del", "da", "das", "dos",
    "di", "della", "degli", "delle", "dal", "dalla", "dai", "dagli",
    "el", "al",
})


def _norm_token(s: str) -> str:
    s = _ascii_fold(s).lower().replace("'", "").replace("-", "")
    return re.sub(r"[^a-z]", "", s)


def surname_candidates(raw_author: str) -> list[str]:
    a_clean = re.sub(r"[\(\)\[\]\{\}]", " ", raw_author.strip())
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

    def add(candidate: str) -> None:
        if candidate and len(candidate) >= 3 and candidate not in seen:
            seen.add(candidate)
            candidates.append(candidate)

    add(primary_spaced)
    collapsed = primary_spaced.replace(" ", "")
    if collapsed != primary_spaced:
        add(collapsed)
    tokens = [t for t in primary_spaced.split() if t]
    if tokens:
        add(tokens[-1])
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


_SCHEMA_SQL = """
PRAGMA journal_mode=OFF;
PRAGMA synchronous=OFF;
PRAGMA temp_store=MEMORY;
PRAGMA locking_mode=EXCLUSIVE;
PRAGMA cache_size=-4194304;   
PRAGMA mmap_size=17179869184; 
PRAGMA foreign_keys=OFF;

CREATE TABLE ol_authors (
    ol_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_norm TEXT NOT NULL
);

CREATE TABLE ol_editions (
    ol_key TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    publish_date TEXT,
    publisher TEXT,
    isbn_13 TEXT,
    isbn_10 TEXT,
    author_surnames_text TEXT NOT NULL DEFAULT '',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT ''
);

CREATE TABLE search_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT UNIQUE,
    title TEXT NOT NULL,
    journal TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    title_norm TEXT NOT NULL,
    container_text TEXT NOT NULL DEFAULT '',
    year INTEGER,
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'crossref',
    source_id TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_sj_doi ON search_journal(doi) WHERE doi IS NOT NULL;
CREATE VIRTUAL TABLE search_journal_fts USING fts5(
    title_norm, author_text, container_text,
    content='search_journal', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE search_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    isbn TEXT,
    ol_key TEXT,
    title TEXT NOT NULL,
    publisher TEXT,
    title_norm TEXT NOT NULL,
    year INTEGER,
    publisher_norm TEXT,
    container_text TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_id TEXT NOT NULL
);
CREATE UNIQUE INDEX uidx_sb_doi ON search_book(doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX uidx_sb_isbn_src ON search_book(isbn, source) WHERE isbn IS NOT NULL;
CREATE VIRTUAL TABLE search_book_fts USING fts5(
    title_norm, author_text, container_text,
    content='search_book', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE search_book_chapter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    title TEXT NOT NULL,
    book_title TEXT,
    publisher TEXT,
    title_norm TEXT NOT NULL,
    year INTEGER,
    book_title_norm TEXT,
    container_text TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    editor_surnames_json TEXT NOT NULL DEFAULT '[]',
    editor_initials_text TEXT NOT NULL DEFAULT '',
    editor_text TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    editor_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'crossref',
    source_id TEXT NOT NULL
);
CREATE UNIQUE INDEX uidx_sbc_doi ON search_book_chapter(doi) WHERE doi IS NOT NULL;
CREATE VIRTUAL TABLE search_book_chapter_fts USING fts5(
    title_norm, author_text, editor_text, container_text,
    content='search_book_chapter', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE search_conference (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    title TEXT NOT NULL,
    container TEXT,
    volume TEXT,
    pages TEXT,
    title_norm TEXT NOT NULL,
    year INTEGER,
    container_norm TEXT,
    container_text TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'crossref',
    source_id TEXT NOT NULL
);
CREATE UNIQUE INDEX uidx_sc_doi ON search_conference(doi) WHERE doi IS NOT NULL;
CREATE VIRTUAL TABLE search_conference_fts USING fts5(
    title_norm, author_text, container_text,
    content='search_conference', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE crossref_works (
    doi TEXT PRIMARY KEY,
    work_type TEXT,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ','now'))
);
"""


_POST_LOAD_INDEX_SQL = """
CREATE INDEX idx_sj_year ON search_journal(year) WHERE year IS NOT NULL;
CREATE INDEX idx_sj_sid ON search_journal(source_id);

CREATE INDEX idx_sb_isbn ON search_book(isbn) WHERE isbn IS NOT NULL;
CREATE INDEX idx_sb_year ON search_book(year) WHERE year IS NOT NULL;
CREATE INDEX idx_sb_sid ON search_book(source_id);

CREATE INDEX idx_sbc_year ON search_book_chapter(year) WHERE year IS NOT NULL;
CREATE INDEX idx_sbc_sid ON search_book_chapter(source_id);

CREATE INDEX idx_sc_year ON search_conference(year) WHERE year IS NOT NULL;
CREATE INDEX idx_sc_sid ON search_conference(source_id);
"""


_FTS_TRIGGERS_SQL = """
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


SEARCH_TABLES = (
    "search_journal",
    "search_book",
    "search_book_chapter",
    "search_conference",
)


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
    rows: list[tuple[str, str, str]] = []
    total = 0
    t0 = time.monotonic()
    for line in _iter_gz_lines(authors_path):
        parsed = _ol_parse_line(line)
        if not parsed:
            continue
        ol_key, _ts_str, payload = parsed
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
                elapsed = time.monotonic() - t0
                print(f"  [{_ts()}] ol_authors streamed: {total:,} ({elapsed:.0f}s)", flush=True)
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO ol_authors (ol_key, name, name_norm) VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    elapsed = time.monotonic() - t0
    print(f"[{_ts()}] ol_authors: {total:,} (done in {elapsed:.0f}s)", flush=True)
    return total


def _insert_ol_book_batch(conn: sqlite3.Connection, book_rows: list[tuple]) -> None:
    conn.executemany(
        """INSERT OR IGNORE INTO search_book
           (doi, isbn, ol_key, title, publisher, title_norm, year,
            publisher_norm, container_text, author_surnames_json, author_initials_text,
            author_text, author_surnames_text, source, source_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        book_rows,
    )


def _ingest_ol_editions(conn: sqlite3.Connection, editions_path: Path) -> int:
    # Preload all authors into memory for fast O(1) lookups (avoids per-row DB queries).
    print(f"  [{_ts()}] Preloading ol_authors into memory ...", flush=True)
    t0_editions = time.monotonic()
    author_map: dict[str, str] = dict(conn.execute("SELECT ol_key, name FROM ol_authors"))
    print(f"  [{_ts()}] Loaded {len(author_map):,} authors into memory.", flush=True)

    edition_rows: list[tuple] = []
    total_editions = 0
    t0_stream = time.monotonic()
    for line in _iter_gz_lines(editions_path):
        parsed = _ol_parse_line(line)
        if not parsed:
            continue
        ol_key, _ts_str, payload = parsed
        title = str(payload.get("title") or "").strip()
        if not title:
            continue

        authors_raw = payload.get("authors") or []
        if not isinstance(authors_raw, list):
            authors_raw = []
        author_names = [
            author_map.get(key)
            for author_entry in authors_raw
            if (key := _ol_author_key(author_entry))
        ]
        author_names = [name for name in author_names if name]

        surnames: list[str] = []
        initials_list: list[str] = []
        for name in author_names:
            family, given = parse_author_name(name)
            if not family:
                cands = surname_candidates(name)
                family = cands[0] if cands else ""
            if family:
                surnames.append(family)
                initials_list.append(extract_initials(given))

        seen_surnames: set[str] = set()
        deduped_surnames: list[str] = []
        deduped_initials: list[str] = []
        for surname, initials in zip(surnames, initials_list):
            key = norm_text(surname)
            if key and key not in seen_surnames:
                seen_surnames.add(key)
                deduped_surnames.append(surname)
                deduped_initials.append(initials)

        author_text = " ".join(norm_text(s) for s in deduped_surnames if s)
        edition_rows.append((
            ol_key,
            title,
            str(payload.get("publish_date") or "").strip() or None,
            _first_value(payload.get("publishers")),
            _first_value(payload.get("isbn_13")),
            _first_value(payload.get("isbn_10")),
            "|".join(deduped_surnames),
            "|".join(deduped_initials),
            author_text,
        ))
        total_editions += 1
        if len(edition_rows) >= 100_000:
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
                elapsed = time.monotonic() - t0_stream
                print(f"  [{_ts()}] ol_editions streamed: {total_editions:,} ({elapsed:.0f}s)", flush=True)
    if edition_rows:
        conn.executemany(
            """INSERT OR IGNORE INTO ol_editions
               (ol_key, title, publish_date, publisher, isbn_13, isbn_10,
                author_surnames_text, author_initials_text, author_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            edition_rows,
        )
        conn.commit()
    elapsed_stream = time.monotonic() - t0_stream
    print(f"[{_ts()}] ol_editions staged: {total_editions:,} (streamed in {elapsed_stream:.0f}s)", flush=True)

    book_rows: list[tuple] = []
    total_books = 0
    t0_books = time.monotonic()
    cur = conn.execute(
        """SELECT ol_key, title, publish_date, publisher, isbn_13, isbn_10,
                  author_surnames_text, author_initials_text, author_text
           FROM ol_editions"""
    )
    for row in cur:
        surnames_raw = row[6] or ""
        initials_raw = row[7] or ""
        author_text = row[8] or ""
        surnames_list = [s for s in surnames_raw.split("|") if s]
        publisher = row[3]
        publisher_norm = norm_text(publisher) if publisher else None
        book_rows.append((
            None,
            row[4] or row[5] or None,
            row[0],
            row[1],
            publisher,
            norm_text(row[1]),
            _normalize_year(row[2]),
            publisher_norm,
            publisher_norm or "",
            json.dumps(surnames_list, ensure_ascii=False),
            initials_raw,
            author_text,
            surnames_raw,
            "openlibrary",
            row[0],
        ))
        total_books += 1
        if len(book_rows) >= 100_000:
            _insert_ol_book_batch(conn, book_rows)
            conn.commit()
            book_rows.clear()
            if total_books % 500_000 == 0:
                elapsed = time.monotonic() - t0_books
                print(f"  [{_ts()}] search_book(ol) streamed: {total_books:,} ({elapsed:.0f}s)", flush=True)
    if book_rows:
        _insert_ol_book_batch(conn, book_rows)
        conn.commit()
    elapsed_books = time.monotonic() - t0_books
    elapsed_total = time.monotonic() - t0_editions
    print(f"[{_ts()}] search_book(openlibrary): {total_books:,} (books in {elapsed_books:.0f}s, editions total {elapsed_total:.0f}s)", flush=True)
    return total_books


def _crossref_authors(item: dict[str, Any], key: str = "author") -> tuple[list[str], list[str]]:
    surnames: list[str] = []
    initials_list: list[str] = []
    for author in item.get(key) or []:
        if not isinstance(author, dict):
            continue
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        if family:
            surnames.append(family)
            initials_list.append(extract_initials(given))
    return surnames, initials_list


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


def _parse_crossref_item(
    item: dict[str, Any],
    *,
    source_sequence: int,
) -> tuple[str, tuple[Any, ...], tuple[str, str] | None] | None:
    title_raw = _first_title(item)
    if not title_raw:
        return None
    typ = item.get("type") or None
    table = _crossref_table(typ)
    if not table:
        return None

    doi = str(item.get("DOI") or "").strip() or None
    source_id = doi or f"crossref:{source_sequence}"
    container = _container_from_item(item)
    publisher = str(item.get("publisher") or "").strip() or None
    volume = str(item.get("volume") or "").strip() or None
    issue = str(item.get("issue") or "").strip() or None
    pages = str(item.get("page") or "").strip() or None

    author_surnames, author_initials = _crossref_authors(item, "author")
    editor_surnames, editor_initials = _crossref_authors(item, "editor")
    if not author_surnames and editor_surnames:
        author_surnames = editor_surnames
        author_initials = editor_initials

    author_text = " ".join(norm_text(s) for s in author_surnames if s)
    author_initials_text = "|".join(author_initials)
    author_surnames_text = "|".join(author_surnames)
    author_surnames_json = json.dumps(author_surnames, ensure_ascii=False)
    editor_text = " ".join(norm_text(s) for s in editor_surnames if s)
    editor_initials_text = "|".join(editor_initials)
    editor_surnames_text = "|".join(editor_surnames)
    editor_surnames_json = json.dumps(editor_surnames, ensure_ascii=False)

    title_norm = norm_text(title_raw)
    container_norm = norm_text(container) if container else ""
    publisher_norm = norm_text(publisher) if publisher else ""
    year = _year_from_item(item)
    work_row = (doi, str(typ)) if doi else None

    if table == "search_journal":
        return table, (
            doi, title_raw, container or None, volume, issue, pages,
            title_norm, container_norm, year,
            author_surnames_json, author_initials_text,
            author_text, author_surnames_text, "crossref", source_id,
        ), work_row
    if table == "search_book":
        return table, (
            doi, _first_value(item.get("ISBN")), title_raw, publisher, title_norm, year,
            publisher_norm or None, publisher_norm,
            author_surnames_json, author_initials_text,
            author_text, author_surnames_text, "crossref", source_id,
        ), work_row
    if table == "search_book_chapter":
        book_title = container or None
        book_title_norm = norm_text(book_title) if book_title else ""
        chapter_container_text = " ".join(part for part in (book_title_norm, publisher_norm) if part)
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


def _insert_crossref_table_batch(
    conn: sqlite3.Connection,
    table: str,
    rows: list[tuple[Any, ...]],
) -> int:
    if not rows:
        return 0
    # Dedupe within this batch only; INSERT OR IGNORE handles DB-level uniqueness.
    seen_dois: set[str] = set()
    filtered: list[tuple[Any, ...]] = []
    for row in rows:
        doi = str(row[0]) if row[0] else ""
        if doi:
            if doi in seen_dois:
                continue
            seen_dois.add(doi)
        filtered.append(row)
    if not filtered:
        return 0
    before = conn.total_changes
    conn.executemany(INSERT_SQL[table], filtered)
    return conn.total_changes - before


def _flush_crossref_batches(
    conn: sqlite3.Connection,
    rows_by_table: dict[str, list[tuple[Any, ...]]],
    work_rows: list[tuple[str, str]],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in SEARCH_TABLES:
        counts[table] = _insert_crossref_table_batch(conn, table, rows_by_table.get(table, []))
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


def _append_progress(path: Path | None, shard_name: str, counts: dict[str, int]) -> None:
    if path is None:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"shard": shard_name, "counts": counts}, sort_keys=True) + "\n")


def _load_completed(path: Path | None) -> set[str]:
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


def _ingest_crossref_batched(
    conn: sqlite3.Connection,
    resume_path: Path,
    crossref_dir: Path,
    *,
    progress_file: Path | None,
    batch_size: int,
    progress_every: int,
    skip_completed: bool,
) -> int:
    shard_names = [line.strip() for line in resume_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    completed = _load_completed(progress_file) if skip_completed else set()
    total_inserted = 0
    total_counts = {table: 0 for table in SEARCH_TABLES}
    processed_shards = 0
    skipped_shards = 0
    source_sequence = 0
    t0_crossref = time.monotonic()

    for shard_name in shard_names:
        if shard_name in completed:
            skipped_shards += 1
            continue
        shard_path = crossref_dir / shard_name
        if not shard_path.is_file():
            print(f"  skip missing shard: {shard_path}", flush=True)
            continue

        rows_by_table: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
        work_rows: list[tuple[str, str]] = []
        shard_counts = {table: 0 for table in SEARCH_TABLES}
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
                    parsed = _parse_crossref_item(item, source_sequence=source_sequence)
                    if not parsed:
                        continue
                    source_sequence += 1
                    table, row, work_row = parsed
                    rows_by_table[table].append(row)
                    if work_row:
                        work_rows.append(work_row)
                    buffered += 1
                    if buffered >= batch_size:
                        counts = _flush_crossref_batches(conn, rows_by_table, work_rows)
                        _merge_counts(shard_counts, counts)
                        buffered = 0
            counts = _flush_crossref_batches(conn, rows_by_table, work_rows)
            _merge_counts(shard_counts, counts)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        processed_shards += 1
        _append_progress(progress_file, shard_name, shard_counts)
        _merge_counts(total_counts, shard_counts)
        inserted = sum(shard_counts.values())
        total_inserted += inserted
        if processed_shards % progress_every == 0:
            total_to_process = len(shard_names) - skipped_shards
            elapsed = time.monotonic() - t0_crossref
            print(
                f"  [{_ts()}] crossref shards: {processed_shards}/{total_to_process} "
                f"({total_inserted:,} inserted; last={inserted:,}; {elapsed:.0f}s elapsed)",
                flush=True,
            )

    elapsed_crossref = time.monotonic() - t0_crossref
    print(f"[{_ts()}] crossref base rows inserted: {total_inserted:,} (done in {elapsed_crossref:.0f}s)", flush=True)
    for table in SEARCH_TABLES:
        print(f"  {table}: {total_counts[table]:,}", flush=True)
    if skipped_shards:
        print(f"  skipped completed shards: {skipped_shards:,}", flush=True)
    return total_inserted


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    for table in (
        "search_journal_fts",
        "search_book_fts",
        "search_book_chapter_fts",
        "search_conference_fts",
    ):
        t0 = time.monotonic()
        print(f"[{_ts()}] Rebuilding {table} ...", flush=True)
        conn.execute(f"INSERT INTO {table}({table}) VALUES ('rebuild')")
        conn.commit()
        print(f"[{_ts()}] {table} done in {time.monotonic() - t0:.0f}s", flush=True)


def _drop_staging_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        DROP TABLE IF EXISTS ol_authors;
        DROP TABLE IF EXISTS ol_editions;
        """
    )
    conn.commit()


def main_from_args(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--ol-authors", type=Path, required=True)
    parser.add_argument("--ol-editions", type=Path, required=True)
    parser.add_argument("--crossref-resume", type=Path, required=True)
    parser.add_argument("--crossref-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--crossref-progress-file", type=Path)
    parser.add_argument("--crossref-batch-size", type=int, default=100_000)
    parser.add_argument("--crossref-progress-every", type=int, default=50)
    parser.add_argument("--skip-ol", action="store_true")
    parser.add_argument("--skip-crossref", action="store_true")
    parser.add_argument("--keep-crossref-progress", action="store_true")
    parser.add_argument("--no-vacuum", action="store_true")
    args = parser.parse_args(argv)

    if args.output.exists():
        print(f"Removing existing database: {args.output}", flush=True)
        args.output.unlink()
    if (
        args.crossref_progress_file
        and args.crossref_progress_file.exists()
        and not args.keep_crossref_progress
    ):
        print(f"Removing existing Crossref progress file: {args.crossref_progress_file}", flush=True)
        args.crossref_progress_file.unlink()

    print(f"Creating database: {args.output}", flush=True)
    conn = sqlite3.connect(str(args.output))
    conn.row_factory = sqlite3.Row
    try:
        print("Creating schema ...", flush=True)
        conn.executescript(_SCHEMA_SQL)
        conn.commit()

        if args.skip_ol:
            print("\n[1-2/4] Skipping OpenLibrary (--skip-ol)", flush=True)
        else:
            print("\n[1/4] Ingesting OpenLibrary authors ...", flush=True)
            _ingest_ol_authors(conn, args.ol_authors)

            print("\n[2/4] Ingesting OpenLibrary editions -> search_book base rows ...", flush=True)
            _ingest_ol_editions(conn, args.ol_editions)

        if args.skip_crossref:
            print("\n[3/4] Skipping Crossref (--skip-crossref)", flush=True)
        else:
            print("\n[3/4] Ingesting Crossref base rows with batched shard writer ...", flush=True)
            _ingest_crossref_batched(
                conn,
                args.crossref_resume,
                args.crossref_dir,
                progress_file=args.crossref_progress_file,
                batch_size=max(args.crossref_batch_size, 1),
                progress_every=max(args.crossref_progress_every, 1),
                skip_completed=args.keep_crossref_progress,
            )

        print(f"\n[{_ts()}] [4/4] Rebuilding FTS indexes after base-row load ...", flush=True)
        _rebuild_fts(conn)

        t0_idx = time.monotonic()
        print(f"[{_ts()}] Creating post-load lookup indexes ...", flush=True)
        conn.executescript(_POST_LOAD_INDEX_SQL)
        conn.commit()
        print(f"[{_ts()}] Post-load indexes done in {time.monotonic() - t0_idx:.0f}s", flush=True)

        print(f"[{_ts()}] Adding FTS triggers ...", flush=True)
        conn.executescript(_FTS_TRIGGERS_SQL)
        conn.commit()

        print(f"[{_ts()}] Dropping staging tables ...", flush=True)
        _drop_staging_tables(conn)

        t0_analyze = time.monotonic()
        print(f"[{_ts()}] Running ANALYZE ...", flush=True)
        conn.execute("ANALYZE")
        conn.commit()
        print(f"[{_ts()}] ANALYZE done in {time.monotonic() - t0_analyze:.0f}s", flush=True)

        if args.no_vacuum:
            print(f"[{_ts()}] Skipping VACUUM (--no-vacuum)", flush=True)
        else:
            t0_vacuum = time.monotonic()
            print(f"[{_ts()}] Running VACUUM ...", flush=True)
            conn.execute("VACUUM")
            conn.commit()
            print(f"[{_ts()}] VACUUM done in {time.monotonic() - t0_vacuum:.0f}s", flush=True)
    finally:
        conn.close()

    size_mb = args.output.stat().st_size / (1024 * 1024)
    print(f"\n[{_ts()}] Done. Database written to: {args.output}  ({size_mb:.1f} MB)", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main_from_args())
