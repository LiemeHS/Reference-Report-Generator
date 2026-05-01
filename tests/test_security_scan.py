from __future__ import annotations

import logging
import subprocess
from types import SimpleNamespace

import pytest

from reference_gen2.security.file_validation import UploadValidationError, ValidatedUpload
from reference_gen2.security.security_scan import run_upload_security_scan


def _validated_upload() -> ValidatedUpload:
    return ValidatedUpload(
        original_filename="paper.pdf",
        normalized_filename="paper.pdf",
        detected_kind="pdf",
        declared_mime="application/pdf",
        size_bytes=32,
    )


def test_security_scan_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ENABLED", False)
    run_upload_security_scan(_validated_upload(), b"%PDF-1.4\n")


def test_security_scan_requires_executable_when_enabled(monkeypatch):
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ENABLED", True)
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_EXECUTABLE", "")
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ARGS", [])

    with pytest.raises(UploadValidationError) as exc:
        run_upload_security_scan(_validated_upload(), b"%PDF-1.4\n")

    assert exc.value.code == "security_scan_unconfigured"


def test_security_scan_invokes_subprocess_without_shell(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run(command, **kwargs):
        captured["command"] = command
        captured.update(kwargs)
        return SimpleNamespace(returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ENABLED", True)
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_EXECUTABLE", "scanner-bin")
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ARGS", ["--mode", "stdin"])
    monkeypatch.setattr("reference_gen2.security.security_scan.subprocess.run", _fake_run)

    run_upload_security_scan(_validated_upload(), b"%PDF-1.4\n")

    assert captured["command"] == ["scanner-bin", "--mode", "stdin"]
    assert captured["shell"] is False


def test_security_scan_timeout_raises_structured_code(monkeypatch):
    def _fake_run(*_args, **_kwargs):
        raise subprocess.TimeoutExpired(cmd=["scanner-bin"], timeout=3)

    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ENABLED", True)
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_EXECUTABLE", "scanner-bin")
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ARGS", [])
    monkeypatch.setattr("reference_gen2.security.security_scan.subprocess.run", _fake_run)

    with pytest.raises(UploadValidationError) as exc:
        run_upload_security_scan(_validated_upload(), b"%PDF-1.4\n")

    assert exc.value.code == "security_scan_timeout"


def test_security_scan_rejection_logs_metadata_only(monkeypatch, caplog):
    def _fake_run(*_args, **_kwargs):
        return SimpleNamespace(returncode=7, stdout=b"", stderr=b"denied")

    monkeypatch.setattr("reference_gen2.security.security_scan.LOG_ENABLED", True)
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ENABLED", True)
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_EXECUTABLE", "scanner-bin")
    monkeypatch.setattr("reference_gen2.security.security_scan.SECURITY_SCAN_ARGS", [])
    monkeypatch.setattr("reference_gen2.security.security_scan.subprocess.run", _fake_run)

    with caplog.at_level(logging.WARNING, logger="reference_gen2.security.security_scan"):
        with pytest.raises(UploadValidationError) as exc:
            run_upload_security_scan(_validated_upload(), b"%PDF-1.4\nsensitive")

    assert exc.value.code == "security_scan_rejected"
    joined = "\n".join(record.getMessage() for record in caplog.records)
    assert "phase1.security_scan_rejected" in joined
    assert "size_bytes=32" in joined
    assert "sensitive" not in joined
