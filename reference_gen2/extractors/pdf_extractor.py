from __future__ import annotations

import re
from statistics import median

import pdfplumber

from reference_gen2.api.settings import EXTRACT_MAX_CHARS, PDF_MAX_PAGES
from reference_gen2.extractors.models import (
    DocumentExtraction,
    ExtractionError,
    ExtractionStats,
    PdfGapCategory,
    PdfIndentationChange,
    PdfLayoutHint,
    TextUnit,
)
from reference_gen2.security.file_validation import StoredUpload

_MULTI_BLANK_LINES_RE = re.compile(r"\n{3,}")
_MIN_COLUMN_LINE_COUNT = 4
_MIN_COLUMN_ROW_GAP = 12.0
_ROW_TOP_TOLERANCE = 3.0
_COLUMN_BUCKET_SIZE = 12.0


def _normalize_pdf_text(text: str) -> str:
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    normalized = "\n".join(line.rstrip() for line in normalized.split("\n"))
    normalized = _MULTI_BLANK_LINES_RE.sub("\n\n", normalized)
    return normalized.strip()


def _gap_category(gap: float, line_height_median: float) -> PdfGapCategory:
    if gap <= max(1.5, line_height_median * 0.15):
        return "none"
    if gap <= max(4.0, line_height_median * 0.55):
        return "small"
    if gap <= max(10.0, line_height_median * 1.1):
        return "medium"
    return "large"


def _indentation_change(current_x0: float, previous_x0: float | None) -> PdfIndentationChange:
    if previous_x0 is None:
        return "same"
    delta = current_x0 - previous_x0
    if delta >= 8:
        return "indented"
    if delta <= -8:
        return "outdented"
    return "same"


def _page_layout_hints(
    page: pdfplumber.page.Page,
    *,
    unit_index: int,
    page_index: int,
) -> list[PdfLayoutHint]:
    filtered_lines = _ordered_pdf_lines(page)
    return _pdf_layout_hints_from_lines(
        filtered_lines,
        unit_index=unit_index,
        page_index=page_index,
    )


def _pdf_layout_hints_from_lines(
    filtered_lines: list[dict[str, object]],
    *,
    unit_index: int,
    page_index: int,
) -> list[PdfLayoutHint]:
    if not filtered_lines:
        return []

    line_heights = [
        max(0.0, float(line.get("bottom", 0.0)) - float(line.get("top", 0.0)))
        for line in filtered_lines
    ]
    line_height_median = median(height for height in line_heights if height > 0) if any(
        height > 0 for height in line_heights
    ) else 10.0

    hints: list[PdfLayoutHint] = []
    previous_bottom: float | None = None
    previous_x0: float | None = None

    for line in filtered_lines:
        text = _normalize_pdf_text(str(line.get("text", "")))
        if not text:
            continue
        top = float(line.get("top", 0.0))
        bottom = float(line.get("bottom", top))
        x0 = float(line.get("x0", 0.0))
        gap_before_value = 0.0 if previous_bottom is None else max(0.0, top - previous_bottom)
        gap_before = _gap_category(gap_before_value, line_height_median)
        indentation_change = _indentation_change(x0, previous_x0)
        is_new_block = previous_bottom is None or gap_before in {"medium", "large"} or indentation_change == "outdented"
        hints.append(
            PdfLayoutHint(
                text=text,
                unit_index=unit_index,
                page_index=page_index,
                is_new_block=is_new_block,
                gap_before=gap_before,
                indentation_change=indentation_change,
            )
        )
        previous_bottom = bottom
        previous_x0 = x0

    return hints


def _line_sort_key(line: dict[str, object]) -> tuple[float, float]:
    return (float(line.get("top", 0.0)), float(line.get("x0", 0.0)))


def _ordered_pdf_lines(page: pdfplumber.page.Page) -> list[dict[str, object]]:
    words = page.extract_words(use_text_flow=False, keep_blank_chars=False) or []
    filtered_words = _filtered_pdf_words(words)
    if filtered_words:
        rows = _rows_from_words(filtered_words)
        split_x = _detect_column_split_x_from_rows(rows)
        if split_x is None:
            return _lines_from_rows(rows)

        left_words = [word for word in filtered_words if _word_center_x(word) < split_x]
        right_words = [word for word in filtered_words if _word_center_x(word) >= split_x]
        if len(left_words) >= _MIN_COLUMN_LINE_COUNT and len(right_words) >= _MIN_COLUMN_LINE_COUNT:
            return _lines_from_rows(_rows_from_words(left_words)) + _lines_from_rows(
                _rows_from_words(right_words)
            )

    raw_lines = page.extract_text_lines(strip=True, return_chars=False, layout=True) or []
    filtered_lines = [
        {**line, "text": _normalize_pdf_text(str(line.get("text", "")))}
        for line in raw_lines
        if _normalize_pdf_text(str(line.get("text", "")))
    ]
    return sorted(filtered_lines, key=_line_sort_key)


def _filtered_pdf_words(words: list[dict[str, object]]) -> list[dict[str, object]]:
    filtered: list[dict[str, object]] = []
    for word in words:
        text = str(word.get("text", "")).strip()
        if not text:
            continue
        filtered.append({**word, "text": text})
    return filtered


def _rows_from_words(words: list[dict[str, object]]) -> list[list[dict[str, object]]]:
    rows: list[list[dict[str, object]]] = []
    sorted_words = sorted(
        words,
        key=lambda word: (float(word.get("top", 0.0)), float(word.get("x0", 0.0))),
    )
    for word in sorted_words:
        top = float(word.get("top", 0.0))
        if not rows:
            rows.append([word])
            continue
        previous_row = rows[-1]
        previous_top = median(float(item.get("top", 0.0)) for item in previous_row)
        if abs(top - previous_top) <= _ROW_TOP_TOLERANCE:
            previous_row.append(word)
        else:
            rows.append([word])
    for row in rows:
        row.sort(key=lambda item: float(item.get("x0", 0.0)))
    return rows


def _detect_column_split_x_from_rows(rows: list[list[dict[str, object]]]) -> float | None:
    if len(rows) < _MIN_COLUMN_LINE_COUNT:
        return None

    candidates: list[float] = []
    for row in rows:
        if len(row) < 2:
            continue
        best_gap = 0.0
        best_split = None
        for left_word, right_word in zip(row, row[1:]):
            gap = float(right_word.get("x0", 0.0)) - float(left_word.get("x1", 0.0))
            if gap > best_gap:
                best_gap = gap
                best_split = float(left_word.get("x1", 0.0)) + gap / 2
        if best_split is not None and best_gap >= _MIN_COLUMN_ROW_GAP:
            candidates.append(best_split)

    if len(candidates) < _MIN_COLUMN_LINE_COUNT:
        return None

    buckets: dict[int, list[float]] = {}
    for candidate in candidates:
        bucket = int(round(candidate / _COLUMN_BUCKET_SIZE))
        buckets.setdefault(bucket, []).append(candidate)

    dominant_bucket = max(buckets.values(), key=len)
    if len(dominant_bucket) < max(_MIN_COLUMN_LINE_COUNT, len(rows) // 5):
        return None

    return median(dominant_bucket)


def _lines_from_rows(rows: list[list[dict[str, object]]]) -> list[dict[str, object]]:
    lines: list[dict[str, object]] = []
    for row in rows:
        text = " ".join(str(word.get("text", "")).strip() for word in row if str(word.get("text", "")).strip())
        normalized_text = _normalize_pdf_text(text)
        if not normalized_text:
            continue
        lines.append(
            {
                "text": normalized_text,
                "x0": min(float(word.get("x0", 0.0)) for word in row),
                "top": min(float(word.get("top", 0.0)) for word in row),
                "bottom": max(float(word.get("bottom", 0.0)) for word in row),
            }
        )
    return lines


def _word_center_x(word: dict[str, object]) -> float:
    return (float(word.get("x0", 0.0)) + float(word.get("x1", 0.0))) / 2


def _page_text_from_lines(lines: list[dict[str, object]]) -> str:
    return _normalize_pdf_text("\n".join(str(line.get("text", "")) for line in lines))


def extract_pdf_text(upload: StoredUpload) -> DocumentExtraction:
    warnings: list[str] = []
    text_units: list[TextUnit] = []
    pdf_layout_hints: list[PdfLayoutHint] = []
    empty_pages = 0
    chars_emitted = 0

    try:
        with pdfplumber.open(upload.temp_path) as pdf:
            pages_seen = len(pdf.pages)
            if pages_seen > PDF_MAX_PAGES:
                raise ExtractionError(
                    "page_limit_exceeded",
                    f"PDF has {pages_seen} pages, exceeding the limit of {PDF_MAX_PAGES}.",
                )

            for page_number, page in enumerate(pdf.pages, start=1):
                ordered_lines = _ordered_pdf_lines(page)
                extracted = _page_text_from_lines(ordered_lines)
                if not extracted:
                    extracted = _normalize_pdf_text(page.extract_text() or "")
                if not extracted:
                    empty_pages += 1
                    continue

                chars_emitted += len(extracted)
                if chars_emitted > EXTRACT_MAX_CHARS:
                    raise ExtractionError(
                        "text_too_large",
                        f"Extracted text exceeds the limit of {EXTRACT_MAX_CHARS} characters.",
                    )

                unit_index = len(text_units)
                text_units.append(
                    TextUnit(
                        unit_index=unit_index,
                        kind="page",
                        label=f"page-{page_number}",
                        text=extracted,
                    )
                )
                pdf_layout_hints.extend(
                    _pdf_layout_hints_from_lines(
                        ordered_lines or _ordered_pdf_lines(page),
                        unit_index=unit_index,
                        page_index=page_number - 1,
                    )
                )
    except ExtractionError:
        raise
    except Exception as exc:
        raise ExtractionError(
            "extraction_failed",
            f"PDF extraction failed: {exc}",
        ) from exc

    if not text_units:
        raise ExtractionError(
            "no_extractable_text",
            "PDF contains no extractable text.",
        )

    if empty_pages:
        warnings.append("empty_pages_skipped")
        if empty_pages < pages_seen:
            warnings.append("partial_text_extraction")

    stats = ExtractionStats(
        input_bytes=upload.size_bytes,
        units_emitted=len(text_units),
        chars_emitted=chars_emitted,
        pages_seen=pages_seen,
        paragraphs_seen=0,
    )
    return DocumentExtraction(
        source_kind="pdf",
        unit_count=len(text_units),
        text_units=text_units,
        pdf_layout_hints=pdf_layout_hints or None,
        warnings=warnings,
        stats=stats,
    )
