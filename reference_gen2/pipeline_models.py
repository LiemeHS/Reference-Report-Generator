from __future__ import annotations

from dataclasses import dataclass, field

from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.extractors.models import DocumentExtraction
from reference_gen2.security.file_validation import StoredUpload


@dataclass(frozen=True)
class DocumentInput:
    source_mode: str
    reference_list: str | None = None
    filename: str | None = None
    declared_mime: str | None = None


@dataclass(frozen=True)
class UploadReceipt:
    original_filename: str
    normalized_filename: str
    detected_kind: str
    declared_mime: str | None
    size_bytes: int

    @classmethod
    def from_stored_upload(cls, stored: StoredUpload) -> "UploadReceipt":
        return cls(
            original_filename=stored.original_filename,
            normalized_filename=stored.normalized_filename,
            detected_kind=stored.detected_kind,
            declared_mime=stored.declared_mime,
            size_bytes=stored.size_bytes,
        )


@dataclass(frozen=True)
class Phase1DocumentReport:
    original_filename: str
    detected_kind: str
    file_size_bytes: int
    extraction_time_ms: float
    heading: str | None
    heading_found: bool
    heading_unit_index: int | None
    start_unit_index: int
    end_unit_index: int
    unit_count: int
    bibliography_char_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Phase1ReportContext:
    source_mode: str
    document: Phase1DocumentReport
    document_summary: str
    extraction_warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "source_mode": self.source_mode,
            "document": {
                "original_filename": self.document.original_filename,
                "detected_kind": self.document.detected_kind,
                "file_size_bytes": self.document.file_size_bytes,
                "extraction_time_ms": self.document.extraction_time_ms,
                "heading": self.document.heading,
                "heading_found": self.document.heading_found,
                "heading_unit_index": self.document.heading_unit_index,
                "start_unit_index": self.document.start_unit_index,
                "end_unit_index": self.document.end_unit_index,
                "unit_count": self.document.unit_count,
                "bibliography_char_count": self.document.bibliography_char_count,
                "warnings": list(self.document.warnings),
            },
            "document_summary": self.document_summary,
            "extraction_warnings": list(self.extraction_warnings),
        }


@dataclass(frozen=True)
class Phase1PipelineResult:
    upload: UploadReceipt
    extraction: DocumentExtraction
    bibliography: BibliographySection
    report_context: Phase1ReportContext
