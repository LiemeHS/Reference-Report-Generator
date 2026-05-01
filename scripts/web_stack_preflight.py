#!/usr/bin/env python3
"""Fail-fast preflight checks for the FastAPI/Starlette runtime stack."""

from __future__ import annotations

import sys
from dataclasses import dataclass

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

import importlib.metadata


class WebStackPreflightError(RuntimeError):
    """Raised when a required dependency compatibility invariant is violated."""


@dataclass(frozen=True)
class DependencyConstraint:
    dependent: str
    dependency: str
    constraint: SpecifierSet


def _read_dependency_constraints(package: str, target: str) -> SpecifierSet:
    """Return the version constraint that package places on target."""

    try:
        requirements = importlib.metadata.requires(package) or []
    except importlib.metadata.PackageNotFoundError:
        raise WebStackPreflightError(f"Required package {package!r} is not installed.")

    for raw in requirements:
        parsed = Requirement(raw)
        if parsed.name.lower() != target.lower():
            continue
        if parsed.marker is not None and not parsed.marker.evaluate():
            continue
        return parsed.specifier
    return SpecifierSet("")


def _installed_version(package: str) -> Version:
    """Return installed package version as a Version object."""

    try:
        return Version(importlib.metadata.version(package))
    except importlib.metadata.PackageNotFoundError as exc:
        raise WebStackPreflightError(f"Required package {package!r} is not installed.") from exc


def _check_constraint(constraint: DependencyConstraint) -> None:
    installed = _installed_version(constraint.dependency)
    if constraint.constraint and installed not in constraint.constraint:
        raise WebStackPreflightError(
            f"{constraint.dependency} {installed} does not satisfy "
            f"{constraint.dependent} requirement {constraint.constraint}."
        )


def assert_fastapi_starlette_compatibility() -> None:
    """Validate FastAPI's declared Starlette requirement is currently satisfied."""

    fastapi_version = _installed_version("fastapi")
    starlette_version = _installed_version("starlette")
    constraint = _read_dependency_constraints("fastapi", "starlette")

    if constraint:
        _check_constraint(
            DependencyConstraint(
                dependent="fastapi",
                dependency="starlette",
                constraint=constraint,
            )
        )

    if fastapi_version.release[0] == 0 and starlette_version.release[0] >= 1:
        raise WebStackPreflightError(
            "Detected starlette >=1.0 with fastapi major-version 0.x. This pairing is a known "
            "compatibility break for this project and commonly hangs TestClient request dispatch."
        )


def assert_web_stack_is_compatible() -> None:
    """Assert compatible versions for the deployed FastAPI adapter stack."""

    assert_fastapi_starlette_compatibility()


def main() -> int:
    try:
        assert_web_stack_is_compatible()
    except WebStackPreflightError as exc:
        print(f"Preflight failed: {exc}", file=sys.stderr)
        return 1
    print("Web stack preflight passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
