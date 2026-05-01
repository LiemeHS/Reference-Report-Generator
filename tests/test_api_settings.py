from __future__ import annotations

import importlib

import pytest

import reference_gen2.api.settings as api_settings


def _reload_settings():
    return importlib.reload(api_settings)


def _set_valid_production_env(monkeypatch):
    monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "1")
    monkeypatch.setenv(
        "REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET",
        "reference_gen2_test_secret_with_enough_entropy",
    )
    monkeypatch.setenv(
        "REFERENCE_GEN2_API_RATE_LIMIT_SECRET",
        "reference_gen2_test_rate_limit_secret",
    )
    monkeypatch.setenv("REFERENCE_GEN2_API_ALLOWED_HOSTS", "app.example")
    monkeypatch.setenv("REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS", "https://app.example")
    monkeypatch.setenv("REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS", "172.20.0.0/24")
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK", "1")


def test_api_settings_reject_default_ownership_secret_in_production(monkeypatch):
    monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "1")
    monkeypatch.delenv("REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET", raising=False)

    try:
        with pytest.raises(SystemExit, match="non-default"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_accept_non_default_ownership_secret_in_production(monkeypatch):
    secret = "reference_gen2_test_secret_with_enough_entropy"
    rate_secret = "reference_gen2_test_rate_limit_secret"
    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv("REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET", secret)
    monkeypatch.setenv("REFERENCE_GEN2_API_RATE_LIMIT_SECRET", rate_secret)

    try:
        settings = _reload_settings()
        assert settings.API_PRODUCTION_MODE is True
        assert settings.REPORT_SERVING_OWNERSHIP_SECRET == secret
        assert settings.API_RATE_LIMIT_SECRET == rate_secret
        assert settings.API_ENABLE_SANITIZED_REPORT_ENDPOINT is False
        assert settings.API_ALLOWED_HOSTS == ["app.example"]
        assert settings.API_POST_ALLOWED_ORIGINS == ["https://app.example"]
        assert settings.SECURITY_SCAN_ACCEPT_RISK is True
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_reject_default_rate_limit_secret_in_production(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.delenv("REFERENCE_GEN2_API_RATE_LIMIT_SECRET", raising=False)

    try:
        with pytest.raises(SystemExit, match="API_RATE_LIMIT_SECRET"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        monkeypatch.delenv("REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET", raising=False)
        _reload_settings()


def test_api_settings_reject_shared_public_secrets_in_production(monkeypatch):
    secret = "reference_gen2_test_shared_secret"
    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv("REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET", secret)
    monkeypatch.setenv("REFERENCE_GEN2_API_RATE_LIMIT_SECRET", secret)

    try:
        with pytest.raises(SystemExit, match="separate"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        monkeypatch.delenv("REFERENCE_GEN2_REPORT_SERVING_OWNERSHIP_SECRET", raising=False)
        monkeypatch.delenv("REFERENCE_GEN2_API_RATE_LIMIT_SECRET", raising=False)
        _reload_settings()


def test_api_settings_reject_empty_allowed_hosts_in_production(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.delenv("REFERENCE_GEN2_API_ALLOWED_HOSTS", raising=False)

    try:
        with pytest.raises(SystemExit, match="API_ALLOWED_HOSTS"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_reject_empty_post_origins_for_production_submissions(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.delenv("REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS", raising=False)

    try:
        with pytest.raises(SystemExit, match="API_POST_ALLOWED_ORIGINS"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_allow_empty_post_origins_when_submissions_disabled(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.delenv("REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv("REFERENCE_GEN2_API_SUBMISSIONS_ENABLED", "0")

    try:
        settings = _reload_settings()
        assert settings.API_PRODUCTION_MODE is True
        assert settings.API_SUBMISSIONS_ENABLED is False
        assert settings.API_POST_ALLOWED_ORIGINS == []
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_reject_empty_trusted_proxy_cidrs_in_production(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.delenv("REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS", raising=False)

    try:
        with pytest.raises(SystemExit, match="TRUSTED_PROXY_CIDRS"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


@pytest.mark.parametrize(
    "broad_range",
    ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "0.0.0.0/0", "::/0"],
)
def test_api_settings_reject_broad_trusted_proxy_cidrs_in_production(
    monkeypatch,
    broad_range,
):
    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv("REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS", broad_range)

    try:
        with pytest.raises(SystemExit, match="broad"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_reject_production_submissions_without_scan_or_risk_acceptance(
    monkeypatch,
):
    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK", "0")
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ENABLED", "0")

    try:
        with pytest.raises(SystemExit, match="SECURITY_SCAN"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_reject_enabled_security_scan_without_executable(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK", "0")
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ENABLED", "1")
    monkeypatch.delenv("REFERENCE_GEN2_SECURITY_SCAN_EXECUTABLE", raising=False)

    try:
        with pytest.raises(SystemExit, match="SECURITY_SCAN_EXECUTABLE"):
            _reload_settings()
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_accept_enabled_security_scan_with_executable(monkeypatch):
    _set_valid_production_env(monkeypatch)
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ACCEPT_RISK", "0")
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_ENABLED", "1")
    monkeypatch.setenv("REFERENCE_GEN2_SECURITY_SCAN_EXECUTABLE", "clamscan")

    try:
        settings = _reload_settings()
        assert settings.SECURITY_SCAN_ENABLED is True
        assert settings.SECURITY_SCAN_EXECUTABLE == "clamscan"
        assert settings.SECURITY_SCAN_ACCEPT_RISK is False
    finally:
        monkeypatch.setenv("REFERENCE_GEN2_PRODUCTION", "0")
        _reload_settings()


def test_api_settings_parse_public_host_and_origin_allowlists(monkeypatch):
    monkeypatch.setenv("REFERENCE_GEN2_API_ALLOWED_HOSTS", "App.Example,localhost:8000")
    monkeypatch.setenv(
        "REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS",
        "https://App.Example, http://localhost:8000",
    )

    try:
        settings = _reload_settings()
        assert settings.API_ALLOWED_HOSTS == ["app.example", "localhost:8000"]
        assert settings.API_POST_ALLOWED_ORIGINS == [
            "https://app.example",
            "http://localhost:8000",
        ]
    finally:
        monkeypatch.delenv("REFERENCE_GEN2_API_ALLOWED_HOSTS", raising=False)
        monkeypatch.delenv("REFERENCE_GEN2_API_POST_ALLOWED_ORIGINS", raising=False)
        _reload_settings()


def test_api_settings_parse_trusted_proxy_cidrs(monkeypatch):
    monkeypatch.setenv(
        "REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS",
        "10.0.0.1, 172.16.0.0/12",
    )

    try:
        settings = _reload_settings()
        assert settings.API_TRUSTED_PROXY_CIDRS == ["10.0.0.1/32", "172.16.0.0/12"]
    finally:
        monkeypatch.delenv("REFERENCE_GEN2_API_TRUSTED_PROXY_CIDRS", raising=False)
        _reload_settings()
