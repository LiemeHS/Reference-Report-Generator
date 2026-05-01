from __future__ import annotations

import sqlite3

from reference_gen2.api.phase7_worker import run_startup_db_warmup
from reference_gen2.reference_matching import warm_localdb_cache


def test_worker_db_warmup_is_disabled_by_default():
    called = False

    def warm_func(_db_path: str, *, max_seconds: int) -> bool:
        nonlocal called
        called = True
        return True

    status = run_startup_db_warmup(
        db_path="/tmp/unused.sqlite3",
        enabled=False,
        max_seconds=3,
        warm_func=warm_func,
    )

    assert status == "disabled"
    assert not called


def test_worker_db_warmup_skips_when_jobs_are_already_queued():
    called = False

    def warm_func(_db_path: str, *, max_seconds: int) -> bool:
        nonlocal called
        called = True
        return True

    status = run_startup_db_warmup(
        db_path="/tmp/unused.sqlite3",
        enabled=True,
        max_seconds=3,
        queued_jobs_present=True,
        warm_func=warm_func,
    )

    assert status == "skipped_queued_jobs"
    assert not called


def test_worker_db_warmup_passes_strict_time_budget():
    calls: list[tuple[str, int]] = []

    def warm_func(db_path: str, *, max_seconds: int) -> bool:
        calls.append((db_path, max_seconds))
        return False

    status = run_startup_db_warmup(
        db_path="/tmp/localdb.sqlite3",
        enabled=True,
        max_seconds=2,
        warm_func=warm_func,
    )

    assert status == "timed_out"
    assert calls == [("/tmp/localdb.sqlite3", 2)]


def test_warm_localdb_cache_uses_readonly_bounded_probes(tmp_path):
    db_path = tmp_path / "localdb.sqlite3"
    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE search_book (id INTEGER PRIMARY KEY, title TEXT)")
        conn.execute("INSERT INTO search_book (title) VALUES ('Example')")

    assert warm_localdb_cache(str(db_path), max_seconds=1)
    assert not warm_localdb_cache(str(db_path), max_seconds=0)
