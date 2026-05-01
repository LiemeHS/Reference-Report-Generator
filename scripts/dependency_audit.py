from __future__ import annotations

import argparse
import importlib.metadata as metadata
import json
import os
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

import tomllib

try:
    from packaging.markers import default_environment
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - fallback only when packaging is unavailable
    Requirement = None
    default_environment = None


ALLOWED_LICENSES = {
    "MIT",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "Apache-2.0",
    "ISC",
    "Python-2.0",
    "PSF",
    "MPL-2.0",
}
REVIEW_REQUIRED_LICENSE_PATTERNS = (
    "gpl",
    "agpl",
    "lgpl",
    "copyleft",
)
KNOWN_EXTERNAL_TOOLS = {
    "anystyle": {
        "ecosystem": "ruby_cli",
        "scope": "external_tool",
        "source": "manual_tool_registry",
        "metadata_command": ["bash", "-lc", "gem list ^anystyle$ -d"],
        "executable_env": "REFERENCE_GEN2_ANYSTYLE_EXECUTABLE",
    }
}
KNOWN_BUILD_TOOLS = {
    "setuptools": "manual_tool_registry",
    "wheel": "manual_tool_registry",
}
LICENSE_SYNONYMS = {
    "mit license": "MIT",
    "mit": "MIT",
    "bsd license": "BSD-3-Clause",
    "new bsd license": "BSD-3-Clause",
    "simplified bsd license": "BSD-2-Clause",
    "bsd-2-clause": "BSD-2-Clause",
    "bsd-3-clause": "BSD-3-Clause",
    "apache software license": "Apache-2.0",
    "apache 2.0": "Apache-2.0",
    "apache-2.0": "Apache-2.0",
    "isc license": "ISC",
    "isc": "ISC",
    "python software foundation license": "PSF",
    "psf": "PSF",
    "mpl 2.0": "MPL-2.0",
    "mpl-2.0": "MPL-2.0",
}
_SPDX_OR_RE = re.compile(r"\s+OR\s+")
LICENSE_CLASSIFIER_MAP = {
    "License :: OSI Approved :: MIT License": "MIT",
    "License :: OSI Approved :: BSD License": "BSD-3-Clause",
    'License :: OSI Approved :: Apache Software License': "Apache-2.0",
    "License :: OSI Approved :: ISC License (ISCL)": "ISC",
    "License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0)": "MPL-2.0",
    "License :: OSI Approved :: Python Software Foundation License": "PSF",
    "License :: OSI Approved :: GNU General Public License (GPL)": "GPL",
    "License :: OSI Approved :: GNU Lesser General Public License v3 (LGPLv3)": "LGPL",
    "License :: OSI Approved :: GNU Affero General Public License v3": "AGPL",
}


@dataclass(frozen=True)
class DependencyRecord:
    name: str
    ecosystem: str
    scope: str
    version: str | None
    source: str
    license: str | None
    homepage: str | None
    status: str
    reason: str


@dataclass(frozen=True)
class DependencyAuditReport:
    generated_from: list[str] = field(default_factory=list)
    policy: dict[str, object] = field(default_factory=dict)
    dependencies: list[DependencyRecord] = field(default_factory=list)


def _canonicalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _extract_requirement_name(requirement: str) -> str | None:
    if Requirement is not None and default_environment is not None:
        try:
            parsed = Requirement(requirement)
        except Exception:
            parsed = None
        if parsed is not None:
            if parsed.marker is not None:
                marker_environment = default_environment()
                marker_environment.setdefault("extra", "")
                if not parsed.marker.evaluate(marker_environment):
                    return None
            return parsed.name

    match = re.match(r"^\s*([A-Za-z0-9_.-]+)", requirement)
    if not match:
        return None
    return match.group(1)


def _load_pyproject_dependencies(pyproject_path: Path) -> dict[str, dict[str, set[str]]]:
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependency_map: dict[str, dict[str, set[str]]] = {}

    for requirement in payload.get("project", {}).get("dependencies", []):
        _register_declared_dependency(
            dependency_map,
            requirement,
            scope="runtime",
            source="pyproject",
        )

    for group_requirements in payload.get("project", {}).get("optional-dependencies", {}).values():
        for requirement in group_requirements:
            _register_declared_dependency(
                dependency_map,
                requirement,
                scope="dev",
                source="pyproject",
            )

    for requirement in payload.get("build-system", {}).get("requires", []):
        _register_declared_dependency(
            dependency_map,
            requirement,
            scope="build",
            source="pyproject",
        )

    return dependency_map


def _load_requirements_dependencies(requirements_path: Path) -> dict[str, dict[str, set[str]]]:
    dependency_map: dict[str, dict[str, set[str]]] = {}
    for raw_line in requirements_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        _register_declared_dependency(
            dependency_map,
            line,
            scope="runtime",
            source="requirements.txt",
        )
    return dependency_map


def _register_declared_dependency(
    dependency_map: dict[str, dict[str, set[str]]],
    requirement: str,
    *,
    scope: str,
    source: str,
) -> None:
    name = _extract_requirement_name(requirement)
    if name is None:
        return
    canonical = _canonicalize_name(name)
    entry = dependency_map.setdefault(
        canonical,
        {
            "name": {name},
            "scope": {scope},
            "source": {source},
        },
    )
    entry["name"].add(name)
    entry["scope"].add(scope)
    entry["source"].add(source)


def _merge_dependency_maps(
    *dependency_maps: dict[str, dict[str, set[str]]],
) -> dict[str, dict[str, set[str]]]:
    merged: dict[str, dict[str, set[str]]] = {}
    for dependency_map in dependency_maps:
        for canonical, entry in dependency_map.items():
            target = merged.setdefault(
                canonical,
                {
                    "name": set(),
                    "scope": set(),
                    "source": set(),
                },
            )
            target["name"].update(entry["name"])
            target["scope"].update(entry["scope"])
            target["source"].update(entry["source"])
    return merged


def _installed_distributions() -> dict[str, metadata.Distribution]:
    distributions: dict[str, metadata.Distribution] = {}
    for dist in metadata.distributions():
        dist_name = dist.metadata.get("Name") or dist.name
        if not dist_name:
            continue
        distributions[_canonicalize_name(dist_name)] = dist
    return distributions


def _resolve_transitive_dependencies(
    roots: Iterable[str],
    installed: dict[str, metadata.Distribution],
) -> dict[str, str]:
    seen: set[str] = set()
    pending = list(roots)
    resolved: dict[str, str] = {}

    while pending:
        canonical = pending.pop()
        if canonical in seen:
            continue
        seen.add(canonical)
        dist = installed.get(canonical)
        if dist is None:
            continue
        for requirement in dist.requires or []:
            dependency_name = _extract_requirement_name(requirement)
            if dependency_name is None:
                continue
            dependency_canonical = _canonicalize_name(dependency_name)
            if dependency_canonical not in seen:
                pending.append(dependency_canonical)
            resolved.setdefault(dependency_canonical, dependency_name)
    return resolved


def _pick_scope(scopes: set[str]) -> str:
    for preferred in ("runtime", "dev", "build", "external_tool", "transitive"):
        if preferred in scopes:
            return preferred
    return "runtime"


def _pick_name(names: set[str], canonical: str) -> str:
    if names:
        return sorted(names, key=lambda value: (value.lower(), value))[0]
    return canonical


def _homepage_for_distribution(dist: metadata.Distribution) -> str | None:
    if dist.metadata.get("Home-page"):
        return dist.metadata["Home-page"]
    for key, value in dist.metadata.items():
        if key == "Project-URL" and value:
            return value
    return None


def _license_from_distribution(dist: metadata.Distribution) -> str | None:
    license_value = (dist.metadata.get("License") or "").strip()
    if license_value:
        return license_value
    license_expression = (dist.metadata.get("License-Expression") or "").strip()
    if license_expression:
        return license_expression
    classifiers = dist.metadata.get_all("Classifier") or []
    for classifier in classifiers:
        if classifier.startswith("License ::"):
            mapped = LICENSE_CLASSIFIER_MAP.get(classifier)
            if mapped:
                return mapped
            return classifier
    return None


def normalize_license_value(license_value: str | None) -> str | None:
    if license_value is None:
        return None

    normalized = re.sub(r"\s+", " ", license_value.strip()).strip()
    if not normalized:
        return None

    classifier_mapped = LICENSE_CLASSIFIER_MAP.get(normalized)
    if classifier_mapped:
        return classifier_mapped

    synonym_key = normalized.lower()
    if synonym_key in LICENSE_SYNONYMS:
        return LICENSE_SYNONYMS[synonym_key]

    if normalized.upper() in {"MIT", "ISC", "PSF", "GPL", "LGPL", "AGPL"}:
        return normalized.upper()

    return normalized


def classify_license_value(license_value: str | None) -> tuple[str, str]:
    normalized = normalize_license_value(license_value)
    if normalized is None:
        return ("review_required", "missing_or_unknown_license_metadata")
    if normalized in ALLOWED_LICENSES:
        return ("allowed", f"allowed_open_source_license:{normalized}")

    disjunctive_terms = _split_spdx_or_expression(normalized)
    if disjunctive_terms:
        unsafe_terms = [
            term
            for term in disjunctive_terms
            if normalize_license_value(term) not in ALLOWED_LICENSES
        ]
        if not unsafe_terms:
            return ("allowed", f"allowed_open_source_spdx_or:{normalized}")

    lowered = normalized.lower()
    if any(pattern in lowered for pattern in REVIEW_REQUIRED_LICENSE_PATTERNS):
        return ("review_required", f"copyleft_or_restricted_review_required:{normalized}")

    if normalized in {"GPL", "LGPL", "AGPL"}:
        return ("review_required", f"copyleft_or_restricted_review_required:{normalized}")

    return ("review_required", f"custom_or_unreviewed_license:{normalized}")


def _split_spdx_or_expression(normalized: str) -> list[str]:
    """Split simple SPDX OR expressions without treating prose as a license set."""
    if " OR " not in normalized:
        return []
    terms = [term.strip("() ") for term in _SPDX_OR_RE.split(normalized) if term.strip("() ")]
    if len(terms) < 2:
        return []
    return terms


def _python_dependency_records(
    *,
    pyproject_path: Path,
    requirements_path: Path,
) -> list[DependencyRecord]:
    declared = _merge_dependency_maps(
        _load_pyproject_dependencies(pyproject_path),
        _load_requirements_dependencies(requirements_path),
    )

    for tool_name, source in KNOWN_BUILD_TOOLS.items():
        canonical = _canonicalize_name(tool_name)
        declared.setdefault(
            canonical,
            {
                "name": {tool_name},
                "scope": {"build"},
                "source": {source},
            },
        )

    installed = _installed_distributions()
    direct_roots = set(declared)
    transitive = _resolve_transitive_dependencies(direct_roots, installed)

    all_names = set(direct_roots) | set(transitive)
    records: list[DependencyRecord] = []

    for canonical in sorted(all_names):
        declared_entry = declared.get(canonical)
        is_direct = declared_entry is not None
        scope = _pick_scope(declared_entry["scope"]) if declared_entry else "transitive"
        source = ",".join(sorted(declared_entry["source"])) if declared_entry else "environment_resolution"
        dist = installed.get(canonical)

        if dist is None:
            status = "not_installed" if is_direct else "missing"
            reason = (
                "declared_dependency_not_installed_in_active_environment"
                if is_direct
                else "transitive_dependency_missing_from_active_environment"
            )
            records.append(
                DependencyRecord(
                    name=_pick_name(declared_entry["name"], canonical) if declared_entry else canonical,
                    ecosystem="python",
                    scope=scope,
                    version=None,
                    source=source,
                    license=None,
                    homepage=None,
                    status=status,
                    reason=reason,
                )
            )
            continue

        raw_license = _license_from_distribution(dist)
        status, reason = classify_license_value(raw_license)
        dist_name = dist.metadata.get("Name") or dist.name or canonical
        records.append(
            DependencyRecord(
                name=dist_name,
                ecosystem="python",
                scope=scope,
                version=dist.version,
                source=source,
                license=normalize_license_value(raw_license),
                homepage=_homepage_for_distribution(dist),
                status=status,
                reason=reason,
            )
        )

    for index, record in enumerate(records):
        if (
            record.scope == "transitive"
            and record.status == "missing"
            and record.name == _canonicalize_name(record.name)
        ):
            transitive_name = transitive.get(_canonicalize_name(record.name))
            if transitive_name:
                records[index] = DependencyRecord(
                    name=transitive_name,
                    ecosystem=record.ecosystem,
                    scope=record.scope,
                    version=record.version,
                    source=record.source,
                    license=record.license,
                    homepage=record.homepage,
                    status=record.status,
                    reason=record.reason,
                )

    return records


def _anystyle_metadata(command: list[str]) -> tuple[bool, dict[str, str | None]]:
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            shell=False,
        )
    except OSError:
        return (False, {})

    if completed.returncode != 0:
        return (False, {})

    version_match = re.search(r"^anystyle \(([^)]+)\)", completed.stdout, re.MULTILINE)
    homepage_match = re.search(r"^\s*Homepage:\s*(.+)$", completed.stdout, re.MULTILINE)
    license_match = re.search(r"^\s*License:\s*(.+)$", completed.stdout, re.MULTILINE)
    return (
        True,
        {
            "version": version_match.group(1).strip() if version_match else None,
            "homepage": homepage_match.group(1).strip() if homepage_match else None,
            "license": license_match.group(1).strip() if license_match else None,
        },
    )


def _external_tool_records() -> list[DependencyRecord]:
    records: list[DependencyRecord] = []
    for tool_name, config in KNOWN_EXTERNAL_TOOLS.items():
        configured = (os.getenv(config["executable_env"]) or "").strip()
        executable = configured if configured and Path(configured).exists() else shutil.which(configured or tool_name)
        if executable is None:
            records.append(
                DependencyRecord(
                    name=tool_name,
                    ecosystem=config["ecosystem"],
                    scope=config["scope"],
                    version=None,
                    source=config["source"],
                    license=None,
                    homepage=None,
                    status="not_installed",
                    reason="required_external_tool_not_installed",
                )
            )
            continue

        found_metadata, metadata_payload = _anystyle_metadata(config["metadata_command"])
        if not found_metadata:
            records.append(
                DependencyRecord(
                    name=tool_name,
                    ecosystem=config["ecosystem"],
                    scope=config["scope"],
                    version=None,
                    source=config["source"],
                    license=None,
                    homepage=executable,
                    status="review_required",
                    reason="external_tool_metadata_unavailable",
                )
            )
            continue

        normalized_license = normalize_license_value(metadata_payload.get("license"))
        status, reason = classify_license_value(normalized_license)
        records.append(
            DependencyRecord(
                name=tool_name,
                ecosystem=config["ecosystem"],
                scope=config["scope"],
                version=metadata_payload.get("version"),
                source=config["source"],
                license=normalized_license,
                homepage=metadata_payload.get("homepage") or executable,
                status=status,
                reason=reason,
            )
        )

    return records


def build_dependency_audit_report(
    *,
    pyproject_path: Path = Path("pyproject.toml"),
    requirements_path: Path = Path("requirements.txt"),
) -> DependencyAuditReport:
    dependencies = _python_dependency_records(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
    )
    dependencies.extend(_external_tool_records())
    dependencies.sort(key=lambda record: (record.ecosystem, record.name.lower()))

    return DependencyAuditReport(
        generated_from=[str(pyproject_path), str(requirements_path)],
        policy={
            "allowed_licenses": sorted(ALLOWED_LICENSES),
            "review_required_patterns": list(REVIEW_REQUIRED_LICENSE_PATTERNS),
            "known_external_tools": sorted(KNOWN_EXTERNAL_TOOLS),
            "status_policy": {
                "allowed": "recognized approved open-source license",
                "review_required": "unknown, custom, or review-needed license state",
                "rejected": "reserved for future stricter enforcement",
                "missing": "dependency missing from resolved environment",
                "not_installed": "declared dependency or tool not installed",
            },
        },
        dependencies=dependencies,
    )


def report_to_dict(report: DependencyAuditReport) -> dict[str, object]:
    payload = asdict(report)
    payload["summary"] = summarize_report(report)
    return payload


def summarize_report(report: DependencyAuditReport) -> dict[str, object]:
    status_counts: dict[str, int] = {}
    external_tools: list[str] = []
    review_items: list[str] = []

    for dependency in report.dependencies:
        status_counts[dependency.status] = status_counts.get(dependency.status, 0) + 1
        if dependency.scope == "external_tool":
            external_tools.append(dependency.name)
        if dependency.status in {"review_required", "missing", "not_installed", "rejected"}:
            review_items.append(f"{dependency.name}:{dependency.status}")

    return {
        "total_dependencies": len(report.dependencies),
        "status_counts": status_counts,
        "external_tools_found": sorted(set(external_tools)),
        "review_items": sorted(review_items),
    }


def format_summary(report: DependencyAuditReport) -> str:
    summary = summarize_report(report)
    lines = [
        "Reference_Gen2 dependency audit",
        f"Total dependencies: {summary['total_dependencies']}",
        f"Allowed: {summary['status_counts'].get('allowed', 0)}",
        f"Review required: {summary['status_counts'].get('review_required', 0)}",
        f"Missing: {summary['status_counts'].get('missing', 0)}",
        f"Not installed: {summary['status_counts'].get('not_installed', 0)}",
        f"Rejected: {summary['status_counts'].get('rejected', 0)}",
    ]
    if summary["external_tools_found"]:
        lines.append(f"External tools: {', '.join(summary['external_tools_found'])}")
    if summary["review_items"]:
        lines.append(f"Needs review: {', '.join(summary['review_items'])}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Reference_Gen2 dependencies for open-source license compliance.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for the machine-readable JSON audit report.",
    )
    parser.add_argument(
        "--pyproject",
        type=Path,
        default=Path("pyproject.toml"),
        help="Path to pyproject.toml.",
    )
    parser.add_argument(
        "--requirements",
        type=Path,
        default=Path("requirements.txt"),
        help="Path to requirements.txt.",
    )
    args = parser.parse_args()

    report = build_dependency_audit_report(
        pyproject_path=args.pyproject,
        requirements_path=args.requirements,
    )
    print(format_summary(report))

    if args.json_output is not None:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(
            json.dumps(report_to_dict(report), indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
