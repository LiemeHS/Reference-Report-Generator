from __future__ import annotations

import logging
import time

from reference_gen2.api.settings import LOG_ENABLED, LOG_LEVEL, LOG_PIPELINE_EVENTS
from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.bibliography_detection import detect_bibliography
from reference_gen2.document_extraction import extract_document_text
from reference_gen2.document_intake import receive_upload_context
from reference_gen2.extractors.models import DocumentExtraction
from reference_gen2.pipeline_models import (
    Phase1DocumentReport,
    Phase1PipelineResult,
    Phase1ReportContext,
    UploadReceipt,
)
from reference_gen2.extractors.models import ExtractionError
from reference_gen2.security.file_validation import StoredUpload, UploadValidationError

logger = logging.getLogger(__name__)
logger.setLevel(LOG_LEVEL)


def _format_log_fields(fields: dict[str, object]) -> str:
    return " ".join(f"{key}={value!r}" for key, value in fields.items())


def _log_pipeline_event(level: int, event: str, **fields: object) -> None:
    if not LOG_ENABLED or not LOG_PIPELINE_EVENTS:
        return
    logger.log(level, "event=%s %s", event, _format_log_fields(fields))


def _build_report_context(
    *,
    stored: StoredUpload,
    extraction: DocumentExtraction,
    bibliography: BibliographySection,
    extraction_time_ms: float,
) -> Phase1ReportContext:
    warnings = list(extraction.warnings) + list(bibliography.warnings)
    document = Phase1DocumentReport(
        original_filename=stored.original_filename,
        detected_kind=stored.detected_kind,
        file_size_bytes=stored.size_bytes,
        extraction_time_ms=extraction_time_ms,
        heading=bibliography.heading,
        heading_found=bibliography.heading is not None,
        heading_unit_index=bibliography.heading_unit_index,
        start_unit_index=bibliography.start_unit_index,
        end_unit_index=bibliography.end_unit_index,
        unit_count=bibliography.end_unit_index - bibliography.start_unit_index + 1,
        bibliography_char_count=len(bibliography.text),
        warnings=warnings,
    )
    return Phase1ReportContext(
        source_mode="upload",
        document=document,
        document_summary=(
            f"Uploaded {stored.original_filename} ({stored.detected_kind}), "
            f"bibliography heading: {bibliography.heading or 'not found'}"
        ),
        extraction_warnings=warnings,
    )


def run_phase1_pipeline(
    filename: str,
    declared_mime: str | None,
    content: bytes,
) -> Phase1PipelineResult:
    """Run the closed Phase 1 document path and stop at bibliography detection."""
    pipeline_started = time.perf_counter()
    try:
        with receive_upload_context(filename, declared_mime, content) as stored:
            extraction = extract_document_text(stored)
            extraction_time_ms = round(
                (time.perf_counter() - pipeline_started) * 1000, 2
            )
            bibliography = detect_bibliography(extraction)
            report_context = _build_report_context(
                stored=stored,
                extraction=extraction,
                bibliography=bibliography,
                extraction_time_ms=extraction_time_ms,
            )
            result = Phase1PipelineResult(
                upload=UploadReceipt.from_stored_upload(stored),
                extraction=extraction,
                bibliography=bibliography,
                report_context=report_context,
            )

        total_time_ms = round((time.perf_counter() - pipeline_started) * 1000, 2)
        _log_pipeline_event(
            logging.INFO,
            "phase1.pipeline_success",
            kind=result.upload.detected_kind,
            size_bytes=result.upload.size_bytes,
            extraction_ms=result.report_context.document.extraction_time_ms,
            total_ms=total_time_ms,
            heading_found=result.report_context.document.heading_found,
            heading_unit_index=result.report_context.document.heading_unit_index,
            unit_count=result.report_context.document.unit_count,
            warnings_count=len(result.report_context.document.warnings),
        )
        return result
    except UploadValidationError as exc:
        _log_pipeline_event(
            logging.WARNING,
            "phase1.pipeline_upload_rejected",
            code=exc.code,
            http_status=exc.http_status,
            declared_mime=declared_mime or "",
            size_bytes=len(content),
            total_ms=round((time.perf_counter() - pipeline_started) * 1000, 2),
        )
        raise
    except ExtractionError as exc:
        _log_pipeline_event(
            logging.WARNING,
            "phase1.pipeline_extraction_failed",
            code=exc.code,
            http_status=exc.http_status,
            size_bytes=len(content),
            total_ms=round((time.perf_counter() - pipeline_started) * 1000, 2),
        )
        raise
    except Exception as exc:
        code = getattr(exc, "code", "phase1_pipeline_failed")
        http_status = getattr(exc, "http_status", 500)
        _log_pipeline_event(
            logging.WARNING,
            "phase1.pipeline_detection_failed",
            code=code,
            http_status=http_status,
            size_bytes=len(content),
            total_ms=round((time.perf_counter() - pipeline_started) * 1000, 2),
        )
        raise
