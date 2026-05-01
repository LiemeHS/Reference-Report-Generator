# Database Ingest Script v2: Optimized with Author Initials and Container Text

## Overview

This document describes an improved database ingest process that:
- ✅ **Captures author initials** from Crossref and OpenLibrary APIs
- ✅ **Indexes normalized container text** for selective title + container + year matching
- ✅ **Maintains backward compatibility** with Reference_Gen2 code
- ✅ **Optimizes storage** by removing redundant fields
- ✅ **Preserves query performance** with efficient FTS indexes

**Key improvement:** Citations in generated reports will now include author initials (e.g., "Smith, J. A." instead of just "Smith").
Phase 4 can also use journal, publisher, book title, and proceedings text in FTS queries, which helps find better non-DOI candidates when a submitted DOI points at unrelated metadata.

---

## What Changed

### Added Fields
- `author_initials_text` - Pipe-separated author initials (e.g., `"J. A.|M. K."`)
- `container_text` - Normalized text indexed by FTS for selective container-aware matching:
  - `search_journal`: normalized `journal`
  - `search_book`: normalized `publisher`
  - `search_book_chapter`: normalized `book_title` plus normalized `publisher` when available
  - `search_conference`: normalized `container`

### Removed Fields (Unused by Reference_Gen2)
- `author_surnames_norm_text` - Redundant with `author_text`
- `journal_norm` - Not accessed by matching code

### Kept for Backward Compatibility
All other fields remain unchanged to ensure Reference_Gen2 continues working without modifications.

Existing DB files remain readable because Phase 4 ignores missing FTS fields per table. To benefit from `container_text`, rebuild the DB with `scripts/ingest_with_initials.py` or migrate each search table and FTS table to include and populate the new column.

---

## Database Schema

### search_journal

```sql
CREATE TABLE search_journal (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT UNIQUE,
    
    -- Display fields
    title TEXT NOT NULL,
    journal TEXT,
    volume TEXT,
    issue TEXT,
    pages TEXT,
    
    -- Matching/search fields
    title_norm TEXT NOT NULL,
    container_text TEXT NOT NULL DEFAULT '',
    year INTEGER,
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    
    -- Backward compatibility
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'crossref',
    source_id TEXT NOT NULL
);

CREATE UNIQUE INDEX idx_search_journal_doi
    ON search_journal(doi) WHERE doi IS NOT NULL;

CREATE INDEX idx_search_journal_year
    ON search_journal(year) WHERE year IS NOT NULL;

CREATE INDEX idx_search_journal_source_id
    ON search_journal(source_id);

CREATE VIRTUAL TABLE search_journal_fts USING fts5(
    title_norm,
    author_text,
    container_text,
    content='search_journal',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- FTS triggers
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
```

### search_book

```sql
CREATE TABLE search_book (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    isbn TEXT,
    ol_key TEXT,
    
    -- Display fields
    title TEXT NOT NULL,
    publisher TEXT,
    
    -- Matching/search fields
    title_norm TEXT NOT NULL,
    year INTEGER,
    publisher_norm TEXT,
    container_text TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    
    -- Backward compatibility
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    source_id TEXT NOT NULL
);

CREATE UNIQUE INDEX uidx_search_book_doi
    ON search_book(doi) WHERE doi IS NOT NULL;

CREATE UNIQUE INDEX uidx_search_book_isbn_source
    ON search_book(isbn, source) WHERE isbn IS NOT NULL;

CREATE INDEX idx_search_book_isbn
    ON search_book(isbn) WHERE isbn IS NOT NULL;

CREATE INDEX idx_search_book_year
    ON search_book(year) WHERE year IS NOT NULL;

CREATE INDEX idx_search_book_source_id
    ON search_book(source_id);

CREATE VIRTUAL TABLE search_book_fts USING fts5(
    title_norm,
    author_text,
    container_text,
    content='search_book',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- FTS triggers (similar to search_journal)
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
```

### search_book_chapter

```sql
CREATE TABLE search_book_chapter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    
    -- Display fields
    title TEXT NOT NULL,
    book_title TEXT,
    publisher TEXT,
    
    -- Matching/search fields
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
    
    -- Backward compatibility
    author_surnames_text TEXT NOT NULL DEFAULT '',
    editor_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'crossref',
    source_id TEXT NOT NULL
);

CREATE UNIQUE INDEX uidx_search_book_chapter_doi
    ON search_book_chapter(doi) WHERE doi IS NOT NULL;

CREATE INDEX idx_search_book_chapter_year
    ON search_book_chapter(year) WHERE year IS NOT NULL;

CREATE INDEX idx_search_book_chapter_source_id
    ON search_book_chapter(source_id);

CREATE VIRTUAL TABLE search_book_chapter_fts USING fts5(
    title_norm,
    author_text,
    editor_text,
    container_text,
    content='search_book_chapter',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- FTS triggers
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
```

### search_conference

```sql
CREATE TABLE search_conference (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    doi TEXT,
    
    -- Display fields
    title TEXT NOT NULL,
    container TEXT,
    volume TEXT,
    pages TEXT,
    
    -- Matching/search fields
    title_norm TEXT NOT NULL,
    year INTEGER,
    container_norm TEXT,
    container_text TEXT NOT NULL DEFAULT '',
    author_surnames_json TEXT NOT NULL DEFAULT '[]',
    author_initials_text TEXT NOT NULL DEFAULT '',
    author_text TEXT NOT NULL DEFAULT '',
    
    -- Backward compatibility
    author_surnames_text TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'crossref',
    source_id TEXT NOT NULL
);

CREATE UNIQUE INDEX uidx_search_conference_doi
    ON search_conference(doi) WHERE doi IS NOT NULL;

CREATE INDEX idx_search_conference_year
    ON search_conference(year) WHERE year IS NOT NULL;

CREATE INDEX idx_search_conference_source_id
    ON search_conference(source_id);

CREATE VIRTUAL TABLE search_conference_fts USING fts5(
    title_norm,
    author_text,
    container_text,
    content='search_conference',
    content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

-- FTS triggers (similar to search_journal)
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
```

---

## Python Ingest Script

The canonical implementation is `scripts/ingest_with_initials.py`. The abbreviated template below shows the same schema shape but omits the production ingest loops.

```python
#!/usr/bin/env python3
"""Build optimized SQLite database with author initials from Crossref and OpenLibrary.

Usage:
  python scripts/ingest_with_initials.py \\
    --ol-authors /data/ol_dump_authors.txt.gz \\
    --ol-editions /data/ol_dump_editions.txt.gz \\
    --crossref-resume /data/crossref.resume \\
    --crossref-dir /data/crossref_dumps \\
    --output /data/refs_2025_v2.db
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import sqlite3
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Any

# Helper functions

def norm_text(text: str | None) -> str:
    """Normalize text for matching: lowercase, remove non-alphanumeric."""
    if not text:
        return ""
    normalized = text.lower()
    normalized = re.sub(r'[^a-z0-9]+', ' ', normalized)
    return ' '.join(normalized.split()).strip()


def extract_initials(given_name: str | None) -> str:
    """Extract initials from given name.
    
    Examples:
        'John' → 'J.'
        'John Andrew' → 'J. A.'
        'Mary-Kate' → 'M.'
        '' → ''
    """
    if not given_name:
        return ""
    
    # Split on spaces and hyphens
    parts = re.split(r'[\s\-]+', given_name.strip())
    initials = []
    
    for part in parts:
        if part and part[0].isalpha():
            initials.append(part[0].upper() + ".")
    
    return " ".join(initials)


def parse_author_name(full_name: str) -> tuple[str, str]:
    """Parse author name into family and given names.
    
    Handles formats:
        'Smith, John A.' → ('Smith', 'John A.')
        'John A. Smith' → ('Smith', 'John A.')
        'Smith' → ('Smith', '')
    
    Returns:
        (family_name, given_name)
    """
    if not full_name:
        return "", ""
    
    full_name = full_name.strip()
    
    # Format: "Family, Given"
    if ',' in full_name:
        parts = full_name.split(',', 1)
        family = parts[0].strip()
        given = parts[1].strip() if len(parts) > 1 else ""
        return family, given
    
    # Format: "Given Family" - take last word as family
    parts = full_name.split()
    if len(parts) >= 2:
        family = parts[-1]
        given = " ".join(parts[:-1])
        return family, given
    
    # Single word - assume it's family name
    return full_name, ""


def surname_candidates(name: str) -> list[str]:
    """Extract surname from full name (backward compatibility)."""
    family, _ = parse_author_name(name)
    return [family] if family else []


# Database creation

def create_schema(conn: sqlite3.Connection) -> None:
    """Create optimized database schema."""
    conn.executescript("""
        PRAGMA journal_mode=OFF;
        PRAGMA synchronous=OFF;
        PRAGMA temp_store=MEMORY;
        PRAGMA locking_mode=EXCLUSIVE;
        PRAGMA cache_size=-200000;
        PRAGMA foreign_keys=OFF;

        -- search_journal table
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

        CREATE UNIQUE INDEX idx_search_journal_doi
            ON search_journal(doi) WHERE doi IS NOT NULL;
        CREATE INDEX idx_search_journal_year
            ON search_journal(year) WHERE year IS NOT NULL;
        CREATE INDEX idx_search_journal_source_id
            ON search_journal(source_id);

        CREATE VIRTUAL TABLE search_journal_fts USING fts5(
            title_norm,
            author_text,
            container_text,
            content='search_journal',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

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

        -- search_book table
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

        CREATE UNIQUE INDEX uidx_search_book_doi
            ON search_book(doi) WHERE doi IS NOT NULL;
        CREATE UNIQUE INDEX uidx_search_book_isbn_source
            ON search_book(isbn, source) WHERE isbn IS NOT NULL;
        CREATE INDEX idx_search_book_isbn
            ON search_book(isbn) WHERE isbn IS NOT NULL;
        CREATE INDEX idx_search_book_year
            ON search_book(year) WHERE year IS NOT NULL;
        CREATE INDEX idx_search_book_source_id
            ON search_book(source_id);

        CREATE VIRTUAL TABLE search_book_fts USING fts5(
            title_norm,
            author_text,
            container_text,
            content='search_book',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

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

        -- search_book_chapter table
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

        CREATE UNIQUE INDEX uidx_search_book_chapter_doi
            ON search_book_chapter(doi) WHERE doi IS NOT NULL;
        CREATE INDEX idx_search_book_chapter_year
            ON search_book_chapter(year) WHERE year IS NOT NULL;
        CREATE INDEX idx_search_book_chapter_source_id
            ON search_book_chapter(source_id);

        CREATE VIRTUAL TABLE search_book_chapter_fts USING fts5(
            title_norm,
            author_text,
            editor_text,
            container_text,
            content='search_book_chapter',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

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

        -- search_conference table
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

        CREATE UNIQUE INDEX uidx_search_conference_doi
            ON search_conference(doi) WHERE doi IS NOT NULL;
        CREATE INDEX idx_search_conference_year
            ON search_conference(year) WHERE year IS NOT NULL;
        CREATE INDEX idx_search_conference_source_id
            ON search_conference(source_id);

        CREATE VIRTUAL TABLE search_conference_fts USING fts5(
            title_norm,
            author_text,
            container_text,
            content='search_conference',
            content_rowid='id',
            tokenize='unicode61 remove_diacritics 2'
        );

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
    """)
    conn.commit()


def process_crossref_author(author_obj: dict[str, Any]) -> tuple[str, str]:
    """Extract family and given names from Crossref author object.
    
    Args:
        author_obj: Crossref author dict with 'family' and optionally 'given' keys
    
    Returns:
        (family_name, initials)
    """
    family = str(author_obj.get('family', '')).strip()
    given = str(author_obj.get('given', '')).strip()
    initials = extract_initials(given)
    return family, initials


def process_openlibrary_author(full_name: str) -> tuple[str, str]:
    """Extract family and given names from OpenLibrary author name.
    
    Args:
        full_name: Full author name string
    
    Returns:
        (family_name, initials)
    """
    family, given = parse_author_name(full_name)
    initials = extract_initials(given)
    return family, initials


# Main ingest logic would go here...
# (This is a template - you'll need to adapt your existing ingest logic
#  to use process_crossref_author() and process_openlibrary_author())

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ol-authors', type=Path, help='OpenLibrary authors dump')
    parser.add_argument('--ol-editions', type=Path, help='OpenLibrary editions dump')
    parser.add_argument('--crossref-resume', type=Path, help='Crossref resume file')
    parser.add_argument('--crossref-dir', type=Path, help='Crossref dumps directory')
    parser.add_argument('--output', type=Path, required=True, help='Output database path')
    
    args = parser.parse_args()
    
    print(f"Creating database: {args.output}")
    conn = sqlite3.connect(str(args.output))
    create_schema(conn)
    
    # TODO: Add your ingest logic here using the helper functions above
    print("Schema created. Add ingest logic to populate tables.")
    
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
```

---

## Migration Guide

### Step 1: Test the New Schema

Run the ingest script on a **small subset** of data first:

```bash
# Test with limited data
python scripts/ingest_with_initials.py \\
  --ol-authors /data/ol_authors_sample.txt.gz \\
  --ol-editions /data/ol_editions_sample.txt.gz \\
  --crossref-dir /data/crossref_sample \\
  --output /data/refs_test.db
```

### Step 2: Verify Database Size

```bash
ls -lh /data/refs_test.db
# Should be similar size to old DB, maybe 5-10% larger
```

### Step 3: Test with Reference_Gen2

Test Reference_Gen2 against the rebuilt DB:

```bash
cd /home/azureuser/Reference_Gen2
python scripts/run_phase125_batch.py --local-db /data/refs_test.db
```

Check that citations include initials and that Phase 4 can use container-aware FTS clauses.

### Step 4: Full Rebuild

Once verified, run the full ingest:

```bash
python scripts/ingest_with_initials.py \\
  --ol-authors /data/ol_dump_authors.txt.gz \\
  --ol-editions /data/ol_dump_editions.txt.gz \\
  --crossref-resume /data/crossref.resume \\
  --crossref-dir /data/crossref_dumps \\
  --output /data/refs_2025_v2.db
```

---

## Reference_Gen2 Code Changes

See the companion document `docs/reference_gen2_initials_integration.md` for the required code changes to:

1. Extract `author_initials_text` from database rows
2. Add `author_initials` field to `LocalDbCandidate` model
3. Use initials when rendering citations in Phase 6A

---

## Performance Expectations

| Metric | Old DB | New DB | Change |
|--------|--------|--------|--------|
| **Size** | 180 GB | ~185-190 GB | +3-5% |
| **Ingest Time** | Hours | Similar | No change |
| **Query Speed** | Fast | Similar | Container clauses add selective FTS filtering |
| **Citation Quality** | Missing initials | ✅ Complete | Fixed! |
| **Candidate Recall** | Title/author only | Title/author/container | Better corroboration after DOI conflicts |

**Why minimal size increase?**
- Initials are short (1-3 chars per author)
- Container text is one short normalized journal, publisher, book title, or proceedings string per row
- We removed `author_surnames_norm_text` (saves ~5%)
- Net increase should remain modest

**Why minimal performance impact?**
- FTS queries still use `author_text` (surnames only)
- Initials are NOT indexed
- Container text is indexed so Phase 4 can add targeted `container_text` clauses when useful

---

## Next Steps

1. ✅ Review this schema
2. ✅ Adapt your existing ingest logic to use the helper functions
3. ✅ Test on a small dataset
4. ✅ Update Reference_Gen2 code
5. ✅ Run full rebuild when ready

Questions? Check the companion integration guide or ask for help!
