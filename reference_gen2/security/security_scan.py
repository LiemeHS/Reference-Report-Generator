from __future__ import annotations

import logging
import subprocess

from reference_gen2.api.settings import (
    LOG_ENABLED,
    LOG_LEVEL,
    SECURITY_SCAN_ARGS,
    SECURITY_SCAN_ENABLED,
    SECURITY_SCAN_EXECUTABLE,
    SECURITY_SCAN_TIMEOUT_SEC,
)
from reference_gen2.security.file_validation import UploadValidationError, ValidatedUpload

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


def _log_scan_event(level: int, event: str, **fields: object) -> None:
    if not LOG_ENABLED:
        return
    payload = " ".join(f"{key}={value!r}" for key, value in fields.items())
    logger.log(level, "event=%s %s", event, payload)


def run_upload_security_scan(validated: ValidatedUpload, content: bytes) -> None:
    """Optional pre-extraction security scan hook.

    Phase 1b keeps this integration abstract on purpose. When disabled it is a
    no-op. When enabled with a configured command, the command receives the
    upload bytes on stdin and should exit with status 0 for safe input.
    """

    if not SECURITY_SCAN_ENABLED:
        return

    if not SECURITY_SCAN_EXECUTABLE:
        _log_scan_event(
            logging.WARNING,
            "phase1.security_scan_unconfigured",
            kind=validated.detected_kind,
            size_bytes=validated.size_bytes,
        )
        raise UploadValidationError(
            "security_scan_unconfigured",
            "Upload security scanning is enabled but no scanner executable is configured.",
            http_status=503,
        )

    command = [SECURITY_SCAN_EXECUTABLE, *SECURITY_SCAN_ARGS]

    try:
        result = subprocess.run(
            command,
            input=content,
            capture_output=True,
            timeout=SECURITY_SCAN_TIMEOUT_SEC,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        _log_scan_event(
            logging.WARNING,
            "phase1.security_scan_timeout",
            kind=validated.detected_kind,
            size_bytes=validated.size_bytes,
        )
        raise UploadValidationError(
            "security_scan_timeout",
            "Upload security scan timed out.",
            http_status=503,
        ) from exc
    except OSError as exc:
        _log_scan_event(
            logging.WARNING,
            "phase1.security_scan_failed",
            kind=validated.detected_kind,
            size_bytes=validated.size_bytes,
        )
        raise UploadValidationError(
            "security_scan_failed",
            "Upload security scan could not be executed safely.",
            http_status=503,
        ) from exc

    if result.returncode != 0:
        _log_scan_event(
            logging.WARNING,
            "phase1.security_scan_rejected",
            kind=validated.detected_kind,
            size_bytes=validated.size_bytes,
            returncode=result.returncode,
        )
        raise UploadValidationError(
            "security_scan_rejected",
            "Upload was rejected by the configured security scan.",
        )
