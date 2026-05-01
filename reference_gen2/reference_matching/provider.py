from __future__ import annotations

import json
import re
import sqlite3
import time
import unicodedata
from html import unescape
from pathlib import Path
from typing import Any

from reference_gen2.reference_matching.models import (
    LocalDbCandidate,
    Phase4RuntimeConfig,
    Phase4SearchConfig,
    SupportedPhase4CTypeName,
)

_TABLE_MAP: dict[SupportedPhase4CTypeName, list[str]] = {
    "journal_article": ["search_journal", "search_conference"],
    "book": ["search_book"],
    "book_chapter": ["search_book_chapter"],
}
_FTS_TABLE_MAP: dict[str, str] = {
    "search_journal": "search_journal_fts",
    "search_conference": "search_conference_fts",
    "search_book": "search_book_fts",
    "search_book_chapter": "search_book_chapter_fts",
}
_DOI_PREFIX_RE = re.compile(
    r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)",
    re.IGNORECASE,
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_MULTI_VALUE_SPLIT_RE = re.compile(r"[|;]")
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
        # OCR often confuses German eszett with Greek beta in bibliographies.
        "Β": "SS",
        "β": "ss",
    }
)


def normalize_doi(value: str | None) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    text = _DOI_PREFIX_RE.sub("", text)
    text = text.strip().strip(".;,")
    return text.lower()


def doi_equivalence_key(value: str | None) -> str:
    normalized = normalize_doi(value)
    if not normalized:
        return ""
    return normalized.rstrip("/")


# Minimum length (after normalization) that a DOI must have before we are
# willing to treat it as a prefix of another DOI. This avoids spurious
# equivalences like "10.1/a" vs "10.1/ab".
_DOI_PREFIX_MIN_LEN = 10
# Characters that indicate a reasonable "boundary" between the short DOI and
# the continuation that got appended (or the truncated suffix that got lost).
_DOI_PREFIX_BOUNDARY_CHARS = {".", "/", "-", "_", ";", ":", "(", ")"}


def doi_prefix_equivalent(left: str | None, right: str | None) -> bool:
    """Return True when one normalized DOI is a plausible truncation of the other.

    Handles common cases where a student reference or candidate record only
    preserved part of a DOI (for example ``10.1111/1467-9566.`` vs the full
    ``10.1111/1467-9566.13038``). Requires that the shorter value be a proper
    prefix of the longer one, that the shorter value already be a plausibly
    complete stem (``10.`` prefix, contains a ``/``, and meets a minimum length),
    and that the character following the shared prefix in the longer value be a
    recognised DOI separator rather than an arbitrary alphanumeric extension.
    """

    a = normalize_doi(left)
    b = normalize_doi(right)
    if not a or not b or a == b:
        return False
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) < _DOI_PREFIX_MIN_LEN:
        return False
    if not short.startswith("10.") or "/" not in short:
        return False
    if not long.startswith(short):
        return False
    next_char = long[len(short)]
    # If the shorter value already ends with a separator the continuation is
    # unambiguously appended data (e.g. "10.1111/1467-9566." + "13038").
    if short[-1] in _DOI_PREFIX_BOUNDARY_CHARS:
        return True
    return next_char in _DOI_PREFIX_BOUNDARY_CHARS


def _ascii_fold(value: str) -> str:
    text = value.translate(_ASCII_FOLD_TRANSLATION)
    text = unicodedata.normalize("NFKD", text)
    return text.encode("ascii", "ignore").decode("ascii")


def normalize_text(value: str | None) -> str:
    raw = (value or "").replace("\u2019", "'").replace("\u2018", "'").strip()
    text = _ascii_fold(raw).lower()
    text = re.sub(r"\b([a-z0-9]+)[\u2019']s\b", r"\1", text)
    return _NON_WORD_RE.sub(" ", text).strip()


def warm_localdb_cache(local_db_path: str, *, max_seconds: float | None = None) -> bool:
    """Touch key search tables once to reduce first-query cold-cache penalties.

    This is intentionally a tiny set of read-only probes, not a full cache
    population pass. When max_seconds is provided, SQLite is interrupted if the
    current connection exceeds that deadline.
    """
    if max_seconds is not None and max_seconds <= 0:
        return False
    deadline = None if max_seconds is None else time.perf_counter() + max_seconds

    def expired() -> bool:
        return deadline is not None and time.perf_counter() >= deadline

    provider = SqliteLocalDbProvider(local_db_path)
    with provider._open_readonly_conn() as conn:
        if deadline is not None:
            conn.set_progress_handler(lambda: 1 if expired() else 0, 1000)
        tables = [
            *{table for tables in _TABLE_MAP.values() for table in tables},
            *_FTS_TABLE_MAP.values(),
        ]
        for table in tables:
            if expired():
                return False
            try:
                conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
            except sqlite3.OperationalError:
                if expired():
                    return False
                continue
            except sqlite3.DatabaseError:
                continue
        return True


class SqliteLocalDbProvider:
    def __init__(
        self,
        local_db_path: str,
        *,
        runtime_config: Phase4RuntimeConfig | None = None,
    ):
        self.local_db_path = local_db_path
        self.runtime_config = runtime_config or Phase4RuntimeConfig(local_db_path=local_db_path)

    def lookup_by_doi(
        self,
        *,
        ctype: SupportedPhase4CTypeName,
        doi: str,
        max_candidates: int,
    ) -> list[LocalDbCandidate]:
        normalized = normalize_doi(doi)
        if not normalized:
            return []
        with self._open_readonly_conn() as conn:
            rows: list[sqlite3.Row] = []
            searched_tables = [
                table
                for table in _target_tables_for_ctype(ctype)
                if self._table_exists(conn, table) and self._has_index_on_column(conn, table, "doi")
            ]
            exact_variants = _dedupe_preserve_order(
                [
                    doi.strip(),
                    normalized,
                    normalized.upper(),
                ]
            )
            for value in exact_variants:
                if not value:
                    continue
                for table in searched_tables:
                    fetched = conn.execute(
                        f"SELECT *, '{table}' AS _table FROM {table} WHERE doi = ? LIMIT ?",
                        (value, max_candidates),
                    ).fetchall()
                    rows.extend(fetched)
                    if rows:
                        break
                if rows:
                    break
            return self._rows_to_candidates_by_table(
                rows,
                source_strategy="doi_exact",
            )[:max_candidates]

    def search_candidates(
        self,
        *,
        ctype: SupportedPhase4CTypeName,
        config: Phase4SearchConfig,
        max_candidates: int,
    ) -> list[LocalDbCandidate]:
        search_limit = max(max_candidates, config.limit)
        with self._open_readonly_conn() as conn:
            rows: list[sqlite3.Row] = []
            for table in config.target_tables or _target_tables_for_ctype(ctype):
                if not self._table_exists(conn, table):
                    continue
                fts_table = _FTS_TABLE_MAP.get(table)
                if fts_table and self._table_exists(conn, fts_table):
                    rows.extend(
                        self._search_via_fts(
                            conn,
                            table=table,
                            fts_table=fts_table,
                            config=config,
                            max_candidates=search_limit,
                        )
                    )
                    continue
                if config.allow_non_fts_fallback and self.runtime_config.allow_non_fts_scan_fallback:
                    rows.extend(
                        self._search_via_like(
                            conn,
                            table=table,
                            config=config,
                            max_candidates=search_limit,
                        )
                    )
        return self._rows_to_candidates_by_table(
            rows,
            source_strategy=config.name,
        )[:search_limit]

    def _open_readonly_conn(self) -> sqlite3.Connection:
        uri = f"{Path(self.local_db_path).resolve().as_uri()}?mode=ro&immutable=1"
        conn = sqlite3.connect(uri, uri=True)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def _table_exists(self, conn: sqlite3.Connection, name: str) -> bool:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE name = ? LIMIT 1",
            (name,),
        ).fetchone()
        return row is not None

    def _has_index_on_column(
        self,
        conn: sqlite3.Connection,
        table: str,
        column: str,
    ) -> bool:
        indexes = conn.execute(f"PRAGMA index_list('{table}')").fetchall()
        for index_row in indexes:
            index_name = index_row["name"] if isinstance(index_row, sqlite3.Row) else index_row[1]
            info_rows = conn.execute(f"PRAGMA index_info('{index_name}')").fetchall()
            indexed_columns = {
                str(info_row["name"] if isinstance(info_row, sqlite3.Row) else info_row[2]).strip()
                for info_row in info_rows
            }
            if column in indexed_columns:
                return True
        return False

    def _fts_columns(self, conn: sqlite3.Connection, fts_table: str) -> set[str]:
        rows = conn.execute(f"PRAGMA table_info('{fts_table}')").fetchall()
        return {
            str(row["name"] if isinstance(row, sqlite3.Row) else row[1]).strip()
            for row in rows
            if str(row["name"] if isinstance(row, sqlite3.Row) else row[1]).strip()
        }

    def _search_via_fts(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        fts_table: str,
        config: Phase4SearchConfig,
        max_candidates: int,
    ) -> list[sqlite3.Row]:
        fts_query = _build_fts_query(
            config,
            available_columns=self._fts_columns(conn, fts_table),
        )
        if not fts_query:
            return []
        rowid_rows = conn.execute(
            f"SELECT rowid, bm25({fts_table}) AS rank FROM {fts_table} WHERE {fts_table} MATCH ? ORDER BY rank LIMIT ?",
            (fts_query, max_candidates * 4),
        ).fetchall()
        if not rowid_rows:
            return []
        rowids = [int(row["rowid"]) for row in rowid_rows]
        placeholders = ",".join("?" for _ in rowids)
        hydrated_rows = conn.execute(
            f"SELECT *, '{table}' AS _table FROM {table} WHERE id IN ({placeholders})",
            rowids,
        ).fetchall()
        hydrated_by_id = {int(row["id"]): row for row in hydrated_rows}
        rows = [hydrated_by_id[rowid] for rowid in rowids if rowid in hydrated_by_id]
        if not config.year:
            return rows
        return [row for row in rows if _row_year_matches(row, config=config)]

    def _search_via_like(
        self,
        conn: sqlite3.Connection,
        *,
        table: str,
        config: Phase4SearchConfig,
        max_candidates: int,
    ) -> list[sqlite3.Row]:
        clauses: list[str] = []
        params: list[str] = []
        if config.title_terms:
            for term in config.title_terms[:3]:
                clauses.append("lower(coalesce(title, '')) LIKE ?")
                params.append(f"%{term}%")
        if config.author_terms:
            clauses.append(
                "(lower(coalesce(author_surnames_text, '')) LIKE ? OR lower(coalesce(author_text, '')) LIKE ?)"
            )
            author_term = config.author_terms[0]
            params.extend([f"%{author_term}%", f"%{author_term}%"])
        if config.container_terms:
            container_term = config.container_terms[0]
            clauses.append(
                "("
                "lower(coalesce(journal, '')) LIKE ? OR "
                "lower(coalesce(container, '')) LIKE ? OR "
                "lower(coalesce(book_title, '')) LIKE ? OR "
                "lower(coalesce(publisher, '')) LIKE ?"
                ")"
            )
            params.extend(
                [f"%{container_term}%"] * 4
            )
        if config.year:
            if config.year_mode == "near":
                try:
                    target_year = int(config.year)
                    clauses.append("cast(year as integer) BETWEEN ? AND ?")
                    params.extend(
                        [str(target_year - config.year_window), str(target_year + config.year_window)]
                    )
                except ValueError:
                    clauses.append("cast(year as text) = ?")
                    params.append(config.year)
            else:
                clauses.append("cast(year as text) = ?")
                params.append(config.year)
        if not clauses:
            return []
        return conn.execute(
            f"SELECT *, '{table}' AS _table FROM {table} WHERE {' AND '.join(clauses)} LIMIT ?",
            (*params, max_candidates * 4),
        ).fetchall()

    def _rows_to_candidates_by_table(
        self,
        rows: list[sqlite3.Row],
        source_strategy: str,
    ) -> list[LocalDbCandidate]:
        candidates: list[LocalDbCandidate] = []
        seen: set[str] = set()
        for row in rows:
            row_dict = dict(row)
            source_table = _row_source_table(row_dict)
            record_id = f"{source_table}:{row_dict.get('id')}"
            if record_id in seen:
                continue
            seen.add(record_id)
            candidates.append(
                LocalDbCandidate(
                    record_id=record_id,
                    record_type=source_table,
                    record_granularity=_record_granularity_for_table(source_table),
                    title=_first_non_empty(
                        row_dict.get("title"),
                        row_dict.get("chapter_title"),
                    ),
                    authors=_row_people(
                        row_dict.get("author_surnames_text"),
                        row_dict.get("author_surnames_json"),
                        row_dict.get("author_text"),
                        row_dict.get("author_initials_text"),
                    ),
                    author_initials=_row_people_initials(
                        row_dict.get("author_surnames_text"),
                        row_dict.get("author_surnames_json"),
                        row_dict.get("author_text"),
                        row_dict.get("author_initials_text"),
                    ),
                    editors=_row_people(
                        row_dict.get("editor_surnames_text"),
                        row_dict.get("editor_surnames_json"),
                        row_dict.get("editor_text"),
                        row_dict.get("editor_initials_text"),
                    ),
                    editor_initials=_row_people_initials(
                        row_dict.get("editor_surnames_text"),
                        row_dict.get("editor_surnames_json"),
                        row_dict.get("editor_text"),
                        row_dict.get("editor_initials_text"),
                    ),
                    issued_year=_string_or_none(row_dict.get("year")),
                    doi=_string_or_none(row_dict.get("doi")),
                    container_title=_first_non_empty(
                        row_dict.get("journal"),
                        row_dict.get("container"),
                        row_dict.get("book_title"),
                    ),
                    publisher=_string_or_none(row_dict.get("publisher")),
                    volume=_string_or_none(row_dict.get("volume")),
                    issue=_string_or_none(row_dict.get("issue")),
                    pages=_string_or_none(row_dict.get("pages")),
                    source_table=source_table,
                    source_strategy=source_strategy,
                    raw_adapter_data=row_dict,
                )
            )
        return candidates


def _target_tables_for_ctype(ctype: SupportedPhase4CTypeName) -> list[str]:
    return list(_TABLE_MAP[ctype])


def _build_fts_query(
    config: Phase4SearchConfig,
    *,
    available_columns: set[str] | None = None,
) -> str:
    fielded_terms = config.fielded_terms or {
        "title_norm": config.title_terms,
        "author_text": config.author_terms,
        "container_text": config.container_terms,
    }
    clauses: list[str] = []
    for field_name, terms in fielded_terms.items():
        if available_columns is not None and field_name not in available_columns:
            continue
        normalized_terms = [normalize_text(term) for term in terms]
        normalized_terms = [term for term in normalized_terms if term]
        if not normalized_terms:
            continue
        term_clauses = [f'{field_name}:{_fts_term_clause(term)}' for term in normalized_terms]
        clauses.append(" AND ".join(term_clauses))
    return " AND ".join(clause for clause in clauses if clause)


def _row_year_matches(row: sqlite3.Row, *, config: Phase4SearchConfig) -> bool:
    row_year = str(row["year"]).strip()
    if not row_year or not config.year:
        return False
    if config.year_mode != "near":
        return row_year == config.year
    try:
        return abs(int(row_year) - int(config.year)) <= max(config.year_window, 0)
    except ValueError:
        return row_year == config.year


def _fts_term_clause(term: str) -> str:
    if " " in term:
        escaped = term.replace('"', '""')
        return f'"{escaped}"'
    return term


def _row_source_table(row_dict: dict[str, Any]) -> str:
    explicit = _string_or_none(row_dict.get("_table"))
    if explicit:
        return explicit
    if row_dict.get("journal") is not None:
        return "search_journal"
    if row_dict.get("book_title") is not None:
        return "search_book_chapter"
    if row_dict.get("publisher") is not None:
        return "search_book"
    return "search_record"


def _record_granularity_for_table(source_table: str | None) -> str:
    if source_table in {"search_journal", "search_conference"}:
        return "article"
    if source_table == "search_book":
        return "book"
    if source_table == "search_book_chapter":
        return "chapter"
    return "unknown"


def _first_non_empty(*values: Any) -> str | None:
    for value in values:
        text = _string_or_none(value)
        if text:
            return text
    return None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = _clean_db_text(str(value))
    return text or None


def _clean_db_text(value: str) -> str:
    text = unescape(value).strip()
    if "<![CDATA[" in text:
        text = text.replace("<![CDATA[", "").replace("]]>", "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = " ".join(text.split())
    return re.sub(r"\s+([,.;:!?])", r"\1", text)


def _dedupe_preserve_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _row_people(
    preferred_text: Any,
    json_text: Any,
    fallback_text: Any,
    initials_text: Any,
) -> list[str]:
    return [name for name, _initials in _row_people_pairs(preferred_text, json_text, fallback_text, initials_text)]


def _row_people_initials(
    preferred_text: Any,
    json_text: Any,
    fallback_text: Any,
    initials_text: Any,
) -> list[str]:
    return [initials for _name, initials in _row_people_pairs(preferred_text, json_text, fallback_text, initials_text)]


def _row_people_pairs(
    preferred_text: Any,
    json_text: Any,
    fallback_text: Any,
    initials_text: Any,
) -> list[tuple[str, str]]:
    names = _preferred_name_values(preferred_text, json_text, fallback_text)
    initials = _text_list(initials_text)
    merged: list[tuple[str, str]] = []
    seen_normalized: set[str] = set()
    for index, candidate in enumerate(names):
        candidate_key = normalize_text(candidate)
        if candidate and candidate_key and candidate_key not in seen_normalized:
            seen_normalized.add(candidate_key)
            merged.append((candidate, initials[index] if index < len(initials) else ""))
    return merged


def _preferred_name_values(
    preferred_text: Any,
    json_text: Any,
    fallback_text: Any,
) -> list[str]:
    preferred_values = _text_list(preferred_text)
    if preferred_values:
        return preferred_values
    json_values = _json_list(json_text)
    if json_values:
        return json_values
    return _text_list(fallback_text)


def _json_list(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text.startswith("["):
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    output: list[str] = []
    for item in parsed:
        candidate = str(item).strip()
        if candidate:
            output.append(candidate)
    return output


def _text_list(value: Any) -> list[str]:
    if not isinstance(value, str):
        return []
    text = value.strip()
    if not text:
        return []
    if text.startswith("["):
        return _json_list(text)
    parts = _MULTI_VALUE_SPLIT_RE.split(text) if _MULTI_VALUE_SPLIT_RE.search(text) else [text]
    output: list[str] = []
    for item in parts:
        candidate = item.strip()
        if candidate:
            output.append(candidate)
    return output
