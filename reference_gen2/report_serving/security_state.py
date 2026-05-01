"""Shared Phase 7 security state for rate limiting and job concurrency."""

from __future__ import annotations

from dataclasses import dataclass
import sqlite3
import time
from pathlib import Path


@dataclass(frozen=True)
class RateLimitSubject:
    bucket_name: str
    subject_key: str
    window_seconds: int
    max_requests: int


@dataclass(frozen=True)
class Phase7SecurityStateConfig:
    db_path: Path
    rate_limit_window_seconds: int
    rate_limit_max_requests: int
    max_active_jobs: int
    active_job_lease_seconds: int
    max_queued_jobs: int = 20
    rate_limit_retention_seconds: int | None = None


class SqlitePhase7SecurityState:
    def __init__(self, config: Phase7SecurityStateConfig):
        self._config = config
        self._ensure_schema()

    @property
    def max_queued_jobs(self) -> int:
        return self._config.max_queued_jobs

    def allow_submission(
        self,
        subjects_or_session_key: str | list[RateLimitSubject],
        *,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        subjects = self._normalize_subjects(subjects_or_session_key)
        with self._open_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._prune_rate_limit_windows(conn, now=now)
            rows: list[tuple[RateLimitSubject, int, bool]] = []
            for subject in subjects:
                window_id = int(now // subject.window_seconds)
                row = conn.execute(
                    """
                    SELECT request_count
                    FROM rate_limit_windows
                    WHERE bucket_name = ? AND subject_key = ? AND window_id = ?
                    LIMIT 1
                    """,
                    (subject.bucket_name, subject.subject_key, window_id),
                ).fetchone()
                request_count = int(row[0]) if row is not None else 0
                if request_count >= subject.max_requests:
                    conn.rollback()
                    return False
                rows.append((subject, window_id, row is None))
            for subject, window_id, is_new_row in rows:
                if is_new_row:
                    conn.execute(
                        """
                        INSERT INTO rate_limit_windows (bucket_name, subject_key, window_id, request_count, updated_at_epoch)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (subject.bucket_name, subject.subject_key, window_id, 1, now),
                    )
                    continue
                conn.execute(
                    """
                    UPDATE rate_limit_windows
                    SET request_count = request_count + 1,
                        updated_at_epoch = ?
                    WHERE bucket_name = ? AND subject_key = ? AND window_id = ?
                    """,
                    (now, subject.bucket_name, subject.subject_key, window_id),
                )
            conn.commit()
            return True

    def should_challenge(
        self,
        subjects: list[RateLimitSubject],
        *,
        threshold_ratio: float,
        now: float | None = None,
    ) -> bool:
        if threshold_ratio <= 0:
            return True
        if threshold_ratio > 1:
            return False
        now = time.time() if now is None else now
        normalized_subjects = self._normalize_subjects(subjects)
        with self._open_conn() as conn:
            self._prune_rate_limit_windows(conn, now=now)
            for subject in normalized_subjects:
                threshold = max(1, int(subject.max_requests * threshold_ratio))
                window_id = int(now // subject.window_seconds)
                row = conn.execute(
                    """
                    SELECT request_count
                    FROM rate_limit_windows
                    WHERE bucket_name = ? AND subject_key = ? AND window_id = ?
                    LIMIT 1
                    """,
                    (subject.bucket_name, subject.subject_key, window_id),
                ).fetchone()
                request_count = int(row[0]) if row is not None else 0
                if request_count >= threshold:
                    return True
        return False

    def try_consume_challenge(
        self,
        challenge_key: str,
        *,
        expires_at_epoch: float,
        now: float | None = None,
    ) -> bool:
        now = time.time() if now is None else now
        with self._open_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM consumed_challenges WHERE expires_at_epoch < ?",
                (now,),
            )
            try:
                conn.execute(
                    """
                    INSERT INTO consumed_challenges (challenge_key, consumed_at_epoch, expires_at_epoch)
                    VALUES (?, ?, ?)
                    """,
                    (challenge_key, now, expires_at_epoch),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
            conn.commit()
            return True

    def try_acquire_job_slot(self, job_id: str, *, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        cutoff = now - self._config.active_job_lease_seconds
        with self._open_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "DELETE FROM active_jobs WHERE acquired_at_epoch < ?",
                (cutoff,),
            )
            active_count = int(
                conn.execute("SELECT COUNT(*) FROM active_jobs").fetchone()[0]
            )
            if active_count >= self._config.max_active_jobs:
                conn.rollback()
                return False
            try:
                conn.execute(
                    """
                    INSERT INTO active_jobs (job_id, acquired_at_epoch)
                    VALUES (?, ?)
                    """,
                    (job_id, now),
                )
            except sqlite3.IntegrityError:
                conn.rollback()
                return False
            conn.commit()
            return True

    def release_job_slot(self, job_id: str) -> None:
        with self._open_conn() as conn:
            conn.execute("DELETE FROM active_jobs WHERE job_id = ?", (job_id,))
            conn.commit()

    def _normalize_subjects(
        self,
        subjects_or_session_key: str | list[RateLimitSubject],
    ) -> list[RateLimitSubject]:
        if isinstance(subjects_or_session_key, str):
            return [
                RateLimitSubject(
                    bucket_name="report_submission",
                    subject_key=subjects_or_session_key,
                    window_seconds=self._config.rate_limit_window_seconds,
                    max_requests=self._config.rate_limit_max_requests,
                )
            ]
        seen: set[tuple[str, str, int]] = set()
        normalized: list[RateLimitSubject] = []
        for subject in subjects_or_session_key:
            if subject.window_seconds <= 0 or subject.max_requests <= 0:
                raise ValueError(
                    "Rate limit subjects must have positive windows and limits."
                )
            key = (subject.bucket_name, subject.subject_key, subject.window_seconds)
            if key in seen:
                continue
            seen.add(key)
            normalized.append(subject)
        if not normalized:
            raise ValueError("At least one rate limit subject is required.")
        return normalized

    def _prune_rate_limit_windows(self, conn: sqlite3.Connection, *, now: float) -> None:
        retention_seconds = self._config.rate_limit_retention_seconds
        if retention_seconds is None:
            retention_seconds = max(
                self._config.rate_limit_window_seconds * 2,
                self._config.active_job_lease_seconds,
            )
        conn.execute(
            "DELETE FROM rate_limit_windows WHERE updated_at_epoch < ?",
            (now - retention_seconds,),
        )

    def _ensure_schema(self) -> None:
        self._config.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._open_conn() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS rate_limit_windows (
                    bucket_name TEXT NOT NULL,
                    subject_key TEXT NOT NULL,
                    window_id INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    updated_at_epoch REAL NOT NULL,
                    PRIMARY KEY (bucket_name, subject_key, window_id)
                );
                CREATE TABLE IF NOT EXISTS active_jobs (
                    job_id TEXT PRIMARY KEY,
                    acquired_at_epoch REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumed_challenges (
                    challenge_key TEXT PRIMARY KEY,
                    consumed_at_epoch REAL NOT NULL,
                    expires_at_epoch REAL NOT NULL
                );
                """
            )
            conn.commit()

    def _open_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._config.db_path), timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn
