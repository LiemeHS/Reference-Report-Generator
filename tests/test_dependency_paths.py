from __future__ import annotations

import json
from email.message import Message
from pathlib import Path
import sys

from scripts import dependency_paths


class _FakeDistribution:
    def __init__(
        self,
        name: str,
        requires: list[str] | None = None,
        requires_dist: list[str] | None = None,
    ):
        self.name = name
        self.metadata = Message()
        self.metadata["Name"] = name
        self.requires = requires_dist if requires_dist is not None else requires or []


def _write_manifests(tmp_path: Path) -> tuple[Path, Path]:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "reference-gen2"
version = "0.1.0"
dependencies = ["fastapi"]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text(
        "fastapi\npython-multipart\n",
        encoding="utf-8",
    )
    return pyproject_path, requirements_path


def _fake_install() -> dict[str, _FakeDistribution]:
    return {
        "reference-gen2": _FakeDistribution(
            "reference-gen2",
            requires_dist=["fastapi>=0.136.0"],
        ),
        "fastapi": _FakeDistribution(
            "fastapi",
            requires_dist=["starlette>=1.0.0", "python-multipart>=0.0.26", "pydantic>=2.0.0"],
        ),
        "starlette": _FakeDistribution("starlette"),
        "python-multipart": _FakeDistribution("python-multipart"),
        "pydantic": _FakeDistribution("pydantic"),
        "setuptools": _FakeDistribution("setuptools"),
        "wheel": _FakeDistribution("wheel"),
    }


def test_dependency_paths_follows_dependency_chain(monkeypatch, tmp_path: Path) -> None:
    pyproject_path, requirements_path = _write_manifests(tmp_path)
    installed = _fake_install()

    monkeypatch.setattr(dependency_paths.dependency_audit.metadata, "distributions", lambda: list(installed.values()))
    report = dependency_paths.build_dependency_path_report(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
        vulnerability_report_path=None,
        requested_packages=["python-multipart", "pydantic"],
        max_depth=6,
        max_paths=5,
    )

    paths = {item.package: item for item in report.package_paths}
    assert paths["python-multipart"].status == "found"
    assert ["python-multipart"] in paths["python-multipart"].paths
    assert ["fastapi", "python-multipart"] in paths["python-multipart"].paths
    assert paths["pydantic"].paths == [["fastapi", "pydantic"]]


def test_dependency_paths_from_vulnerability_report(monkeypatch, tmp_path: Path) -> None:
    pyproject_path, requirements_path = _write_manifests(tmp_path)
    installed = _fake_install()

    monkeypatch.setattr(dependency_paths.dependency_audit.metadata, "distributions", lambda: list(installed.values()))

    vuln_report_path = tmp_path / "vuln.json"
    vuln_report_path.write_text(
        json.dumps(
            {
                "packages": [
                    {"name": "python-multipart", "advisories": [{"id": "GHSA-test"}]},
                    {"name": "starlette", "advisories": []},
                ]
            }
        ),
        encoding="utf-8",
    )
    report = dependency_paths.build_dependency_path_report(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
        vulnerability_report_path=vuln_report_path,
        requested_packages=[],
        max_depth=6,
        max_paths=5,
    )

    assert [path.package for path in report.package_paths] == ["python-multipart"]
    assert ["python-multipart"] in report.package_paths[0].paths
    assert ["fastapi", "python-multipart"] in report.package_paths[0].paths


def test_dependency_paths_missing_package_is_reported(monkeypatch, tmp_path: Path) -> None:
    pyproject_path, requirements_path = _write_manifests(tmp_path)
    installed = _fake_install()
    del installed["python-multipart"]

    monkeypatch.setattr(dependency_paths.dependency_audit.metadata, "distributions", lambda: list(installed.values()))
    report = dependency_paths.build_dependency_path_report(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
        vulnerability_report_path=None,
        requested_packages=["python-multipart"],
        max_depth=6,
        max_paths=5,
    )

    item = report.package_paths[0]
    assert item.status == "not_installed"
    assert item.paths == []


def test_main_writes_json_report(monkeypatch, tmp_path: Path) -> None:
    output_path = tmp_path / "dependency_paths_report.json"
    fake_report = dependency_paths.DependencyPathReport(
        generated_from=["pyproject.toml", "requirements.txt"],
        query={"targets": ["python-multipart"]},
        package_paths=[
            dependency_paths.PackageDependencyPath(
                package="python-multipart",
                canonical="python-multipart",
                status="found",
                paths=[["fastapi", "python-multipart"]],
            ),
        ],
    )

    monkeypatch.setattr(
        dependency_paths,
        "build_dependency_path_report",
        lambda **kwargs: fake_report,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "dependency_paths.py",
            "--json-output",
            str(output_path),
        ],
    )

    exit_code = dependency_paths.main()

    assert exit_code == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["package_paths"][0]["canonical"] == "python-multipart"
