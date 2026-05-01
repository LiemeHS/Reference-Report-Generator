from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from scripts.web_stack_preflight import WebStackPreflightError
from scripts import web_stack_preflight


def test_web_stack_preflight_fastapi_starlette_compatibility(monkeypatch) -> None:
    """Fail quickly when FastAPI/Starlette dependency metadata is incompatible."""

    monkeypatch.setattr(
        web_stack_preflight.importlib.metadata,
        "version",
        lambda package: {
            "fastapi": "0.136.0",
            "starlette": "0.46.0",
        }[package],
    )
    monkeypatch.setattr(
        web_stack_preflight.importlib.metadata,
        "requires",
        lambda package: {
            "fastapi": ["starlette>=0.46.0"],
            "starlette": [],
        }[package],
    )

    web_stack_preflight.assert_web_stack_is_compatible()


def test_web_stack_preflight_blocks_fastapi_0_with_starlette_1_x(monkeypatch) -> None:
    """Treat FastAPI 0.x paired with Starlette 1.x as an unsafe environment."""
    monkeypatch.setattr(
        web_stack_preflight.importlib.metadata,
        "version",
        lambda package: {
            "fastapi": "0.136.0",
            "starlette": "1.0.0",
        }[package],
    )
    monkeypatch.setattr(
        web_stack_preflight.importlib.metadata,
        "requires",
        lambda package: {
            "fastapi": ["starlette>=1.0.0"],
            "starlette": [],
        }[package],
    )

    with pytest.raises(WebStackPreflightError, match="compatibility"):
        web_stack_preflight.assert_web_stack_is_compatible()
