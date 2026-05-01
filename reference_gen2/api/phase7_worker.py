"""Background worker for queued Phase 7 jobs."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from reference_gen2.api.settings import (
    API_ACTIVE_JOB_LEASE_SECONDS,
    API_MAX_CONCURRENT_JOBS,
    API_MAX_QUEUED_JOBS,
    API_RATE_LIMIT_MAX_REQUESTS,
    API_RATE_LIMIT_WINDOW_SECONDS,
    API_SECURITY_STATE_DB_PATH,
    LOCAL_DB_PATH,
    PHASE7_WORKER_DB_WARMUP_ENABLED,
    PHASE7_WORKER_DB_WARMUP_MAX_SECONDS,
    PHASE7_WORKER_CONCURRENCY,
    PHASE7_WORKER_POLL_SECONDS,
    REPORT_SERVING_CLEANUP_INTERVAL_SECONDS,
    REPORT_SERVING_JOB_DIR,
    REPORT_SERVING_JOB_TTL_SECONDS,
    REPORT_SERVING_OWNERSHIP_SECRET,
    REPORT_SERVING_TMP_DIR,
    REPORT_SERVING_TTL_SECONDS,
    TEXT_INPUT_MAX_CHARS,
)
from reference_gen2.report_serving import (
    Phase7SecurityStateConfig,
    ReportServingConfig,
    SqlitePhase7SecurityState,
    cleanup_expired_jobs,
    cleanup_expired_reports,
    create_phase7_execution_backend,
)
from reference_gen2.reference_matching import warm_localdb_cache

logger = logging.getLogger(__name__)


def _serving_config() -> ReportServingConfig:
    return ReportServingConfig(
        report_dir=REPORT_SERVING_TMP_DIR,
        job_dir=REPORT_SERVING_JOB_DIR,
        ttl_seconds=REPORT_SERVING_TTL_SECONDS,
        job_ttl_seconds=REPORT_SERVING_JOB_TTL_SECONDS,
        cleanup_interval_seconds=REPORT_SERVING_CLEANUP_INTERVAL_SECONDS,
        ownership_secret=REPORT_SERVING_OWNERSHIP_SECRET,
    )


def build_worker_backend():
    serving_config = _serving_config()
    security_state = SqlitePhase7SecurityState(
        Phase7SecurityStateConfig(
            db_path=API_SECURITY_STATE_DB_PATH,
            rate_limit_window_seconds=API_RATE_LIMIT_WINDOW_SECONDS,
            rate_limit_max_requests=API_RATE_LIMIT_MAX_REQUESTS,
            max_active_jobs=API_MAX_CONCURRENT_JOBS,
            active_job_lease_seconds=API_ACTIVE_JOB_LEASE_SECONDS,
            max_queued_jobs=API_MAX_QUEUED_JOBS,
        )
    )
    return create_phase7_execution_backend(
        mode="worker",
        serving_config=serving_config,
        security_state=security_state,
        db_path=LOCAL_DB_PATH,
        text_input_max_chars=TEXT_INPUT_MAX_CHARS,
        max_queued_jobs=API_MAX_QUEUED_JOBS,
    )


def run_startup_db_warmup(
    *,
    db_path: str | None = LOCAL_DB_PATH,
    enabled: bool = PHASE7_WORKER_DB_WARMUP_ENABLED,
    max_seconds: int = PHASE7_WORKER_DB_WARMUP_MAX_SECONDS,
    queued_jobs_present: bool = False,
    warm_func: Callable[..., bool] = warm_localdb_cache,
) -> str:
    if not enabled:
        return "disabled"
    if queued_jobs_present:
        logger.info("event=phase7.worker_db_warmup_skipped reason=queued_jobs")
        return "skipped_queued_jobs"
    if not db_path:
        logger.info("event=phase7.worker_db_warmup_skipped reason=no_db_path")
        return "skipped_no_db_path"

    started = time.perf_counter()
    try:
        completed = warm_func(db_path, max_seconds=max_seconds)
    except Exception:
        logger.exception("event=phase7.worker_db_warmup_failed status=error")
        return "failed"

    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    status = "completed" if completed else "timed_out"
    logger.info(
        "event=phase7.worker_db_warmup_complete status=%s elapsed_ms=%s max_seconds=%s",
        status,
        elapsed_ms,
        max_seconds,
    )
    return status


def run_worker_forever(
    *,
    poll_seconds: int = PHASE7_WORKER_POLL_SECONDS,
    concurrency: int = PHASE7_WORKER_CONCURRENCY,
    db_warmup_enabled: bool = PHASE7_WORKER_DB_WARMUP_ENABLED,
    db_warmup_max_seconds: int = PHASE7_WORKER_DB_WARMUP_MAX_SECONDS,
) -> None:
    serving_config = _serving_config()
    backend = build_worker_backend()
    run_startup_db_warmup(
        enabled=db_warmup_enabled,
        max_seconds=db_warmup_max_seconds,
        queued_jobs_present=backend.submission_pressure() > 0,
    )
    if concurrency <= 0:
        raise ValueError("Worker concurrency must be a positive integer.")
    if concurrency > 1:
        logger.info(
            "event=phase7.worker_concurrency status=enabled workers=%s",
            concurrency,
        )
        threads = [
            threading.Thread(
                target=_run_worker_loop,
                kwargs={
                    "poll_seconds": poll_seconds,
                    "serving_config": serving_config,
                },
                name=f"phase7-worker-{index + 1}",
            )
            for index in range(concurrency)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return
    _run_worker_loop(
        poll_seconds=poll_seconds,
        serving_config=serving_config,
        backend=backend,
    )


def _run_worker_loop(
    *,
    poll_seconds: int,
    serving_config: ReportServingConfig,
    backend=None,
) -> None:
    backend = backend or build_worker_backend()
    last_cleanup = 0.0
    while True:
        processed = backend.run_available_jobs(limit=1)
        now = time.time()
        if now - last_cleanup >= REPORT_SERVING_CLEANUP_INTERVAL_SECONDS:
            cleanup_expired_reports(config=serving_config, now=now)
            cleanup_expired_jobs(config=serving_config, now=now)
            last_cleanup = now
        if processed == 0:
            time.sleep(poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    logger.info("event=phase7.worker_start status=ok")
    run_worker_forever()


if __name__ == "__main__":
    main()
