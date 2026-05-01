from __future__ import annotations

"""Phase 2 segmentation only.

This module answers "where are the reference boundaries inside the bibliography?"
and intentionally stops before parsing or classification.
"""

from reference_gen2.bibliography.models import BibliographySection
from reference_gen2.extractors.models import DocumentExtraction
from reference_gen2.extractors.models import ExtractionStats
from reference_gen2.extractors.models import TextUnit
from reference_gen2.reference_segmentation.models import (
    ReferenceStyleHint,
    SegmentationResult,
)
from reference_gen2.reference_segmentation.normalization import (
    normalize_reference_list_text,
    prepare_reference_text_input,
)
from reference_gen2.reference_segmentation.splitter import split_reference_items as _split


def split_reference_items(
    reference_list_text: str,
    extraction: DocumentExtraction,
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> list[str]:
    return _split(
        reference_list_text,
        extraction,
        style_hint=style_hint,
    ).references


def segment_references(
    bibliography: BibliographySection,
    extraction: DocumentExtraction,
    *,
    style_hint: ReferenceStyleHint = "unknown",
) -> SegmentationResult:
    reference_list_text = normalize_reference_list_text(
        bibliography,
        extraction,
        style_hint=style_hint,
    )
    outcome = _split(
        reference_list_text,
        extraction,
        style_hint=style_hint,
    )
    return SegmentationResult(
        reference_list_text=reference_list_text,
        references=outcome.references,
        warnings=outcome.warnings,
        style_hint_used=style_hint,
        profile_used=outcome.profile_used,
    )


def segment_reference_text(
    reference_list_text: str,
    *,
    style_hint: ReferenceStyleHint = "unknown",
    max_chars: int = 120000,
) -> SegmentationResult:
    prepared = prepare_reference_text_input(reference_list_text, max_chars=max_chars)
    extraction = _text_extraction_context(prepared)
    outcome = _split(
        prepared,
        extraction,
        style_hint=style_hint,
    )
    return SegmentationResult(
        reference_list_text=prepared,
        references=outcome.references,
        warnings=outcome.warnings,
        style_hint_used=style_hint,
        profile_used=outcome.profile_used,
    )


def _text_extraction_context(reference_list_text: str) -> DocumentExtraction:
    text_unit = TextUnit(
        unit_index=0,
        kind="text",
        label=None,
        text=reference_list_text,
        layout="normal",
    )
    return DocumentExtraction(
        source_kind="text",
        unit_count=1,
        text_units=[text_unit],
        warnings=[],
        stats=ExtractionStats(
            input_bytes=len(reference_list_text.encode("utf-8")),
            units_emitted=1,
            chars_emitted=len(reference_list_text),
            pages_seen=0,
            paragraphs_seen=0,
        ),
    )
