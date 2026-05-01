"""Execution backends for Phase 7 synchronous and worker-backed jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import time
from typing import Any, Literal

from reference_gen2.report_generation import ReportGenerationError, render_html_report
from reference_gen2.report_serving.jobs import (
    JobState,
    complete_job,
    create_job,
    fail_job,
    update_job_status,
)
from reference_gen2.report_serving.security_state import SqlitePhase7SecurityState
from reference_gen2.report_serving.service import ReportServingConfig, store_report_html
from reference_gen2.security.atomic_files import atomic_write_bytes, atomic_write_text, ensure_private_dir
from reference_gen2.services.hosted_report_pipeline import (
    HostedReportPipelineError,
    run_hosted_report_pipeline,
    run_text_report_pipeline,
)

logger = logging.getLogger(__name__)

SubmissionKind = Literal["upload", "text", "sanitized_report"]


class Phase7SubmissionError(Exception):
    """Safe error wrapper for execution backend failures."""

    def __init__(self, *, code: str, message: str, phase: str, http_status: int) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.phase = phase
        self.http_status = http_status


@dataclass(frozen=True)
class UploadJobSubmission:
    filename: str
    content: bytes = field(repr=False)
    declared_mime: str | None = None
    style_hint: str | None = "unknown"


@dataclass(frozen=True)
class TextJobSubmission:
    reference_list_text: str = field(repr=False)
    style_hint: str | None = "unknown"


@dataclass(frozen=True)
class SanitizedReportJobSubmission:
    payload: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class _QueuedWorkItem:
    job_id: str
    owner_key: str
    kind: SubmissionKind
    created_at_epoch: float
    payload_path: str
    request_path: str | None = None
    filename: str | None = None
    declared_mime: str | None = None
    style_hint: str | None = None


class Phase7ExecutionBackend:
    """Base execution backend for hosted report jobs."""

    mode = "unknown"

    async def submit_upload(
        self,
        *,
        owner_session_id: str,
        submission: UploadJobSubmission,
    ) -> JobState:
        raise NotImplementedError

    async def submit_text(
        self,
        *,
        owner_session_id: str,
        submission: TextJobSubmission,
    ) -> JobState:
        raise NotImplementedError

    async def submit_sanitized_report(
        self,
        *,
        owner_session_id: str,
        submission: SanitizedReportJobSubmission,
    ) -> JobState:
        raise NotImplementedError

    def run_available_jobs(self, *, limit: int = 1) -> int:
        return 0

    def has_submission_capacity(self) -> bool:
        return True

    def submission_pressure(self) -> float:
        return 0.0


class SyncPhase7ExecutionBackend(Phase7ExecutionBackend):
    """Run jobs inline within the API process."""

    mode = "sync"

    def __init__(
        self,
        *,
        serving_config: ReportServingConfig,
        security_state: SqlitePhase7SecurityState,
        db_path: str | None,
        text_input_max_chars: int,
        max_queued_jobs: int,
    ) -> None:
        self._serving_config = serving_config
        self._security_state = security_state
        self._db_path = db_path
        self._text_input_max_chars = text_input_max_chars

    async def submit_upload(
        self,
        *,
        owner_session_id: str,
        submission: UploadJobSubmission,
    ) -> JobState:
        self._require_db_path()
        job = create_job(owner_session_id, config=self._serving_config)
        try:
            generated = await self._run_bounded(
                job.job_id,
                lambda: run_hosted_report_pipeline(
                    filename=submission.filename,
                    declared_mime=submission.declared_mime,
                    content=submission.content,
                    db_path=self._db_path or "",
                    style_hint=submission.style_hint,
                ),
            )
            return self._complete_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                html=generated.html,
            )
        except HostedReportPipelineError as exc:
            self._fail_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                code=exc.code,
                message=exc.message,
                phase=exc.phase,
            )
            raise Phase7SubmissionError(
                code=exc.code,
                message=exc.message,
                phase=exc.phase,
                http_status=exc.http_status,
            ) from exc
        except Exception:
            self._fail_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                code="internal_server_error",
                message="Internal server error.",
                phase="phase7",
            )
            raise

    async def submit_text(
        self,
        *,
        owner_session_id: str,
        submission: TextJobSubmission,
    ) -> JobState:
        self._require_db_path()
        job = create_job(owner_session_id, config=self._serving_config)
        try:
            generated = await self._run_bounded(
                job.job_id,
                lambda: run_text_report_pipeline(
                    reference_list_text=submission.reference_list_text,
                    db_path=self._db_path or "",
                    style_hint=submission.style_hint,
                    max_chars=self._text_input_max_chars,
                ),
            )
            return self._complete_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                html=generated.html,
            )
        except HostedReportPipelineError as exc:
            self._fail_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                code=exc.code,
                message=exc.message,
                phase=exc.phase,
            )
            raise Phase7SubmissionError(
                code=exc.code,
                message=exc.message,
                phase=exc.phase,
                http_status=exc.http_status,
            ) from exc
        except Exception:
            self._fail_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                code="internal_server_error",
                message="Internal server error.",
                phase="phase7",
            )
            raise

    async def submit_sanitized_report(
        self,
        *,
        owner_session_id: str,
        submission: SanitizedReportJobSubmission,
    ) -> JobState:
        job = create_job(owner_session_id, config=self._serving_config)
        try:
            html = render_html_report(submission.payload)
            return self._complete_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                html=html,
            )
        except ReportGenerationError as exc:
            self._fail_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                code="invalid_sanitized_report_payload",
                message="Invalid sanitized report payload.",
                phase="phase6",
            )
            raise Phase7SubmissionError(
                code="invalid_sanitized_report_payload",
                message="Invalid sanitized report payload.",
                phase="phase6",
                http_status=400,
            ) from exc
        except Exception:
            self._fail_job(
                job_id=job.job_id,
                owner_session_id=owner_session_id,
                code="internal_server_error",
                message="Internal server error.",
                phase="phase7",
            )
            raise

    async def _run_bounded(self, job_id: str, callback: Any) -> Any:
        if not self._security_state.try_acquire_job_slot(job_id):
            logger.info("event=phase7.job_rejected code=too_many_inflight_jobs status_class=4xx")
            raise Phase7SubmissionError(
                code="too_many_inflight_jobs",
                message="Too many jobs are running right now.",
                phase="phase7",
                http_status=429,
            )
        try:
            return callback()
        finally:
            self._security_state.release_job_slot(job_id)

    def _complete_job(self, *, job_id: str, owner_session_id: str, html: str) -> JobState:
        status = store_report_html(html, config=self._serving_config)
        return complete_job(
            job_id,
            owner_session_id=owner_session_id,
            report_id=status.report_id,
            config=self._serving_config,
        )

    def _fail_job(
        self,
        *,
        job_id: str,
        owner_session_id: str,
        code: str,
        message: str,
        phase: str,
    ) -> JobState:
        return fail_job(
            job_id,
            owner_session_id=owner_session_id,
            code=code,
            message=message,
            phase=phase,
            config=self._serving_config,
        )

    def _require_db_path(self) -> None:
        if self._db_path:
            return
        raise Phase7SubmissionError(
            code="report_generation_not_configured",
            message="Report generation is not configured.",
            phase="phase7",
            http_status=503,
        )


class QueuedPhase7ExecutionBackend(Phase7ExecutionBackend):
    """Persist job work items for a separate worker process."""

    mode = "worker"

    def __init__(
        self,
        *,
        serving_config: ReportServingConfig,
        security_state: SqlitePhase7SecurityState,
        db_path: str | None,
        text_input_max_chars: int,
        max_queued_jobs: int,
    ) -> None:
        self._serving_config = serving_config
        self._security_state = security_state
        self._db_path = db_path
        self._text_input_max_chars = text_input_max_chars
        self._max_queued_jobs = max_queued_jobs

    async def submit_upload(
        self,
        *,
        owner_session_id: str,
        submission: UploadJobSubmission,
    ) -> JobState:
        self._require_db_path()
        self._enforce_queue_capacity()
        job = create_job(owner_session_id, config=self._serving_config)
        owner_key = self._read_owner_key(job.job_id)
        payload_path = self._payload_path(job.job_id, "bin")
        atomic_write_bytes(payload_path, submission.content)
        self._write_work_item(
            _QueuedWorkItem(
                job_id=job.job_id,
                owner_key=owner_key,
                kind="upload",
                created_at_epoch=time.time(),
                payload_path=str(payload_path),
                filename=submission.filename,
                declared_mime=submission.declared_mime,
                style_hint=submission.style_hint,
            ),
        )
        return update_job_status(
            job.job_id,
            status="queued",
            owner_session_id=owner_session_id,
            config=self._serving_config,
        )

    async def submit_text(
        self,
        *,
        owner_session_id: str,
        submission: TextJobSubmission,
    ) -> JobState:
        self._require_db_path()
        self._enforce_queue_capacity()
        job = create_job(owner_session_id, config=self._serving_config)
        owner_key = self._read_owner_key(job.job_id)
        payload_path = self._payload_path(job.job_id, "txt")
        atomic_write_text(payload_path, submission.reference_list_text, encoding="utf-8")
        self._write_work_item(
            _QueuedWorkItem(
                job_id=job.job_id,
                owner_key=owner_key,
                kind="text",
                created_at_epoch=time.time(),
                payload_path=str(payload_path),
                style_hint=submission.style_hint,
            ),
        )
        return update_job_status(
            job.job_id,
            status="queued",
            owner_session_id=owner_session_id,
            config=self._serving_config,
        )

    async def submit_sanitized_report(
        self,
        *,
        owner_session_id: str,
        submission: SanitizedReportJobSubmission,
    ) -> JobState:
        self._enforce_queue_capacity()
        job = create_job(owner_session_id, config=self._serving_config)
        owner_key = self._read_owner_key(job.job_id)
        payload_path = self._payload_path(job.job_id, "report.json")
        atomic_write_text(
            payload_path,
            json.dumps(submission.payload, separators=(",", ":")),
            encoding="utf-8",
        )
        self._write_work_item(
            _QueuedWorkItem(
                job_id=job.job_id,
                owner_key=owner_key,
                kind="sanitized_report",
                created_at_epoch=time.time(),
                payload_path=str(payload_path),
            ),
        )
        return update_job_status(
            job.job_id,
            status="queued",
            owner_session_id=owner_session_id,
            config=self._serving_config,
        )

    def run_available_jobs(self, *, limit: int = 1) -> int:
        processed = 0
        for request_path in sorted(self._requests_dir().glob("*.json")):
            if processed >= limit:
                break
            claimed_path = self._claim_request_path(request_path)
            if claimed_path is None:
                continue
            item = self._load_work_item(claimed_path)
            if item is None:
                self._unlink_claimed_request(claimed_path)
                continue
            if not self._security_state.try_acquire_job_slot(item.job_id):
                self._release_claimed_request(claimed_path)
                break
            try:
                self._run_one(item)
                processed += 1
            finally:
                self._security_state.release_job_slot(item.job_id)
        return processed

    def _run_one(self, item: _QueuedWorkItem) -> None:
        queue_wait_ms = max(0.0, (time.time() - item.created_at_epoch) * 1000)
        logger.info("event=phase7.job_started queue_wait_ms=%.2f", queue_wait_ms)
        update_job_status(
            item.job_id,
            status="running",
            owner_key=item.owner_key,
            config=self._serving_config,
        )
        try:
            html = self._render_html(item)
            status = store_report_html(html, config=self._serving_config)
            complete_job(
                item.job_id,
                owner_key=item.owner_key,
                report_id=status.report_id,
                config=self._serving_config,
            )
            logger.info("event=phase7.job_completed status=completed")
        except HostedReportPipelineError as exc:
            fail_job(
                item.job_id,
                owner_key=item.owner_key,
                code=exc.code,
                message=exc.message,
                phase=exc.phase,
                config=self._serving_config,
            )
            logger.info("event=phase7.job_failed phase=%s", exc.phase)
        except ReportGenerationError:
            fail_job(
                item.job_id,
                owner_key=item.owner_key,
                code="invalid_sanitized_report_payload",
                message="Invalid sanitized report payload.",
                phase="phase6",
                config=self._serving_config,
            )
            logger.info("event=phase7.job_failed phase=phase6")
        except Exception:
            fail_job(
                item.job_id,
                owner_key=item.owner_key,
                code="internal_server_error",
                message="Internal server error.",
                phase="phase7",
                config=self._serving_config,
            )
            logger.info("event=phase7.job_failed phase=phase7")
        finally:
            self._cleanup_work_item(item)

    def _render_html(self, item: _QueuedWorkItem) -> str:
        payload_path = Path(item.payload_path)
        if item.kind == "upload":
            return run_hosted_report_pipeline(
                filename=item.filename or "upload",
                declared_mime=item.declared_mime,
                content=payload_path.read_bytes(),
                db_path=self._db_path or "",
                style_hint=item.style_hint,
            ).html
        if item.kind == "text":
            return run_text_report_pipeline(
                reference_list_text=payload_path.read_text(encoding="utf-8"),
                db_path=self._db_path or "",
                style_hint=item.style_hint,
                max_chars=self._text_input_max_chars,
            ).html
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        return render_html_report(payload)

    def _cleanup_work_item(self, item: _QueuedWorkItem) -> None:
        request_path = (
            Path(item.request_path) if item.request_path else self._request_path(item.job_id)
        )
        for path in (
            request_path,
            self._request_path(item.job_id),
            Path(item.payload_path),
        ):
            try:
                path.unlink()
            except FileNotFoundError:
                continue

    def _write_work_item(self, item: _QueuedWorkItem) -> None:
        request_path = self._request_path(item.job_id)
        ensure_private_dir(request_path.parent)
        atomic_write_text(
            request_path,
            json.dumps(
                {
                    "job_id": item.job_id,
                    "owner_key": item.owner_key,
                    "kind": item.kind,
                    "created_at_epoch": item.created_at_epoch,
                    "payload_path": item.payload_path,
                    "filename": item.filename,
                    "declared_mime": item.declared_mime,
                    "style_hint": item.style_hint,
                },
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        logger.info("event=phase7.job_queued status=queued")

    def _claim_request_path(self, request_path: Path) -> Path | None:
        running_path = self._running_request_path(request_path.stem)
        ensure_private_dir(running_path.parent)
        if running_path.exists():
            logger.info("event=phase7.job_claim_skipped reason=already_running")
            return None
        try:
            os.rename(request_path, running_path)
        except FileNotFoundError:
            logger.info("event=phase7.job_claim_skipped reason=missing")
            return None
        except OSError:
            logger.info("event=phase7.job_claim_skipped reason=rename_failed")
            return None
        logger.info("event=phase7.job_claimed status=claimed")
        return running_path

    def _release_claimed_request(self, running_path: Path) -> None:
        request_path = self._request_path(running_path.stem)
        if request_path.exists():
            return
        try:
            os.rename(running_path, request_path)
        except OSError:
            logger.info("event=phase7.job_claim_skipped reason=release_failed")

    def _unlink_claimed_request(self, running_path: Path) -> None:
        try:
            running_path.unlink()
        except FileNotFoundError:
            return

    def _load_work_item(self, request_path: Path) -> _QueuedWorkItem | None:
        try:
            payload = json.loads(request_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return _QueuedWorkItem(
                job_id=str(payload["job_id"]),
                owner_key=str(payload["owner_key"]),
                kind=str(payload["kind"]),
                created_at_epoch=float(payload["created_at_epoch"]),
                payload_path=str(payload["payload_path"]),
                request_path=str(request_path),
                filename=payload.get("filename"),
                declared_mime=payload.get("declared_mime"),
                style_hint=payload.get("style_hint"),
            )
        except (KeyError, TypeError, ValueError):
            return None

    def _read_owner_key(self, job_id: str) -> str:
        job_payload = json.loads(
            (self._serving_config.job_dir / f"{job_id}.json").read_text(encoding="utf-8")
        )
        return str(job_payload["owner_key"])

    def _require_db_path(self) -> None:
        if self._db_path:
            return
        raise Phase7SubmissionError(
            code="report_generation_not_configured",
            message="Report generation is not configured.",
            phase="phase7",
            http_status=503,
        )

    def _enforce_queue_capacity(self) -> None:
        if self.has_submission_capacity():
            return
        logger.info("event=phase7.job_rejected code=too_many_queued_jobs status_class=4xx")
        raise Phase7SubmissionError(
            code="too_many_queued_jobs",
            message="Too many jobs are queued right now.",
            phase="phase7",
            http_status=429,
        )

    def has_submission_capacity(self) -> bool:
        return (
            sum(1 for _ in self._requests_dir().glob("*.json"))
            < self._max_queued_jobs
        )

    def submission_pressure(self) -> float:
        if self._max_queued_jobs <= 0:
            return 1.0
        queued_count = sum(1 for _ in self._requests_dir().glob("*.json"))
        return min(1.0, queued_count / self._max_queued_jobs)

    def _requests_dir(self) -> Path:
        return self._serving_config.job_dir / "requests"

    def _running_dir(self) -> Path:
        return self._serving_config.job_dir / "running"

    def _request_path(self, job_id: str) -> Path:
        return self._requests_dir() / f"{job_id}.json"

    def _running_request_path(self, job_id: str) -> Path:
        return self._running_dir() / f"{job_id}.json"

    def _payload_path(self, job_id: str, suffix: str) -> Path:
        return self._serving_config.job_dir / "payloads" / f"{job_id}.{suffix}"


def create_phase7_execution_backend(
    *,
    mode: str,
    serving_config: ReportServingConfig,
    security_state: SqlitePhase7SecurityState,
    db_path: str | None,
    text_input_max_chars: int,
    max_queued_jobs: int,
) -> Phase7ExecutionBackend:
    normalized = mode.strip().lower()
    if normalized == "sync":
        return SyncPhase7ExecutionBackend(
            serving_config=serving_config,
            security_state=security_state,
            db_path=db_path,
            text_input_max_chars=text_input_max_chars,
            max_queued_jobs=max_queued_jobs,
        )
    if normalized == "worker":
        return QueuedPhase7ExecutionBackend(
            serving_config=serving_config,
            security_state=security_state,
            db_path=db_path,
            text_input_max_chars=text_input_max_chars,
            max_queued_jobs=max_queued_jobs,
        )
    raise ValueError(f"Unsupported Phase 7 execution backend: {mode!r}")
