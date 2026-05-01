"""FastAPI adapter for Phase 7 hosted report serving."""

from __future__ import annotations

import base64
import binascii
from contextlib import asynccontextmanager
import hashlib
import hmac
import ipaddress
import json
from importlib import resources
import logging
from pathlib import Path
import secrets
import threading
import time
from typing import Any
from urllib.parse import urlsplit

try:  # pragma: no cover - exercised when FastAPI is installed
    from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.exceptions import RequestValidationError
    from starlette.middleware.cors import CORSMiddleware
    from starlette.staticfiles import StaticFiles
except ImportError as exc:  # pragma: no cover - import guard for core-only installs
    raise RuntimeError(
        "FastAPI is required for reference_gen2.api.phase7_app. "
        "Install project web dependencies before importing this adapter."
    ) from exc

from reference_gen2.api.settings import (
    API_ACTIVE_JOB_LEASE_SECONDS,
    API_CHALLENGE_MAX_NUMBER,
    API_CHALLENGE_MODE,
    API_CHALLENGE_QUEUE_RATIO,
    API_CHALLENGE_SOFT_LIMIT_RATIO,
    API_CHALLENGE_TTL_SECONDS,
    API_EXECUTION_BACKEND,
    API_GLOBAL_RATE_LIMIT_MAX_REQUESTS,
    API_GLOBAL_RATE_LIMIT_WINDOW_SECONDS,
    API_MAX_CONCURRENT_JOBS,
    API_MAX_QUEUED_JOBS,
    API_MAX_REQUEST_BYTES,
    API_NETWORK_BURST_MAX_REQUESTS,
    API_NETWORK_BURST_WINDOW_SECONDS,
    API_NETWORK_SUSTAINED_MAX_REQUESTS,
    API_NETWORK_SUSTAINED_WINDOW_SECONDS,
    API_RATE_LIMIT_MAX_REQUESTS,
    API_RATE_LIMIT_SECRET,
    API_RATE_LIMIT_WINDOW_SECONDS,
    API_SECURITY_STATE_DB_PATH,
    API_SESSION_COOKIE_SAMESITE,
    API_SESSION_COOKIE_SECURE,
    API_AUTH_IDENTITY_HEADER_NAMES,
    API_ALLOWED_HOSTS,
    API_ENABLE_SANITIZED_REPORT_ENDPOINT,
    API_POST_ALLOWED_ORIGINS,
    API_SUBMISSIONS_ENABLED,
    API_TRUSTED_PROXY_CIDRS,
    API_TRUST_AUTH_IDENTITY_HEADERS,
    CORS_ALLOWED_ORIGINS,
    LOCAL_DB_PATH,
    REPORT_SERVING_JOB_DIR,
    REPORT_SERVING_TMP_DIR,
    REPORT_SERVING_CLEANUP_INTERVAL_SECONDS,
    REPORT_SERVING_JOB_TTL_SECONDS,
    REPORT_SERVING_OWNERSHIP_SECRET,
    REPORT_SERVING_TTL_SECONDS,
    TEXT_INPUT_MAX_CHARS,
)
from reference_gen2.report_serving import (
    InvalidJobIdError,
    InvalidReportIdError,
    InvalidSessionIdError,
    JobNotFoundError,
    JobOwnershipError,
    Phase7SecurityStateConfig,
    Phase7SubmissionError,
    RateLimitSubject,
    ReportNotFoundError,
    ReportServingConfig,
    SqlitePhase7SecurityState,
    SanitizedReportJobSubmission,
    TextJobSubmission,
    UploadJobSubmission,
    assert_report_owned,
    cleanup_expired_jobs,
    cleanup_legacy_job_state,
    cleanup_expired_reports,
    create_session_id,
    create_phase7_execution_backend,
    get_job,
    get_job_by_report_id,
    serve_report_html,
    validate_report_id,
    validate_session_id,
)


logger = logging.getLogger(__name__)

FRONTEND_SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "connect-src 'self'; "
        "img-src 'self' data:; "
        "style-src 'self'; "
        "script-src 'self'; "
        "form-action 'self'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'"
    ),
    "Referrer-Policy": "no-referrer",
}

API_SECURITY_HEADERS: dict[str, str] = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def create_app(
    config: ReportServingConfig | None = None,
    *,
    db_path: str | None = None,
    security_state_db_path: Path | None = None,
    execution_backend_mode: str | None = None,
    max_request_bytes: int | None = None,
    max_concurrent_jobs: int | None = None,
    max_queued_jobs: int | None = None,
    rate_limit_window_seconds: int | None = None,
    rate_limit_max_requests: int | None = None,
    rate_limit_secret: str | None = None,
    trusted_proxy_cidrs: list[str] | None = None,
    network_burst_window_seconds: int | None = None,
    network_burst_max_requests: int | None = None,
    network_sustained_window_seconds: int | None = None,
    network_sustained_max_requests: int | None = None,
    global_rate_limit_window_seconds: int | None = None,
    global_rate_limit_max_requests: int | None = None,
    challenge_mode: str | None = None,
    challenge_max_number: int | None = None,
    challenge_ttl_seconds: int | None = None,
    challenge_soft_limit_ratio: float | None = None,
    challenge_queue_ratio: float | None = None,
    cors_allowed_origins: list[str] | None = None,
    text_input_max_chars: int | None = None,
    session_cookie_secure: bool | None = None,
    session_cookie_samesite: str | None = None,
    trust_auth_identity_headers: bool | None = None,
    auth_identity_header_names: list[str] | None = None,
    submissions_enabled: bool | None = None,
    enable_sanitized_report_endpoint: bool | None = None,
    allowed_hosts: list[str] | None = None,
    post_allowed_origins: list[str] | None = None,
) -> FastAPI:
    """Create the Phase 7 FastAPI adapter.

    The adapter keeps hosted lifecycle concerns in Phase 7 and delegates
    document processing to the Phase 1-6 orchestration service.
    """
    serving_config = config or ReportServingConfig(
        report_dir=Path(REPORT_SERVING_TMP_DIR),
        job_dir=Path(REPORT_SERVING_JOB_DIR),
        ttl_seconds=REPORT_SERVING_TTL_SECONDS,
        job_ttl_seconds=REPORT_SERVING_JOB_TTL_SECONDS,
        cleanup_interval_seconds=REPORT_SERVING_CLEANUP_INTERVAL_SECONDS,
        ownership_secret=REPORT_SERVING_OWNERSHIP_SECRET,
    )
    pipeline_db_path = LOCAL_DB_PATH if db_path is None else db_path
    request_limit = API_MAX_REQUEST_BYTES if max_request_bytes is None else max_request_bytes
    concurrency_limit = (
        API_MAX_CONCURRENT_JOBS if max_concurrent_jobs is None else max_concurrent_jobs
    )
    queue_limit = API_MAX_QUEUED_JOBS if max_queued_jobs is None else max_queued_jobs
    rate_limit_window = (
        API_RATE_LIMIT_WINDOW_SECONDS
        if rate_limit_window_seconds is None
        else rate_limit_window_seconds
    )
    rate_limit_max = (
        API_RATE_LIMIT_MAX_REQUESTS
        if rate_limit_max_requests is None
        else rate_limit_max_requests
    )
    quota_secret = API_RATE_LIMIT_SECRET if rate_limit_secret is None else rate_limit_secret
    proxy_cidrs = API_TRUSTED_PROXY_CIDRS if trusted_proxy_cidrs is None else trusted_proxy_cidrs
    burst_window = (
        API_NETWORK_BURST_WINDOW_SECONDS
        if network_burst_window_seconds is None
        else network_burst_window_seconds
    )
    burst_max = (
        API_NETWORK_BURST_MAX_REQUESTS
        if network_burst_max_requests is None
        else network_burst_max_requests
    )
    sustained_window = (
        API_NETWORK_SUSTAINED_WINDOW_SECONDS
        if network_sustained_window_seconds is None
        else network_sustained_window_seconds
    )
    sustained_max = (
        API_NETWORK_SUSTAINED_MAX_REQUESTS
        if network_sustained_max_requests is None
        else network_sustained_max_requests
    )
    global_window = (
        API_GLOBAL_RATE_LIMIT_WINDOW_SECONDS
        if global_rate_limit_window_seconds is None
        else global_rate_limit_window_seconds
    )
    global_max = (
        API_GLOBAL_RATE_LIMIT_MAX_REQUESTS
        if global_rate_limit_max_requests is None
        else global_rate_limit_max_requests
    )
    challenge_policy = API_CHALLENGE_MODE if challenge_mode is None else challenge_mode
    challenge_number_limit = (
        API_CHALLENGE_MAX_NUMBER
        if challenge_max_number is None
        else challenge_max_number
    )
    challenge_ttl = (
        API_CHALLENGE_TTL_SECONDS
        if challenge_ttl_seconds is None
        else challenge_ttl_seconds
    )
    challenge_soft_ratio = (
        API_CHALLENGE_SOFT_LIMIT_RATIO
        if challenge_soft_limit_ratio is None
        else challenge_soft_limit_ratio
    )
    challenge_load_ratio = (
        API_CHALLENGE_QUEUE_RATIO if challenge_queue_ratio is None else challenge_queue_ratio
    )
    allowed_origins = CORS_ALLOWED_ORIGINS if cors_allowed_origins is None else cors_allowed_origins
    text_limit = TEXT_INPUT_MAX_CHARS if text_input_max_chars is None else text_input_max_chars
    backend_mode = (
        API_EXECUTION_BACKEND if execution_backend_mode is None else execution_backend_mode
    )
    cookie_secure = (
        API_SESSION_COOKIE_SECURE if session_cookie_secure is None else session_cookie_secure
    )
    cookie_samesite = (
        API_SESSION_COOKIE_SAMESITE if session_cookie_samesite is None else session_cookie_samesite
    )
    trust_identity_headers = (
        API_TRUST_AUTH_IDENTITY_HEADERS
        if trust_auth_identity_headers is None
        else trust_auth_identity_headers
    )
    identity_header_names = (
        API_AUTH_IDENTITY_HEADER_NAMES
        if auth_identity_header_names is None
        else auth_identity_header_names
    )
    accept_submissions = (
        API_SUBMISSIONS_ENABLED if submissions_enabled is None else submissions_enabled
    )
    sanitized_report_endpoint_enabled = (
        API_ENABLE_SANITIZED_REPORT_ENDPOINT
        if enable_sanitized_report_endpoint is None
        else enable_sanitized_report_endpoint
    )
    host_allowlist = API_ALLOWED_HOSTS if allowed_hosts is None else allowed_hosts
    origin_allowlist = (
        API_POST_ALLOWED_ORIGINS if post_allowed_origins is None else post_allowed_origins
    )
    security_state = SqlitePhase7SecurityState(
        Phase7SecurityStateConfig(
            db_path=(
                API_SECURITY_STATE_DB_PATH
                if security_state_db_path is None
                else security_state_db_path
            ),
            rate_limit_window_seconds=rate_limit_window,
            rate_limit_max_requests=rate_limit_max,
            max_active_jobs=concurrency_limit,
            active_job_lease_seconds=API_ACTIVE_JOB_LEASE_SECONDS,
            max_queued_jobs=queue_limit,
            rate_limit_retention_seconds=max(
                rate_limit_window,
                burst_window,
                sustained_window,
                global_window,
            )
            * 2,
        )
    )
    execution_backend = create_phase7_execution_backend(
        mode=backend_mode,
        serving_config=serving_config,
        security_state=security_state,
        db_path=pipeline_db_path,
        text_input_max_chars=text_limit,
        max_queued_jobs=queue_limit,
    )
    cleanup_scheduler = _CleanupScheduler(serving_config)
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        cleanup_expired_reports(config=serving_config)
        cleanup_expired_jobs(config=serving_config)
        cleanup_legacy_job_state(config=serving_config)
        yield

    app = FastAPI(title="Reference_Gen2 Phase 7", lifespan=lifespan)
    app.state.phase7_execution_backend = execution_backend
    app.mount(
        "/static",
        StaticFiles(packages=[("reference_gen2.api", "static")]),
        name="static",
    )
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Content-Type"],
            allow_credentials=False,
        )

    @app.middleware("http")
    async def enforce_request_size(request: Request, call_next: Any):
        cleanup_scheduler.maybe_run()
        host_error = _validate_host(request, allowed_hosts=host_allowlist)
        if host_error is not None:
            status_code, code, message = host_error
            return _error_response(
                status_code=status_code,
                code=code,
                message=message,
                phase="phase7",
            )
        if request.method == "POST":
            if not accept_submissions and _is_submission_path(
                request.url.path,
                sanitized_report_endpoint_enabled=sanitized_report_endpoint_enabled,
            ):
                return _error_response(
                    status_code=503,
                    code="submissions_disabled",
                    message="New submissions are temporarily disabled.",
                    phase="phase7",
                )
            origin_error = _validate_post_origin(
                request,
                post_allowed_origins=origin_allowlist,
                trusted_proxy_cidrs=proxy_cidrs,
            )
            if origin_error is not None:
                status_code, code, message = origin_error
                return _error_response(
                    status_code=status_code,
                    code=code,
                    message=message,
                    phase="phase7",
                )
            if (
                _is_submission_path(
                    request.url.path,
                    sanitized_report_endpoint_enabled=sanitized_report_endpoint_enabled,
                )
                and not execution_backend.has_submission_capacity()
            ):
                return _error_response(
                    status_code=429,
                    code="too_many_queued_jobs",
                    message="Too many jobs are queued right now.",
                    phase="phase7",
                )
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                return _error_response(
                    status_code=400,
                    code="invalid_content_length",
                    message="Invalid request size header.",
                    phase="phase7",
                )
            if length > request_limit:
                return _error_response(
                    status_code=413,
                    code="request_too_large",
                    message="Request body is too large.",
                    phase="phase7",
                )
        response = await call_next(request)
        _apply_security_headers(response, API_SECURITY_HEADERS)
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(_request: Request, exc: HTTPException) -> JSONResponse:
        if isinstance(exc.detail, dict):
            detail = exc.detail
            code = str(detail.get("code", "http_error"))
            message = str(detail.get("message", "Request failed."))
            phase = str(detail.get("phase", "phase7"))
        else:
            code = _http_status_code_name(exc.status_code)
            message = _safe_http_message(exc.status_code)
            phase = "phase7"
        return _error_response(
            status_code=_safe_http_status(exc.status_code),
            code=code,
            message=message,
            phase=phase,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request,
        _exc: RequestValidationError,
    ) -> JSONResponse:
        return _error_response(
            status_code=400,
            code="invalid_request",
            message="Invalid request.",
            phase="phase7",
        )

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(_request: Request, _exc: Exception) -> JSONResponse:
        return _error_response(
            status_code=500,
            code="internal_server_error",
            message="Internal server error.",
            phase="phase7",
        )

    @app.get("/")
    def frontend() -> HTMLResponse:
        return HTMLResponse(
            content=_frontend_html(),
            status_code=200,
            headers=dict(FRONTEND_SECURITY_HEADERS),
            media_type="text/html",
        )

    @app.get("/challenge")
    def create_challenge() -> JSONResponse:
        return JSONResponse(
            _create_altcha_challenge(
                secret=quota_secret,
                max_number=challenge_number_limit,
                ttl_seconds=challenge_ttl,
            )
        )

    @app.post("/reports/upload")
    async def upload_report(
        request: Request,
        file: UploadFile = File(...),
        style_hint: str = Form("unknown"),
        altcha: str | None = Form(None),
    ) -> JSONResponse:
        session_id, created_session = _get_or_create_session_id(request, config=serving_config)
        owner_session_id = _owner_session_id(
            request,
            session_id,
            config=serving_config,
            trust_auth_identity_headers=trust_identity_headers,
            auth_identity_header_names=identity_header_names,
        )
        _enforce_rate_limit(
            security_state,
            request,
            owner_session_id,
            secret=quota_secret,
            trusted_proxy_cidrs=proxy_cidrs,
            session_window_seconds=rate_limit_window,
            session_max_requests=rate_limit_max,
            network_burst_window_seconds=burst_window,
            network_burst_max_requests=burst_max,
            network_sustained_window_seconds=sustained_window,
            network_sustained_max_requests=sustained_max,
            global_window_seconds=global_window,
            global_max_requests=global_max,
            altcha_payload=altcha,
            challenge_mode=challenge_policy,
            challenge_max_number=challenge_number_limit,
            challenge_soft_limit_ratio=challenge_soft_ratio,
            challenge_queue_ratio=challenge_load_ratio,
            execution_backend=execution_backend,
        )
        content = await _read_upload_limited(file, max_bytes=request_limit)
        try:
            job = await execution_backend.submit_upload(
                owner_session_id=owner_session_id,
                submission=UploadJobSubmission(
                    filename=file.filename or "upload",
                    declared_mime=file.content_type,
                    content=content,
                    style_hint=style_hint,
                ),
            )
        except Phase7SubmissionError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "phase": exc.phase,
                },
            ) from exc
        response = JSONResponse(_job_payload(job))
        _maybe_set_session_cookie(
            response,
            session_id,
            created_session,
            config=serving_config,
            cookie_secure=cookie_secure,
            cookie_samesite=cookie_samesite,
        )
        _apply_security_headers(response, API_SECURITY_HEADERS)
        return response

    @app.post("/reports/text")
    async def create_report_from_text(request: Request) -> JSONResponse:
        session_id, created_session = _get_or_create_session_id(request, config=serving_config)
        owner_session_id = _owner_session_id(
            request,
            session_id,
            config=serving_config,
            trust_auth_identity_headers=trust_identity_headers,
            auth_identity_header_names=identity_header_names,
        )
        payload = await _read_json_limited(
            request,
            max_bytes=request_limit,
            invalid_code="invalid_text_report_payload",
            invalid_message="Invalid text report payload.",
        )
        if not isinstance(payload, dict):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_text_report_payload",
                    "message": "Invalid text report payload.",
                    "phase": "phase7",
                },
            )
        altcha = payload.get("altcha")
        if altcha is not None and not isinstance(altcha, str):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_challenge",
                    "message": "Verification failed.",
                    "phase": "phase7",
                },
            )
        _enforce_rate_limit(
            security_state,
            request,
            owner_session_id,
            secret=quota_secret,
            trusted_proxy_cidrs=proxy_cidrs,
            session_window_seconds=rate_limit_window,
            session_max_requests=rate_limit_max,
            network_burst_window_seconds=burst_window,
            network_burst_max_requests=burst_max,
            network_sustained_window_seconds=sustained_window,
            network_sustained_max_requests=sustained_max,
            global_window_seconds=global_window,
            global_max_requests=global_max,
            altcha_payload=altcha,
            challenge_mode=challenge_policy,
            challenge_max_number=challenge_number_limit,
            challenge_soft_limit_ratio=challenge_soft_ratio,
            challenge_queue_ratio=challenge_load_ratio,
            execution_backend=execution_backend,
        )
        reference_list_text = payload.get("reference_list_text")
        if not isinstance(reference_list_text, str):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_reference_text",
                    "message": "Invalid reference text.",
                    "phase": "phase2",
                },
            )
        style_hint = payload.get("style_hint", "unknown")
        if style_hint is not None and not isinstance(style_hint, str):
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_style_hint",
                    "message": "Invalid style hint.",
                    "phase": "request",
                },
            )
        try:
            job = await execution_backend.submit_text(
                owner_session_id=owner_session_id,
                submission=TextJobSubmission(
                    reference_list_text=reference_list_text,
                    style_hint=style_hint,
                ),
            )
        except Phase7SubmissionError as exc:
            raise HTTPException(
                status_code=exc.http_status,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "phase": exc.phase,
                },
            ) from exc
        response = JSONResponse(_job_payload(job))
        _maybe_set_session_cookie(
            response,
            session_id,
            created_session,
            config=serving_config,
            cookie_secure=cookie_secure,
            cookie_samesite=cookie_samesite,
        )
        _apply_security_headers(response, API_SECURITY_HEADERS)
        return response

    if sanitized_report_endpoint_enabled:

        @app.post("/reports")
        async def create_report(request: Request) -> JSONResponse:
            session_id, created_session = _get_or_create_session_id(
                request,
                config=serving_config,
            )
            owner_session_id = _owner_session_id(
                request,
                session_id,
                config=serving_config,
                trust_auth_identity_headers=trust_identity_headers,
                auth_identity_header_names=identity_header_names,
            )
            payload = await _read_json_limited(
                request,
                max_bytes=request_limit,
                invalid_code="invalid_report_payload",
                invalid_message="Invalid report payload.",
            )
            altcha = payload.get("altcha") if isinstance(payload, dict) else None
            if altcha is not None and not isinstance(altcha, str):
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "invalid_challenge",
                        "message": "Verification failed.",
                        "phase": "phase7",
                    },
                )
            if isinstance(payload, dict) and "altcha" in payload:
                payload = dict(payload)
                payload.pop("altcha", None)
            _enforce_rate_limit(
                security_state,
                request,
                owner_session_id,
                secret=quota_secret,
                trusted_proxy_cidrs=proxy_cidrs,
                session_window_seconds=rate_limit_window,
                session_max_requests=rate_limit_max,
                network_burst_window_seconds=burst_window,
                network_burst_max_requests=burst_max,
                network_sustained_window_seconds=sustained_window,
                network_sustained_max_requests=sustained_max,
                global_window_seconds=global_window,
                global_max_requests=global_max,
                altcha_payload=altcha,
                challenge_mode=challenge_policy,
                challenge_max_number=challenge_number_limit,
                challenge_soft_limit_ratio=challenge_soft_ratio,
                challenge_queue_ratio=challenge_load_ratio,
                execution_backend=execution_backend,
            )
            try:
                job = await execution_backend.submit_sanitized_report(
                    owner_session_id=owner_session_id,
                    submission=SanitizedReportJobSubmission(payload=payload),
                )
            except Phase7SubmissionError as exc:
                raise HTTPException(
                    status_code=exc.http_status,
                    detail={
                        "code": exc.code,
                        "message": exc.message,
                        "phase": exc.phase,
                    },
                ) from exc
            response = JSONResponse(_job_payload(job))
            _maybe_set_session_cookie(
                response,
                session_id,
                created_session,
                config=serving_config,
                cookie_secure=cookie_secure,
                cookie_samesite=cookie_samesite,
            )
            _apply_security_headers(response, API_SECURITY_HEADERS)
            return response

    @app.get("/jobs/{job_id}")
    def get_job_status(job_id: str, request: Request) -> JSONResponse:
        try:
            session_id = _require_session_id(request, config=serving_config)
            owner_session_id = _owner_session_id(
                request,
                session_id,
                config=serving_config,
                trust_auth_identity_headers=trust_identity_headers,
                auth_identity_header_names=identity_header_names,
            )
            job = get_job(job_id, owner_session_id=owner_session_id, config=serving_config)
        except (InvalidJobIdError, InvalidSessionIdError) as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_job_or_session",
                    "message": "Invalid job or session.",
                    "phase": "phase7",
                },
            ) from exc
        except (JobNotFoundError, JobOwnershipError) as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "job_not_found_or_expired",
                    "message": "Job not found or expired.",
                    "phase": "phase7",
                },
            ) from exc
        return JSONResponse(_job_payload(job))

    @app.get("/reports/{report_id}/status")
    def get_report_status(report_id: str, request: Request) -> JSONResponse:
        try:
            validate_report_id(report_id, serving_config)
            session_id = _require_session_id(request, config=serving_config)
            owner_session_id = _owner_session_id(
                request,
                session_id,
                config=serving_config,
                trust_auth_identity_headers=trust_identity_headers,
                auth_identity_header_names=identity_header_names,
            )
            job = get_job_by_report_id(
                report_id,
                owner_session_id=owner_session_id,
                config=serving_config,
            )
        except InvalidReportIdError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_report_id",
                    "message": "Invalid report id.",
                    "phase": "phase7",
                },
            ) from exc
        except (JobNotFoundError, JobOwnershipError, InvalidSessionIdError) as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "report_not_found_or_expired",
                    "message": "Report not found or expired.",
                    "phase": "phase7",
                },
            ) from exc
        payload = _job_payload(job)
        payload["report_id"] = report_id
        return JSONResponse(payload)

    @app.get("/reports/{report_id}")
    def get_report(report_id: str, request: Request) -> HTMLResponse:
        try:
            validate_report_id(report_id, serving_config)
            session_id = _require_session_id(request, config=serving_config)
            owner_session_id = _owner_session_id(
                request,
                session_id,
                config=serving_config,
                trust_auth_identity_headers=trust_identity_headers,
                auth_identity_header_names=identity_header_names,
            )
            assert_report_owned(
                report_id,
                owner_session_id=owner_session_id,
                config=serving_config,
            )
            served = serve_report_html(report_id, config=serving_config, consume=False)
        except InvalidReportIdError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "invalid_report_id",
                    "message": "Invalid report id.",
                    "phase": "phase7",
                },
            ) from exc
        except (ReportNotFoundError, JobNotFoundError, JobOwnershipError, InvalidSessionIdError) as exc:
            raise HTTPException(
                status_code=410,
                detail={
                    "code": "report_not_found_or_expired",
                    "message": "Report not found or expired.",
                    "phase": "phase7",
                },
            ) from exc
        return HTMLResponse(
            content=served.html,
            status_code=200,
            headers=served.headers,
            media_type="text/html",
        )

    return app


class _CleanupScheduler:
    def __init__(self, config: ReportServingConfig):
        self._config = config
        self._lock = threading.Lock()
        self._last_run = 0.0

    def maybe_run(self, *, now: float | None = None) -> None:
        now = time.time() if now is None else now
        with self._lock:
            if now - self._last_run < self._config.cleanup_interval_seconds:
                return
            self._last_run = now
        cleanup_expired_reports(config=self._config, now=now)
        cleanup_expired_jobs(config=self._config, now=now)


def _frontend_html() -> str:
    return (
        resources.files("reference_gen2.api.static")
        .joinpath("index.html")
        .read_text(encoding="utf-8")
    )


async def _read_upload_limited(file: UploadFile, *, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            logger.warning(
                "event=phase7.upload_rejected code=request_too_large status_class=4xx"
            )
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "request_too_large",
                    "message": "Request body is too large.",
                    "phase": "phase7",
                },
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _read_json_limited(
    request: Request,
    *,
    max_bytes: int,
    invalid_code: str,
    invalid_message: str,
) -> Any:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            logger.warning(
                "event=phase7.json_rejected code=request_too_large status_class=4xx"
            )
            raise HTTPException(
                status_code=413,
                detail={
                    "code": "request_too_large",
                    "message": "Request body is too large.",
                    "phase": "phase7",
                },
            )
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=400,
            detail={
                "code": invalid_code,
                "message": invalid_message,
                "phase": "phase7",
            },
        ) from exc


def _is_submission_path(
    path: str,
    *,
    sanitized_report_endpoint_enabled: bool = True,
) -> bool:
    paths = {"/reports/upload", "/reports/text"}
    if sanitized_report_endpoint_enabled:
        paths.add("/reports")
    return path in paths


def _validate_host(
    request: Request,
    *,
    allowed_hosts: list[str],
) -> tuple[int, str, str] | None:
    if not allowed_hosts:
        return None
    host = _request_host(request)
    if host is None or host not in {_normalize_host(value) for value in allowed_hosts}:
        logger.info("event=phase7.request_rejected code=invalid_host status_class=4xx")
        return 400, "invalid_host", "Invalid request host."
    return None


def _validate_post_origin(
    request: Request,
    *,
    post_allowed_origins: list[str],
    trusted_proxy_cidrs: list[str],
) -> tuple[int, str, str] | None:
    origin = request.headers.get("origin", "").strip()
    if not origin:
        return None
    normalized_origin = _normalize_origin(origin)
    if normalized_origin is None:
        logger.info("event=phase7.request_rejected code=invalid_origin status_class=4xx")
        return 403, "invalid_origin", "Invalid request origin."
    allowed_origins = {_normalize_origin(value) for value in post_allowed_origins}
    allowed_origins.discard(None)
    same_origin = _same_origin(request, trusted_proxy_cidrs=trusted_proxy_cidrs)
    if same_origin is not None:
        allowed_origins.add(same_origin)
    if normalized_origin not in allowed_origins:
        logger.info("event=phase7.request_rejected code=invalid_origin status_class=4xx")
        return 403, "invalid_origin", "Invalid request origin."
    return None


def _same_origin(request: Request, *, trusted_proxy_cidrs: list[str]) -> str | None:
    host = _request_host(request)
    if host is None:
        return None
    scheme = request.url.scheme.lower()
    remote = _request_client_ip(request)
    if _is_trusted_proxy(remote, trusted_proxy_cidrs):
        proto = request.headers.get("x-forwarded-proto", "").split(",", 1)[0].strip()
        scheme = (proto or scheme).lower()
    if scheme not in {"http", "https"}:
        return None
    return f"{scheme}://{host}"


def _request_host(request: Request) -> str | None:
    return _normalize_host(request.headers.get("host", "").strip())


def _normalize_origin(value: str) -> str | None:
    parsed = urlsplit(value.strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    host = _normalize_host(parsed.netloc)
    if host is None:
        return None
    return f"{parsed.scheme.lower()}://{host}"


def _normalize_host(value: str) -> str | None:
    host = value.strip().lower().rstrip(".")
    if not host or any(char.isspace() or char in "\r\n/" for char in host):
        return None
    if host.startswith("["):
        closing = host.find("]")
        if closing == -1:
            return None
        bracketed = host[: closing + 1]
        remainder = host[closing + 1 :]
        if remainder and not (remainder.startswith(":") and remainder[1:].isdigit()):
            return None
        return f"{bracketed}{remainder}"
    if ":" in host:
        hostname, port = host.rsplit(":", 1)
        if not hostname or not port.isdigit():
            return None
        return f"{hostname}:{port}"
    return host


def _get_or_create_session_id(
    request: Request,
    *,
    config: ReportServingConfig,
) -> tuple[str, bool]:
    candidate = request.cookies.get(config.session_cookie_name, "")
    if candidate:
        try:
            return validate_session_id(candidate, config), False
        except InvalidSessionIdError:
            pass
    return create_session_id(config), True


def _require_session_id(request: Request, *, config: ReportServingConfig) -> str:
    candidate = request.cookies.get(config.session_cookie_name, "")
    return validate_session_id(candidate, config)


def _owner_session_id(
    request: Request,
    session_id: str,
    *,
    config: ReportServingConfig,
    trust_auth_identity_headers: bool,
    auth_identity_header_names: list[str],
) -> str:
    validated_session_id = validate_session_id(session_id, config)
    identity_fingerprint = _trusted_auth_identity_fingerprint(
        request,
        trust_auth_identity_headers=trust_auth_identity_headers,
        auth_identity_header_names=auth_identity_header_names,
        secret=config.ownership_secret,
    )
    if identity_fingerprint is None:
        return validated_session_id
    digest = hmac.new(
        config.ownership_secret.encode("utf-8"),
        f"{validated_session_id}\0{identity_fingerprint}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{config.session_id_prefix}_{digest[: config.session_id_bytes * 2]}"


def _trusted_auth_identity_fingerprint(
    request: Request,
    *,
    trust_auth_identity_headers: bool,
    auth_identity_header_names: list[str],
    secret: str,
) -> str | None:
    if not trust_auth_identity_headers:
        return None
    for header_name in auth_identity_header_names:
        value = request.headers.get(header_name, "").strip()
        if value:
            material = f"{header_name.lower()}={value}".encode("utf-8")
            digest = hmac.new(secret.encode("utf-8"), material, hashlib.sha256).hexdigest()
            return f"auth_{digest}"
    raise HTTPException(
        status_code=401,
        detail={
            "code": "auth_identity_required",
            "message": "Authenticated identity is required.",
            "phase": "phase7",
        },
    )


def _maybe_set_session_cookie(
    response: JSONResponse,
    session_id: str,
    created_session: bool,
    *,
    config: ReportServingConfig,
    cookie_secure: bool,
    cookie_samesite: str,
) -> None:
    if not created_session:
        return
    response.set_cookie(
        key=config.session_cookie_name,
        value=session_id,
        max_age=config.job_ttl_seconds,
        httponly=True,
        samesite=cookie_samesite,
        secure=cookie_secure,
        path="/",
    )


def _job_payload(job: Any) -> dict[str, Any]:
    payload = {
        "job_id": job.job_id,
        "status": job.status,
        "expires_at": job.expires_at,
    }
    if job.report_url:
        payload["report_url"] = job.report_url
    if job.report_id:
        payload["report_id"] = job.report_id
    if job.error:
        payload["error"] = {
            "code": job.error.code,
            "message": job.error.message,
            "phase": job.error.phase,
        }
    return payload


def _enforce_rate_limit(
    security_state: SqlitePhase7SecurityState,
    request: Request,
    session_id: str,
    *,
    secret: str,
    trusted_proxy_cidrs: list[str],
    session_window_seconds: int,
    session_max_requests: int,
    network_burst_window_seconds: int,
    network_burst_max_requests: int,
    network_sustained_window_seconds: int,
    network_sustained_max_requests: int,
    global_window_seconds: int,
    global_max_requests: int,
    altcha_payload: str | None,
    challenge_mode: str,
    challenge_max_number: int,
    challenge_soft_limit_ratio: float,
    challenge_queue_ratio: float,
    execution_backend: Any,
) -> None:
    now = time.time()
    subjects = _rate_limit_subjects(
        request,
        session_id,
        secret=secret,
        trusted_proxy_cidrs=trusted_proxy_cidrs,
        session_window_seconds=session_window_seconds,
        session_max_requests=session_max_requests,
        network_burst_window_seconds=network_burst_window_seconds,
        network_burst_max_requests=network_burst_max_requests,
        network_sustained_window_seconds=network_sustained_window_seconds,
        network_sustained_max_requests=network_sustained_max_requests,
        global_window_seconds=global_window_seconds,
        global_max_requests=global_max_requests,
        now=now,
    )
    if _should_require_challenge(
        security_state,
        subjects,
        challenge_mode=challenge_mode,
        challenge_soft_limit_ratio=challenge_soft_limit_ratio,
        challenge_queue_ratio=challenge_queue_ratio,
        execution_backend=execution_backend,
        now=now,
    ):
        if not _verify_altcha_payload(
            altcha_payload,
            secret=secret,
            max_number=challenge_max_number,
            security_state=security_state,
            now=now,
        ):
            logger.info("event=phase7.job_rejected code=challenge_required status_class=4xx")
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "challenge_required",
                    "message": "Verification is required.",
                    "phase": "phase7",
                },
            )
    if security_state.allow_submission(subjects, now=now):
        return
    logger.info("event=phase7.job_rejected code=rate_limited status_class=4xx")
    raise HTTPException(
        status_code=429,
        detail={
            "code": "rate_limited",
            "message": "Too many requests were submitted.",
            "phase": "phase7",
        },
    )


def _should_require_challenge(
    security_state: SqlitePhase7SecurityState,
    subjects: list[RateLimitSubject],
    *,
    challenge_mode: str,
    challenge_soft_limit_ratio: float,
    challenge_queue_ratio: float,
    execution_backend: Any,
    now: float,
) -> bool:
    if challenge_mode == "off":
        return False
    if challenge_mode == "always":
        return True
    if execution_backend.submission_pressure() >= challenge_queue_ratio:
        return True
    return security_state.should_challenge(
        subjects,
        threshold_ratio=challenge_soft_limit_ratio,
        now=now,
    )


def _rate_limit_subjects(
    request: Request,
    session_id: str,
    *,
    secret: str,
    trusted_proxy_cidrs: list[str],
    session_window_seconds: int,
    session_max_requests: int,
    network_burst_window_seconds: int,
    network_burst_max_requests: int,
    network_sustained_window_seconds: int,
    network_sustained_max_requests: int,
    global_window_seconds: int,
    global_max_requests: int,
    now: float,
) -> list[RateLimitSubject]:
    subjects = [
        _hmac_rate_limit_subject(
            bucket_name="session_submission",
            purpose="session",
            material=session_id,
            window_seconds=session_window_seconds,
            max_requests=session_max_requests,
            secret=secret,
            now=now,
        ),
        _hmac_rate_limit_subject(
            bucket_name="global_submission",
            purpose="global",
            material="all",
            window_seconds=global_window_seconds,
            max_requests=global_max_requests,
            secret=secret,
            now=now,
        ),
    ]
    client_ip = _trusted_client_ip(request, trusted_proxy_cidrs=trusted_proxy_cidrs)
    if client_ip is None:
        return subjects
    subjects.append(
        _hmac_rate_limit_subject(
            bucket_name="network_full_burst",
            purpose="network_full",
            material=str(client_ip),
            window_seconds=network_burst_window_seconds,
            max_requests=network_burst_max_requests,
            secret=secret,
            now=now,
        )
    )
    subjects.append(
        _hmac_rate_limit_subject(
            bucket_name="network_prefix_sustained",
            purpose="network_prefix",
            material=_network_prefix(client_ip),
            window_seconds=network_sustained_window_seconds,
            max_requests=network_sustained_max_requests,
            secret=secret,
            now=now,
        )
    )
    return subjects


def _create_altcha_challenge(
    *,
    secret: str,
    max_number: int,
    ttl_seconds: int,
    now: float | None = None,
) -> dict[str, str | int]:
    now = time.time() if now is None else now
    number = secrets.randbelow(max_number + 1)
    expires_at = int(now + ttl_seconds)
    salt = f"refgen2:{expires_at}:{max_number}:{secrets.token_hex(16)}"
    algorithm = "SHA-256"
    challenge = hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()
    signature = _altcha_signature(
        secret=secret,
        algorithm=algorithm,
        challenge=challenge,
        salt=salt,
    )
    return {
        "algorithm": algorithm,
        "challenge": challenge,
        "maxnumber": max_number,
        "salt": salt,
        "signature": signature,
    }


def _verify_altcha_payload(
    payload: str | None,
    *,
    secret: str,
    max_number: int,
    security_state: SqlitePhase7SecurityState,
    now: float | None = None,
) -> bool:
    if not payload or len(payload) > 4096:
        return False
    now = time.time() if now is None else now
    try:
        decoded = base64.b64decode(payload, validate=True)
        data = json.loads(decoded.decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    algorithm = data.get("algorithm")
    challenge = data.get("challenge")
    salt = data.get("salt")
    signature = data.get("signature")
    number = data.get("number")
    if not (
        algorithm == "SHA-256"
        and isinstance(challenge, str)
        and isinstance(salt, str)
        and isinstance(signature, str)
        and isinstance(number, int)
    ):
        return False
    parsed = _parse_altcha_salt(salt)
    if parsed is None:
        return False
    expires_at, issued_max_number = parsed
    if expires_at < now or issued_max_number > max_number or number < 0:
        return False
    if number > issued_max_number:
        return False
    expected_signature = _altcha_signature(
        secret=secret,
        algorithm=algorithm,
        challenge=challenge,
        salt=salt,
    )
    if not hmac.compare_digest(signature, expected_signature):
        return False
    expected_challenge = hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()
    if not hmac.compare_digest(challenge, expected_challenge):
        return False
    challenge_key = hmac.new(
        secret.encode("utf-8"),
        f"altcha-consumed\0{signature}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return security_state.try_consume_challenge(
        challenge_key,
        expires_at_epoch=expires_at,
        now=now,
    )


def _parse_altcha_salt(salt: str) -> tuple[int, int] | None:
    parts = salt.split(":", 3)
    if len(parts) != 4 or parts[0] != "refgen2":
        return None
    try:
        expires_at = int(parts[1])
        max_number = int(parts[2])
    except ValueError:
        return None
    if expires_at <= 0 or max_number <= 0:
        return None
    return expires_at, max_number


def _altcha_signature(
    *,
    secret: str,
    algorithm: str,
    challenge: str,
    salt: str,
) -> str:
    return hmac.new(
        secret.encode("utf-8"),
        f"{algorithm}\n{challenge}\n{salt}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _hmac_rate_limit_subject(
    *,
    bucket_name: str,
    purpose: str,
    material: str,
    window_seconds: int,
    max_requests: int,
    secret: str,
    now: float,
) -> RateLimitSubject:
    window_id = int(now // window_seconds)
    digest = hmac.new(
        secret.encode("utf-8"),
        f"{purpose}\0{window_id}\0{material}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return RateLimitSubject(
        bucket_name=bucket_name,
        subject_key=f"hmac_{digest}",
        window_seconds=window_seconds,
        max_requests=max_requests,
    )


def _trusted_client_ip(
    request: Request,
    *,
    trusted_proxy_cidrs: list[str],
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    forwarded = _forwarded_client_ip(request)
    remote = _request_client_ip(request)
    if forwarded is not None and _is_trusted_proxy(remote, trusted_proxy_cidrs):
        return forwarded
    if forwarded is None:
        return remote
    return None


def _forwarded_client_ip(
    request: Request,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    x_forwarded_for = request.headers.get("x-forwarded-for", "")
    if x_forwarded_for:
        for part in x_forwarded_for.split(","):
            parsed = _parse_ip_address(part.strip())
            if parsed is not None:
                return parsed
    return _parse_ip_address(request.headers.get("x-real-ip", ""))


def _request_client_ip(
    request: Request,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    if request.client is None:
        return None
    return _parse_ip_address(request.client.host)


def _is_trusted_proxy(
    remote: ipaddress.IPv4Address | ipaddress.IPv6Address | None,
    trusted_proxy_cidrs: list[str],
) -> bool:
    if "*" in trusted_proxy_cidrs:
        return True
    if remote is None:
        return False
    for value in trusted_proxy_cidrs:
        try:
            network = ipaddress.ip_network(value, strict=False)
        except ValueError:
            continue
        if remote in network:
            return True
    return False


def _parse_ip_address(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return ipaddress.ip_address(stripped)
    except ValueError:
        return None


def _network_prefix(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str:
    if isinstance(address, ipaddress.IPv4Address):
        return str(ipaddress.ip_network(f"{address}/24", strict=False))
    return str(ipaddress.ip_network(f"{address}/64", strict=False))


def _error_response(
    *,
    status_code: int,
    code: str,
    message: str,
    phase: str,
) -> JSONResponse:
    logger.info(
        "event=phase7.api_error code=%s phase=%s status_class=%s",
        code,
        phase,
        f"{status_code // 100}xx",
    )
    response = JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "phase": phase,
            }
        },
    )
    _apply_security_headers(response, API_SECURITY_HEADERS)
    return response


def _apply_security_headers(response: Any, headers: dict[str, str]) -> None:
    for key, value in headers.items():
        response.headers[key] = value


def _http_status_code_name(status_code: int) -> str:
    if status_code == 400:
        return "invalid_request"
    if status_code == 404:
        return "not_found"
    if status_code == 410:
        return "gone"
    if status_code == 413:
        return "request_too_large"
    if status_code == 422:
        return "invalid_request"
    if status_code == 503:
        return "service_unavailable"
    if status_code >= 500:
        return "internal_server_error"
    return "http_error"


def _safe_http_message(status_code: int) -> str:
    if status_code == 400:
        return "Invalid request."
    if status_code == 404:
        return "Not found."
    if status_code == 410:
        return "Resource is gone."
    if status_code == 413:
        return "Request body is too large."
    if status_code == 422:
        return "Invalid request."
    if status_code == 503:
        return "Service unavailable."
    if status_code >= 500:
        return "Internal server error."
    return "Request failed."


def _safe_http_status(value: int) -> int:
    if 400 <= value <= 599:
        return value
    return 500


app = create_app()
