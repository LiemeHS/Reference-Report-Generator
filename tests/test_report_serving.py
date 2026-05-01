from __future__ import annotations

import logging
import os
import stat
import time

import pytest

from reference_gen2.report_serving import (
    InvalidReportIdError,
    InvalidSessionIdError,
    JobNotFoundError,
    JobOwnershipError,
    ReportNotFoundError,
    ReportServingConfig,
    assert_report_owned,
    cleanup_expired_jobs,
    cleanup_expired_reports,
    complete_job,
    create_job,
    create_session_id,
    create_report_id,
    delete_report_html,
    cleanup_legacy_job_state,
    get_job,
    get_job_by_report_id,
    report_status,
    serve_report_html,
    security_headers,
    serve_report_once,
    store_report_html,
    validate_report_id,
)
from reference_gen2.report_generation.service import (
    report_inline_script_csp_hash,
    report_inline_style_csp_hash,
)


def _config(tmp_path):
    return ReportServingConfig(
        report_dir=tmp_path / "reports",
        job_dir=tmp_path / "jobs",
        ttl_seconds=60,
    )


def _assert_not_group_or_world_accessible(path):
    assert path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO) == 0


def test_report_ids_are_high_entropy_and_path_safe(tmp_path):
    config = _config(tmp_path)
    report_ids = {create_report_id(config) for _ in range(50)}

    assert len(report_ids) == 50
    for report_id in report_ids:
        assert validate_report_id(report_id, config) == report_id
        assert report_id.startswith("cycle_")
        assert len(report_id) == len("cycle_") + 32


@pytest.mark.parametrize(
    "report_id",
    [
        "",
        "cycle_abc",
        "cycle_zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
        "../cycle_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "cycle_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/evil",
        "report_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ],
)
def test_invalid_report_ids_rejected_before_path_resolution(tmp_path, report_id):
    with pytest.raises(InvalidReportIdError):
        validate_report_id(report_id, _config(tmp_path))


def test_store_report_html_exposes_only_public_metadata(tmp_path):
    config = _config(tmp_path)
    status = store_report_html("<!doctype html><p>ok</p>", config=config)

    assert status.status == "available"
    assert status.report_id.startswith("cycle_")
    assert status.expires_at is not None
    assert str(config.report_dir) not in repr(status)
    assert (config.report_dir / f"{status.report_id}.html").exists()


def test_serve_report_once_returns_headers_and_deletes_artifact(tmp_path):
    config = _config(tmp_path)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)

    served = serve_report_once(stored.report_id, config=config)

    assert "<p>ok</p>" in served.html
    assert served.headers == security_headers()
    assert not (config.report_dir / f"{stored.report_id}.html").exists()
    with pytest.raises(ReportNotFoundError):
        serve_report_once(stored.report_id, config=config)


def test_serve_report_html_can_be_reused_without_consumption(tmp_path):
    config = _config(tmp_path)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)

    first = serve_report_html(stored.report_id, config=config, consume=False)
    second = serve_report_html(stored.report_id, config=config, consume=False)

    assert first.html == "<!doctype html><p>ok</p>"
    assert second.html == "<!doctype html><p>ok</p>"
    assert first.headers == security_headers()
    assert second.headers == security_headers()
    assert (config.report_dir / f"{stored.report_id}.html").exists()


def test_missing_and_deleted_reports_return_gone_status(tmp_path):
    config = _config(tmp_path)
    report_id = create_report_id(config)

    status = report_status(report_id, config=config)

    assert status.report_id == report_id
    assert status.status == "gone"
    assert status.expires_at is None


def test_cleanup_expired_reports_removes_only_expired_artifacts(tmp_path):
    config = ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=10)
    expired = store_report_html("old", config=config)
    fresh = store_report_html("new", config=config)
    old_time = time.time() - 30
    os.utime(config.report_dir / f"{expired.report_id}.html", (old_time, old_time))

    deleted_count = cleanup_expired_reports(config=config)

    assert deleted_count == 1
    assert not (config.report_dir / f"{expired.report_id}.html").exists()
    assert (config.report_dir / f"{fresh.report_id}.html").exists()


def test_security_headers_include_required_policy():
    headers = security_headers()

    assert headers["Cache-Control"] == "no-store, no-cache, must-revalidate, private"
    assert headers["Pragma"] == "no-cache"
    assert headers["Expires"] == "0"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    policy = headers["Content-Security-Policy"]
    assert "default-src 'none'" in policy
    assert f"script-src {report_inline_script_csp_hash()}" in policy
    assert f"style-src {report_inline_style_csp_hash()}" in policy
    assert "'unsafe-inline'" not in policy
    assert "base-uri 'none'" in policy
    assert "frame-ancestors 'none'" in policy
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["Content-Type"] == "text/html; charset=utf-8"


def test_report_serving_logs_only_aggregate_metadata(tmp_path, caplog):
    config = _config(tmp_path)
    forbidden = "secret.docx 10.1234/example Alpha Raw Reference /data/private.db"
    caplog.set_level(logging.INFO, logger="reference_gen2.report_serving.service")

    stored = store_report_html(f"safe html without {forbidden[:0]}", config=config)
    serve_report_once(stored.report_id, config=config)
    cleanup_expired_reports(config=config)

    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "phase7.report_stored" in joined
    assert "phase7.report_served" in joined
    assert "phase7.cleanup_complete" in joined
    for token in forbidden.split():
        assert token not in joined
    assert stored.report_id not in joined


def test_job_lifecycle_exposes_only_safe_owned_metadata(tmp_path):
    config = _config(tmp_path)
    session_id = create_session_id(config)
    job = create_job(session_id, config=config)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)

    completed = complete_job(
        job.job_id,
        owner_session_id=session_id,
        report_id=stored.report_id,
        config=config,
    )

    assert completed.status == "completed"
    assert completed.report_url == f"/reports/{stored.report_id}"
    assert completed.expires_at is not None
    assert str(config.job_dir) not in repr(completed)
    assert str(config.report_dir) not in repr(completed)
    _assert_not_group_or_world_accessible(config.job_dir)
    _assert_not_group_or_world_accessible(config.job_dir / f"{job.job_id}.json")


def test_job_lookup_requires_owning_session(tmp_path):
    config = _config(tmp_path)
    owner_session = create_session_id(config)
    other_session = create_session_id(config)
    job = create_job(owner_session, config=config)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)
    complete_job(
        job.job_id,
        owner_session_id=owner_session,
        report_id=stored.report_id,
        config=config,
    )

    with pytest.raises(JobOwnershipError):
        get_job(job.job_id, owner_session_id=other_session, config=config)
    with pytest.raises(JobOwnershipError):
        assert_report_owned(stored.report_id, owner_session_id=other_session, config=config)


def test_report_to_job_lookup_tracks_served_reports_as_gone(tmp_path):
    config = _config(tmp_path)
    session_id = create_session_id(config)
    job = create_job(session_id, config=config)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)
    complete_job(
        job.job_id,
        owner_session_id=session_id,
        report_id=stored.report_id,
        config=config,
    )

    assert get_job_by_report_id(stored.report_id, owner_session_id=session_id, config=config).status == "completed"
    serve_report_once(stored.report_id, config=config)
    assert get_job_by_report_id(stored.report_id, owner_session_id=session_id, config=config).status == "gone"


def test_report_to_job_lookup_uses_direct_index_file(tmp_path):
    config = _config(tmp_path)
    session_id = create_session_id(config)
    job = create_job(session_id, config=config)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)
    complete_job(
        job.job_id,
        owner_session_id=session_id,
        report_id=stored.report_id,
        config=config,
    )

    index_path = config.job_dir / "report_index" / f"{stored.report_id}.json"

    assert index_path.exists()
    _assert_not_group_or_world_accessible(config.job_dir / "report_index")
    _assert_not_group_or_world_accessible(index_path)
    assert session_id not in (config.job_dir / f"{job.job_id}.json").read_text(encoding="utf-8")
    assert session_id not in index_path.read_text(encoding="utf-8")
    assert get_job_by_report_id(stored.report_id, owner_session_id=session_id, config=config).job_id == job.job_id


def test_cleanup_expired_jobs_removes_job_state_and_owned_report(tmp_path):
    config = ReportServingConfig(
        report_dir=tmp_path / "reports",
        job_dir=tmp_path / "jobs",
        ttl_seconds=60,
        job_ttl_seconds=10,
    )
    session_id = create_session_id(config)
    job = create_job(session_id, config=config)
    stored = store_report_html("<!doctype html><p>ok</p>", config=config)
    complete_job(
        job.job_id,
        owner_session_id=session_id,
        report_id=stored.report_id,
        config=config,
        now=time.time() - 30,
    )

    deleted_count = cleanup_expired_jobs(config=config)

    assert deleted_count == 1
    assert not (config.job_dir / f"{job.job_id}.json").exists()
    assert not (config.job_dir / "report_index" / f"{stored.report_id}.json").exists()
    assert not (config.report_dir / f"{stored.report_id}.html").exists()
    with pytest.raises(JobNotFoundError):
        get_job(job.job_id, owner_session_id=session_id, config=config)


def test_delete_report_html_returns_false_when_missing(tmp_path):
    config = _config(tmp_path)

    assert delete_report_html(create_report_id(config), config=config) is False


def test_invalid_session_ids_rejected(tmp_path):
    with pytest.raises(InvalidSessionIdError):
        get_job("job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", owner_session_id="bad", config=_config(tmp_path))


def test_cleanup_legacy_job_state_removes_old_owner_session_format(tmp_path):
    config = _config(tmp_path)
    config.job_dir.mkdir(parents=True, exist_ok=True)
    legacy_job = config.job_dir / "job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
    legacy_index_dir = config.job_dir / "report_index"
    legacy_index_dir.mkdir(parents=True, exist_ok=True)
    legacy_index = legacy_index_dir / "cycle_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.json"
    legacy_job.write_text(
        '{"job_id":"job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","owner_session_id":"sess_old","created_at_epoch":1,"updated_at_epoch":1,"status":"pending","report_id":null,"error":null}',
        encoding="utf-8",
    )
    legacy_index.write_text(
        '{"report_id":"cycle_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","job_id":"job_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","owner_session_id":"sess_old","updated_at_epoch":1}',
        encoding="utf-8",
    )

    deleted = cleanup_legacy_job_state(config=config)

    assert deleted == 2
    assert not legacy_job.exists()
    assert not legacy_index.exists()
