#!/usr/bin/env python3
"""Render a stable report HTML fixture for local visual verification."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

def _load_module(module_name: str, rel_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, str(rel_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load module {module_name} from {rel_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _bootstrap_report_modules() -> Any:
    """Load only the minimal package pieces needed by report rendering."""

    fake_package = ModuleType("reference_gen2")
    fake_package.__path__ = [str(ROOT / "reference_gen2")]
    sys.modules["reference_gen2"] = fake_package

    _load_module(
        "reference_gen2.reference_styles",
        ROOT / "reference_gen2" / "reference_styles.py",
    )

    finalization = ModuleType("reference_gen2.finalization")
    finalization.__path__ = [str(ROOT / "reference_gen2" / "finalization")]
    finalization.SanitizedCycleReport = object
    finalization.serialize_sanitized_report = lambda report: report
    sys.modules["reference_gen2.finalization"] = finalization

    report_service = _load_module(
        "reference_gen2.report_generation.service",
        ROOT / "reference_gen2" / "report_generation" / "service.py",
    )
    return report_service


def _build_report() -> dict[str, Any]:
    return {
        "cycle_id": "debug_report",
        "status": "ok",
        "source_mode": "upload",
        "style_hint": "apa7_nl",
        "phase1": {
            "upload_kind": "pdf",
            "report": {
                "source_kind": "pdf",
                "size_bytes": 0,
                "extraction_time_ms": 0.0,
                "heading_found": True,
                "heading_unit_index": 1,
                "start_unit_index": 1,
                "end_unit_index": 1,
                "unit_count": 1,
                "bibliography_char_count": 10,
            },
        },
        "phase2": {
            "reference_count": 1,
            "style_hint_used": "apa7_nl",
            "profile_used": "default_profile",
        },
        "phase3": [
            {
                "opaque_reference_id": "ref_0001",
                "ctype": "journal_article",
                "parser_backend": "anystyle",
                "parser_model_used": "default",
                "display_reference": (
                    "Alpha, A. (2020). Example paper. Journal Name. https://doi.org/10.1234/example"
                ),
                "match_eligible": True,
                "match_target": "crossref",
                "missing_fields_for_match": ["author"],
                "parsed_fields": {
                    "Authors": "Alpha, A.",
                    "Year": "2020",
                    "Title": "Example paper",
                    "Container": "Journal Name",
                    "DOI": "10.1234/example",
                },
            }
        ],
        "phase4": [
            {
                "opaque_reference_id": "ref_0001",
                "attempted": True,
                "status": "matched_provisional",
                "strategy_used": "doi_exact",
                "candidate_count": 1,
                "best_record_id": "search_journal:1",
                "best_candidate_display": "Alpha. (2020). Example paper. Journal Name.",
                "reasons": ["phase4_candidates_found"],
                "warnings": ["Example warning"],
            }
        ],
        "phase5": [
            {
                "opaque_reference_id": "ref_0001",
                "phase4_status": "matched_provisional",
                "final_status": "verified",
                "final_confidence": "high",
                "confidence_score": 0.93,
                "accepted_record_id": "search_journal:1",
                "accepted_match_display": (
                    "Alpha. (2020). Example paper. Journal Name. https://doi.org/10.1234/example"
                ),
                "accepted_match_render": {
                    "text": "Alpha. (2020). Example paper. Journal Name. https://doi.org/10.1234/example",
                    "html": '<div class="csl-entry">Alpha. (2020). <i>Example paper</i>. <a href="https://doi.org/10.1234/example">https://doi.org/10.1234/example</a></div>',
                    "style": "apa-standard",
                    "locale": "en-US",
                },
                "runner_up_match_display": "Beta. (2020). Alternate title.",
                "reasons": ["phase5_final_status:verified"],
            }
        ],
    }

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render a local report fixture.")
    parser.add_argument(
        "--output",
        default="/tmp/reference_gen2_report_debug.html",
        help="Output path for the rendered HTML artifact.",
    )
    parser.add_argument(
        "--title",
        default="Referentierapport",
        help="Optional report HTML title.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_service = _bootstrap_report_modules()
    config = report_service.StaticReportConfig(title=args.title)
    report_html = report_service.generate_html_report(_build_report(), output, config=config)
    output.write_text(report_html, encoding="utf-8")
    print(f"Saved report artifact to {output.resolve()}")


if __name__ == "__main__":
    main()
