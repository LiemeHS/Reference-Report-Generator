from __future__ import annotations

from dataclasses import dataclass, field

from reference_gen2.extractors.models import DocumentExtraction, PdfLayoutHint
from reference_gen2.reference_segmentation.heuristics import (
    buffer_ends_with_author_separator,
    buffer_in_author_block,
    buffer_lacks_terminal_metadata,
    buffer_looks_finished,
    buffer_starts_like_reference_head,
    candidate_looks_like_ref_start,
    detect_marker_mode,
    is_obvious_continuation_line,
    line_has_year,
    looks_like_tail_fragment_start,
    looks_like_author_start,
    looks_like_org_website_start,
    looks_like_title_led_start,
    normalize_line,
    strip_list_marker,
)
from reference_gen2.reference_segmentation.models import (
    ReferenceSegmentationError,
    ReferenceStyleHint,
)
from reference_gen2.reference_segmentation.profiles import profile_for_style
from reference_gen2.reference_segmentation.profiles import infer_profile_for_lines
from reference_gen2.reference_segmentation.profiles import SegmentationProfile


@dataclass
class SplitOutcome:
    references: list[str]
    warnings: list[str] = field(default_factory=list)
    profile_used: str = "unknown_profile"


def _split_lines(text: str) -> list[str]:
    return [line.rstrip() for line in text.split("\n")]


def _pdf_hint_lookup(extraction: DocumentExtraction) -> dict[str, list[PdfLayoutHint]]:
    lookup: dict[str, list[PdfLayoutHint]] = {}
    for hint in extraction.pdf_layout_hints or []:
        normalized = normalize_line(hint.text)
        if not normalized:
            continue
        lookup.setdefault(normalized, []).append(hint)
    return lookup


def _pdf_hint_for_line(
    line: str,
    hint_lookup: dict[str, list[PdfLayoutHint]],
) -> PdfLayoutHint | None:
    normalized = normalize_line(line)
    if not normalized:
        return None
    matches = hint_lookup.get(normalized)
    if matches:
        return matches[0]
    for key, values in hint_lookup.items():
        if normalized.startswith(key) or key.startswith(normalized):
            return values[0]
    return None


def _docx_should_split_before_candidate(
    buffer: list[str],
    line: str,
    *,
    profile: SegmentationProfile,
    strong_start: bool,
) -> bool:
    if not strong_start:
        return False
    if is_obvious_continuation_line(line):
        return False
    if buffer_in_author_block(buffer) or buffer_ends_with_author_separator(buffer):
        return False
    if profile.conservative_unknown and not _buffer_starts_with_structured_reference(buffer):
        return False
    return True


def _buffer_starts_with_structured_reference(buffer: list[str]) -> bool:
    if not buffer:
        return False
    first_line = normalize_line(buffer[0])
    return looks_like_author_start(first_line) or looks_like_org_website_start(first_line)


def _pdf_should_split_before_candidate(
    buffer: list[str],
    line: str,
    *,
    profile: SegmentationProfile,
    strong_start: bool,
    pdf_hint: PdfLayoutHint | None,
) -> bool:
    if pdf_hint is None:
        return False
    if is_obvious_continuation_line(line):
        return False
    if buffer_in_author_block(buffer) or buffer_ends_with_author_separator(buffer):
        return False
    if profile.conservative_unknown and not _buffer_starts_with_structured_reference(buffer):
        return False
    if not strong_start:
        return False
    if pdf_hint.is_new_block and buffer_looks_finished(buffer):
        return True
    buffer_length = len(" ".join(buffer))
    joined_buffer = " ".join(buffer).strip()
    if joined_buffer.endswith(".") and line_has_year(joined_buffer) and buffer_length >= 40:
        return True
    if pdf_hint.is_new_block and buffer_length >= 100:
        return True
    if pdf_hint.gap_before in {"medium", "large"} and buffer_length >= 120:
        return True
    if pdf_hint.indentation_change == "outdented" and buffer_length >= 100:
        return True
    return False


def _pdf_tail_fragment_should_attach(
    buffer: list[str],
    line: str,
    *,
    pdf_hint: PdfLayoutHint | None,
) -> bool:
    if not buffer or not line.strip():
        return False
    if not buffer_starts_like_reference_head(buffer):
        return False
    if looks_like_tail_fragment_start(line):
        return True
    if looks_like_author_start(line) or looks_like_org_website_start(line):
        return False
    if pdf_hint is not None and pdf_hint.is_new_block and pdf_hint.indentation_change == "outdented":
        if buffer_looks_finished(buffer) and not looks_like_tail_fragment_start(line):
            return False
    if is_obvious_continuation_line(line) and buffer_lacks_terminal_metadata(buffer):
        return True
    return False


def _pdf_should_force_split_on_standalone_author_start(
    buffer: list[str],
    line: str,
) -> bool:
    joined_buffer = " ".join(buffer).strip()
    if not joined_buffer:
        return False
    if not looks_like_author_start(line):
        return False
    if not looks_like_org_website_start(joined_buffer):
        return False
    if not line_has_year(joined_buffer):
        return False
    if buffer_in_author_block(buffer) or buffer_ends_with_author_separator(buffer):
        return False
    if is_obvious_continuation_line(line):
        return False
    return True


def split_reference_items(
    reference_list_text: str,
    extraction: DocumentExtraction,
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> SplitOutcome:
    if not reference_list_text.strip():
        raise ReferenceSegmentationError(
            "empty_reference_list_text",
            "Reference segmentation received empty bibliography text.",
        )

    lines = _split_lines(reference_list_text)
    profile = (
        infer_profile_for_lines(lines)
        if style_hint == "unknown"
        else profile_for_style(style_hint)
    )
    marker_mode = detect_marker_mode(lines, extraction)
    pdf_hint_lookup = _pdf_hint_lookup(extraction) if extraction.source_kind == "pdf" else {}
    warnings: list[str] = []
    references: list[str] = []
    buffer: list[str] = []

    def flush_buffer() -> None:
        if not buffer:
            return
        joined = " ".join(part.strip() for part in buffer if part.strip()).strip()
        if joined:
            references.append(joined)
        buffer.clear()

    def explicit_marker_start(raw_line: str) -> tuple[str, bool]:
        stripped, had_marker = strip_list_marker(raw_line)
        if not had_marker:
            return normalize_line(raw_line), False
        if marker_mode is None:
            return normalize_line(raw_line), False
        return normalize_line(stripped), True

    index = 0
    while index < len(lines):
        raw_line = lines[index]
        line = normalize_line(raw_line)
        if not line:
            next_nonblank_index = index + 1
            while next_nonblank_index < len(lines) and not normalize_line(lines[next_nonblank_index]):
                next_nonblank_index += 1
            if not buffer:
                index += 1
                continue
            if next_nonblank_index >= len(lines):
                flush_buffer()
                index += 1
                continue
            next_line = normalize_line(lines[next_nonblank_index])
            next_pdf_hint = (
                _pdf_hint_for_line(next_line, pdf_hint_lookup)
                if extraction.source_kind == "pdf"
                else None
            )
            if extraction.source_kind == "pdf" and _pdf_tail_fragment_should_attach(
                buffer,
                next_line,
                pdf_hint=next_pdf_hint,
            ):
                warnings.append("segmentation_pdf_tail_continuation_attached")
                index += 1
                continue
            if extraction.source_kind == "text" and candidate_looks_like_ref_start(
                lines,
                next_nonblank_index,
                profile,
            ):
                flush_buffer()
            if candidate_looks_like_ref_start(
                lines,
                next_nonblank_index,
                profile,
            ) and buffer_looks_finished(buffer):
                flush_buffer()
            elif is_obvious_continuation_line(next_line):
                warnings.append("segmentation_ambiguous_layout")
            else:
                flush_buffer()
            index += 1
            continue

        line, had_explicit_marker = explicit_marker_start(raw_line)
        candidate_start = candidate_looks_like_ref_start(lines, index, profile)
        strong_start = (
            looks_like_author_start(line)
            or looks_like_org_website_start(line)
            or (profile.allow_title_led_starts and looks_like_title_led_start(line))
        )
        pdf_hint = _pdf_hint_for_line(line, pdf_hint_lookup) if extraction.source_kind == "pdf" else None

        if not buffer:
            buffer.append(line)
            index += 1
            continue

        if had_explicit_marker:
            flush_buffer()
            buffer.append(line)
            index += 1
            continue

        if candidate_start:
            if extraction.source_kind == "pdf" and _pdf_should_force_split_on_standalone_author_start(
                buffer,
                line,
            ):
                flush_buffer()
                buffer.append(line)
                warnings.append("segmentation_pdf_strict_author_split")
                index += 1
                continue
            if extraction.source_kind == "pdf" and _pdf_tail_fragment_should_attach(
                buffer,
                line,
                pdf_hint=pdf_hint,
            ):
                buffer.append(line)
                warnings.append("segmentation_pdf_tail_continuation_attached")
                index += 1
                continue
            if extraction.source_kind in {"docx", "text"} and _docx_should_split_before_candidate(
                buffer,
                line,
                profile=profile,
                strong_start=strong_start,
            ):
                flush_buffer()
                buffer.append(line)
                index += 1
                continue
            if extraction.source_kind == "pdf" and _pdf_should_split_before_candidate(
                buffer,
                line,
                profile=profile,
                strong_start=strong_start,
                pdf_hint=pdf_hint,
            ):
                flush_buffer()
                buffer.append(line)
                index += 1
                continue
            if buffer_in_author_block(buffer) or buffer_ends_with_author_separator(buffer):
                buffer.append(line)
                warnings.append("segmentation_ambiguous_layout")
                index += 1
                continue
            if profile.conservative_unknown and not _buffer_starts_with_structured_reference(buffer):
                buffer.append(line)
                warnings.append("segmentation_ambiguous_layout")
                index += 1
                continue
            if buffer_looks_finished(buffer):
                flush_buffer()
                buffer.append(line)
                index += 1
                continue
            if (
                strong_start
                and len(" ".join(buffer)) >= 220
                and not is_obvious_continuation_line(line)
                and not profile.conservative_unknown
            ):
                flush_buffer()
                buffer.append(line)
                warnings.append("segmentation_ambiguous_layout")
                index += 1
                continue
            buffer.append(line)
            warnings.append("segmentation_ambiguous_layout")
            index += 1
            continue

        buffer.append(line)
        index += 1

    flush_buffer()

    references = [item for item in references if item]
    if not references:
        raise ReferenceSegmentationError(
            "segmentation_no_references",
            "Reference segmentation did not produce any reference items.",
            details={"reference_list_char_count": len(reference_list_text)},
        )
    if len(references) > 1000:
        raise ReferenceSegmentationError(
            "segmentation_excessive_item_count",
            "Reference segmentation produced an implausibly large number of items.",
            details={"reference_count": len(references)},
        )

    deduped_warnings = list(dict.fromkeys(warnings))
    return SplitOutcome(
        references=references,
        warnings=deduped_warnings,
        profile_used=profile.name,
    )
