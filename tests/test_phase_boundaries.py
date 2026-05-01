from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _internal_imports(package: str) -> set[str]:
    package_dir = ROOT / "reference_gen2" / package
    imports: set[str] = set()
    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("reference_gen2."):
                        imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("reference_gen2."):
                    imports.add(node.module)
    return imports


def _assert_no_disallowed_imports(package: str, disallowed_prefixes: set[str]) -> None:
    offending = sorted(
        import_name
        for import_name in _internal_imports(package)
        if any(
            import_name == prefix or import_name.startswith(f"{prefix}.")
            for prefix in disallowed_prefixes
        )
    )
    assert offending == []


def test_phase2_segmentation_has_no_downstream_phase_dependencies():
    _assert_no_disallowed_imports(
        "reference_segmentation",
        {
            "reference_gen2.reference_parsing",
            "reference_gen2.reference_matching",
            "reference_gen2.reference_evaluation",
            "reference_gen2.finalization",
            "reference_gen2.citation_rendering",
            "reference_gen2.report_generation",
        },
    )


def test_phase3_parsing_does_not_import_phase2_internals():
    _assert_no_disallowed_imports(
        "reference_parsing",
        {
            "reference_gen2.reference_segmentation.heuristics",
            "reference_gen2.reference_segmentation.normalization",
            "reference_gen2.reference_segmentation.profiles",
            "reference_gen2.reference_segmentation.service",
            "reference_gen2.reference_segmentation.splitter",
        },
    )


def test_phase3_parsing_has_no_downstream_phase_dependencies():
    _assert_no_disallowed_imports(
        "reference_parsing",
        {
            "reference_gen2.reference_matching",
            "reference_gen2.reference_evaluation",
            "reference_gen2.finalization",
            "reference_gen2.citation_rendering",
            "reference_gen2.report_generation",
        },
    )


def test_phase4_matching_has_no_downstream_phase_dependencies():
    _assert_no_disallowed_imports(
        "reference_matching",
        {
            "reference_gen2.document_intake",
            "reference_gen2.document_extraction",
            "reference_gen2.extractors",
            "reference_gen2.bibliography_detection",
            "reference_gen2.reference_segmentation",
            "reference_gen2.reference_evaluation",
            "reference_gen2.finalization",
            "reference_gen2.citation_rendering",
            "reference_gen2.report_generation",
        },
    )


def test_phase5_evaluation_uses_only_public_upstream_models():
    _assert_no_disallowed_imports(
        "reference_evaluation",
        {
            "reference_gen2.reference_matching.provider",
            "reference_gen2.reference_matching.service",
            "reference_gen2.document_intake",
            "reference_gen2.document_extraction",
            "reference_gen2.extractors",
            "reference_gen2.bibliography_detection",
            "reference_gen2.reference_segmentation",
            "reference_gen2.finalization",
            "reference_gen2.citation_rendering",
            "reference_gen2.report_generation",
        },
    )


def test_finalization_is_the_only_raw_to_sanitized_report_boundary():
    _assert_no_disallowed_imports(
        "finalization",
        {
            "reference_gen2.document_intake",
            "reference_gen2.document_extraction",
            "reference_gen2.extractors",
            "reference_gen2.bibliography_detection",
            "reference_gen2.reference_segmentation.service",
            "reference_gen2.reference_segmentation.splitter",
            "reference_gen2.reference_segmentation.heuristics",
            "reference_gen2.reference_parsing.service",
            "reference_gen2.reference_matching.provider",
            "reference_gen2.reference_matching.service",
            "reference_gen2.reference_evaluation.service",
            "reference_gen2.report_generation",
        },
    )


def test_phase6_report_generation_stays_on_finalized_contract():
    _assert_no_disallowed_imports(
        "report_generation",
        {
            "reference_gen2.reference_parsing",
            "reference_gen2.reference_matching",
            "reference_gen2.reference_evaluation",
            "reference_gen2.citation_rendering",
        },
    )


def test_phase6a_citation_rendering_stays_candidate_only():
    _assert_no_disallowed_imports(
        "citation_rendering",
        {
            "reference_gen2.reference_parsing",
            "reference_gen2.reference_evaluation",
            "reference_gen2.finalization",
            "reference_gen2.report_generation",
        },
    )


def test_runtime_packages_do_not_import_database_ingest_tooling():
    offending: list[str] = []
    for path in (ROOT / "reference_gen2").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("scripts."):
                        offending.append(f"{path.relative_to(ROOT)}:{alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module == "scripts" or node.module.startswith("scripts."):
                    offending.append(f"{path.relative_to(ROOT)}:{node.module}")
    assert sorted(offending) == []
