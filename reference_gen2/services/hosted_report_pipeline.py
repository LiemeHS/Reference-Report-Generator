"""Synchronous Phase 1-6 orchestration for hosted Phase 7 uploads."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
import time
from typing import Any

from reference_gen2.api.settings import (
    EXTRACT_MAX_CHARS,
    PDF_MAX_PAGES,
    UPLOAD_MAX_BYTES,
)
from reference_gen2.finalization import finalize_cycle_report
from reference_gen2.reference_styles import normalize_reference_style
from reference_gen2.reference_evaluation import Phase5RuntimeConfig, evaluate_reference
from reference_gen2.reference_matching import Phase4RuntimeConfig, match_reference
from reference_gen2.reference_parsing import parse_references_with_recovery
from reference_gen2.reference_segmentation import (
    ReferenceStyleHint,
    segment_reference_text,
    segment_references,
)
from reference_gen2.report_generation import render_html_report
from reference_gen2.services.citation_style_detection import (
    CitationStyleDetectionResult,
    detect_citation_style,
)
from reference_gen2.services.document_pipeline import run_phase1_pipeline

logger = logging.getLogger(__name__)

class HostedReportPipelineError(Exception):
    """Safe wrapper for Phase 1-6 hosted upload failures."""

    def __init__(
        self,
        *,
        phase: str,
        code: str,
        message: str,
        http_status: int = 500,
    ):
        super().__init__(message)
        self.phase = phase
        self.code = code
        self.message = message
        self.http_status = http_status


@dataclass(frozen=True)
class HostedReportPipelineResult:
    """Public-safe output from synchronous hosted report generation."""

    html: str
    status: str
    reference_count: int
    final_status_counts: dict[str, int] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    style_detection: CitationStyleDetectionResult = field(
        default_factory=CitationStyleDetectionResult
    )


_SAFE_PIPELINE_ERROR_MESSAGES = {
    "empty_file": "The uploaded file is empty.",
    "empty_filename": "The uploaded file has no usable filename.",
    "file_too_large": (
        f"The uploaded file is too large. The current limit is {UPLOAD_MAX_BYTES} bytes."
    ),
    "unsupported_extension": "Only PDF and DOCX files are supported.",
    "invalid_signature": (
        "The uploaded file does not appear to be a valid PDF or DOCX file."
    ),
    "invalid_pdf_container": "The uploaded PDF could not be inspected safely.",
    "invalid_docx_container": "The uploaded DOCX file could not be inspected safely.",
    "mime_mismatch": (
        "The uploaded file type does not match the declared content type."
    ),
    "suspicious_pdf_structure": (
        "The uploaded PDF has a structure that is too complex to inspect safely."
    ),
    "suspicious_docx_content": (
        "The uploaded DOCX contains content that is not allowed for safe processing."
    ),
    "security_scan_unconfigured": "Upload scanning is not configured on the server.",
    "security_scan_timeout": "The upload security scan timed out.",
    "security_scan_failed": "The upload security scan could not be completed.",
    "security_scan_rejected": "The upload was rejected by the security scan.",
    "page_limit_exceeded": (
        f"The PDF has too many pages. The current limit is {PDF_MAX_PAGES} pages."
    ),
    "text_too_large": (
        "The extracted document text is too large. "
        f"The current limit is {EXTRACT_MAX_CHARS} characters."
    ),
    "no_extractable_text": (
        "No readable text could be extracted from the document. If this is a "
        "scanned PDF, run OCR first and upload the OCR text or an OCR-enabled PDF."
    ),
    "extraction_timeout": (
        "Document text extraction took too long. Try a smaller file or paste the "
        "reference list as text."
    ),
    "extraction_failed": (
        "Document text extraction failed. Try exporting the document again or "
        "paste the reference list as text."
    ),
    "bibliography_heading_not_found": (
        "No bibliography or reference-list heading was found in the extracted text."
    ),
    "bibliography_detection_failed": "The bibliography section could not be detected.",
    "empty_bibliography_section": (
        "A bibliography heading was found, but no usable references followed it."
    ),
    "bibliography_section_too_short": "The detected bibliography section is too short to process.",
    "bibliography_section_too_large": "The detected bibliography section is too large to process.",
    "empty_reference_text": "No reference text was found to process.",
    "reference_text_too_large": (
        "The pasted reference text is too large to process in one request."
    ),
    "reference_text_invalid_characters": (
        "The reference text contains unsupported control characters."
    ),
    "segmentation_no_references": (
        "No individual references could be detected in the bibliography text."
    ),
    "invalid_style_hint": "The selected citation style is not supported.",
}


def _safe_pipeline_error_message(code: str) -> str:
    return _SAFE_PIPELINE_ERROR_MESSAGES.get(
        code,
        "Hosted report generation failed.",
    )


def normalize_style_hint(value: str | None) -> ReferenceStyleHint:
    """Validate and normalize a public style hint value."""
    style_hint = normalize_reference_style(value)
    if style_hint is None:
        raise HostedReportPipelineError(
            phase="request",
            code="invalid_style_hint",
            message="Invalid style hint.",
            http_status=400,
        )
    return style_hint


def _resolve_effective_style_hint(
    requested_style_hint: ReferenceStyleHint,
    segmented: Any,
    *,
    bibliography_heading: str | None = None,
) -> CitationStyleDetectionResult:
    """Resolve runtime style in auto mode from high-confidence list cues."""

    return detect_citation_style(
        requested_style_hint=requested_style_hint,
        segmentation_profile=str(getattr(segmented, "profile_used", "unknown_profile")),
        references=list(getattr(segmented, "references", [])),
        bibliography_heading=bibliography_heading,
    )


def _style_hint_from_detection(
    detection: CitationStyleDetectionResult,
) -> ReferenceStyleHint:
    if detection.confidence == "high":
        return detection.detected_style
    return "unknown"


def run_hosted_report_pipeline(
    *,
    filename: str,
    declared_mime: str | None,
    content: bytes,
    db_path: str,
    style_hint: str | None = "unknown",
) -> HostedReportPipelineResult:
    """Run Phase 1-6 and return only rendered sanitized report HTML.

    This service intentionally avoids script-only debug artifacts. The raw phase
    objects are held only in memory long enough to finalize the sanitized report.
    """
    normalized_style_hint = normalize_style_hint(style_hint)
    timings_ms: dict[str, float] = {}
    started = time.perf_counter()
    phase = "phase1"

    try:
        phase_started = time.perf_counter()
        phase1 = run_phase1_pipeline(filename, declared_mime, content)
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase2"
        phase_started = time.perf_counter()
        segmented = segment_references(
            phase1.bibliography,
            phase1.extraction,
            style_hint=normalized_style_hint,
        )
        timings_ms[phase] = _elapsed_ms(phase_started)
        style_detection = _resolve_effective_style_hint(
            normalized_style_hint,
            segmented,
            bibliography_heading=getattr(phase1.bibliography, "heading", None),
        )
        effective_style_hint = _style_hint_from_detection(style_detection)
        _log_style_detection(
            source_mode="upload",
            requested_style_hint=normalized_style_hint,
            detection=style_detection,
        )

        phase = "phase3"
        phase_started = time.perf_counter()
        parsed, recovered = parse_references_with_recovery(
            segmented.references,
            style_hint=effective_style_hint,
        )
        phase_source = recovered or parsed
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase4"
        phase_started = time.perf_counter()
        phase4_config = Phase4RuntimeConfig(
            local_db_path=db_path,
            prefer_recovered=True,
        )
        matched = [
            match_reference(parsed_result, config=phase4_config)
            for parsed_result in phase_source
        ]
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase5"
        phase_started = time.perf_counter()
        phase5_config = Phase5RuntimeConfig()
        phase5_results = [
            evaluate_reference(parsed_result, phase4_result, config=phase5_config)
            for parsed_result, phase4_result in zip(phase_source, matched)
        ]
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase6"
        phase_started = time.perf_counter()
        timings_ms["total"] = _elapsed_ms(started)
        report = finalize_cycle_report(
            style_hint=effective_style_hint,
            requested_style_hint=normalized_style_hint,
            timings_ms=timings_ms,
            phase1=phase1,
            phase2=segmented,
            phase3=parsed,
            phase3b=recovered,
            phase4=matched,
            phase5=phase5_results,
            source_mode="upload",
        )
        html = render_html_report(report)
        timings_ms[phase] = _elapsed_ms(phase_started)
        timings_ms["total"] = _elapsed_ms(started)
    except HostedReportPipelineError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", exc.__class__.__name__))
        raise HostedReportPipelineError(
            phase=phase,
            code=code,
            message=_safe_pipeline_error_message(code),
            http_status=int(getattr(exc, "http_status", 500)),
        ) from exc

    final_status_counts = _count_final_statuses(phase5_results)
    logger.info(
        "event=phase7.upload_pipeline_success status=ok references=%s final_status_counts=%s total_ms=%s",
        len(phase_source),
        final_status_counts,
        timings_ms["total"],
    )
    return HostedReportPipelineResult(
        html=html,
        status=report.status,
        reference_count=len(phase_source),
        final_status_counts=final_status_counts,
        timings_ms=timings_ms,
        style_detection=style_detection,
    )


def run_text_report_pipeline(
    *,
    reference_list_text: str,
    db_path: str,
    style_hint: str | None = "unknown",
    max_chars: int = 120000,
) -> HostedReportPipelineResult:
    """Run Phase 2-6 for public pasted reference-list text."""
    normalized_style_hint = normalize_style_hint(style_hint)
    timings_ms: dict[str, float] = {}
    started = time.perf_counter()
    phase = "phase2"

    try:
        phase_started = time.perf_counter()
        segmented = segment_reference_text(
            reference_list_text,
            style_hint=normalized_style_hint,
            max_chars=max_chars,
        )
        timings_ms[phase] = _elapsed_ms(phase_started)
        style_detection = _resolve_effective_style_hint(
            normalized_style_hint,
            segmented,
        )
        effective_style_hint = _style_hint_from_detection(style_detection)
        _log_style_detection(
            source_mode="text",
            requested_style_hint=normalized_style_hint,
            detection=style_detection,
        )

        phase = "phase3"
        phase_started = time.perf_counter()
        parsed, recovered = parse_references_with_recovery(
            segmented.references,
            style_hint=effective_style_hint,
        )
        phase_source = recovered or parsed
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase4"
        phase_started = time.perf_counter()
        phase4_config = Phase4RuntimeConfig(
            local_db_path=db_path,
            prefer_recovered=True,
        )
        matched = [
            match_reference(parsed_result, config=phase4_config)
            for parsed_result in phase_source
        ]
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase5"
        phase_started = time.perf_counter()
        phase5_config = Phase5RuntimeConfig()
        phase5_results = [
            evaluate_reference(parsed_result, phase4_result, config=phase5_config)
            for parsed_result, phase4_result in zip(phase_source, matched)
        ]
        timings_ms[phase] = _elapsed_ms(phase_started)

        phase = "phase6"
        phase_started = time.perf_counter()
        timings_ms["total"] = _elapsed_ms(started)
        report = finalize_cycle_report(
            style_hint=effective_style_hint,
            requested_style_hint=normalized_style_hint,
            timings_ms=timings_ms,
            phase1=None,
            phase2=segmented,
            phase3=parsed,
            phase3b=recovered,
            phase4=matched,
            phase5=phase5_results,
            source_mode="text",
        )
        html = render_html_report(report)
        timings_ms[phase] = _elapsed_ms(phase_started)
        timings_ms["total"] = _elapsed_ms(started)
    except HostedReportPipelineError:
        raise
    except Exception as exc:
        code = str(getattr(exc, "code", exc.__class__.__name__))
        raise HostedReportPipelineError(
            phase=phase,
            code=code,
            message=_safe_pipeline_error_message(code),
            http_status=int(getattr(exc, "http_status", 500)),
        ) from exc

    final_status_counts = _count_final_statuses(phase5_results)
    logger.info(
        "event=phase7.text_pipeline_success status=ok references=%s final_status_counts=%s total_ms=%s",
        len(phase_source),
        final_status_counts,
        timings_ms["total"],
    )
    return HostedReportPipelineResult(
        html=html,
        status=report.status,
        reference_count=len(phase_source),
        final_status_counts=final_status_counts,
        timings_ms=timings_ms,
        style_detection=style_detection,
    )


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _count_final_statuses(results: list[Any]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for result in results:
        status = str(getattr(result, "final_status", "unknown"))
        counts[status] = counts.get(status, 0) + 1
    return counts


def _log_style_detection(
    *,
    source_mode: str,
    requested_style_hint: ReferenceStyleHint,
    detection: CitationStyleDetectionResult,
) -> None:
    logger.info(
        "event=phase7.style_detection source_mode=%s requested_style=%s detected_style=%s confidence=%s signals=%s",
        source_mode,
        requested_style_hint,
        detection.detected_style,
        detection.confidence,
        detection.signals,
    )
