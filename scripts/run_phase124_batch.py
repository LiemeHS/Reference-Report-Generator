from __future__ import annotations

import argparse
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

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
    _markdown_report,
    _quick_glance_report,
    _real_anystyle_executable,
    _serialize,
    _set_runtime_anystyle_executable,
    _set_runtime_upload_tmp_dir,
)


def _phase4_markdown_report(payload: dict[str, Any]) -> str:
    lines = _markdown_report(payload).rstrip().splitlines()
    phase4 = payload.get("phase4")
    if phase4:
        lines.extend(["", "## Phase 4", ""])
        for index, result in enumerate(phase4, start=1):
            input_summary = result.get("input_summary") or {}
            best_candidate = result.get("best_candidate") or {}
            top_candidates = result.get("top_candidates") or []
            lines.extend(
                [
                    f"### Matched Reference {index}",
                    "",
                    f"- Reference ID: `{result.get('reference_id', '')}`",
                    f"- CType: `{input_summary.get('ctype', '')}`",
                    f"- Match Target: `{input_summary.get('match_target', '')}`",
                    f"- Status: `{result.get('status', '')}`",
                    f"- Strategy Used: `{result.get('strategy_used', '')}`",
                    f"- Candidate Count: `{len(result.get('candidates', []))}`",
                    f"- Top Candidate Count: `{len(top_candidates)}`",
                    f"- Reasons: `{result.get('reasons', [])}`",
                    f"- Lookup Trace: `{result.get('lookup_trace', {})}`",
                    "",
                ]
            )
            if best_candidate:
                lines.extend(
                    [
                        "- Best Candidate:",
                        f"- Record ID: `{best_candidate.get('record_id', '')}`",
                        f"- Title: `{best_candidate.get('title', '')}`",
                        f"- DOI: `{best_candidate.get('doi', '')}`",
                        f"- Source Strategy: `{best_candidate.get('source_strategy', '')}`",
                        f"- Ordering Score: `{best_candidate.get('ordering_score', 0.0)}`",
                        f"- Match Signals: `{best_candidate.get('match_signals', {})}`",
                        "",
                    ]
                )
    return "\n".join(lines).rstrip() + "\n"


def _phase4_quick_glance_report(payload: dict[str, Any]) -> str:
    lines = _quick_glance_report(payload).rstrip().splitlines()
    phase4 = payload.get("phase4")
    if not phase4:
        return "\n".join(lines).rstrip() + "\n"
    lines.extend(["", "## Phase 4", ""])
    for result in phase4:
        best_candidate = result.get("best_candidate") or {}
        lines.extend(
            [
                f"### Reference {result.get('reference_id', '')}",
                "",
                f"- Phase 4 Status: `{result.get('status', '')}`",
                f"- Strategy: `{result.get('strategy_used', '')}`",
                f"- Top Candidates: `{len(result.get('top_candidates', []))}`",
                f"- Best Record ID: `{best_candidate.get('record_id', '')}`",
                f"- Best DOI: `{best_candidate.get('doi', '')}`",
                f"- Match Signals: `{best_candidate.get('match_signals', {})}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _log_progress(enabled: bool, message: str) -> None:
    if enabled:
        print(message, flush=True)


def _round_ms(value: float) -> float:
    return round(value * 1000, 2)


def _phase4_timing_summary(phase4_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for result in phase4_results:
        input_summary = result.get("input_summary") or {}
        lookup_trace = result.get("lookup_trace") or {}
        output.append(
            {
                "reference_id": result.get("reference_id"),
                "ctype": input_summary.get("ctype"),
                "status": result.get("status"),
                "strategy_used": result.get("strategy_used"),
                "doi_attempted": lookup_trace.get("doi_attempted"),
                "strategies_attempted": lookup_trace.get("strategies_attempted", []),
                "candidate_count": lookup_trace.get("candidate_count", 0),
                "timings_ms": lookup_trace.get("timings_ms", {}),
            }
        )
    return output


def _phase4_triage_summary(
    phase3_results: list[dict[str, Any]],
    phase4_results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize Phase 3/4 failure shape for fixture regression triage.

    This is intentionally diagnostic-only. Runtime matching policy still lives
    in Phase 4/5; the batch harness just makes skipped and suspicious cases
    easier to audit without reading the full JSON object for every reference.
    """

    output: list[dict[str, Any]] = []
    for index, phase4 in enumerate(phase4_results):
        phase3 = phase3_results[index] if index < len(phase3_results) else {}
        parsed_data = phase3.get("parsed_data") or {}
        match_prep = phase3.get("match_preparation") or {}
        input_summary = phase4.get("input_summary") or {}
        lookup_trace = phase4.get("lookup_trace") or {}
        missing_fields = _triage_missing_fields(parsed_data, input_summary, match_prep)
        status = str(phase4.get("status") or "")
        reasons = [str(reason) for reason in phase4.get("reasons") or []]
        warnings = [str(warning) for warning in phase3.get("warnings") or []]
        bucket = _triage_bucket(
            status=status,
            missing_fields=missing_fields,
            reasons=reasons,
            warnings=warnings,
            candidate_count=int(lookup_trace.get("candidate_count") or 0),
        )
        output.append(
            {
                "reference_id": phase4.get("reference_id") or phase3.get("reference_id"),
                "index": index + 1,
                "bucket": bucket,
                "phase4_status": status,
                "ctype": input_summary.get("ctype") or phase3.get("ctype"),
                "title": _first(parsed_data.get("title")),
                "year": parsed_data.get("issued_year") or _first(parsed_data.get("date")),
                "container": _first(parsed_data.get("container_title")),
                "doi": _first(parsed_data.get("doi")),
                "missing_fields": missing_fields,
                "strategy_used": phase4.get("strategy_used"),
                "candidate_count": lookup_trace.get("candidate_count", 0),
                "best_candidate_doi": (phase4.get("best_candidate") or {}).get("doi"),
                "best_candidate_title": (phase4.get("best_candidate") or {}).get("title"),
                "reasons": reasons,
                "parser_warnings": warnings,
            }
        )
    return output


def _triage_missing_fields(
    parsed_data: dict[str, Any],
    input_summary: dict[str, Any],
    match_prep: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    ctype = input_summary.get("ctype")
    lookup_keys = match_prep.get("lookup_key_fields") or {}
    if ctype in {"journal_article", "book", "book_chapter"}:
        if not lookup_keys.get("title") and not lookup_keys.get("chapter_title"):
            missing.append("title")
        if not (parsed_data.get("issued_year") or lookup_keys.get("issued_year")):
            missing.append("issued_year")
    if ctype == "journal_article" and not parsed_data.get("container_title"):
        missing.append("container_title")
    if ctype == "book_chapter" and not (
        lookup_keys.get("book_title") or parsed_data.get("container_title")
    ):
        missing.append("book_title")
    return missing


def _triage_bucket(
    *,
    status: str,
    missing_fields: list[str],
    reasons: list[str],
    warnings: list[str],
    candidate_count: int,
) -> str:
    if any(reason.startswith("phase4_unsupported_ctype") for reason in reasons):
        return "parser_or_classification"
    if missing_fields or any(warning.startswith("parser_missing_") for warning in warnings):
        return "parser_or_extraction"
    if status == "skipped":
        return "lookup_ineligible"
    if status == "no_match" and candidate_count == 0:
        return "db_coverage_or_lookup_recall"
    if status == "candidate_only":
        return "scoring_or_weak_evidence"
    if status == "matched_provisional":
        return "matched_for_phase5_review"
    if status == "error":
        return "lookup_error"
    return "needs_manual_review"


def _first(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if value else None
    return value


def _warn_slow_phase4(enabled: bool, result: dict[str, Any]) -> None:
    lookup_trace = result.get("lookup_trace") or {}
    timings = lookup_trace.get("timings_ms") or {}
    doi_ms = float(timings.get("doi", 0.0) or 0.0)
    fallback_ms = float(timings.get("fallback", 0.0) or 0.0)
    total_ms = float(timings.get("total", 0.0) or 0.0)
    if total_ms < 1000.0 and doi_ms < 500.0 and fallback_ms < 500.0:
        return
    input_summary = result.get("input_summary") or {}
    _log_progress(
        enabled,
        (
            "WARNING slow_phase4"
            f" reference_id={result.get('reference_id','')}"
            f" ctype={input_summary.get('ctype','')}"
            f" status={result.get('status','')}"
            f" strategy={result.get('strategy_used','')}"
            f" doi_ms={doi_ms:.2f}"
            f" fallback_ms={fallback_ms:.2f}"
            f" total_ms={total_ms:.2f}"
            f" candidate_count={lookup_trace.get('candidate_count', 0)}"
            f" strategies={lookup_trace.get('strategies_attempted', [])}"
        ),
    )


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
        "phase4_timing_summary": [],
        "phase4_triage": [],
        "timings_ms": {
            "phase1": 0.0,
            "phase2": 0.0,
            "phase3": 0.0,
            "phase4": 0.0,
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
    matched = None

    with tempfile.TemporaryDirectory(prefix="reference_gen2_phase124_batch_") as temp_dir:
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
            phase1 = run_phase1_pipeline(
                input_path.name,
                _declared_mime(input_path),
                content,
            )
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

            phase4_source = recovered or parsed
            if reference_index is not None:
                if reference_index < 1 or reference_index > len(phase4_source):
                    raise ValueError(f"reference_index out of range: {reference_index}")
                phase4_source = [phase4_source[reference_index - 1]]
            elif max_references is not None:
                phase4_source = phase4_source[: max(0, max_references)]

            if warm_db:
                warm_started = time.perf_counter()
                _log_progress(progress, "Phase 4 warm-up start")
                warm_localdb_cache(db_path)
                payload["timings_ms"]["db_warmup"] = _round_ms(time.perf_counter() - warm_started)
                _log_progress(progress, f"Phase 4 warm-up complete elapsed_ms={payload['timings_ms']['db_warmup']}")

            phase_started = time.perf_counter()
            _log_progress(progress, f"Phase 4 start references={len(phase4_source)} relaxed={relaxed}")
            matched = []
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
            for index, parsed_result in enumerate(phase4_source, start=1):
                ref_started = time.perf_counter()
                doi_attempted = bool(
                    (parsed_result.match_preparation.lookup_key_fields.get("doi") if parsed_result.match_preparation else [])
                )
                _log_progress(
                    progress,
                    f"Phase 4 reference {index}/{len(phase4_source)} start reference_id={parsed_result.reference_id} ctype={parsed_result.ctype} doi_attempted={doi_attempted}",
                )
                result = match_reference(parsed_result, config=phase4_config)
                matched.append(result)
                ref_ms = _round_ms(time.perf_counter() - ref_started)
                result_payload = _serialize(result)
                _log_progress(
                    progress,
                    (
                        f"Phase 4 reference {index}/{len(phase4_source)} complete"
                        f" reference_id={result.reference_id}"
                        f" status={result.status}"
                        f" strategy={result.strategy_used}"
                        f" candidate_count={result.lookup_trace.candidate_count}"
                        f" elapsed_ms={ref_ms}"
                    ),
                )
                _warn_slow_phase4(progress, result_payload)
            payload["timings_ms"]["phase4"] = _round_ms(time.perf_counter() - phase_started)
            _log_progress(progress, f"Phase 4 complete elapsed_ms={payload['timings_ms']['phase4']}")
            payload["phase4"] = _serialize(matched)
            payload["phase4_timing_summary"] = _phase4_timing_summary(payload["phase4"])
            payload["phase4_triage"] = _phase4_triage_summary(
                payload["phase3b"] or payload["phase3"] or [],
                payload["phase4"],
            )
        except Exception as exc:
            payload["status"] = "error"
            if payload["phase1"] is None:
                phase_name = "phase1"
            elif payload["phase2"] is None:
                phase_name = "phase2"
            elif payload["phase3"] is None:
                phase_name = "phase3"
            else:
                phase_name = "phase4"
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
    json_path = output_dir / f"{stem}.phase124.json"
    md_path = output_dir / f"{stem}.phase124.md"
    quick_md_path = output_dir / f"{stem}.phase124.quick.md"
    write_started = time.perf_counter()
    _log_progress(progress, "Artifact write start")
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(_phase4_markdown_report(payload), encoding="utf-8")
    quick_md_path.write_text(_phase4_quick_glance_report(payload), encoding="utf-8")
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
            )
        )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local Phase 1 to 4 review outputs for PDF and DOCX files."
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
        help="Directory where .phase124.json and .phase124.md files will be written.",
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
        help="Only send the first N recovered references into Phase 4.",
    )
    parser.add_argument(
        "--reference-index",
        type=int,
        default=None,
        help="Only send one 1-based recovered reference index into Phase 4.",
    )
    parser.add_argument(
        "--relaxed",
        action="store_true",
        help="Enable relaxed Phase 4 matching config for better recall (more candidates, broader queries).",
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
