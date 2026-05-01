#!/usr/bin/env python3
"""Trace dependency roots for packages in the active environment."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

from scripts import dependency_audit


@dataclass(frozen=True)
class PackageDependencyPath:
    package: str
    canonical: str
    status: str
    paths: list[list[str]] = field(default_factory=list)


@dataclass(frozen=True)
class DependencyPathReport:
    generated_from: list[str] = field(default_factory=list)
    query: dict[str, object] = field(default_factory=dict)
    package_paths: list[PackageDependencyPath] = field(default_factory=list)


def _load_manifest_roots(
    *, pyproject_path: Path, requirements_path: Path
) -> dict[str, set[str]]:
    declared = dependency_audit._merge_dependency_maps(
        dependency_audit._load_pyproject_dependencies(pyproject_path),
        dependency_audit._load_requirements_dependencies(requirements_path),
    )
    for tool_name in dependency_audit.KNOWN_BUILD_TOOLS:
        declared.setdefault(
            dependency_audit._canonicalize_name(tool_name),
            {
                "name": {tool_name},
                "scope": {"build"},
                "source": {"manual_tool_registry"},
            },
        )
    return declared


def _build_dependency_graph(installed: dict[str, object]) -> dict[str, list[str]]:
    graph: dict[str, list[str]] = {}
    for canonical, dist in installed.items():
        requirements: list[str] = []
        for requirement in getattr(dist, "requires", []) or []:
            dependency_name = dependency_audit._extract_requirement_name(requirement)
            if dependency_name is None:
                continue
            requirements.append(dependency_audit._canonicalize_name(dependency_name))
        graph[canonical] = sorted(set(requirements))
    return graph


def _collect_targets(
    *,
    vulnerability_report_path: Path | None,
    requested_packages: list[str],
    installed: dict[str, object],
) -> list[str]:
    requested = {
        dependency_audit._canonicalize_name(package)
        for package in requested_packages
        if package and package.strip()
    }

    if vulnerability_report_path is None and requested:
        return sorted(requested)
    if vulnerability_report_path is None and not requested:
        return sorted(installed)

    payload = json.loads(vulnerability_report_path.read_text(encoding="utf-8")) if vulnerability_report_path else {}
    vuln_packages: set[str] = set()
    for item in payload.get("packages", []):
        if not isinstance(item, dict):
            continue
        advisories = item.get("advisories")
        if not advisories:
            continue
        name = str(item.get("name", "")).strip()
        if name:
            vuln_packages.add(dependency_audit._canonicalize_name(name))

    combined = requested | vuln_packages
    if not combined:
        return sorted(installed)
    return sorted(combined)


def _collect_paths(
    graph: dict[str, list[str]],
    roots: set[str],
    target: str,
    *,
    max_depth: int,
    max_paths: int,
) -> list[list[str]]:
    if max_paths <= 0 or max_depth <= 0:
        return []

    found: list[list[str]] = []

    def _search(node: str, current_path: list[str], seen: set[str]) -> None:
        if len(found) >= max_paths:
            return
        if len(current_path) > max_depth:
            return
        if node == target:
            found.append(current_path.copy())
            return

        for dependency in graph.get(node, []):
            if dependency in seen:
                continue
            seen.add(dependency)
            current_path.append(dependency)
            _search(dependency, current_path, seen)
            current_path.pop()
            seen.remove(dependency)
            if len(found) >= max_paths:
                return

    for root in sorted(roots):
        if len(found) >= max_paths:
            return found
        if root == target:
            found.append([root])
            continue
        if root not in graph:
            continue
        _search(root, [root], {root})

    return found


def build_dependency_path_report(
    *,
    pyproject_path: Path,
    requirements_path: Path,
    vulnerability_report_path: Path | None,
    requested_packages: list[str],
    max_depth: int = 8,
    max_paths: int = 5,
) -> DependencyPathReport:
    declared = _load_manifest_roots(
        pyproject_path=pyproject_path,
        requirements_path=requirements_path,
    )
    installed = dependency_audit._installed_distributions()
    graph = _build_dependency_graph(installed)
    roots = set(declared)

    targets = _collect_targets(
        vulnerability_report_path=vulnerability_report_path,
        requested_packages=requested_packages,
        installed=installed,
    )

    records: list[PackageDependencyPath] = []
    for canonical in targets:
        raw_name = canonical
        installed_dist = installed.get(canonical)
        if installed_dist is None:
            records.append(
                PackageDependencyPath(
                    package=raw_name,
                    canonical=canonical,
                    status="not_installed",
                )
            )
            continue
        dist_name = getattr(installed_dist.metadata, "get", lambda _: None)("Name")
        package_name = dist_name if isinstance(dist_name, str) else canonical
        paths = _collect_paths(
            graph=graph,
            roots=roots,
            target=canonical,
            max_depth=max_depth,
            max_paths=max_paths,
        )

        if not paths:
            status = "disconnected_from_manifest_roots"
        else:
            status = "found"
        records.append(
            PackageDependencyPath(
                package=package_name,
                canonical=canonical,
                status=status,
                paths=paths,
            )
        )

    return DependencyPathReport(
        generated_from=[str(pyproject_path), str(requirements_path)],
        query={
            "targets": sorted(targets),
            "requested_packages": sorted(requested_packages),
            "max_depth": max_depth,
            "max_paths": max_paths,
            "from_vulnerability_report": str(vulnerability_report_path)
            if vulnerability_report_path is not None
            else None,
        },
        package_paths=sorted(records, key=lambda record: record.canonical),
    )


def summarize_paths_report(report: DependencyPathReport) -> dict[str, object]:
    return {
        "packages_requested": len(report.query.get("requested_packages", [])),
        "paths_found": sum(1 for item in report.package_paths if item.status == "found"),
        "disconnected": sum(
            1 for item in report.package_paths if item.status == "disconnected_from_manifest_roots"
        ),
        "not_installed": sum(1 for item in report.package_paths if item.status == "not_installed"),
        "total": len(report.package_paths),
    }


def report_to_dict(report: DependencyPathReport) -> dict[str, object]:
    payload = asdict(report)
    payload["summary"] = summarize_paths_report(report)
    return payload


def format_summary(report: DependencyPathReport) -> str:
    summary = summarize_paths_report(report)
    lines = [
        "Reference_Gen2 dependency path report",
        f"Targets requested: {summary['total']}",
        f"Paths found: {summary['paths_found']}",
        f"Disconnected: {summary['disconnected']}",
        f"Not installed: {summary['not_installed']}",
    ]
    for item in report.package_paths:
        if item.status != "found":
            lines.append(f"{item.package} ({item.canonical}): {item.status}")
            continue
        path_strings = [" -> ".join(path) for path in item.paths[:3]]
        lines.append(f"{item.package} ({item.canonical}): {len(item.paths)} path(s)")
        for path_string in path_strings:
            lines.append(f"  - {path_string}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trace dependency path chains for selected packages.",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        default=None,
        help="Optional path for machine-readable JSON dependency-path report.",
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
    parser.add_argument(
        "--package",
        action="append",
        default=[],
        help="Package name to trace. Repeat for multiple packages.",
    )
    parser.add_argument(
        "--from-vuln-report",
        type=Path,
        default=None,
        help="Optional vulnerability report path to trace only packages with advisories.",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="Maximum path length when tracing dependency chains.",
    )
    parser.add_argument(
        "--max-paths",
        type=int,
        default=5,
        help="Maximum number of paths to keep per target package.",
    )
    args = parser.parse_args()

    report = build_dependency_path_report(
        pyproject_path=args.pyproject,
        requirements_path=args.requirements,
        vulnerability_report_path=args.from_vuln_report,
        requested_packages=args.package,
        max_depth=args.max_depth,
        max_paths=args.max_paths,
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
