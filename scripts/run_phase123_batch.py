from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import reference_gen2.api.settings as api_settings
from reference_gen2.finalization import finalize_cycle_report, sanitize_error_payload, serialize_sanitized_report
import reference_gen2.reference_parsing.anystyle_adapter as anystyle_adapter
import reference_gen2.security.temp_storage as temp_storage
from reference_gen2.reference_parsing import parse_references_with_recovery
from reference_gen2.reference_segmentation.models import ReferenceStyleHint
from reference_gen2.reference_segmentation import segment_references
from reference_gen2.services.document_pipeline import run_phase1_pipeline

SUPPORTED_SUFFIXES = {".pdf", ".docx"}
DEFAULT_INPUT_DIR = Path("manual_tests/input")
DEFAULT_OUTPUT_DIR = Path("manual_tests/output")


def _real_anystyle_executable() -> str | None:
    configured = os.getenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    if configured and shutil.which(configured):
        return configured
    discovered = shutil.which("anystyle")
    if discovered:
        return discovered
    candidate = os.path.expanduser("~/.local/share/gem/ruby/3.2.0/bin/anystyle")
    if os.path.isfile(candidate):
        return candidate
    return None


def _declared_mime(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        return "application/pdf"
    return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return _serialize(asdict(value))
    if isinstance(value, dict):
        return {str(key): _serialize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, tuple):
        return [_serialize(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def _error_payload(phase: str, exc: Exception) -> dict[str, Any]:
    return {
        "phase": phase,
        "code": getattr(exc, "code", exc.__class__.__name__),
        "message": getattr(exc, "message", str(exc)),
        "details": _serialize(getattr(exc, "details", {})),
    }


def _sanitized_markdown_report(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Sanitized Cycle Report: {payload.get('cycle_id', '-')}",
        "",
        f"- Status: `{payload.get('status', '')}`",
        f"- Source Mode: `{payload.get('source_mode', '')}`",
        f"- Style Hint: `{payload.get('style_hint', '')}`",
    ]

    error = payload.get("error")
    if error:
        lines.extend(
            [
                f"- Error Phase: `{error.get('phase', '')}`",
                f"- Error Code: `{error.get('code', '')}`",
                f"- Error Message: {error.get('message', '')}",
            ]
        )

    phase1 = payload.get("phase1")
    if phase1:
        report = phase1.get("report", {})
        lines.extend(
            [
                "",
                "## Phase 1",
                "",
                f"- Upload Kind: `{phase1.get('upload_kind', '')}`",
                f"- Heading Found: `{report.get('heading_found', False)}`",
                f"- Unit Count: `{report.get('unit_count', 0)}`",
                f"- Bibliography Characters: `{report.get('bibliography_char_count', 0)}`",
                f"- Warnings: `{report.get('warnings', [])}`",
            ]
        )

    phase2 = payload.get("phase2")
    if phase2:
        lines.extend(
            [
                "",
                "## Phase 2",
                "",
                f"- Reference Count: `{phase2.get('reference_count', 0)}`",
                f"- Style Hint Used: `{phase2.get('style_hint_used', '')}`",
                f"- Profile Used: `{phase2.get('profile_used', '')}`",
                f"- Warnings: `{phase2.get('warnings', [])}`",
            ]
        )

    phase3 = payload.get("phase3", [])
    if phase3:
        lines.extend(["", "## Phase 3", ""])
        for result in phase3:
            lines.extend(
                [
                    f"### Reference `{result.get('opaque_reference_id', '')}`",
                    "",
                    f"- Final CType: `{result.get('ctype', '')}`",
                    f"- Parser Backend: `{result.get('parser_backend', '')}`",
                    f"- Warnings: `{result.get('warnings', [])}`",
                    f"- Recovery Status: `{result.get('recovery_status', '')}`",
                    f"- Match Eligible: `{result.get('match_eligible', False)}`",
                    f"- Match Target: `{result.get('match_target', '')}`",
                    "",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def _sanitized_quick_glance_report(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Sanitized Quick Glance: {payload.get('cycle_id', '-')}",
        "",
        f"- Status: `{payload.get('status', '')}`",
    ]
    phase3 = payload.get("phase3b") or payload.get("phase3") or []
    if not phase3:
        lines.extend(["", "No sanitized Phase 3 results available.", ""])
        return "\n".join(lines).rstrip() + "\n"

    for result in phase3:
        lines.extend(
            [
                f"## Reference {result.get('opaque_reference_id', '')}",
                "",
                f"- Final Type: `{result.get('ctype', '')}`",
                f"- Warnings: `{result.get('warnings', [])}`",
                f"- Recovery Status: `{result.get('recovery_status', '')}`",
                f"- Match Eligible: `{result.get('match_eligible', False)}`",
                f"- Match Target: `{result.get('match_target', '')}`",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"


def _markdown_report(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Phase 1-3 Review: {payload['input_file']}",
        "",
        f"- Status: `{payload['status']}`",
    ]

    error = payload.get("error")
    if error:
        lines.extend(
            [
                f"- Error Phase: `{error['phase']}`",
                f"- Error Code: `{error['code']}`",
                f"- Error Message: {error['message']}",
            ]
        )
        if error.get("details"):
            lines.append(f"- Error Details: `{json.dumps(error['details'], ensure_ascii=False)}`")

    phase1 = payload.get("phase1")
    if phase1:
        bibliography = phase1.get("bibliography", {})
        upload = phase1.get("upload", {})
        lines.extend(
            [
                "",
                "## Phase 1",
                "",
                f"- Kind: `{upload.get('detected_kind', '')}`",
                f"- Filename: `{upload.get('original_filename', '')}`",
                f"- Heading: `{bibliography.get('heading', '')}`",
                f"- Bibliography Characters: `{bibliography.get('char_count', 0)}`",
            ]
        )

    phase2 = payload.get("phase2")
    if phase2:
        lines.extend(["", "## Phase 2", ""])
        lines.append(f"- Reference Count: `{len(phase2.get('references', []))}`")
        warnings = phase2.get("warnings", [])
        lines.append(f"- Warnings: `{warnings}`")
        for index, reference in enumerate(phase2.get("references", []), start=1):
            lines.extend(["", f"### Reference {index}", "", reference])

    phase3 = payload.get("phase3")
    if phase3:
        lines.extend(["", "## Phase 3", ""])
        for index, result in enumerate(phase3, start=1):
            parsed_data = result.get("parsed_data") or {}
            title = ", ".join(parsed_data.get("title", []))
            authors = parsed_data.get("author", [])
            author_labels = [
                ", ".join(part for part in [author.get("family"), author.get("given")] if part)
                or (author.get("literal") or "")
                for author in authors
            ]
            lines.extend(
                [
                    f"### Parsed Reference {index}",
                    "",
                    f"- Reference ID: `{result.get('reference_id', '')}`",
                    f"- Warnings: `{result.get('warnings', [])}`",
                    f"- Pre CType: `{(result.get('pre_classification') or {}).get('ctype', '')}`",
                    f"- Final CType: `{result.get('ctype', '')}`",
                    f"- AnyStyle Type: `{parsed_data.get('type', '')}`",
                    f"- Match Eligible: `{((result.get('match_preparation') or {}).get('eligible_for_db_match', False))}`",
                    f"- Match Target: `{((result.get('match_preparation') or {}).get('match_target', 'none'))}`",
                    f"- Title: `{title}`",
                    f"- Authors: `{author_labels}`",
                    f"- DOI: `{parsed_data.get('doi', [])}`",
                    f"- URL: `{parsed_data.get('url', [])}`",
                    f"- Classification Trace: `{result.get('classification_trace', [])}`",
                    "",
                    "```json",
                    json.dumps(result, indent=2, ensure_ascii=False),
                    "```",
                    "",
                ]
            )

    phase3b = payload.get("phase3b")
    if phase3b:
        lines.extend(["", "## Phase 3b", ""])
        for index, result in enumerate(phase3b, start=1):
            parsed_data = result.get("parsed_data") or {}
            lines.extend(
                [
                    f"### Recovered Reference {index}",
                    "",
                    f"- Recovery Status: `{result.get('recovery_status', 'unchanged')}`",
                    f"- Recovery Trace: `{result.get('recovery_trace', [])}`",
                    f"- Final CType: `{result.get('ctype', '')}`",
                    f"- AnyStyle Type: `{parsed_data.get('type', '')}`",
                    f"- Raw: {result.get('raw_reference', '')}",
                    "",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def _join_name_parts(items: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = ", ".join(
            part
            for part in [item.get("family"), item.get("given")]
            if isinstance(part, str) and part
        ).strip()
        if not label:
            literal = item.get("literal")
            if isinstance(literal, str):
                label = literal.strip()
        if label:
            labels.append(label)
    return labels


def _compact_field(value: Any) -> str:
    if isinstance(value, list):
        compacted = [str(item).strip() for item in value if str(item).strip()]
        return "; ".join(compacted) if compacted else "-"
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or "-"
    return "-"


def _quick_glance_report(payload: dict[str, Any]) -> str:
    lines: list[str] = [
        f"# Phase 3 Quick Glance: {payload['input_file']}",
        "",
        f"- Status: `{payload['status']}`",
    ]

    error = payload.get("error")
    if error:
        lines.extend(
            [
                f"- Error Phase: `{error['phase']}`",
                f"- Error Code: `{error['code']}`",
                f"- Error Message: {error['message']}",
                "",
            ]
        )
        return "\n".join(lines).rstrip() + "\n"

    phase3 = payload.get("phase3b") or payload.get("phase3") or []
    if not phase3:
        lines.extend(["", "No Phase 3 results available.", ""])
        return "\n".join(lines).rstrip() + "\n"

    for index, result in enumerate(phase3, start=1):
        parsed_data = result.get("parsed_data") or {}
        authors = _join_name_parts(parsed_data.get("author", []))
        editors = _join_name_parts(parsed_data.get("editor", []))
        publisher_org_parts = []
        for key in ("publisher", "institution", "organization"):
            publisher_org_parts.extend(parsed_data.get(key, []) or [])
        lines.extend(
            [
                f"## Reference {index}",
                "",
                f"- Raw: {result.get('raw_reference', '')}",
                f"- Final Type: `{result.get('ctype', '')}`",
                f"- AnyStyle Type: `{parsed_data.get('type', '')}`",
                f"- Parser Backend: `{result.get('parser_backend', '')}`",
                f"- Warnings: `{result.get('warnings', [])}`",
                f"- Recovery Status: `{result.get('recovery_status', 'unchanged')}`",
                "- Parsed Fields:",
                f"- Authors: {_compact_field(authors)}",
                f"- Editors: {_compact_field(editors)}",
                f"- Title: {_compact_field(parsed_data.get('title', []))}",
                f"- Container: {_compact_field(parsed_data.get('container_title', []))}",
                f"- Publisher/Org: {_compact_field(publisher_org_parts)}",
                f"- Year: {_compact_field(parsed_data.get('date', []))}",
                f"- Volume/Issue/Pages: {_compact_field(parsed_data.get('volume', []))} / {_compact_field(parsed_data.get('issue', []))} / {_compact_field(parsed_data.get('pages', []))}",
                f"- DOI/URL: {_compact_field(parsed_data.get('doi', []))} / {_compact_field(parsed_data.get('url', []))}",
                "",
            ]
        )

    return "\n".join(lines).rstrip() + "\n"


def _set_runtime_anystyle_executable(executable: str | None) -> None:
    if executable is None:
        return
    os.environ["REFERENCE_GEN2_ANYSTYLE_EXECUTABLE"] = executable
    api_settings.ANYSTYLE_EXECUTABLE = executable
    anystyle_adapter.ANYSTYLE_EXECUTABLE = executable
    api_settings.ANYSTYLE_ENABLED = True
    anystyle_adapter.ANYSTYLE_ENABLED = True


def _set_runtime_upload_tmp_dir(path: Path) -> tuple[Path, Path]:
    previous_settings_path = api_settings.UPLOAD_TMP_DIR
    previous_storage_path = temp_storage.UPLOAD_TMP_DIR
    api_settings.UPLOAD_TMP_DIR = path
    temp_storage.UPLOAD_TMP_DIR = path
    return previous_settings_path, previous_storage_path


def process_document(
    input_path: Path,
    output_dir: Path,
    *,
    style_hint: ReferenceStyleHint = "unknown",
    rich_output: bool = False,
) -> dict[str, Any]:
    _set_runtime_anystyle_executable(_real_anystyle_executable())

    payload: dict[str, Any]
    phase1 = None
    segmented = None
    parsed = None
    recovered = None
    sanitized_error = None
    rich_payload: dict[str, Any] = {
        "input_file": str(input_path),
        "style_hint": style_hint,
        "status": "ok",
        "phase1": None,
        "phase2": None,
        "phase3": None,
        "phase3b": None,
        "error": None,
    }

    with tempfile.TemporaryDirectory(prefix="reference_gen2_phase123_batch_") as temp_dir:
        previous_tmp_env = os.environ.get("REFERENCE_GEN2_UPLOAD_TMP_DIR")
        temp_path = Path(temp_dir)
        os.environ["REFERENCE_GEN2_UPLOAD_TMP_DIR"] = temp_dir
        previous_settings_path, previous_storage_path = _set_runtime_upload_tmp_dir(
            temp_path
        )
        try:
            content = input_path.read_bytes()
            phase1 = run_phase1_pipeline(
                input_path.name,
                _declared_mime(input_path),
                content,
            )
            rich_payload["phase1"] = {
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

            segmented = segment_references(
                phase1.bibliography,
                phase1.extraction,
                style_hint=style_hint,
            )
            rich_payload["phase2"] = _serialize(segmented)

            parsed, recovered = parse_references_with_recovery(
                segmented.references,
                style_hint=style_hint,
            )
            rich_payload["phase3"] = _serialize(parsed)
            rich_payload["phase3b"] = _serialize(recovered)
        except Exception as exc:
            rich_payload["status"] = "error"
            if rich_payload["phase1"] is None:
                phase_name = "phase1"
            elif rich_payload["phase2"] is None:
                phase_name = "phase2"
            else:
                phase_name = "phase3"
            rich_payload["error"] = _error_payload(phase_name, exc)
            sanitized_error = sanitize_error_payload(phase=phase_name, exc=exc)
        finally:
            api_settings.UPLOAD_TMP_DIR = previous_settings_path
            temp_storage.UPLOAD_TMP_DIR = previous_storage_path
            if previous_tmp_env is None:
                os.environ.pop("REFERENCE_GEN2_UPLOAD_TMP_DIR", None)
            else:
                os.environ["REFERENCE_GEN2_UPLOAD_TMP_DIR"] = previous_tmp_env

    sanitized_report = finalize_cycle_report(
        style_hint=style_hint,
        phase1=phase1,
        phase2=segmented,
        phase3=parsed,
        phase3b=recovered,
        error=sanitized_error,
        source_mode="upload",
    )
    payload = rich_payload if rich_output else serialize_sanitized_report(sanitized_report)

    output_dir.mkdir(parents=True, exist_ok=True)
    stem = input_path.stem
    json_path = output_dir / f"{stem}.phase123.json"
    md_path = output_dir / f"{stem}.phase123.md"
    quick_md_path = output_dir / f"{stem}.phase123.quick.md"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if rich_output:
        md_path.write_text(_markdown_report(payload), encoding="utf-8")
        quick_md_path.write_text(_quick_glance_report(payload), encoding="utf-8")
    else:
        md_path.write_text(_sanitized_markdown_report(payload), encoding="utf-8")
        quick_md_path.write_text(_sanitized_quick_glance_report(payload), encoding="utf-8")
    return payload


def run_batch(
    input_path: Path,
    output_dir: Path,
    *,
    recursive: bool = False,
    style_hint: ReferenceStyleHint = "unknown",
    rich_output: bool = False,
) -> list[dict[str, Any]]:
    executable = _real_anystyle_executable()
    _set_runtime_anystyle_executable(executable)

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
                style_hint=style_hint,
                rich_output=rich_output,
            )
        )
    return results


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run local Phase 1 to 3 review outputs for PDF and DOCX files."
    )
    parser.add_argument(
        "input_path",
        nargs="?",
        default=str(DEFAULT_INPUT_DIR),
        help="Directory or single .pdf/.docx file to process.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where .phase123.json and .phase123.md files will be written.",
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
        "--rich-output",
        action="store_true",
        help="Write rich debug artifacts with raw content instead of the default sanitized outputs.",
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

    results = run_batch(
        input_path,
        output_dir,
        recursive=args.recursive,
        style_hint=args.style_hint,
        rich_output=args.rich_output,
    )
    if not results:
        print(f"No supported .pdf or .docx files found in {input_path}")
        return 0

    success_count = sum(1 for result in results if result["status"] == "ok")
    print(f"Processed {len(results)} file(s). Successes: {success_count}. Output: {output_dir}")
    for result in results:
        status = result["status"]
        label = result.get("input_file") or result.get("cycle_id") or "<sanitized-cycle>"
        print(f"- [{status}] {label}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
