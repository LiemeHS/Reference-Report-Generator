from __future__ import annotations

import asyncio
from pathlib import Path
import stat
import threading

from reference_gen2.report_serving import (
    Phase7SecurityStateConfig,
    QueuedPhase7ExecutionBackend,
    ReportServingConfig,
    SqlitePhase7SecurityState,
    SyncPhase7ExecutionBackend,
    TextJobSubmission,
    UploadJobSubmission,
    get_job,
    serve_report_once,
)
from reference_gen2.services.hosted_report_pipeline import (
    HostedReportPipelineError,
    HostedReportPipelineResult,
)


def _assert_private_path(path: Path) -> None:
    mode = path.stat().st_mode
    assert mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def _serving_config(tmp_path: Path) -> ReportServingConfig:
    return ReportServingConfig(
        report_dir=tmp_path / "reports",
        job_dir=tmp_path / "jobs",
        ttl_seconds=60,
        job_ttl_seconds=60,
    )


def _security_state(tmp_path: Path) -> SqlitePhase7SecurityState:
    return SqlitePhase7SecurityState(
        Phase7SecurityStateConfig(
            db_path=tmp_path / "phase7_security.sqlite3",
            rate_limit_window_seconds=60,
            rate_limit_max_requests=20,
            max_active_jobs=2,
            active_job_lease_seconds=60,
        )
    )


def test_sync_backend_completes_text_jobs(monkeypatch, tmp_path: Path):
    def fake_pipeline(**_kwargs):
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_text_report_pipeline",
        fake_pipeline,
    )
    config = _serving_config(tmp_path)
    backend = SyncPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )

    job = asyncio.run(
        backend.submit_text(
            owner_session_id="sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            submission=TextJobSubmission(
                reference_list_text="Smith, J. (2020). Example title.",
                style_hint="apa7_nl",
            ),
        )
    )

    assert job.status == "completed"
    assert job.report_id is not None
    served = serve_report_once(job.report_id, config=config)
    assert "Reference Report" in served.html


def test_worker_backend_queues_then_completes_upload_jobs(monkeypatch, tmp_path: Path):
    def fake_pipeline(**_kwargs):
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_hosted_report_pipeline",
        fake_pipeline,
    )
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )

    queued = asyncio.run(
        backend.submit_upload(
            owner_session_id=session_id,
            submission=UploadJobSubmission(
                filename="paper.pdf",
                declared_mime="application/pdf",
                content=b"%PDF-1.4",
                style_hint="apa7_nl",
            ),
        )
    )

    assert queued.status == "queued"
    request_path = config.job_dir / "requests" / f"{queued.job_id}.json"
    payload_path = config.job_dir / "payloads" / f"{queued.job_id}.bin"
    assert request_path.exists()
    assert payload_path.exists()
    _assert_private_path(config.job_dir)
    _assert_private_path(request_path.parent)
    _assert_private_path(payload_path.parent)
    _assert_private_path(request_path)
    _assert_private_path(payload_path)
    assert session_id not in request_path.read_text(encoding="utf-8")
    assert session_id not in (config.job_dir / f"{queued.job_id}.json").read_text(encoding="utf-8")

    assert backend.run_available_jobs(limit=1) == 1

    completed = get_job(queued.job_id, owner_session_id=session_id, config=config)
    assert completed.status == "completed"
    assert completed.report_id is not None
    assert not request_path.exists()
    assert not payload_path.exists()


def test_worker_backend_queues_then_completes_text_jobs(monkeypatch, tmp_path: Path):
    def fake_pipeline(**kwargs):
        assert kwargs["reference_list_text"] == "Raw Reference"
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_text_report_pipeline",
        fake_pipeline,
    )
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )

    queued = asyncio.run(
        backend.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(
                reference_list_text="Raw Reference",
                style_hint="apa7_nl",
            ),
        )
    )

    assert queued.status == "queued"
    payload_path = config.job_dir / "payloads" / f"{queued.job_id}.txt"
    assert payload_path.exists()
    _assert_private_path(payload_path)
    assert backend.run_available_jobs(limit=1) == 1
    completed = get_job(queued.job_id, owner_session_id=session_id, config=config)
    assert completed.status == "completed"


def test_worker_backend_claims_request_before_processing(monkeypatch, tmp_path: Path):
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )

    queued = asyncio.run(
        backend.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(reference_list_text="Raw Reference"),
        )
    )
    request_path = config.job_dir / "requests" / f"{queued.job_id}.json"
    running_path = config.job_dir / "running" / f"{queued.job_id}.json"

    def fake_pipeline(**_kwargs):
        assert not request_path.exists()
        assert running_path.exists()
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_text_report_pipeline",
        fake_pipeline,
    )

    assert backend.run_available_jobs(limit=1) == 1
    assert not request_path.exists()
    assert not running_path.exists()


def test_worker_backend_second_backend_cannot_claim_same_job(tmp_path: Path):
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    first = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )
    second = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )
    queued = asyncio.run(
        first.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(reference_list_text="Raw Reference"),
        )
    )
    request_path = config.job_dir / "requests" / f"{queued.job_id}.json"

    claimed = first._claim_request_path(request_path)

    assert claimed == config.job_dir / "running" / f"{queued.job_id}.json"
    assert not request_path.exists()
    assert second._claim_request_path(request_path) is None


def test_worker_backend_failed_jobs_clean_up_claim_and_payload(monkeypatch, tmp_path: Path):
    def fake_pipeline(**_kwargs):
        raise HostedReportPipelineError(
            code="invalid_reference_text",
            message="Invalid reference text.",
            phase="phase1",
            http_status=400,
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_text_report_pipeline",
        fake_pipeline,
    )
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )
    queued = asyncio.run(
        backend.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(reference_list_text="Bad Reference"),
        )
    )

    assert backend.run_available_jobs(limit=1) == 1

    assert not (config.job_dir / "requests" / f"{queued.job_id}.json").exists()
    assert not (config.job_dir / "running" / f"{queued.job_id}.json").exists()
    assert not (config.job_dir / "payloads" / f"{queued.job_id}.txt").exists()
    failed = get_job(queued.job_id, owner_session_id=session_id, config=config)
    assert failed.status == "failed"


def test_worker_backend_skips_malformed_requests_without_blocking_later_jobs(monkeypatch, tmp_path: Path):
    def fake_pipeline(**_kwargs):
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_text_report_pipeline",
        fake_pipeline,
    )
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )
    queued = asyncio.run(
        backend.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(reference_list_text="Raw Reference"),
        )
    )
    malformed_dir = config.job_dir / "requests"
    malformed_dir.mkdir(parents=True, exist_ok=True)
    malformed_path = malformed_dir / "000_malformed.json"
    malformed_path.write_text("{not json", encoding="utf-8")

    assert backend.run_available_jobs(limit=2) == 1

    completed = get_job(queued.job_id, owner_session_id=session_id, config=config)
    assert completed.status == "completed"
    assert not malformed_path.exists()
    assert not (config.job_dir / "running" / malformed_path.name).exists()


def test_worker_backend_runs_two_jobs_concurrently_once(monkeypatch, tmp_path: Path):
    first_started = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    calls: list[str] = []
    lock = threading.Lock()

    def fake_pipeline(**kwargs):
        with lock:
            calls.append(kwargs["reference_list_text"])
            if len(calls) == 1:
                first_started.set()
            elif len(calls) == 2:
                second_started.set()
        assert release.wait(timeout=5)
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr(
        "reference_gen2.report_serving.runner.run_text_report_pipeline",
        fake_pipeline,
    )
    config = _serving_config(tmp_path)
    session_id = "sess_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    backend = QueuedPhase7ExecutionBackend(
        serving_config=config,
        security_state=_security_state(tmp_path),
        db_path="/private/db.sqlite",
        text_input_max_chars=1000,
        max_queued_jobs=20,
    )
    first = asyncio.run(
        backend.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(reference_list_text="First Reference"),
        )
    )
    second = asyncio.run(
        backend.submit_text(
            owner_session_id=session_id,
            submission=TextJobSubmission(reference_list_text="Second Reference"),
        )
    )

    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(backend.run_available_jobs(limit=1)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()

    assert first_started.wait(timeout=5)
    assert second_started.wait(timeout=5)
    assert get_job(first.job_id, owner_session_id=session_id, config=config).status == "running"
    assert get_job(second.job_id, owner_session_id=session_id, config=config).status == "running"

    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(results) == [1, 1]
    assert sorted(calls) == ["First Reference", "Second Reference"]
    for job in (first, second):
        completed = get_job(job.job_id, owner_session_id=session_id, config=config)
        assert completed.status == "completed"
        assert not (config.job_dir / "requests" / f"{job.job_id}.json").exists()
        assert not (config.job_dir / "running" / f"{job.job_id}.json").exists()
        assert not (config.job_dir / "payloads" / f"{job.job_id}.txt").exists()
