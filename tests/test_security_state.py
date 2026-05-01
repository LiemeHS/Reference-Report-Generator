from __future__ import annotations

from pathlib import Path

from reference_gen2.report_serving.security_state import (
    Phase7SecurityStateConfig,
    RateLimitSubject,
    SqlitePhase7SecurityState,
)


def _config(tmp_path: Path) -> Phase7SecurityStateConfig:
    return Phase7SecurityStateConfig(
        db_path=tmp_path / "phase7_security.sqlite3",
        rate_limit_window_seconds=60,
        rate_limit_max_requests=2,
        max_active_jobs=1,
        active_job_lease_seconds=60,
    )


def test_sqlite_security_state_shares_rate_limits_across_instances(tmp_path: Path):
    first = SqlitePhase7SecurityState(_config(tmp_path))
    second = SqlitePhase7SecurityState(_config(tmp_path))

    assert first.allow_submission("sess_one", now=100.0) is True
    assert second.allow_submission("sess_one", now=101.0) is True
    assert first.allow_submission("sess_one", now=102.0) is False


def test_sqlite_security_state_checks_multiple_subjects_atomically(tmp_path: Path):
    state = SqlitePhase7SecurityState(_config(tmp_path))
    subjects = [
        RateLimitSubject("session_submission", "session_hmac", 60, 10),
        RateLimitSubject("network_full_burst", "network_hmac", 60, 1),
    ]

    assert state.allow_submission(subjects, now=100.0) is True
    assert state.allow_submission(subjects, now=101.0) is False

    session_only = RateLimitSubject("session_submission", "session_hmac", 60, 10)
    assert state.allow_submission([session_only], now=102.0) is True


def test_sqlite_security_state_prunes_expired_rate_limit_windows(tmp_path: Path):
    config = Phase7SecurityStateConfig(
        db_path=tmp_path / "phase7_security.sqlite3",
        rate_limit_window_seconds=60,
        rate_limit_max_requests=2,
        max_active_jobs=1,
        active_job_lease_seconds=60,
        rate_limit_retention_seconds=60,
    )
    state = SqlitePhase7SecurityState(config)

    assert state.allow_submission("sess_one", now=100.0) is True
    assert state.allow_submission("sess_two", now=500.0) is True

    with state._open_conn() as conn:
        rows = conn.execute(
            "SELECT subject_key FROM rate_limit_windows ORDER BY subject_key"
        ).fetchall()

    assert [row[0] for row in rows] == ["sess_two"]


def test_sqlite_security_state_shares_active_job_slots_across_instances(tmp_path: Path):
    first = SqlitePhase7SecurityState(_config(tmp_path))
    second = SqlitePhase7SecurityState(_config(tmp_path))

    assert first.try_acquire_job_slot("job_one", now=100.0) is True
    assert second.try_acquire_job_slot("job_two", now=101.0) is False
    first.release_job_slot("job_one")
    assert second.try_acquire_job_slot("job_two", now=102.0) is True


def test_sqlite_security_state_rejects_duplicate_active_job_slot(tmp_path: Path):
    state = SqlitePhase7SecurityState(
        Phase7SecurityStateConfig(
            db_path=tmp_path / "phase7_security.sqlite3",
            rate_limit_window_seconds=60,
            rate_limit_max_requests=2,
            max_active_jobs=2,
            active_job_lease_seconds=60,
        )
    )

    assert state.try_acquire_job_slot("job_one", now=100.0) is True
    assert state.try_acquire_job_slot("job_one", now=101.0) is False


def test_sqlite_security_state_allows_two_active_jobs_then_rejects_third(tmp_path: Path):
    state = SqlitePhase7SecurityState(
        Phase7SecurityStateConfig(
            db_path=tmp_path / "phase7_security.sqlite3",
            rate_limit_window_seconds=60,
            rate_limit_max_requests=2,
            max_active_jobs=2,
            active_job_lease_seconds=60,
        )
    )

    assert state.try_acquire_job_slot("job_one", now=100.0) is True
    assert state.try_acquire_job_slot("job_two", now=101.0) is True
    assert state.try_acquire_job_slot("job_three", now=102.0) is False
