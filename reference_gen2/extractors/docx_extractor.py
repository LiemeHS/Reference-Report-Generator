from __future__ import annotations

from io import BytesIO

from docx import Document
from docx.document import Document as DocumentObject
from docx.oxml.table import CT_Tbl
from docx.oxml.text.paragraph import CT_P
from docx.table import Table, _Cell
from docx.text.paragraph import Paragraph

from reference_gen2.api.settings import EXTRACT_MAX_CHARS
from reference_gen2.extractors.models import DocumentExtraction, ExtractionError, ExtractionStats, TextUnit
from reference_gen2.security.file_validation import StoredUpload


def _normalize_docx_text(text: str) -> str:
    return (text or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _iter_block_items(parent: DocumentObject | _Cell):
    parent_element = parent.element.body if isinstance(parent, DocumentObject) else parent._tc
    for child in parent_element.iterchildren():
        if isinstance(child, CT_P):
            yield Paragraph(child, parent)
        elif isinstance(child, CT_Tbl):
            yield Table(child, parent)


def _paragraph_style_name(paragraph: Paragraph) -> str | None:
    try:
        style = paragraph.style
        if style is None:
            return None
        name = (style.name or "").strip()
        return name or None
    except Exception:
        return None


def extract_docx_text(upload: StoredUpload) -> DocumentExtraction:
    warnings: list[str] = []
    text_units: list[TextUnit] = []
    chars_emitted = 0
    paragraphs_seen = 0
    style_metadata_missing = False

    try:
        # Read the DOCX into memory first so python-docx does not keep a lock on
        # the temp file after extraction on Windows.
        document = Document(BytesIO(upload.temp_path.read_bytes()))

        def emit_paragraph(paragraph: Paragraph, *, label_override: str | None = None) -> None:
            nonlocal chars_emitted, paragraphs_seen, style_metadata_missing
            paragraphs_seen += 1
            normalized = _normalize_docx_text(paragraph.text)

            label = label_override
            if label is None:
                label = _paragraph_style_name(paragraph)
                if paragraph.style is not None and label is None:
                    style_metadata_missing = True

            chars_emitted += len(normalized)
            if chars_emitted > EXTRACT_MAX_CHARS:
                raise ExtractionError(
                    "text_too_large",
                    f"Extracted text exceeds the limit of {EXTRACT_MAX_CHARS} characters.",
                )

            text_units.append(
                TextUnit(
                    unit_index=len(text_units),
                    kind="paragraph",
                    label=label,
                    text=normalized,
                    layout="blank" if not normalized else "normal",
                )
            )

        for block in _iter_block_items(document):
            if isinstance(block, Paragraph):
                emit_paragraph(block)
                continue

            if isinstance(block, Table):
                # Nested tables are intentionally flattened only through their immediate cell paragraphs
                # in Phase 2; deeper nested-table handling is deferred.
                for row in block.rows:
                    for cell in row.cells:
                        for child in _iter_block_items(cell):
                            if isinstance(child, Paragraph):
                                emit_paragraph(child, label_override="table-cell")
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            "extraction_failed",
            f"DOCX extraction failed: {exc}",
        ) from exc

    if not text_units:
        raise ExtractionError(
            "no_extractable_text",
            "DOCX contains no extractable text.",
        )

    if style_metadata_missing:
        warnings.append("style_metadata_missing")

    stats = ExtractionStats(
        input_bytes=upload.size_bytes,
        units_emitted=len(text_units),
        chars_emitted=chars_emitted,
        pages_seen=0,
        paragraphs_seen=paragraphs_seen,
    )
    return DocumentExtraction(
        source_kind="docx",
        unit_count=len(text_units),
        text_units=text_units,
        warnings=warnings,
        stats=stats,
    )
