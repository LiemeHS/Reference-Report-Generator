from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import re
import sqlite3
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from reference_gen2.api.phase7_app import _read_json_limited, _read_upload_limited, create_app
from reference_gen2.report_generation.service import (
    report_inline_script_csp_hash,
    report_inline_style_csp_hash,
)
from reference_gen2.report_serving import ReportServingConfig
from reference_gen2.services.hosted_report_pipeline import (
    HostedReportPipelineError,
    HostedReportPipelineResult,
)
from scripts.web_stack_preflight import WebStackPreflightError, assert_web_stack_is_compatible
from tests.test_report_generation import _report


class _ChunkedUpload:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def read(self, _size: int) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _ChunkedRequest:
    def __init__(self, chunks: list[bytes]):
        self._chunks = list(chunks)

    async def stream(self):
        for chunk in self._chunks:
            yield chunk


try:
    assert_web_stack_is_compatible()
except WebStackPreflightError as exc:
    pytest.fail(f"Web stack preflight failed: {exc}", pytrace=False)


def _solve_challenge(challenge: dict[str, object]) -> str:
    salt = str(challenge["salt"])
    target = str(challenge["challenge"])
    max_number = int(challenge["maxnumber"])
    for number in range(max_number + 1):
        digest = hashlib.sha256(f"{salt}{number}".encode("utf-8")).hexdigest()
        if digest == target:
            payload = {
                "algorithm": challenge["algorithm"],
                "challenge": challenge["challenge"],
                "number": number,
                "salt": challenge["salt"],
                "signature": challenge["signature"],
            }
            return base64.b64encode(
                json.dumps(payload, separators=(",", ":")).encode("utf-8")
            ).decode("ascii")
    raise AssertionError("challenge was not solvable")


def test_phase7_frontend_returns_static_upload_page(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert "Rapport genereren" in response.text
    assert 'action="/reports/upload"' in response.text
    assert 'action="/reports/text"' in response.text
    assert 'href="/static/phase7.css"' in response.text
    assert 'src="/static/phase7.js"' in response.text
    assert "Tekst plakken" in response.text
    assert 'name="style_hint"' in response.text
    assert 'name="reference_list_text"' in response.text
    assert 'name="file"' in response.text


def test_phase7_frontend_uses_no_external_assets_or_browser_storage(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.get("/")
    html = response.text.lower()

    assert not re.search(r"https?://|//[^\\s\"']+", html)
    assert "cdn" not in html
    assert "localstorage" not in html
    assert "sessionstorage" not in html
    assert "react" not in html
    assert "tailwind" not in html
    assert "/reports/text" in html
    assert "<style>" not in html
    assert "<script>" not in html


def test_phase7_frontend_style_selector_maps_supported_backend_values(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.get("/")

    for value in [
        "unknown",
        "apa7_nl",
        "apa7_en",
        "chicago",
        "harvard",
        "mla",
        "vancouver",
    ]:
        assert f'value="{value}"' in response.text


def test_phase7_adapter_rejects_raw_or_non_serialized_report_payload(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.post("/reports", json={"html": "<!doctype html><p>raw</p>"})

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_sanitized_report_payload",
            "message": "Invalid sanitized report payload.",
            "phase": "phase6",
        }
    }


def test_phase7_adapter_serves_sanitized_report_mapping_reusably(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app, base_url="https://testserver")

    create_response = client.post("/reports", json=_report())

    assert create_response.status_code == 200
    payload = create_response.json()
    assert set(payload) == {"job_id", "report_id", "status", "expires_at", "report_url"}
    assert payload["status"] == "completed"
    assert payload["report_url"] == f"/reports/{payload['report_id']}"
    assert create_response.cookies.get("reference_gen2_session")
    set_cookie = create_response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "secure" in set_cookie
    assert "samesite=lax" in set_cookie

    status_response = client.get(f"/reports/{payload['report_id']}/status")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "completed"
    assert status_response.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"
    assert status_response.headers["x-content-type-options"] == "nosniff"
    assert status_response.headers["x-frame-options"] == "DENY"
    assert status_response.headers["referrer-policy"] == "no-referrer"

    job_response = client.get(f"/jobs/{payload['job_id']}")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "completed"
    assert job_response.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"

    report_response = client.get(payload["report_url"])
    assert report_response.status_code == 200
    assert "Referentierapport" in report_response.text
    assert report_response.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"
    assert report_response.headers["x-content-type-options"] == "nosniff"
    assert report_response.headers["x-frame-options"] == "DENY"
    policy = report_response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert f"script-src {report_inline_script_csp_hash()}" in policy
    assert f"style-src {report_inline_style_csp_hash()}" in policy
    assert "'unsafe-inline'" not in policy
    assert report_response.headers["referrer-policy"] == "no-referrer"
    assert "<script>" in report_response.text
    assert "style=" not in report_response.text
    assert "window.print()" not in report_response.text
    assert "printReportButton" not in report_response.text

    second_response = client.get(payload["report_url"])
    assert second_response.status_code == 200
    assert "Referentierapport" in second_response.text
    assert client.get(f"/jobs/{payload['job_id']}").json()["status"] == "completed"


def test_phase7_adapter_rejects_invalid_report_id(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.get("/reports/cycle_abc/status")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_report_id",
            "message": "Invalid report id.",
            "phase": "phase7",
        }
    }


def test_phase7_adapter_requires_owned_session_for_report_and_job_access(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    owner = TestClient(app, base_url="https://owner.test")
    other = TestClient(app, base_url="https://other.test")

    create_response = owner.post("/reports", json=_report())
    payload = create_response.json()

    assert other.get(f"/jobs/{payload['job_id']}").status_code == 400
    assert other.get(payload["report_url"]).status_code == 410


def test_phase7_auth_headers_are_ignored_by_default_for_session_ownership(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app, base_url="https://testserver")

    create_response = client.post(
        "/reports",
        json=_report(),
        headers={"Remote-User": "alice@example.test"},
    )
    payload = create_response.json()

    job_response = client.get(
        f"/jobs/{payload['job_id']}",
        headers={"Remote-User": "bob@example.test"},
    )

    assert job_response.status_code == 200
    assert job_response.json()["status"] == "completed"


def test_phase7_configured_auth_identity_changes_owner_on_user_switch(tmp_path):
    config = ReportServingConfig(
        report_dir=tmp_path / "reports",
        job_dir=tmp_path / "jobs",
        ttl_seconds=60,
    )
    app = create_app(
        config,
        trust_auth_identity_headers=True,
        auth_identity_header_names=["Remote-User"],
    )
    client = TestClient(app, base_url="https://testserver")

    alice_response = client.post(
        "/reports",
        json=_report(),
        headers={"Remote-User": "alice@example.test"},
    )
    bob_response = client.post(
        "/reports",
        json=_report(),
        headers={"Remote-User": "bob@example.test"},
    )
    alice_payload = alice_response.json()
    bob_payload = bob_response.json()
    alice_job_state = (config.job_dir / f"{alice_payload['job_id']}.json").read_text(
        encoding="utf-8"
    )
    bob_job_state = (config.job_dir / f"{bob_payload['job_id']}.json").read_text(
        encoding="utf-8"
    )
    alice_index_state = (
        config.job_dir / "report_index" / f"{alice_payload['report_id']}.json"
    ).read_text(encoding="utf-8")
    alice_owner_key = json.loads(alice_job_state)["owner_key"]
    bob_owner_key = json.loads(bob_job_state)["owner_key"]

    assert client.get(
        f"/jobs/{alice_payload['job_id']}",
        headers={"Remote-User": "alice@example.test"},
    ).status_code == 200
    assert client.get(
        f"/jobs/{alice_payload['job_id']}",
        headers={"Remote-User": "bob@example.test"},
    ).status_code == 404
    assert client.get(
        alice_payload["report_url"],
        headers={"Remote-User": "bob@example.test"},
    ).status_code == 410
    assert alice_owner_key != bob_owner_key
    assert "alice@example.test" not in alice_job_state
    assert "alice@example.test" not in alice_index_state
    assert "bob@example.test" not in bob_job_state


def test_phase7_configured_auth_identity_requires_forwarded_identity(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        trust_auth_identity_headers=True,
        auth_identity_header_names=["Remote-User"],
    )
    client = TestClient(app, base_url="https://testserver")

    response = client.post("/reports", json=_report())

    assert response.status_code == 401
    assert response.json() == {
        "error": {
            "code": "auth_identity_required",
            "message": "Authenticated identity is required.",
            "phase": "phase7",
        }
    }


def test_phase7_upload_endpoint_creates_hosted_report_from_file(monkeypatch, tmp_path):
    captured = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_hosted_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
    )
    client = TestClient(app, base_url="https://testserver")

    response = client.post(
        "/reports/upload",
        files={"file": ("private-paper.pdf", b"%PDF-1.4", "application/pdf")},
        data={"style_hint": "vancouver"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"job_id", "report_id", "status", "expires_at", "report_url"}
    assert payload["status"] == "completed"
    assert captured["filename"] == "private-paper.pdf"
    assert captured["declared_mime"] == "application/pdf"
    assert captured["content"] == b"%PDF-1.4"
    assert captured["db_path"] == "/private/localdb.sqlite"
    assert captured["style_hint"] == "vancouver"
    assert "private-paper" not in response.text
    assert "localdb" not in response.text

    report_response = client.get(payload["report_url"])
    assert report_response.status_code == 200
    assert "Reference Report" in report_response.text
    assert client.get(payload["report_url"]).status_code == 200


def test_phase7_text_endpoint_creates_report_from_pasted_text(monkeypatch, tmp_path):
    captured = {}

    def fake_pipeline(**kwargs):
        captured.update(kwargs)
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_text_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
        text_input_max_chars=1000,
    )
    client = TestClient(app, base_url="https://testserver")

    response = client.post(
        "/reports/text",
        json={
            "reference_list_text": "Raw Reference Title doi:10.1234/private",
            "style_hint": "apa7_nl",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"job_id", "report_id", "status", "expires_at", "report_url"}
    assert captured["reference_list_text"] == "Raw Reference Title doi:10.1234/private"
    assert captured["db_path"] == "/private/localdb.sqlite"
    assert captured["style_hint"] == "apa7_nl"


def test_phase7_adapter_worker_mode_keeps_routes_stable_while_queueing(monkeypatch, tmp_path):
    captured = {}

    def fake_pipeline(**_kwargs):
        captured.update(_kwargs)
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_text_report_pipeline", fake_pipeline)
    config = ReportServingConfig(
        report_dir=tmp_path / "reports",
        job_dir=tmp_path / "jobs",
        ttl_seconds=60,
    )
    app = create_app(
        config,
        db_path="/private/localdb.sqlite",
        execution_backend_mode="worker",
        text_input_max_chars=1000,
    )
    client = TestClient(app, base_url="https://testserver")

    response = client.post(
        "/reports/text",
        json={"reference_list_text": "Queued Reference", "style_hint": "apa7_nl"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"job_id", "status", "expires_at"}
    assert payload["status"] == "queued"

    backend = app.state.phase7_execution_backend
    assert backend.run_available_jobs(limit=1) == 1

    job_response = client.get(f"/jobs/{payload['job_id']}")
    assert job_response.status_code == 200
    assert job_response.json()["status"] == "completed"
    report_url = job_response.json()["report_url"]
    report_response = client.get(report_url)
    assert report_response.status_code == 200
    assert "Reference Report" in report_response.text
    assert captured["style_hint"] == "apa7_nl"
    assert captured["max_chars"] == 1000
    assert "Raw Reference Title" not in response.text
    assert "10.1234/private" not in response.text
    assert "localdb" not in response.text
    assert client.get(report_url).status_code == 200


def test_phase7_adapter_worker_mode_completes_two_text_jobs_with_parallel_workers(monkeypatch, tmp_path):
    release = threading.Event()
    started_count = 0
    started_lock = threading.Lock()
    both_started = threading.Event()

    def fake_pipeline(**_kwargs):
        nonlocal started_count
        with started_lock:
            started_count += 1
            if started_count == 2:
                both_started.set()
        assert release.wait(timeout=5)
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
            final_status_counts={"accepted": 1},
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_text_report_pipeline", fake_pipeline)
    config = ReportServingConfig(
        report_dir=tmp_path / "reports",
        job_dir=tmp_path / "jobs",
        ttl_seconds=60,
    )
    app = create_app(
        config,
        db_path="/private/localdb.sqlite",
        execution_backend_mode="worker",
        text_input_max_chars=1000,
    )
    client = TestClient(app, base_url="https://testserver")

    first = client.post(
        "/reports/text",
        json={"reference_list_text": "First Reference", "style_hint": "apa7_nl"},
    ).json()
    second = client.post(
        "/reports/text",
        json={"reference_list_text": "Second Reference", "style_hint": "apa7_nl"},
    ).json()

    backend = app.state.phase7_execution_backend
    results: list[int] = []
    threads = [
        threading.Thread(target=lambda: results.append(backend.run_available_jobs(limit=1)))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()

    assert both_started.wait(timeout=5)
    release.set()
    for thread in threads:
        thread.join(timeout=5)

    assert sorted(results) == [1, 1]
    for payload in (first, second):
        job_response = client.get(f"/jobs/{payload['job_id']}")
        assert job_response.status_code == 200
        assert job_response.json()["status"] == "completed"
        report_response = client.get(job_response.json()["report_url"])
        assert report_response.status_code == 200
        assert "Reference Report" in report_response.text


def test_phase7_text_endpoint_requires_server_db_configuration(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="",
    )
    client = TestClient(app)

    response = client.post(
        "/reports/text",
        json={"reference_list_text": "Smith, J. (2020). Title."},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "report_generation_not_configured",
            "message": "Report generation is not configured.",
            "phase": "phase7",
        }
    }


def test_phase7_text_endpoint_returns_safe_pipeline_errors(monkeypatch, tmp_path):
    def fake_pipeline(**_kwargs):
        raise HostedReportPipelineError(
            phase="phase2",
            code="empty_reference_text",
            message="Hosted report generation failed.",
            http_status=400,
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_text_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
    )
    client = TestClient(app)

    response = client.post(
        "/reports/text",
        json={"reference_list_text": "Raw Reference Title doi:10.1234/private"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "empty_reference_text",
            "message": "Hosted report generation failed.",
            "phase": "phase2",
        }
    }
    assert "Raw Reference Title" not in response.text
    assert "10.1234/private" not in response.text
    assert "localdb" not in response.text


def test_phase7_text_endpoint_rejects_malformed_payload(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
    )
    client = TestClient(app)

    malformed_json = client.post(
        "/reports/text",
        content="{not-json",
        headers={"content-type": "application/json"},
    )
    missing_text = client.post("/reports/text", json={"reference_list_text": 123})

    assert malformed_json.status_code == 400
    assert malformed_json.json() == {
        "error": {
            "code": "invalid_text_report_payload",
            "message": "Invalid text report payload.",
            "phase": "phase7",
        }
    }
    assert missing_text.status_code == 400
    assert missing_text.json() == {
        "error": {
            "code": "invalid_reference_text",
            "message": "Invalid reference text.",
            "phase": "phase2",
        }
    }


def test_phase7_upload_endpoint_requires_server_db_configuration(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="",
    )
    client = TestClient(app)

    response = client.post(
        "/reports/upload",
        files={"file": ("paper.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "report_generation_not_configured",
            "message": "Report generation is not configured.",
            "phase": "phase7",
        }
    }


def test_phase7_upload_endpoint_returns_safe_pipeline_errors(monkeypatch, tmp_path):
    def fake_pipeline(**_kwargs):
        raise HostedReportPipelineError(
            phase="phase1",
            code="invalid_signature",
            message="Hosted report generation failed.",
            http_status=422,
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_hosted_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
    )
    client = TestClient(app)

    response = client.post(
        "/reports/upload",
        files={"file": ("private-paper.pdf", b"nope", "application/pdf")},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "invalid_signature",
            "message": "Hosted report generation failed.",
            "phase": "phase1",
        }
    }
    assert "private-paper" not in response.text
    assert "localdb" not in response.text


def test_phase7_upload_endpoint_rejects_oversized_request_before_pipeline(monkeypatch, tmp_path):
    called = False

    def fake_pipeline(**_kwargs):
        nonlocal called
        called = True
        return HostedReportPipelineResult(
            html="<!doctype html><h1>Reference Report</h1>",
            status="ok",
            reference_count=1,
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_hosted_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
        max_request_bytes=10,
    )
    client = TestClient(app)

    response = client.post(
        "/reports/upload",
        files={"file": ("private-paper.pdf", b"x" * 100, "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json() == {
        "error": {
            "code": "request_too_large",
            "message": "Request body is too large.",
            "phase": "phase7",
        }
    }
    assert called is False
    assert "private-paper" not in response.text
    assert "localdb" not in response.text


def test_phase7_capped_upload_read_rejects_oversized_content(caplog):
    upload = _ChunkedUpload([b"12345", b"67890", b"!"])

    with caplog.at_level(logging.INFO):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(_read_upload_limited(upload, max_bytes=10))  # type: ignore[arg-type]

    assert getattr(exc_info.value, "status_code") == 413
    assert "request_too_large" in caplog.text
    assert "12345" not in caplog.text
    assert "67890" not in caplog.text


def test_phase7_capped_json_read_rejects_oversized_content_without_content_length(caplog):
    request = _ChunkedRequest([b'{"reference_list_text":"', b"x" * 20, b'"}'])

    with caplog.at_level(logging.INFO):
        with pytest.raises(Exception) as exc_info:
            asyncio.run(
                _read_json_limited(  # type: ignore[arg-type]
                    request,
                    max_bytes=10,
                    invalid_code="invalid_text_report_payload",
                    invalid_message="Invalid text report payload.",
                )
            )

    assert getattr(exc_info.value, "status_code") == 413
    assert "request_too_large" in caplog.text
    assert "reference_list_text" not in caplog.text
    assert "xxxxxxxx" not in caplog.text


def test_phase7_capped_json_read_rejects_invalid_payload():
    request = _ChunkedRequest([b'{"reference_list_text":'])

    with pytest.raises(Exception) as exc_info:
        asyncio.run(
            _read_json_limited(  # type: ignore[arg-type]
                request,
                max_bytes=100,
                invalid_code="invalid_text_report_payload",
                invalid_message="Invalid text report payload.",
            )
        )

    assert getattr(exc_info.value, "status_code") == 400
    assert exc_info.value.detail == {
        "code": "invalid_text_report_payload",
        "message": "Invalid text report payload.",
        "phase": "phase7",
    }


def test_phase7_default_cors_is_restrictive(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        cors_allowed_origins=[],
    )
    client = TestClient(app)

    response = client.options(
        "/reports/upload",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert "access-control-allow-origin" not in response.headers


def test_phase7_configured_cors_allows_only_configured_origins(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        cors_allowed_origins=["https://frontend.example"],
    )
    client = TestClient(app)

    allowed = client.options(
        "/reports/upload",
        headers={
            "Origin": "https://frontend.example",
            "Access-Control-Request-Method": "POST",
        },
    )
    blocked = client.options(
        "/reports/upload",
        headers={
            "Origin": "https://other.example",
            "Access-Control-Request-Method": "POST",
        },
    )

    assert allowed.headers["access-control-allow-origin"] == "https://frontend.example"
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in blocked.headers


def test_phase7_post_origin_check_allows_same_origin(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        enable_sanitized_report_endpoint=True,
    )
    client = TestClient(app, base_url="https://app.example")

    response = client.post(
        "/reports",
        json=_report(),
        headers={"Origin": "https://app.example"},
    )

    assert response.status_code == 200


def test_phase7_post_origin_ignores_forwarded_proto_from_untrusted_client(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        enable_sanitized_report_endpoint=True,
        trusted_proxy_cidrs=[],
    )
    client = TestClient(app, base_url="http://app.example")

    response = client.post(
        "/reports",
        json=_report(),
        headers={
            "Origin": "https://app.example",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "invalid_origin"


def test_phase7_post_origin_honors_forwarded_proto_from_trusted_proxy(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        enable_sanitized_report_endpoint=True,
        trusted_proxy_cidrs=["*"],
    )
    client = TestClient(app, base_url="http://app.example")

    response = client.post(
        "/reports",
        json=_report(),
        headers={
            "Origin": "https://app.example",
            "X-Forwarded-Proto": "https",
        },
    )

    assert response.status_code == 200


def test_phase7_post_origin_check_rejects_cross_site_origin(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        enable_sanitized_report_endpoint=True,
    )
    client = TestClient(app, base_url="https://app.example")

    response = client.post(
        "/reports",
        json=_report(),
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json() == {
        "error": {
            "code": "invalid_origin",
            "message": "Invalid request origin.",
            "phase": "phase7",
        }
    }


def test_phase7_host_allowlist_rejects_unexpected_host(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        allowed_hosts=["app.example"],
    )
    client = TestClient(app, base_url="https://other.example")

    response = client.get("/")

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_host",
            "message": "Invalid request host.",
            "phase": "phase7",
        }
    }


def test_phase7_host_allowlist_accepts_public_host_with_forwarded_proto(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        allowed_hosts=["app.example"],
    )
    client = TestClient(app, base_url="http://app.example")

    response = client.get("/", headers={"X-Forwarded-Proto": "https"})

    assert response.status_code == 200


def test_phase7_sanitized_report_endpoint_can_be_disabled(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        enable_sanitized_report_endpoint=False,
    )
    client = TestClient(app)

    response = client.post("/reports", json=_report())

    assert response.status_code == 404


def test_phase7_submissions_disabled_blocks_new_public_work(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        submissions_enabled=False,
    )
    client = TestClient(app)

    frontend = client.get("/")
    response = client.post(
        "/reports/text",
        json={"reference_list_text": "Smith, J. (2020). Title."},
    )

    assert frontend.status_code == 200
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "submissions_disabled",
            "message": "New submissions are temporarily disabled.",
            "phase": "phase7",
        }
    }


def test_phase7_network_quota_survives_cookie_clearing_without_raw_ip(tmp_path):
    security_db = tmp_path / "security.sqlite3"
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        security_state_db_path=security_db,
        enable_sanitized_report_endpoint=True,
        rate_limit_secret="test_rate_limit_secret_with_entropy",
        trusted_proxy_cidrs=["*"],
        rate_limit_max_requests=20,
        network_burst_window_seconds=3600,
        network_burst_max_requests=1,
        network_sustained_window_seconds=3600,
        network_sustained_max_requests=20,
        global_rate_limit_window_seconds=60,
        global_rate_limit_max_requests=20,
        challenge_mode="off",
    )

    first_client = TestClient(app)
    second_client = TestClient(app)
    first = first_client.post(
        "/reports",
        json=_report(),
        headers={"X-Forwarded-For": "203.0.113.9"},
    )
    second = second_client.post(
        "/reports",
        json=_report(),
        headers={"X-Forwarded-For": "203.0.113.9"},
    )

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["error"]["code"] == "rate_limited"

    with sqlite3.connect(security_db) as conn:
        stored_values = "\n".join(
            str(value)
            for row in conn.execute(
                "SELECT bucket_name, subject_key FROM rate_limit_windows"
            )
            for value in row
        )

    assert "203.0.113.9" not in stored_values
    assert "203.0.113" not in stored_values
    assert "network_full_burst" in stored_values
    assert "hmac_" in stored_values


def test_phase7_worker_backend_rejects_when_global_queue_is_full(tmp_path):
    app = create_app(
        ReportServingConfig(
            report_dir=tmp_path / "reports",
            job_dir=tmp_path / "jobs",
            ttl_seconds=60,
        ),
        security_state_db_path=tmp_path / "security.sqlite3",
        execution_backend_mode="worker",
        enable_sanitized_report_endpoint=True,
        max_queued_jobs=1,
        rate_limit_secret="test_rate_limit_secret_with_entropy",
        rate_limit_max_requests=20,
        network_burst_max_requests=20,
        network_sustained_max_requests=20,
        global_rate_limit_max_requests=20,
    )
    client = TestClient(app)

    first = client.post("/reports", json=_report())
    second = client.post("/reports", json=_report())

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {
        "error": {
            "code": "too_many_queued_jobs",
            "message": "Too many jobs are queued right now.",
            "phase": "phase7",
        }
    }


def test_phase7_challenge_endpoint_issues_local_pow_challenge(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        security_state_db_path=tmp_path / "security.sqlite3",
        rate_limit_secret="test_rate_limit_secret_with_entropy",
        challenge_max_number=50,
    )
    client = TestClient(app)

    response = client.get("/challenge")

    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm"] == "SHA-256"
    assert isinstance(payload["challenge"], str)
    assert payload["maxnumber"] == 50
    assert str(payload["salt"]).startswith("refgen2:")
    assert isinstance(payload["signature"], str)


def test_phase7_challenge_required_then_valid_solution_allows_submission(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        security_state_db_path=tmp_path / "security.sqlite3",
        enable_sanitized_report_endpoint=True,
        rate_limit_secret="test_rate_limit_secret_with_entropy",
        challenge_mode="always",
        challenge_max_number=50,
    )
    client = TestClient(app)

    blocked = client.post("/reports", json=_report())
    challenge = client.get("/challenge").json()
    solved = _solve_challenge(challenge)
    accepted = client.post("/reports", json={**_report(), "altcha": solved})
    replay = client.post("/reports", json={**_report(), "altcha": solved})

    assert blocked.status_code == 429
    assert blocked.json() == {
        "error": {
            "code": "challenge_required",
            "message": "Verification is required.",
            "phase": "phase7",
        }
    }
    assert accepted.status_code == 200
    assert replay.status_code == 429
    assert replay.json()["error"]["code"] == "challenge_required"


def test_phase7_session_cookie_flags_can_be_overridden_for_local_dev(tmp_path):
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        session_cookie_secure=False,
        session_cookie_samesite="strict",
    )
    client = TestClient(app)

    response = client.post("/reports", json=_report())

    assert response.status_code == 200
    set_cookie = response.headers["set-cookie"].lower()
    assert "httponly" in set_cookie
    assert "secure" not in set_cookie
    assert "samesite=strict" in set_cookie


def test_phase7_error_responses_include_no_store_security_headers(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.get("/jobs/not-a-job-id")

    assert response.status_code == 400
    assert response.headers["cache-control"] == "no-store, no-cache, must-revalidate, private"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["expires"] == "0"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_phase7_malformed_json_uses_safe_error_shape(tmp_path):
    app = create_app(ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60))
    client = TestClient(app)

    response = client.post(
        "/reports",
        content="{not-json",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "invalid_report_payload",
            "message": "Invalid report payload.",
            "phase": "phase7",
        }
    }


def test_phase7_error_logs_omit_sensitive_values(monkeypatch, caplog, tmp_path):
    sensitive_values = [
        "private-paper.pdf",
        "/private/localdb.sqlite",
        "Raw Reference Title",
        "10.1234/private-doi",
        "https://example.test/private",
    ]

    def fake_pipeline(**_kwargs):
        raise HostedReportPipelineError(
            phase="phase1",
            code="invalid_signature",
            message="Hosted report generation failed.",
            http_status=422,
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_hosted_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
    )
    client = TestClient(app)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/reports/upload",
            files={
                "file": (
                    "private-paper.pdf",
                    b"Raw Reference Title doi:10.1234/private-doi https://example.test/private",
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 422
    assert "phase7.api_error" in caplog.text
    for value in sensitive_values:
        assert value not in response.text
        assert value not in caplog.text


def test_phase7_text_error_logs_omit_sensitive_values(monkeypatch, caplog, tmp_path):
    sensitive_values = [
        "/private/localdb.sqlite",
        "Raw Reference Title",
        "10.1234/private-doi",
        "https://example.test/private",
    ]

    def fake_pipeline(**_kwargs):
        raise HostedReportPipelineError(
            phase="phase2",
            code="reference_text_invalid_characters",
            message="Hosted report generation failed.",
            http_status=400,
        )

    monkeypatch.setattr("reference_gen2.report_serving.runner.run_text_report_pipeline", fake_pipeline)
    app = create_app(
        ReportServingConfig(report_dir=tmp_path / "reports", ttl_seconds=60),
        db_path="/private/localdb.sqlite",
    )
    client = TestClient(app)

    with caplog.at_level(logging.INFO):
        response = client.post(
            "/reports/text",
            json={
                "reference_list_text": (
                    "Raw Reference Title doi:10.1234/private-doi "
                    "https://example.test/private"
                )
            },
        )

    assert response.status_code == 400
    assert "phase7.api_error" in caplog.text
    for value in sensitive_values:
        assert value not in response.text
        assert value not in caplog.text
