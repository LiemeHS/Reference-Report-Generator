"""Framework-neutral Phase 7 job/session lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import json
import logging
from pathlib import Path
import re
import secrets
import hashlib
import time
from typing import Any

from reference_gen2.report_serving.service import (
    ReportServingConfig,
    delete_report_html,
    report_status,
    validate_report_id,
)
from reference_gen2.security.atomic_files import atomic_write_text, ensure_private_dir

logger = logging.getLogger(__name__)


class JobServingError(ValueError):
    """Base error for Phase 7 job lifecycle failures."""


class InvalidJobIdError(JobServingError):
    """Raised when a job id is not safe to resolve."""


class InvalidSessionIdError(JobServingError):
    """Raised when a session id is not safe to use."""


class JobNotFoundError(JobServingError):
    """Raised when a job is missing or expired."""


class JobOwnershipError(JobServingError):
    """Raised when a session does not own a job/report."""


@dataclass(frozen=True)
class JobError:
    """Public-safe error metadata for a failed job."""

    code: str
    message: str
    phase: str


@dataclass(frozen=True)
class JobState:
    """Public-safe metadata for a hosted job."""

    job_id: str
    status: str
    expires_at: str
    report_id: str | None = None
    report_url: str | None = None
    error: JobError | None = None


@dataclass(frozen=True)
class _JobRecord:
    job_id: str
    owner_key: str
    created_at_epoch: float
    updated_at_epoch: float
    status: str
    report_id: str | None = None
    error: JobError | None = None


@dataclass(frozen=True)
class _ReportJobIndexRecord:
    report_id: str
    job_id: str
    owner_key: str
    updated_at_epoch: float


def create_session_id(config: ReportServingConfig | None = None) -> str:
    """Create a high-entropy server-owned session id."""
    config = config or ReportServingConfig()
    return f"{config.session_id_prefix}_{secrets.token_hex(config.session_id_bytes)}"


def validate_session_id(session_id: str, config: ReportServingConfig | None = None) -> str:
    """Validate a session id before it is trusted for ownership checks."""
    config = config or ReportServingConfig()
    expected_hex_chars = config.session_id_bytes * 2
    pattern = rf"^{re.escape(config.session_id_prefix)}_[a-f0-9]{{{expected_hex_chars}}}$"
    if not re.fullmatch(pattern, session_id or ""):
        raise InvalidSessionIdError("Invalid session id")
    return session_id


def derive_owner_key(session_id: str, config: ReportServingConfig | None = None) -> str:
    config = config or ReportServingConfig()
    validated = validate_session_id(session_id, config)
    digest = hmac.new(
        config.ownership_secret.encode("utf-8"),
        validated.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"own_{digest}"


def create_job_id(config: ReportServingConfig | None = None) -> str:
    """Create a high-entropy, path-safe job id."""
    config = config or ReportServingConfig()
    return f"{config.job_id_prefix}_{secrets.token_hex(config.id_bytes)}"


def validate_job_id(job_id: str, config: ReportServingConfig | None = None) -> str:
    """Validate a job id before it is used for filesystem resolution."""
    config = config or ReportServingConfig()
    expected_hex_chars = config.id_bytes * 2
    pattern = rf"^{re.escape(config.job_id_prefix)}_[a-f0-9]{{{expected_hex_chars}}}$"
    if not re.fullmatch(pattern, job_id or ""):
        raise InvalidJobIdError("Invalid job id")
    return job_id


def create_job(
    owner_session_id: str,
    *,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> JobState:
    """Create a pending job owned by the provided session."""
    config = config or ReportServingConfig()
    validated_session = validate_session_id(owner_session_id, config)
    now = time.time() if now is None else now
    record = _JobRecord(
        job_id=create_job_id(config),
        owner_key=derive_owner_key(validated_session, config),
        created_at_epoch=now,
        updated_at_epoch=now,
        status="pending",
    )
    _write_job_record(record, config)
    logger.info("event=phase7.job_created status=pending")
    return _public_job_state(record, config=config)


def complete_job(
    job_id: str,
    *,
    owner_session_id: str | None = None,
    owner_key: str | None = None,
    report_id: str,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> JobState:
    """Mark a job completed and associate it with a report artifact."""
    config = config or ReportServingConfig()
    record = _load_owned_job_record(
        job_id,
        config=config,
        owner_session_id=owner_session_id,
        owner_key=owner_key,
    )
    now = time.time() if now is None else now
    updated = _JobRecord(
        job_id=record.job_id,
        owner_key=record.owner_key,
        created_at_epoch=record.created_at_epoch,
        updated_at_epoch=now,
        status="completed",
        report_id=report_id,
    )
    _write_job_record(updated, config)
    _write_report_job_index(updated, config)
    logger.info("event=phase7.job_completed status=completed")
    return _public_job_state(updated, config=config)


def fail_job(
    job_id: str,
    *,
    owner_session_id: str | None = None,
    owner_key: str | None = None,
    code: str,
    message: str,
    phase: str,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> JobState:
    """Mark a job failed with sanitized error metadata."""
    config = config or ReportServingConfig()
    record = _load_owned_job_record(
        job_id,
        config=config,
        owner_session_id=owner_session_id,
        owner_key=owner_key,
    )
    now = time.time() if now is None else now
    updated = _JobRecord(
        job_id=record.job_id,
        owner_key=record.owner_key,
        created_at_epoch=record.created_at_epoch,
        updated_at_epoch=now,
        status="failed",
        error=JobError(code=code, message=message, phase=phase),
    )
    _write_job_record(updated, config)
    logger.info("event=phase7.job_failed status=failed phase=%s", phase)
    return _public_job_state(updated, config=config)


def get_job(
    job_id: str,
    *,
    owner_session_id: str,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> JobState:
    """Return public-safe job state for the owning session."""
    config = config or ReportServingConfig()
    record = _load_owned_job_record(
        job_id,
        config=config,
        owner_session_id=owner_session_id,
        now=now,
    )
    return _public_job_state(record, config=config, now=now)


def update_job_status(
    job_id: str,
    *,
    status: str,
    owner_session_id: str | None = None,
    owner_key: str | None = None,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> JobState:
    """Update a job status without attaching a report or failure payload."""
    config = config or ReportServingConfig()
    record = _load_owned_job_record(
        job_id,
        config=config,
        owner_session_id=owner_session_id,
        owner_key=owner_key,
    )
    now = time.time() if now is None else now
    updated = _JobRecord(
        job_id=record.job_id,
        owner_key=record.owner_key,
        created_at_epoch=record.created_at_epoch,
        updated_at_epoch=now,
        status=status,
        report_id=record.report_id,
        error=None,
    )
    _write_job_record(updated, config)
    logger.info("event=phase7.job_status_updated status=%s", status)
    return _public_job_state(updated, config=config)


def get_job_by_report_id(
    report_id: str,
    *,
    owner_session_id: str,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> JobState:
    """Resolve a report id back to its owning job and return the public job state."""
    config = config or ReportServingConfig()
    record = _find_owned_record_by_report_id(
        report_id,
        owner_session_id=owner_session_id,
        config=config,
        now=now,
    )
    return _public_job_state(record, config=config, now=now)


def assert_report_owned(
    report_id: str,
    *,
    owner_session_id: str,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> None:
    """Validate that the provided session owns the given report id."""
    config = config or ReportServingConfig()
    _find_owned_record_by_report_id(
        report_id,
        owner_session_id=owner_session_id,
        config=config,
        now=now,
    )


def cleanup_expired_jobs(
    *,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> int:
    """Delete expired job state and any remaining owned report artifacts."""
    config = config or ReportServingConfig()
    now = time.time() if now is None else now
    job_dir = config.job_dir
    if not job_dir.exists():
        logger.info("event=phase7.job_cleanup_complete deleted_count=0")
        return 0

    deleted_count = 0
    for job_path in job_dir.glob("*.json"):
        record = _load_job_record_from_path(job_path)
        if record is None:
            continue
        if _job_is_expired(record, config, now=now):
            _delete_job_work_artifacts(record.job_id, config)
            if record.report_id:
                try:
                    delete_report_html(record.report_id, config=config)
                except Exception:
                    pass
                _delete_report_job_index(record.report_id, config)
            _unlink_job(job_path)
            deleted_count += 1
    logger.info("event=phase7.job_cleanup_complete deleted_count=%s", deleted_count)
    return deleted_count


def cleanup_legacy_job_state(
    *,
    config: ReportServingConfig | None = None,
) -> int:
    """Delete pre-owner-key job/index artifacts from older Phase 7 formats."""
    config = config or ReportServingConfig()
    deleted_count = 0
    if config.job_dir.exists():
        for job_path in config.job_dir.glob("*.json"):
            payload = _load_json_payload(job_path)
            if not payload:
                continue
            if "owner_key" in payload and "owner_session_id" not in payload:
                continue
            _unlink_job(job_path)
            report_id = payload.get("report_id")
            if isinstance(report_id, str) and report_id:
                _delete_report_job_index(report_id, config)
            job_id = payload.get("job_id")
            if isinstance(job_id, str) and job_id:
                _delete_job_work_artifacts(job_id, config)
            deleted_count += 1
    index_dir = config.job_dir / "report_index"
    if index_dir.exists():
        for index_path in index_dir.glob("*.json"):
            payload = _load_json_payload(index_path)
            if payload and "owner_key" in payload and "owner_session_id" not in payload:
                continue
            _unlink_job(index_path)
            deleted_count += 1
    if deleted_count:
        logger.info("event=phase7.job_legacy_cleanup deleted_count=%s", deleted_count)
    return deleted_count


def _public_job_state(
    record: _JobRecord,
    *,
    config: ReportServingConfig,
    now: float | None = None,
) -> JobState:
    status = record.status
    report_id = record.report_id
    report_url = f"/reports/{report_id}" if report_id else None
    error = record.error
    if _job_is_expired(record, config, now=now):
        status = "gone"
        report_id = None
        report_url = None
        error = None
    elif status == "completed" and report_id:
        artifact_status = report_status(report_id, config=config).status
        if artifact_status in {"gone", "expired"}:
            status = "gone"
            report_id = None
            report_url = None
    return JobState(
        job_id=record.job_id,
        status=status,
        expires_at=_job_expires_at_iso(record, config),
        report_id=report_id,
        report_url=report_url,
        error=error,
    )


def _load_owned_job_record(
    job_id: str,
    *,
    config: ReportServingConfig,
    owner_session_id: str | None = None,
    owner_key: str | None = None,
    now: float | None = None,
) -> _JobRecord:
    validated_owner_key = _resolve_owner_key(
        config,
        owner_session_id=owner_session_id,
        owner_key=owner_key,
    )
    record = _load_job_record(job_id, config)
    if record.owner_key != validated_owner_key:
        raise JobOwnershipError("Job is not owned by this session")
    if _job_is_expired(record, config, now=now):
        _delete_job_work_artifacts(record.job_id, config)
        if record.report_id:
            try:
                delete_report_html(record.report_id, config=config)
            except Exception:
                pass
            _delete_report_job_index(record.report_id, config)
        _unlink_job(_job_path(record.job_id, config))
        raise JobNotFoundError("Job expired")
    return record


def _find_owned_record_by_report_id(
    report_id: str,
    *,
    owner_session_id: str,
    config: ReportServingConfig,
    now: float | None = None,
) -> _JobRecord:
    validated_owner_key = derive_owner_key(owner_session_id, config)
    validated_report_id = validate_report_id(report_id, config)
    index_record = _load_report_job_index(validated_report_id, config)
    if index_record is None:
        raise JobNotFoundError("Job not found")
    if index_record.owner_key != validated_owner_key:
        raise JobOwnershipError("Job is not owned by this session")
    record = _load_job_record(index_record.job_id, config)
    if record.report_id != report_id:
        _delete_report_job_index(report_id, config)
        raise JobNotFoundError("Job not found")
    if _job_is_expired(record, config, now=now):
        _delete_job_work_artifacts(record.job_id, config)
        if record.report_id:
            try:
                delete_report_html(record.report_id, config=config)
            except Exception:
                pass
            _delete_report_job_index(record.report_id, config)
        _unlink_job(_job_path(record.job_id, config))
        raise JobNotFoundError("Job expired")
    return record


def _load_job_record(job_id: str, config: ReportServingConfig) -> _JobRecord:
    job_path = _job_path(job_id, config)
    if not job_path.exists():
        raise JobNotFoundError("Job not found")
    record = _load_job_record_from_path(job_path)
    if record is None:
        raise JobNotFoundError("Job not found")
    return record


def _load_job_record_from_path(job_path: Path) -> _JobRecord | None:
    payload = _load_json_payload(job_path)
    if payload is None:
        return None
    try:
        error_payload = payload.get("error")
        error = None
        if isinstance(error_payload, dict):
            error = JobError(
                code=str(error_payload.get("code", "unknown_error")),
                message=str(error_payload.get("message", "Request failed.")),
                phase=str(error_payload.get("phase", "phase7")),
            )
        owner_key = payload.get("owner_key")
        if not isinstance(owner_key, str) or not owner_key:
            return None
        return _JobRecord(
            job_id=str(payload["job_id"]),
            owner_key=owner_key,
            created_at_epoch=float(payload["created_at_epoch"]),
            updated_at_epoch=float(payload["updated_at_epoch"]),
            status=str(payload["status"]),
            report_id=str(payload["report_id"]) if payload.get("report_id") else None,
            error=error,
        )
    except (KeyError, TypeError, ValueError):
        return None


def _load_json_payload(job_path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(job_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _write_job_record(record: _JobRecord, config: ReportServingConfig) -> None:
    job_path = _job_path(record.job_id, config)
    payload: dict[str, Any] = {
        "job_id": record.job_id,
        "owner_key": record.owner_key,
        "created_at_epoch": record.created_at_epoch,
        "updated_at_epoch": record.updated_at_epoch,
        "status": record.status,
        "report_id": record.report_id,
        "error": None,
    }
    if record.error:
        payload["error"] = {
            "code": record.error.code,
            "message": record.error.message,
            "phase": record.error.phase,
        }
    atomic_write_text(job_path, json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _write_report_job_index(record: _JobRecord, config: ReportServingConfig) -> None:
    if not record.report_id:
        return
    index_path = _report_job_index_path(record.report_id, config)
    payload = {
        "report_id": record.report_id,
        "job_id": record.job_id,
        "owner_key": record.owner_key,
        "updated_at_epoch": record.updated_at_epoch,
    }
    atomic_write_text(index_path, json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def _load_report_job_index(
    report_id: str,
    config: ReportServingConfig,
) -> _ReportJobIndexRecord | None:
    index_path = _report_job_index_path(report_id, config)
    if not index_path.exists():
        return None
    try:
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        return _ReportJobIndexRecord(
            report_id=str(payload["report_id"]),
            job_id=str(payload["job_id"]),
            owner_key=str(payload["owner_key"]),
            updated_at_epoch=float(payload["updated_at_epoch"]),
        )
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        return None


def _delete_report_job_index(report_id: str, config: ReportServingConfig) -> None:
    try:
        _report_job_index_path(report_id, config).unlink()
    except FileNotFoundError:
        return


def _resolve_owner_key(
    config: ReportServingConfig,
    *,
    owner_session_id: str | None,
    owner_key: str | None,
) -> str:
    if owner_session_id:
        return derive_owner_key(owner_session_id, config)
    if owner_key:
        return str(owner_key)
    raise InvalidSessionIdError("Missing session or owner key")


def _delete_job_work_artifacts(job_id: str, config: ReportServingConfig) -> None:
    for path in (
        config.job_dir / "requests" / f"{job_id}.json",
        config.job_dir / "running" / f"{job_id}.json",
        config.job_dir / "payloads" / f"{job_id}.bin",
        config.job_dir / "payloads" / f"{job_id}.txt",
        config.job_dir / "payloads" / f"{job_id}.report.json",
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            continue


def _job_path(job_id: str, config: ReportServingConfig) -> Path:
    validated = validate_job_id(job_id, config)
    job_dir = config.job_dir.resolve()
    ensure_private_dir(job_dir)
    job_path = (job_dir / f"{validated}.json").resolve()
    try:
        job_path.relative_to(job_dir)
    except ValueError as exc:
        raise InvalidJobIdError("Invalid job path") from exc
    return job_path


def _report_job_index_path(report_id: str, config: ReportServingConfig) -> Path:
    job_dir = config.job_dir.resolve()
    index_dir = (job_dir / "report_index").resolve()
    ensure_private_dir(job_dir)
    ensure_private_dir(index_dir)
    report_id = str(report_id)
    index_path = (index_dir / f"{report_id}.json").resolve()
    try:
        index_path.relative_to(index_dir)
    except ValueError as exc:
        raise InvalidJobIdError("Invalid report index path") from exc
    return index_path


def _job_is_expired(
    record: _JobRecord,
    config: ReportServingConfig,
    *,
    now: float | None = None,
) -> bool:
    now = time.time() if now is None else now
    return now - record.updated_at_epoch > config.job_ttl_seconds


def _job_expires_at_iso(record: _JobRecord, config: ReportServingConfig) -> str:
    expires_at = record.updated_at_epoch + config.job_ttl_seconds
    return datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()


def _unlink_job(job_path: Path) -> None:
    try:
        job_path.unlink()
    except FileNotFoundError:
        return
