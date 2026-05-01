"""Framework-neutral Phase 7 report serving lifecycle helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
from pathlib import Path
import re
import secrets
import time

from reference_gen2.report_generation.service import (
    report_inline_script_csp_hash,
    report_inline_style_csp_hash,
)
from reference_gen2.security.atomic_files import atomic_write_text


REPORT_SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; "
        f"style-src {report_inline_style_csp_hash()}; "
        f"script-src {report_inline_script_csp_hash()}; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
    "Content-Type": "text/html; charset=utf-8",
}

logger = logging.getLogger(__name__)


class ReportServingError(ValueError):
    """Base error for Phase 7 report serving failures."""


class InvalidReportIdError(ReportServingError):
    """Raised when a report id is not safe to resolve."""


class ReportNotFoundError(ReportServingError):
    """Raised when a report is missing or already consumed."""


@dataclass(frozen=True)
class ReportServingConfig:
    """Configuration for private ephemeral report serving."""

    report_dir: Path = Path("/tmp/reference_gen2_reports")
    job_dir: Path = Path("/tmp/reference_gen2_jobs")
    ttl_seconds: int = 3600
    job_ttl_seconds: int = 3600
    id_prefix: str = "cycle"
    job_id_prefix: str = "job"
    id_bytes: int = 16
    session_id_prefix: str = "sess"
    session_id_bytes: int = 16
    session_cookie_name: str = "reference_gen2_session"
    cleanup_interval_seconds: int = 300
    ownership_secret: str = "reference_gen2_dev_ownership_secret_change_me"

    def __post_init__(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if self.job_ttl_seconds <= 0:
            raise ValueError("job_ttl_seconds must be positive")
        if self.id_bytes < 12:
            raise ValueError("id_bytes must be at least 12 for report id entropy")
        if self.session_id_bytes < 12:
            raise ValueError("session_id_bytes must be at least 12 for session id entropy")
        if self.cleanup_interval_seconds <= 0:
            raise ValueError("cleanup_interval_seconds must be positive")
        if len(self.ownership_secret) < 16:
            raise ValueError("ownership_secret must be at least 16 characters")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.id_prefix):
            raise ValueError("id_prefix must be lowercase alphanumeric/underscore and start with a letter")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.job_id_prefix):
            raise ValueError(
                "job_id_prefix must be lowercase alphanumeric/underscore and start with a letter"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_]*", self.session_id_prefix):
            raise ValueError(
                "session_id_prefix must be lowercase alphanumeric/underscore and start with a letter"
            )


@dataclass(frozen=True)
class ReportServeResult:
    """HTML content and headers ready for a web adapter response."""

    html: str
    headers: dict[str, str]


@dataclass(frozen=True)
class ReportStatus:
    """Public-safe status for a hosted report artifact."""

    report_id: str
    status: str
    expires_at: str | None = None


def create_report_id(config: ReportServingConfig | None = None) -> str:
    """Create a high-entropy, path-safe report id."""
    config = config or ReportServingConfig()
    return f"{config.id_prefix}_{secrets.token_hex(config.id_bytes)}"


def validate_report_id(report_id: str, config: ReportServingConfig | None = None) -> str:
    """Validate a report id before it is used for filesystem resolution."""
    config = config or ReportServingConfig()
    expected_hex_chars = config.id_bytes * 2
    pattern = rf"^{re.escape(config.id_prefix)}_[a-f0-9]{{{expected_hex_chars}}}$"
    if not re.fullmatch(pattern, report_id or ""):
        raise InvalidReportIdError("Invalid report id")
    return report_id


def security_headers() -> dict[str, str]:
    """Return required hosted-report security headers."""
    return dict(REPORT_SECURITY_HEADERS)


def store_report_html(
    html: str,
    *,
    config: ReportServingConfig | None = None,
    report_id: str | None = None,
) -> ReportStatus:
    """Store rendered HTML in private ephemeral storage.

    The caller receives only the report id and expiry metadata, never a
    filesystem path.
    """
    config = config or ReportServingConfig()
    report_id = validate_report_id(report_id, config) if report_id else create_report_id(config)
    report_path = _report_path(report_id, config)
    try:
        atomic_write_text(report_path, html, encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise ReportServingError("Could not store report") from exc
    logger.info(
        "event=phase7.report_stored status=available bytes=%s ttl_seconds=%s",
        len(html.encode("utf-8")),
        config.ttl_seconds,
    )
    return ReportStatus(
        report_id=report_id,
        status="available",
        expires_at=_expires_at_iso(report_path, config),
    )


def serve_report_once(
    report_id: str,
    *,
    config: ReportServingConfig | None = None,
) -> ReportServeResult:
    """Compatibility wrapper for one-shot serving."""
    return serve_report_html(report_id, config=config, consume=True)


def serve_report_html(
    report_id: str,
    *,
    config: ReportServingConfig | None = None,
    consume: bool = False,
) -> ReportServeResult:
    """Read a report artifact and optionally delete it after read."""
    config = config or ReportServingConfig()
    report_path = _report_path(report_id, config)
    if not report_path.exists():
        raise ReportNotFoundError("Report not found")
    if _is_expired(report_path, config):
        _unlink_report(report_path)
        logger.info("event=phase7.report_expired status=gone")
        raise ReportNotFoundError("Report expired")
    try:
        html = report_path.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - filesystem failure
        raise ReportServingError("Could not read report") from exc
    if consume:
        _unlink_report(report_path)
        logger.info("event=phase7.report_served status=deleted bytes=%s", len(html.encode("utf-8")))
    else:
        logger.info("event=phase7.report_served status=kept bytes=%s", len(html.encode("utf-8")))
    return ReportServeResult(html=html, headers=security_headers())


def report_status(
    report_id: str,
    *,
    config: ReportServingConfig | None = None,
) -> ReportStatus:
    """Return public-safe report availability status."""
    config = config or ReportServingConfig()
    report_path = _report_path(report_id, config)
    if not report_path.exists():
        return ReportStatus(report_id=report_id, status="gone")
    if _is_expired(report_path, config):
        return ReportStatus(report_id=report_id, status="expired", expires_at=_expires_at_iso(report_path, config))
    return ReportStatus(report_id=report_id, status="available", expires_at=_expires_at_iso(report_path, config))


def delete_report_html(
    report_id: str,
    *,
    config: ReportServingConfig | None = None,
) -> bool:
    """Delete a hosted report artifact if it exists."""
    config = config or ReportServingConfig()
    report_path = _report_path(report_id, config)
    if not report_path.exists():
        return False
    _unlink_report(report_path)
    logger.info("event=phase7.report_deleted status=deleted")
    return True


def cleanup_expired_reports(
    *,
    config: ReportServingConfig | None = None,
    now: float | None = None,
) -> int:
    """Delete expired report HTML artifacts and return the deletion count."""
    config = config or ReportServingConfig()
    now = time.time() if now is None else now
    report_dir = config.report_dir
    if not report_dir.exists():
        logger.info("event=phase7.cleanup_complete deleted_count=0")
        return 0
    deleted_count = 0
    for report_path in report_dir.glob("*.html"):
        if _is_expired(report_path, config, now=now):
            _unlink_report(report_path)
            deleted_count += 1
    logger.info("event=phase7.cleanup_complete deleted_count=%s", deleted_count)
    return deleted_count


def _report_path(report_id: str, config: ReportServingConfig) -> Path:
    validated = validate_report_id(report_id, config)
    report_dir = config.report_dir.resolve()
    report_path = (report_dir / f"{validated}.html").resolve()
    try:
        report_path.relative_to(report_dir)
    except ValueError as exc:  # pragma: no cover - validate_report_id should prevent this
        raise InvalidReportIdError("Invalid report path") from exc
    return report_path


def _is_expired(
    report_path: Path,
    config: ReportServingConfig,
    *,
    now: float | None = None,
) -> bool:
    now = time.time() if now is None else now
    return now - report_path.stat().st_mtime > config.ttl_seconds


def _expires_at_iso(report_path: Path, config: ReportServingConfig) -> str:
    expires_at = report_path.stat().st_mtime + config.ttl_seconds
    return datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()


def _unlink_report(report_path: Path) -> None:
    try:
        report_path.unlink()
    except FileNotFoundError:
        return
