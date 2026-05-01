from __future__ import annotations

import json
from email.message import Message
from pathlib import Path

from scripts import dependency_audit


class _FakeDistribution:
    def __init__(
        self,
        name: str,
        version: str,
        *,
        requires: list[str] | None = None,
        license_value: str | None = None,
        license_expression: str | None = None,
        classifiers: list[str] | None = None,
        homepage: str | None = None,
    ):
        self.name = name
        self.version = version
        self.requires = requires or []
        self.metadata = Message()
        self.metadata["Name"] = name
        if license_value is not None:
            self.metadata["License"] = license_value
        if license_expression is not None:
            self.metadata["License-Expression"] = license_expression
        if homepage is not None:
            self.metadata["Home-page"] = homepage
        for classifier in classifiers or []:
            self.metadata.add_header("Classifier", classifier)


def _write_manifest_files(tmp_path: Path) -> tuple[Path, Path]:
    pyproject_path = tmp_path / "pyproject.toml"
    pyproject_path.write_text(
        """
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "reference-gen2"
version = "0.1.0"
dependencies = [
  "pdfplumber",
  "python-docx",
]

[project.optional-dependencies]
test = [
  "pytest",
]
""".strip()
        + "\n",
        encoding="utf-8",
    )
    requirements_path = tmp_path / "requirements.txt"
    requirements_path.write_text("-c constraints.txt\npdfplumber\npython-docx\n", encoding="utf-8")
    return pyproject_path, requirements_path


def test_load_dependency_manifests_extracts_expected_scopes(tmp_path: Path):
    pyproject_path, requirements_path = _write_manifest_files(tmp_path)

    pyproject_dependencies = dependency_audit._load_pyproject_dependencies(pyproject_path)
    requirements_dependencies = dependency_audit._load_requirements_dependencies(requirements_path)

    assert pyproject_dependencies["pdfplumber"]["scope"] == {"runtime"}
    assert pyproject_dependencies["pytest"]["scope"] == {"dev"}
    assert pyproject_dependencies["setuptools"]["scope"] == {"build"}
    assert requirements_dependencies["python-docx"]["source"] == {"requirements.txt"}
    assert "-c" not in requirements_dependencies


def test_normalize_license_value_maps_common_variants():
    assert dependency_audit.normalize_license_value("MIT License") == "MIT"
    assert dependency_audit.normalize_license_value("BSD License") == "BSD-3-Clause"
    assert dependency_audit.normalize_license_value("Apache Software License") == "Apache-2.0"
    assert dependency_audit.normalize_license_value("") is None
    assert dependency_audit.normalize_license_value("Custom Research License") == "Custom Research License"


def test_classify_license_value_handles_allowed_and_review_states():
    assert dependency_audit.classify_license_value("MIT") == (
        "allowed",
        "allowed_open_source_license:MIT",
    )
    assert dependency_audit.classify_license_value(None) == (
        "review_required",
        "missing_or_unknown_license_metadata",
    )
    assert dependency_audit.classify_license_value("GPL") == (
        "review_required",
        "copyleft_or_restricted_review_required:GPL",
    )


def test_classify_license_value_allows_safe_spdx_or_expressions():
    assert dependency_audit.classify_license_value("Apache-2.0 OR BSD-3-Clause") == (
        "allowed",
        "allowed_open_source_spdx_or:Apache-2.0 OR BSD-3-Clause",
    )
    assert dependency_audit.classify_license_value("Apache-2.0 OR BSD-2-Clause") == (
        "allowed",
        "allowed_open_source_spdx_or:Apache-2.0 OR BSD-2-Clause",
    )


def test_classify_license_value_keeps_unreviewed_aliases_and_compound_prose_visible():
    assert dependency_audit.classify_license_value("PSF-2.0") == (
        "review_required",
        "custom_or_unreviewed_license:PSF-2.0",
    )
    assert dependency_audit.classify_license_value("MIT-CMU") == (
        "review_required",
        "custom_or_unreviewed_license:MIT-CMU",
    )
    assert dependency_audit.classify_license_value("BSD-3-Clause, Apache-2.0, dependency licenses") == (
        "review_required",
        "custom_or_unreviewed_license:BSD-3-Clause, Apache-2.0, dependency licenses",
    )
    assert dependency_audit.classify_license_value("MIT OR GPL") == (
        "review_required",
        "copyleft_or_restricted_review_required:MIT OR GPL",
    )


def test_license_from_distribution_uses_license_expression_metadata():
    dist = _FakeDistribution("fastapi", "0.136.0", license_expression="MIT")

    assert dependency_audit._license_from_distribution(dist) == "MIT"
    assert dependency_audit.classify_license_value(
        dependency_audit._license_from_distribution(dist)
    ) == (
        "allowed",
        "allowed_open_source_license:MIT",
    )


def test_build_dependency_audit_report_covers_python_and_anystyle(
    monkeypatch,
    tmp_path: Path,
):
    pyproject_path, requirements_path = _write_manifest_files(tmp_path)
    installed = {
        "pdfplumber": _FakeDistribution(
            "pdfplumber",
            "0.11.9",
            requires=["pdfminer.six>=20231228"],
            classifiers=["License :: OSI Approved :: BSD License"],
            homepage="https://github.com/jsvine/pdfplumber",
        ),
        "python-docx": _FakeDistribution(
            "python-docx",
            "1.2.0",
            license_value="MIT",
            homepage="https://github.com/python-openxml/python-docx",
        ),
        "pytest": _FakeDistribution(
            "pytest",
            "9.0.3",
            classifiers=["License :: OSI Approved :: MIT License"],
            homepage="https://docs.pytest.org/",
        ),
        "setuptools": _FakeDistribution(
            "setuptools",
            "82.0.1",
            classifiers=["License :: OSI Approved :: MIT License"],
        ),
        "wheel": _FakeDistribution(
            "wheel",
            "0.46.3",
            license_value="MIT",
        ),
        "pdfminer-six": _FakeDistribution(
            "pdfminer.six",
            "20231228",
            license_value="MIT",
        ),
    }

    monkeypatch.setattr(
        dependency_audit.metadata,
        "distributions",
        lambda: list(installed.values()),
    )
    monkeypatch.setattr(dependency_audit.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        dependency_audit,
        "_anystyle_metadata",
        lambda _command: (
            True,
            {
                "version": "1.6.0",
                "homepage": "http://anystyle.io",
                "license": "BSD-2-Clause",
            },
        ),
    )

    report = dependency_audit.build_dependency_audit_report(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
    )

    names = {record.name: record for record in report.dependencies}
    assert "pdfplumber" in names
    assert "python-docx" in names
    assert "pytest" in names
    assert "setuptools" in names
    assert "wheel" in names
    assert "anystyle" in names
    assert "pdfminer.six" in names
    assert names["pdfminer.six"].scope == "transitive"
    assert names["anystyle"].status == "allowed"
    assert names["python-docx"].status == "allowed"


def test_build_dependency_audit_report_marks_missing_direct_and_transitive_dependencies(
    monkeypatch,
    tmp_path: Path,
):
    pyproject_path, requirements_path = _write_manifest_files(tmp_path)
    installed = {
        "pdfplumber": _FakeDistribution(
            "pdfplumber",
            "0.11.9",
            requires=["pdfminer.six>=20231228"],
            license_value="MIT",
        ),
        "setuptools": _FakeDistribution("setuptools", "82.0.1", license_value="MIT"),
        "wheel": _FakeDistribution("wheel", "0.46.3", license_value="MIT"),
    }

    monkeypatch.setattr(
        dependency_audit.metadata,
        "distributions",
        lambda: list(installed.values()),
    )
    monkeypatch.setattr(dependency_audit.shutil, "which", lambda _name: None)

    report = dependency_audit.build_dependency_audit_report(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
    )

    names = {record.name: record for record in report.dependencies}
    assert names["python-docx"].status == "not_installed"
    assert names["pytest"].status == "not_installed"
    assert names["pdfminer.six"].status == "missing"
    assert names["anystyle"].status == "not_installed"


def test_report_to_dict_includes_summary(monkeypatch, tmp_path: Path):
    pyproject_path, requirements_path = _write_manifest_files(tmp_path)
    installed = {
        "pdfplumber": _FakeDistribution("pdfplumber", "0.11.9", license_value="MIT"),
        "python-docx": _FakeDistribution("python-docx", "1.2.0", license_value="MIT"),
        "pytest": _FakeDistribution("pytest", "9.0.3", license_value="MIT"),
        "setuptools": _FakeDistribution("setuptools", "82.0.1", license_value="MIT"),
        "wheel": _FakeDistribution("wheel", "0.46.3", license_value="MIT"),
    }

    monkeypatch.setattr(
        dependency_audit.metadata,
        "distributions",
        lambda: list(installed.values()),
    )
    monkeypatch.setattr(dependency_audit.shutil, "which", lambda _name: None)

    report = dependency_audit.build_dependency_audit_report(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
    )
    payload = dependency_audit.report_to_dict(report)

    assert "dependencies" in payload
    assert "summary" in payload
    assert "total_dependencies" in payload["summary"]


def test_main_writes_json_report(monkeypatch, tmp_path: Path):
    output_path = tmp_path / "dependency_audit.json"

    fake_report = dependency_audit.DependencyAuditReport(
        generated_from=["pyproject.toml", "requirements.txt"],
        policy={"allowed_licenses": ["MIT"]},
        dependencies=[
            dependency_audit.DependencyRecord(
                name="pdfplumber",
                ecosystem="python",
                scope="runtime",
                version="0.11.9",
                source="pyproject",
                license="MIT",
                homepage="https://example.invalid",
                status="allowed",
                reason="allowed_open_source_license:MIT",
            )
        ],
    )

    monkeypatch.setattr(
        dependency_audit,
        "build_dependency_audit_report",
        lambda pyproject_path, requirements_path: fake_report,
    )
    monkeypatch.setattr(
        dependency_audit,
        "format_summary",
        lambda report: "Reference_Gen2 dependency audit\nAllowed: 1",
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "dependency_audit.py",
            "--json-output",
            str(output_path),
        ],
    )

    exit_code = dependency_audit.main()

    assert exit_code == 0
    written = json.loads(output_path.read_text(encoding="utf-8"))
    assert written["dependencies"][0]["name"] == "pdfplumber"
    assert written["summary"]["status_counts"]["allowed"] == 1
