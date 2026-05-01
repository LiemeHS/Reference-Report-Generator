from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

from reference_gen2.finalization import finalize_cycle_report
from reference_gen2.report_generation import generate_html_report
from reference_gen2.reference_evaluation import Phase5RuntimeConfig, evaluate_reference
from reference_gen2.reference_matching import (
    Phase4RuntimeConfig,
    match_reference,
    warm_localdb_cache,
)
from reference_gen2.reference_parsing import parse_references_with_recovery
from reference_gen2.reference_segmentation import segment_references
from reference_gen2.reference_segmentation.models import ReferenceStyleHint
from reference_gen2.services.document_pipeline import run_phase1_pipeline

from scripts.run_phase123_batch import (
    DEFAULT_INPUT_DIR,
    DEFAULT_OUTPUT_DIR,
    SUPPORTED_SUFFIXES,
    _declared_mime,
    _error_payload,
    _real_anystyle_executable,
    _serialize,
    _set_runtime_anystyle_executable,
    _set_runtime_upload_tmp_dir,
)
from scripts.run_phase124_batch import _log_progress, _phase4_markdown_report, _phase4_quick_glance_report, _phase4_timing_summary, _round_ms, _warn_slow_phase4


def _phase5_markdown_report(payload: dict[str, Any]) -> str:
    lines = _phase4_markdown_report(payload).rstrip().splitlines()
    phase5 = payload.get("phase5")
    if phase5:
        lines.extend(["", "## Phase 5", ""])
        for index, result in enumerate(phase5, start=1):
            report_signals = result.get("report_signals") or {}
            score_breakdown = result.get("score_breakdown") or {}
            lines.extend(
                [
                    f"### Evaluated Reference {index}",
                    "",
                    f"- Reference ID: `{result.get('reference_id', '')}`",
                    f"- Phase 4 Status: `{result.get('phase4_status', '')}`",
                    f"- Final Status: `{result.get('final_status', '')}`",
                    f"- Final Confidence: `{result.get('final_confidence', '')}`",
                    f"- Confidence Score: `{result.get('confidence_score', 0.0)}`",
                    f"- Top Candidate Gap: `{result.get('top_candidate_gap', None)}`",
                    f"- Best Candidate Record ID: `{(result.get('accepted_candidate') or {}).get('record_id', '')}`",
                    f"- Runner-up Record ID: `{(result.get('runner_up_candidate') or {}).get('record_id', '')}`",
                    f"- Reasons: `{result.get('reasons', [])}`",
                    f"- Strengths: `{report_signals.get('strengths', [])}`",
                    f"- Concerns: `{report_signals.get('concerns', [])}`",
                    f"- Review Flags: `{report_signals.get('review_flags', [])}`",
                    f"- Evidence Checks: `{report_signals.get('evidence_checks', [])}`",
                    f"- Evidence Summary: `{report_signals.get('final_evidence_summary', [])}`",
                    f"- Score Breakdown: `{score_breakdown}`",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def _phase5_quick_glance_report(payload: dict[str, Any]) -> str:
    lines = _phase4_quick_glance_report(payload).rstrip().splitlines()
    phase5 = payload.get("phase5")
    if not phase5:
        return "\n".join(lines).rstrip() + "\n"
    lines.extend(["", "## Phase 5", ""])
    for result in phase5:
        report_signals = result.get("report_signals") or {}
        lines.extend(
            [
                f"### Reference {result.get('reference_id', '')}",
                "",
                f"- Final Status: `{result.get('final_status', '')}`",
                f"- Confidence: `{result.get('final_confidence', '')}`",
                f"- Confidence Score: `{result.get('confidence_score', 0.0)}`",
                f"- Best Candidate Record ID: `{(result.get('accepted_candidate') or {}).get('record_id', '')}`",
                f"- Review Flags: `{report_signals.get('review_flags', [])}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _phase5_timing_summary(phase5_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "reference_id": result.get("reference_id"),
            "phase4_status": result.get("phase4_status"),
            "final_status": result.get("final_status"),
            "final_confidence": result.get("final_confidence"),
            "confidence_score": result.get("confidence_score"),
        }
        for result in phase5_results
    ]


def process_document(
    input_path: Path,
    output_dir: Path,
    *,
    db_path: str,
    style_hint: ReferenceStyleHint = "unknown",
    progress: bool = False,
    warm_db: bool = False,
    max_references: int | None = None,
    reference_index: int | None = None,
    relaxed: bool = False,
    html_report: bool = False,
) -> dict[str, Any]:
    _set_runtime_anystyle_executable(_real_anystyle_executable())
    total_started = time.perf_counter()

    payload: dict[str, Any] = {
        "input_file": str(input_path),
        "style_hint": style_hint,
        "db_path": db_path,
        "status": "ok",
        "phase1": None,
        "phase2": None,
        "phase3": None,
        "phase3b": None,
        "phase4": None,
        "phase5": None,
        "phase4_timing_summary": [],
        "phase5_timing_summary": [],
        "timings_ms": {
            "phase1": 0.0,
            "phase2": 0.0,
            "phase3": 0.0,
            "phase4": 0.0,
            "phase5": 0.0,
            "output_write": 0.0,
            "total": 0.0,
            "db_warmup": 0.0,
        },
        "error": None,
    }

    phase1 = None
    segmented = None
    parsed = None
    recovered = None
    matched = []
    phase5_results = []

    with tempfile.TemporaryDirectory(prefix="reference_gen2_phase125_batch_") as temp_dir:
        previous_tmp_env = os.environ.get("REFERENCE_GEN2_UPLOAD_TMP_DIR")
        temp_path = Path(temp_dir)
        os.environ["REFERENCE_GEN2_UPLOAD_TMP_DIR"] = temp_dir
        previous_settings_path, previous_storage_path = _set_runtime_upload_tmp_dir(
            temp_path
        )
        try:
            _log_progress(
                progress,
                f"START file={input_path} db_path={db_path} warm_db={warm_db} progress={progress}",
            )
            content = input_path.read_bytes()
            phase_started = time.perf_counter()
            _log_progress(progress, "Phase 1 start")
            phase1 = run_phase1_pipeline(input_path.name, _declared_mime(input_path), content)
            payload["timings_ms"]["phase1"] = _round_ms(time.perf_counter() - phase_started)
            _log_progress(progress, f"Phase 1 complete elapsed_ms={payload['timings_ms']['phase1']}")
            payload["phase1"] = {
                "upload": _serialize(phase1.upload),
                "bibliography": {
                    "heading": phase1.bibliography.heading,
                    "heading_unit_index": phase1.bibliography.heading_unit_index,
                    "start_unit_index": phase1.bibliography.start_unit_index,
                    "end_unit_index": phase1.bibliography.end_unit_index,
                    "char_count": len(phase1.bibliography.text),
                    "warnings": list(phase1.bibliography.warnings),
                    "text": phase1.bibliography.text,
                },
                "report_context": _serialize(phase1.report_context),
            }

            phase_started = time.perf_counter()
            _log_progress(progress, "Phase 2 start")
            segmented = segment_references(
                phase1.bibliography,
                phase1.extraction,
                style_hint=style_hint,
            )
            payload["timings_ms"]["phase2"] = _round_ms(time.perf_counter() - phase_started)
            _log_progress(
                progress,
                f"Phase 2 complete references={len(segmented.references)} elapsed_ms={payload['timings_ms']['phase2']}",
            )
            payload["phase2"] = _serialize(segmented)

            phase_started = time.perf_counter()
            _log_progress(progress, "Phase 3 start")
            parsed, recovered = parse_references_with_recovery(
                segmented.references,
                style_hint=style_hint,
            )
            payload["timings_ms"]["phase3"] = _round_ms(time.perf_counter() - phase_started)
            _log_progress(
                progress,
                f"Phase 3 complete parsed={len(parsed)} recovered={len(recovered)} elapsed_ms={payload['timings_ms']['phase3']}",
            )
            payload["phase3"] = _serialize(parsed)
            payload["phase3b"] = _serialize(recovered)

            phase_source = recovered or parsed
            if reference_index is not None:
                if reference_index < 1 or reference_index > len(phase_source):
                    raise ValueError(f"reference_index out of range: {reference_index}")
                phase_source = [phase_source[reference_index - 1]]
            elif max_references is not None:
                phase_source = phase_source[: max(0, max_references)]

            if warm_db:
                warm_started = time.perf_counter()
                _log_progress(progress, "Phase 4 warm-up start")
                warm_localdb_cache(db_path)
                payload["timings_ms"]["db_warmup"] = _round_ms(time.perf_counter() - warm_started)
                _log_progress(progress, f"Phase 4 warm-up complete elapsed_ms={payload['timings_ms']['db_warmup']}")

            phase4_started = time.perf_counter()
            _log_progress(progress, f"Phase 4 start references={len(phase_source)} relaxed={relaxed}")
            if relaxed:
                phase4_config = Phase4RuntimeConfig(
                    local_db_path=db_path,
                    prefer_recovered=True,
                    enable_relaxed_queries=True,
                    max_fallback_strategies=6,
                    broad_query_guard_enabled=False,
                    near_year_distance=2,
                    max_candidates=10,
                )
            else:
                phase4_config = Phase4RuntimeConfig(local_db_path=db_path, prefer_recovered=True)
            for index, parsed_result in enumerate(phase_source, start=1):
                ref_started = time.perf_counter()
                doi_attempted = bool(
                    parsed_result.match_preparation.lookup_key_fields.get("doi")
                    if parsed_result.match_preparation
                    else []
                )
                _log_progress(
                    progress,
                    f"Phase 4 reference {index}/{len(phase_source)} start reference_id={parsed_result.reference_id} ctype={parsed_result.ctype} doi_attempted={doi_attempted}",
                )
                result = match_reference(parsed_result, config=phase4_config)
                matched.append(result)
                ref_ms = _round_ms(time.perf_counter() - ref_started)
                result_payload = _serialize(result)
                _log_progress(
                    progress,
                    (
                        f"Phase 4 reference {index}/{len(phase_source)} complete"
                        f" reference_id={result.reference_id}"
                        f" status={result.status}"
                        f" strategy={result.strategy_used}"
                        f" candidate_count={result.lookup_trace.candidate_count}"
                        f" elapsed_ms={ref_ms}"
                    ),
                )
                _warn_slow_phase4(progress, result_payload)
            payload["timings_ms"]["phase4"] = _round_ms(time.perf_counter() - phase4_started)
            _log_progress(progress, f"Phase 4 complete elapsed_ms={payload['timings_ms']['phase4']}")
            payload["phase4"] = _serialize(matched)
            payload["phase4_timing_summary"] = _phase4_timing_summary(payload["phase4"])

            phase5_started = time.perf_counter()
            _log_progress(progress, f"Phase 5 start references={len(phase_source)}")
            phase5_config = Phase5RuntimeConfig()
            for index, (parsed_result, phase4_result) in enumerate(zip(phase_source, matched), start=1):
                ref_started = time.perf_counter()
                _log_progress(
                    progress,
                    f"Phase 5 reference {index}/{len(phase_source)} start reference_id={parsed_result.reference_id}",
                )
                result = evaluate_reference(parsed_result, phase4_result, config=phase5_config)
                phase5_results.append(result)
                ref_ms = _round_ms(time.perf_counter() - ref_started)
                _log_progress(
                    progress,
                    (
                        f"Phase 5 reference {index}/{len(phase_source)} complete"
                        f" reference_id={result.reference_id}"
                        f" final_status={result.final_status}"
                        f" confidence={result.final_confidence}"
                        f" elapsed_ms={ref_ms}"
                    ),
                )
            payload["timings_ms"]["phase5"] = _round_ms(time.perf_counter() - phase5_started)
            _log_progress(progress, f"Phase 5 complete elapsed_ms={payload['timings_ms']['phase5']}")
            payload["phase5"] = _serialize(phase5_results)
            payload["phase5_timing_summary"] = _phase5_timing_summary(payload["phase5"])
        except Exception as exc:
            payload["status"] = "error"
            if payload["phase1"] is None:
                phase_name = "phase1"
            elif payload["phase2"] is None:
                phase_name = "phase2"
            elif payload["phase3"] is None:
                phase_name = "phase3"
            elif payload["phase4"] is None:
                phase_name = "phase4"
            else:
                phase_name = "phase5"
            payload["error"] = _error_payload(phase_name, exc)
        finally:
            import reference_gen2.api.settings as api_settings
            import reference_gen2.security.temp_storage as temp_storage

            api_settings.UPLOAD_TMP_DIR = previous_settings_path
            temp_storage.UPLOAD_TMP_DIR = previous_storage_path
            if previous_tmp_env is None:
                os.environ.pop("REFERENCE_GEN2_UPLOAD_TMP_DIR", None)
            else:
                os.environ["REFERENCE_GEN2_UPLOAD_TMP_DIR"] = previous_tmp_env

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    json_path = output_dir / f"{stem}.phase125.json"
    md_path = output_dir / f"{stem}.phase125.md"
    quick_md_path = output_dir / f"{stem}.phase125.quick.md"
    html_path = output_dir / f"{stem}.phase125.html"
    write_started = time.perf_counter()
    _log_progress(progress, "Artifact write start")
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_phase5_markdown_report(payload), encoding="utf-8")
    quick_md_path.write_text(_phase5_quick_glance_report(payload), encoding="utf-8")
    if html_report:
        sanitized_report = finalize_cycle_report(
            style_hint=style_hint,
            phase1=phase1,
            phase2=segmented,
            phase3=parsed,
            phase3b=recovered,
            phase4=matched,
            phase5=phase5_results,
            error=payload.get("error"),
            source_mode="upload",
        )
        generate_html_report(sanitized_report, html_path)
    payload["timings_ms"]["output_write"] = _round_ms(time.perf_counter() - write_started)
    payload["timings_ms"]["total"] = _round_ms(time.perf_counter() - total_started)
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _log_progress(progress, f"Artifact write complete elapsed_ms={payload['timings_ms']['output_write']}")
    _log_progress(progress, f"DONE file={input_path} total_ms={payload['timings_ms']['total']}")
    return payload


def run_batch(
    input_path: Path,
    output_dir: Path,
    *,
    db_path: str,
    recursive: bool = False,
    style_hint: ReferenceStyleHint = "unknown",
    progress: bool = False,
    warm_db: bool = False,
    max_references: int | None = None,
    reference_index: int | None = None,
    relaxed: bool = False,
    html_report: bool = False,
) -> list[dict[str, Any]]:
    _set_runtime_anystyle_executable(_real_anystyle_executable())

    if input_path.is_file():
        candidates = [input_path]
    else:
        pattern = "**/*" if recursive else "*"
        candidates = sorted(path for path in input_path.glob(pattern) if path.is_file())

    results: list[dict[str, Any]] = []
    for path in candidates:
        if path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        results.append(
            process_document(
                path,
                output_dir,
                db_path=db_path,
                style_hint=style_hint,
                progress=progress,
                warm_db=warm_db,
                max_references=max_references,
                reference_index=reference_index,
                relaxed=relaxed,
                html_report=html_report,
            )
        )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local Phase 1 to 5 review outputs for PDF and DOCX files."
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default=str(DEFAULT_INPUT_DIR),
        help="Directory or single .pdf/.docx file to process.",
    )
    parser.add_argument(
        "--db-path",
        required=True,
        help="Path to the SQLite local search database used by Phase 4.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where .phase125.json and .phase125.md files will be written.",
    )
    parser.add_argument(
        "--style-hint",
        default="unknown",
        choices=["unknown", "apa7_nl", "apa7_en", "mla", "chicago", "harvard", "vancouver"],
        help="Optional style hint passed into Phase 2 and Phase 3.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Recursively scan the input directory for supported files.",
    )
    parser.add_argument(
        "--progress",
        action="store_true",
        help="Print live phase and per-reference progress lines while the file is processed.",
    )
    parser.add_argument(
        "--warm-db",
        action="store_true",
        help="Warm the SQLite search DB with tiny read-only probes before Phase 4 starts.",
    )
    parser.add_argument(
        "--max-references",
        type=int,
        default=None,
        help="Only send the first N recovered references into Phase 4 and Phase 5.",
    )
    parser.add_argument(
        "--reference-index",
        type=int,
        default=None,
        help="Only send one 1-based recovered reference index into Phase 4 and Phase 5.",
    )
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="Enable relaxed Phase 4 matching config for better recall before Phase 5 evaluation.",
    )
    parser.add_argument(
        "--html-report",
        action="store_true",
        help="Also write a standalone sanitized .phase125.html report.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    input_path = Path(args.input_path)
    output_dir = Path(args.output_dir)

    if not input_path.exists():
        print(f"Input path does not exist: {input_path}")
        return 1
    if input_path.is_file() and input_path.suffix.lower() not in SUPPORTED_SUFFIXES:
        print(f"Unsupported input file type: {input_path}")
        return 1
    if not Path(args.db_path).exists():
        print(f"DB path does not exist: {args.db_path}")
        return 1

    results = run_batch(
        input_path,
        output_dir,
        db_path=args.db_path,
        recursive=args.recursive,
        style_hint=args.style_hint,
        progress=args.progress,
        warm_db=args.warm_db,
        max_references=args.max_references,
        reference_index=args.reference_index,
        relaxed=args.relaxed,
        html_report=args.html_report,
    )
    if not results:
        print(f"No supported .pdf or .docx files found in {input_path}")
        return 0

    success_count = sum(1 for result in results if result["status"] == "ok")
    print(
        f"Processed {len(results)} file(s). Successes: {success_count}. Output: {output_dir}"
    )
    for result in results:
        print(f"- [{result['status']}] {result['input_file']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
