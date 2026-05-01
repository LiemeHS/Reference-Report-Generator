from __future__ import annotations

import logging
import os
import pathlib
import shlex
import ipaddress
from urllib.parse import urlsplit


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise SystemExit(
            f"Invalid {name}={raw!r}. Expected a positive integer."
        ) from exc
    if value <= 0:
        raise SystemExit(f"Invalid {name}={raw!r}. Expected a positive integer.")
    return value


def _ratio(name: str, default: str) -> float:
    raw = os.getenv(name, default).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise SystemExit(f"Invalid {name}={raw!r}. Expected a ratio from 0 to 1.") from exc
    if value < 0 or value > 1:
        raise SystemExit(f"Invalid {name}={raw!r}. Expected a ratio from 0 to 1.")
    return value


def _choice(name: str, default: str, allowed: set[str]) -> str:
    raw = os.getenv(name, default).strip().lower()
    if raw not in allowed:
        joined = ", ".join(sorted(allowed))
        raise SystemExit(f"Invalid {name}={raw!r}. Expected one of: {joined}.")
    return raw


def _parse_upload_extensions(raw: str) -> set[str]:
    extensions = set()
    for part in raw.split(","):
        ext = part.strip().lower()
        if not ext:
            continue
        if not ext.startswith("."):
            ext = f".{ext}"
        extensions.add(ext)
    if not extensions:
        raise SystemExit(
            "REFERENCE_GEN2_UPLOAD_ALLOWED_EXTENSIONS must contain at least one extension."
        )
    unsupported = extensions - {".pdf", ".docx"}
    if unsupported:
        joined = ", ".join(sorted(unsupported))
        raise SystemExit(
            f"Unsupported REFERENCE_GEN2_UPLOAD_ALLOWED_EXTENSIONS value(s): {joined}. "
            "Phase 1 only supports .pdf and .docx."
        )
    return extensions


def _parse_csv_values(raw: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for part in raw.split(","):
        value = part.strip()
        if not value or value in seen:
            continue
        seen.add(value)
        values.append(value)
    return values


def _parse_header_names(raw: str) -> list[str]:
    values = _parse_csv_values(raw)
    for value in values:
        if any(char.isspace() or char in "\r\n:" for char in value):
            raise SystemExit(
                "REFERENCE_GEN2_TRUSTED_AUTH_IDENTITY_HEADERS contains an invalid header name."
            )
    return values


def _parse_host_values(raw: str) -> list[str]:
    values = _parse_csv_values(raw)
    normalized: list[str] = []
    for value in values:
        lowered = value.rstrip(".").lower()
        if (
            not lowered
            or "://" in lowered
            or "/" in lowered
            or any(char.isspace() or char in "\r\n" for char in lowered)
        ):
            raise SystemExit(
                "REFERENCE_GEN2_API_ALLOWED_HOSTS contains an invalid host value."
            )
        normalized.append(lowered)
    return normalized


def _parse_origin_values(raw: str) -> list[str]:
    values = _parse_csv_values(raw)
    normalized: list[str] = []
    for value in values:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise SystemExit(
                "REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS must contain absolute "
                "http(s) origins without paths."
            )
        normalized.append(f"{parsed.scheme.lower()}://{parsed.netloc.rstrip('.').lower()}")
    return normalized


def _parse_trusted_proxy_values(raw: str, *, production_mode: bool) -> list[str]:
    values = _parse_csv_values(raw)
    normalized: list[str] = []
    for value in values:
        if value == "*":
            if production_mode:
                raise SystemExit(
                    "REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS='*' is not allowed "
                    "when REFERENCE_GEN2_PRODUCTION=1."
                )
            normalized.append(value)
            continue
        try:
            normalized.append(str(ipaddress.ip_network(value, strict=False)))
        except ValueError as exc:
            raise SystemExit(
                "REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS must contain CIDR ranges "
                "or IP addresses."
            ) from exc
    return normalized


def _contains_broad_private_proxy_range(values: list[str]) -> str | None:
    broad_ranges = {
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "0.0.0.0/0",
        "::/0",
    }
    for value in values:
        if value in broad_ranges:
            return value
    return None


def _parse_command_args(raw: str) -> list[str]:
    if not raw.strip():
        return []
    return shlex.split(raw, posix=(os.name != "nt"))


def _log_level(name: str, default: str) -> int:
    raw = os.getenv(name, default).strip().upper()
    value = getattr(logging, raw, None)
    if not isinstance(value, int):
        raise SystemExit(
            f"Invalid {name}={raw!r}. Expected a standard logging level name."
        )
    return value


UPLOAD_TMP_DIR = pathlib.Path(
    os.getenv("REFERENCE_GEN2_UPLOAD_TMP_DIR", "/tmp/reference_gen2_uploads").strip()
    or "/tmp/reference_gen2_uploads"
)
UPLOAD_MAX_BYTES = _positive_int("REFERENCE_GEN2_UPLOAD_MAX_BYTES", "26214400")
UPLOAD_ALLOWED_EXTENSIONS = _parse_upload_extensions(
    os.getenv("REFERENCE_GEN2_UPLOAD_ALLOWED_EXTENSIONS", ".pdf,.docx")
)

PDF_MAX_PAGES = _positive_int("REFERENCE_GEN2_PDF_MAX_PAGES", "150")
PDF_MAX_OBJECTS = _positive_int("REFERENCE_GEN2_PDF_MAX_OBJECTS", "10000")
PDF_MAX_STREAMS = _positive_int("REFERENCE_GEN2_PDF_MAX_STREAMS", "5000")
PDF_MAX_OBJECTS_PER_MB = _positive_int("REFERENCE_GEN2_PDF_MAX_OBJECTS_PER_MB", "2000")

EXTRACT_MAX_CHARS = _positive_int("REFERENCE_GEN2_EXTRACT_MAX_CHARS", "200000")
EXTRACT_TIMEOUT_SEC = _positive_int("REFERENCE_GEN2_EXTRACT_TIMEOUT_SEC", "20")

BIB_MIN_CHARS = _positive_int("REFERENCE_GEN2_BIB_MIN_CHARS", "300")
BIB_MAX_CHARS = _positive_int("REFERENCE_GEN2_BIB_MAX_CHARS", "120000")
BIB_MIN_UNITS = _positive_int("REFERENCE_GEN2_BIB_MIN_UNITS", "1")
BIB_REQUIRE_HEADING = _env_flag("REFERENCE_GEN2_BIB_REQUIRE_HEADING", True)
BIB_PDF_HEADING_SCAN_LINES = _positive_int(
    "REFERENCE_GEN2_BIB_PDF_HEADING_SCAN_LINES", "6"
)
BIB_PDF_HEADING_MIN_LINE_CHARS = _positive_int(
    "REFERENCE_GEN2_BIB_PDF_HEADING_MIN_LINE_CHARS", "3"
)

REPORT_SERVING_TMP_DIR = pathlib.Path(
    os.getenv("REFERENCE_GEN2_REPORT_SERVING_TMP_DIR", "/tmp/reference_gen2_reports").strip()
    or "/tmp/reference_gen2_reports"
)
REPORT_SERVING_JOB_DIR = pathlib.Path(
    os.getenv("REFERENCE_GEN2_REPORT_SERVING_JOB_DIR", "/tmp/reference_gen2_jobs").strip()
    or "/tmp/reference_gen2_jobs"
)
REPORT_SERVING_TTL_SECONDS = _positive_int("REFERENCE_GEN2_REPORT_SERVING_TTL_SECONDS", "3600")
REPORT_SERVING_JOB_TTL_SECONDS = _positive_int(
    "REFERENCE_GEN2_REPORT_SERVING_JOB_TTL_SECONDS",
    str(REPORT_SERVING_TTL_SECONDS),
)
REPORT_SERVING_CLEANUP_INTERVAL_SECONDS = _positive_int(
    "REFERENCE_GEN2_REPORT_SERVING_CLEANUP_INTERVAL_SECONDS",
    "300",
)
API_PRODUCTION_MODE = _env_flag("REFERENCE_GEN2_PRODUCTION", False)
DEFAULT_REPORT_SERVING_OWNERSHIP_SECRET = "reference_gen2_dev_ownership_secret_change_me"
REPORT_SERVING_OWNERSHIP_SECRET = (
    os.getenv(
        "REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET",
        DEFAULT_REPORT_SERVING_OWNERSHIP_SECRET,
    ).strip()
    or DEFAULT_REPORT_SERVING_OWNERSHIP_SECRET
)
if len(REPORT_SERVING_OWNERSHIP_SECRET) < 16:
    raise SystemExit(
        "REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET must be at least 16 characters."
    )
if API_PRODUCTION_MODE and REPORT_SERVING_OWNERSHIP_SECRET == DEFAULT_REPORT_SERVING_OWNERSHIP_SECRET:
    raise SystemExit(
        "REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET must be set to a "
        "non-default value when REFERENCE_GEN2_PRODUCTION=1."
    )
DEFAULT_API_RATE_LIMIT_SECRET = "reference_gen2_dev_rate_limit_secret_change_me"
API_RATE_LIMIT_SECRET = (
    os.getenv(
        "REFERENCE_GEN2_API_RATE_LIMIT_SECRET",
        DEFAULT_API_RATE_LIMIT_SECRET,
    ).strip()
    or DEFAULT_API_RATE_LIMIT_SECRET
)
if len(API_RATE_LIMIT_SECRET) < 16:
    raise SystemExit("REFERENCE_GEN2_API_RATE_LIMIT_SECRET must be at least 16 characters.")
if API_PRODUCTION_MODE and API_RATE_LIMIT_SECRET == DEFAULT_API_RATE_LIMIT_SECRET:
    raise SystemExit(
        "REFERENCE_GEN2_API_RATE_LIMIT_SECRET must be set to a non-default value "
        "when REFERENCE_GEN2_PRODUCTION=1."
    )
if API_PRODUCTION_MODE and API_RATE_LIMIT_SECRET == REPORT_SERVING_OWNERSHIP_SECRET:
    raise SystemExit(
        "REFERENCE_GEN2_API_RATE_LIMIT_SECRET must be separate from "
        "REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET when production mode is enabled."
    )
API_SECURITY_STATE_DB_PATH = pathlib.Path(
    os.getenv(
        "REFERENCE_GEN2_API_SECURITY_STATE_DB_PATH",
        "/tmp/reference_gen2_phase7_security_state.sqlite3",
    ).strip()
    or "/tmp/reference_gen2_phase7_security_state.sqlite3"
)
API_ACTIVE_JOB_LEASE_SECONDS = _positive_int(
    "REFERENCE_GEN2_API_ACTIVE_JOB_LEASE_SECONDS",
    "900",
)
LOCAL_DB_PATH = os.getenv("REFERENCE_GEN2_LOCAL_DB_PATH", "").strip() or None
API_MAX_REQUEST_BYTES = _positive_int(
    "REFERENCE_GEN2_API_MAX_REQUEST_BYTES",
    str(UPLOAD_MAX_BYTES),
)
API_EXECUTION_BACKEND = os.getenv(
    "REFERENCE_GEN2_API_EXECUTION_BACKEND",
    "sync",
).strip().lower()
if API_EXECUTION_BACKEND not in {"sync", "worker"}:
    raise SystemExit(
        "REFERENCE_GEN2_API_EXECUTION_BACKEND must be one of: sync, worker."
    )
TEXT_INPUT_MAX_CHARS = _positive_int("REFERENCE_GEN2_TEXT_INPUT_MAX_CHARS", str(BIB_MAX_CHARS))
PHASE7_WORKER_POLL_SECONDS = _positive_int("REFERENCE_GEN2_PHASE7_WORKER_POLL_SECONDS", "2")
PHASE7_WORKER_CONCURRENCY = _positive_int("REFERENCE_GEN2_PHASE7_WORKER_CONCURRENCY", "1")
PHASE7_WORKER_DB_WARMUP_ENABLED = _env_flag(
    "REFERENCE_GEN2_PHASE7_DB_WARMUP_ENABLED",
    False,
)
PHASE7_WORKER_DB_WARMUP_MAX_SECONDS = _positive_int(
    "REFERENCE_GEN2_PHASE7_DB_WARMUP_MAX_SECONDS",
    "3",
)
API_MAX_CONCURRENT_JOBS = _positive_int("REFERENCE_GEN2_API_MAX_CONCURRENT_JOBS", "2")
API_MAX_QUEUED_JOBS = _positive_int("REFERENCE_GEN2_API_MAX_QUEUED_JOBS", "20")
API_RATE_LIMIT_WINDOW_SECONDS = _positive_int(
    "REFERENCE_GEN2_API_RATE_LIMIT_WINDOW_SECONDS",
    "60",
)
API_RATE_LIMIT_MAX_REQUESTS = _positive_int(
    "REFERENCE_GEN2_API_RATE_LIMIT_MAX_REQUESTS",
    "20",
)
API_NETWORK_BURST_WINDOW_SECONDS = _positive_int(
    "REFERENCE_GEN2_API_NETWORK_BURST_WINDOW_SECONDS",
    "3600",
)
API_NETWORK_BURST_MAX_REQUESTS = _positive_int(
    "REFERENCE_GEN2_API_NETWORK_BURST_MAX_REQUESTS",
    "30",
)
API_NETWORK_SUSTAINED_WINDOW_SECONDS = _positive_int(
    "REFERENCE_GEN2_API_NETWORK_SUSTAINED_WINDOW_SECONDS",
    "28800",
)
API_NETWORK_SUSTAINED_MAX_REQUESTS = _positive_int(
    "REFERENCE_GEN2_API_NETWORK_SUSTAINED_MAX_REQUESTS",
    "120",
)
API_GLOBAL_RATE_LIMIT_WINDOW_SECONDS = _positive_int(
    "REFERENCE_GEN2_API_GLOBAL_RATE_LIMIT_WINDOW_SECONDS",
    "60",
)
API_GLOBAL_RATE_LIMIT_MAX_REQUESTS = _positive_int(
    "REFERENCE_GEN2_API_GLOBAL_RATE_LIMIT_MAX_REQUESTS",
    "300",
)
API_TRUSTED_PROXY_CIDRS = _parse_trusted_proxy_values(
    os.getenv("REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS", ""),
    production_mode=API_PRODUCTION_MODE,
)
API_CHALLENGE_MODE = _choice(
    "REFERENCE_GEN2_API_CHALLENGE_MODE",
    "auto",
    {"off", "auto", "always"},
)
API_CHALLENGE_MAX_NUMBER = _positive_int(
    "REFERENCE_GEN2_API_CHALLENGE_MAX_NUMBER",
    "12000",
)
API_CHALLENGE_TTL_SECONDS = _positive_int(
    "REFERENCE_GEN2_API_CHALLENGE_TTL_SECONDS",
    "300",
)
API_CHALLENGE_SOFT_LIMIT_RATIO = _ratio(
    "REFERENCE_GEN2_API_CHALLENGE_SOFT_LIMIT_RATIO",
    "0.75",
)
API_CHALLENGE_QUEUE_RATIO = _ratio(
    "REFERENCE_GEN2_API_CHALLENGE_QUEUE_RATIO",
    "0.70",
)
API_SESSION_COOKIE_SECURE = _env_flag("REFERENCE_GEN2_API_SESSION_COOKIE_SECURE", True)
API_SESSION_COOKIE_SAMESITE = os.getenv(
    "REFERENCE_GEN2_API_SESSION_COOKIE_SAMESITE",
    "lax",
).strip().lower()
if API_SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise SystemExit(
        "REFERENCE_GEN2_API_SESSION_COOKIE_SAMESITE must be one of: lax, strict, none."
    )
if API_SESSION_COOKIE_SAMESITE == "none" and not API_SESSION_COOKIE_SECURE:
    raise SystemExit(
        "REFERENCE_GEN2_API_SESSION_COOKIE_SAMESITE=none requires "
        "REFERENCE_GEN2_API_SESSION_COOKIE_SECURE=1."
    )
API_TRUST_AUTH_IDENTITY_HEADERS = _env_flag(
    "REFERENCE_GEN2_API_TRUST_AUTH_IDENTITY_HEADERS",
    False,
)
API_AUTH_IDENTITY_HEADER_NAMES = _parse_header_names(
    os.getenv(
        "REFERENCE_GEN2_API_AUTH_IDENTITY_HEADER_NAMES",
        "remote-user,remote-email",
    )
)
if API_MAX_REQUEST_BYTES > UPLOAD_MAX_BYTES:
    raise SystemExit(
        "REFERENCE_GEN2_API_MAX_REQUEST_BYTES must be less than or equal to "
        "REFERENCE_GEN2_UPLOAD_MAX_BYTES."
    )
if API_MAX_REQUEST_BYTES <= 0:
    raise SystemExit("REFERENCE_GEN2_API_MAX_REQUEST_BYTES must be positive.")
API_SUBMISSIONS_ENABLED = _env_flag("REFERENCE_GEN2_API_SUBMISSIONS_ENABLED", True)
API_ENABLE_SANITIZED_REPORT_ENDPOINT = _env_flag(
    "REFERENCE_GEN2_API_ENABLE_SANITIZED_REPORT_ENDPOINT",
    not API_PRODUCTION_MODE,
)
API_ALLOWED_HOSTS = _parse_host_values(
    os.getenv("REFERENCE_GEN2_API_ALLOWED_HOSTS", "")
)
API_POST_ALLOWED_ORIGINS = _parse_origin_values(
    os.getenv("REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS", "")
)
CORS_ALLOWED_ORIGINS = _parse_csv_values(
    os.getenv("REFERENCE_GEN2_CORS_ALLOWED_ORIGINS", "")
)

DOCX_REJECT_MACROS = _env_flag("REFERENCE_GEN2_DOCX_REJECT_MACROS", True)
DOCX_REJECT_EMBEDDED_OBJECTS = _env_flag(
    "REFERENCE_GEN2_DOCX_REJECT_EMBEDDED_OBJECTS", True
)
DOCX_REJECT_EXTERNAL_RELATIONSHIPS = _env_flag(
    "REFERENCE_GEN2_DOCX_REJECT_EXTERNAL_RELATIONSHIPS", True
)

SECURITY_SCAN_ENABLED = _env_flag("REFERENCE_GEN2_SECURITY_SCAN_ENABLED", False)
SECURITY_SCAN_EXECUTABLE = os.getenv(
    "REFERENCE_GEN2_SECURITY_SCAN_EXECUTABLE", ""
).strip()
SECURITY_SCAN_ARGS = _parse_command_args(
    os.getenv("REFERENCE_GEN2_SECURITY_SCAN_ARGS", "")
)
SECURITY_SCAN_TIMEOUT_SEC = _positive_int(
    "REFERENCE_GEN2_SECURITY_SCAN_TIMEOUT_SEC", "15"
)
SECURITY_SCAN_ACCEPT_RISK = _env_flag(
    "REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK",
    False,
)

if API_PRODUCTION_MODE:
    if not API_ALLOWED_HOSTS:
        raise SystemExit(
            "REFERENCE_GEN2_API_ALLOWED_HOSTS must be set when "
            "REFERENCE_GEN2_PRODUCTION=1."
        )
    if API_SUBMISSIONS_ENABLED and not API_POST_ALLOWED_ORIGINS:
        raise SystemExit(
            "REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS must be set when "
            "REFERENCE_GEN2_PRODUCTION=1 and submissions are enabled."
        )
    if not API_TRUSTED_PROXY_CIDRS:
        raise SystemExit(
            "REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS must be set to the explicit "
            "public reverse-proxy CIDR when REFERENCE_GEN2_PRODUCTION=1."
        )
    broad_proxy_range = _contains_broad_private_proxy_range(API_TRUSTED_PROXY_CIDRS)
    if broad_proxy_range is not None:
        raise SystemExit(
            "REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS must not use broad private "
            f"or default ranges in production: {broad_proxy_range}."
        )
    if API_SUBMISSIONS_ENABLED:
        if SECURITY_SCAN_ENABLED and not SECURITY_SCAN_EXECUTABLE:
            raise SystemExit(
                "REFERENCE_GEN2_SECURITY_SCAN_EXECUTABLE must be set when "
                "REFERENCE_GEN2_SECURITY_SCAN_ENABLED=1."
            )
        if not SECURITY_SCAN_ENABLED and not SECURITY_SCAN_ACCEPT_RISK:
            raise SystemExit(
                "Public production submissions require "
                "REFERENCE_GEN2_SECURITY_SCAN_ENABLED=1 or explicit "
                "REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK=1."
            )

ANYSTYLE_ENABLED = _env_flag("REFERENCE_GEN2_ANYSTYLE_ENABLED", True)
ANYSTYLE_EXECUTABLE = os.getenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", "anystyle").strip()
ANYSTYLE_PARSE_ARGS = _parse_command_args(
    os.getenv("REFERENCE_GEN2_ANYSTYLE_PARSE_ARGS", "")
)
ANYSTYLE_TIMEOUT_SEC = _positive_int("REFERENCE_GEN2_ANYSTYLE_TIMEOUT_SEC", "15")

LOG_ENABLED = _env_flag("REFERENCE_GEN2_LOG_ENABLED", True)
LOG_LEVEL = _log_level("REFERENCE_GEN2_LOG_LEVEL", "INFO")
LOG_PIPELINE_EVENTS = _env_flag("REFERENCE_GEN2_LOG_PIPELINE_EVENTS", True)
