from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


TextUnitLayout = Literal["normal", "blank"]
PdfGapCategory = Literal["none", "small", "medium", "large"]
PdfIndentationChange = Literal["same", "indented", "outdented"]


@dataclass(frozen=True)
class TextUnit:
    unit_index: int
    kind: Literal["page", "paragraph", "text"]
    label: str | None
    text: str
    layout: TextUnitLayout | None = None


@dataclass(frozen=True)
class PdfLayoutHint:
    text: str
    unit_index: int
    page_index: int
    is_new_block: bool
    gap_before: PdfGapCategory
    indentation_change: PdfIndentationChange


@dataclass(frozen=True)
class ExtractionStats:
    input_bytes: int
    units_emitted: int
    chars_emitted: int
    pages_seen: int
    paragraphs_seen: int


@dataclass(frozen=True)
class DocumentExtraction:
    source_kind: Literal["pdf", "docx", "text"]
    unit_count: int
    text_units: list[TextUnit]
    warnings: list[str]
    stats: ExtractionStats
    pdf_layout_hints: list[PdfLayoutHint] | None = None


class ExtractionError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
