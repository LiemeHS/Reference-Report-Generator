from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SanitizedDocumentSummary:
    source_kind: str
    size_bytes: int
    extraction_time_ms: float
    heading_found: bool
    heading_unit_index: int | None
    start_unit_index: int
    end_unit_index: int
    unit_count: int
    bibliography_char_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizedPhase1Summary:
    upload_kind: str
    report: SanitizedDocumentSummary
    extraction_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizedPhase2Summary:
    reference_count: int
    warnings: list[str] = field(default_factory=list)
    style_hint_used: str = "unknown"
    profile_used: str = "unknown_profile"


@dataclass(frozen=True)
class SanitizedPhase3ReferenceSummary:
    opaque_reference_id: str
    ctype: str
    parser_backend: str
    parser_model_used: str | None
    display_reference: str | None = None
    warnings: list[str] = field(default_factory=list)
    recovery_status: str = "unchanged"
    recovery_trace: list[str] = field(default_factory=list)
    match_eligible: bool = False
    match_target: str = "none"
    missing_fields_for_match: list[str] = field(default_factory=list)
    parsed_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedPhase4Summary:
    opaque_reference_id: str
    attempted: bool
    status: str
    strategy_used: str | None
    candidate_count: int
    best_record_id: str | None = None
    best_candidate_display: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedCitationRender:
    """Privacy-safe rendered citation for a matched candidate."""

    text: str
    html: str
    style: str
    locale: str
    warnings: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass(frozen=True)
class SanitizedPhase5Summary:
    """Privacy-safe Phase 5 evaluation summary for finalization.

    Includes display-safe reference and candidate strings for static reports.
    Excludes adapter payloads and structured lookup internals.
    """
    opaque_reference_id: str
    phase4_status: str
    final_status: str
    final_confidence: str
    confidence_score: float
    accepted_record_id: str | None = None
    runner_up_record_id: str | None = None
    accepted_match_display: str | None = None
    runner_up_match_display: str | None = None
    accepted_match_render: SanitizedCitationRender | None = None
    runner_up_match_render: SanitizedCitationRender | None = None
    reasons: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    evidence_checks: list[dict[str, str]] = field(default_factory=list)
    field_comparisons: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizedCycleError:
    phase: str
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedCycleReport:
    cycle_id: str
    status: Literal["ok", "error"]
    source_mode: str
    style_hint: str
    requested_style_hint: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    phase1: SanitizedPhase1Summary | None = None
    phase2: SanitizedPhase2Summary | None = None
    phase3: list[SanitizedPhase3ReferenceSummary] = field(default_factory=list)
    phase3b: list[SanitizedPhase3ReferenceSummary] = field(default_factory=list)
    phase4: list[SanitizedPhase4Summary] = field(default_factory=list)
    phase5: list[SanitizedPhase5Summary] = field(default_factory=list)
    error: SanitizedCycleError | None = None

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class SanitizedDocumentSummary:
    source_kind: str
    size_bytes: int
    extraction_time_ms: float
    heading_found: bool
    heading_unit_index: int | None
    start_unit_index: int
    end_unit_index: int
    unit_count: int
    bibliography_char_count: int
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizedPhase1Summary:
    upload_kind: str
    report: SanitizedDocumentSummary
    extraction_warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizedPhase2Summary:
    reference_count: int
    warnings: list[str] = field(default_factory=list)
    style_hint_used: str = "unknown"
    profile_used: str = "unknown_profile"


@dataclass(frozen=True)
class SanitizedPhase3ReferenceSummary:
    opaque_reference_id: str
    ctype: str
    parser_backend: str
    parser_model_used: str | None
    display_reference: str | None = None
    warnings: list[str] = field(default_factory=list)
    recovery_status: str = "unchanged"
    recovery_trace: list[str] = field(default_factory=list)
    match_eligible: bool = False
    match_target: str = "none"
    missing_fields_for_match: list[str] = field(default_factory=list)
    parsed_fields: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedPhase4Summary:
    opaque_reference_id: str
    attempted: bool
    status: str
    strategy_used: str | None
    candidate_count: int
    best_record_id: str | None = None
    best_candidate_display: str | None = None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedCitationRender:
    """Privacy-safe rendered citation for a matched candidate."""

    text: str
    html: str
    style: str
    locale: str
    warnings: list[str] = field(default_factory=list)
    partial: bool = False


@dataclass(frozen=True)
class SanitizedPhase5Summary:
    """Privacy-safe Phase 5 evaluation summary for finalization.
    
    Includes display-safe reference and candidate strings for static reports.
    Excludes adapter payloads and structured lookup internals.
    """
    opaque_reference_id: str
    phase4_status: str
    final_status: str
    final_confidence: str
    confidence_score: float
    accepted_record_id: str | None = None
    runner_up_record_id: str | None = None
    accepted_match_display: str | None = None
    runner_up_match_display: str | None = None
    accepted_match_render: SanitizedCitationRender | None = None
    runner_up_match_render: SanitizedCitationRender | None = None
    reasons: list[str] = field(default_factory=list)
    review_flags: list[str] = field(default_factory=list)
    evidence_checks: list[dict[str, str]] = field(default_factory=list)
    field_comparisons: list[dict[str, object]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SanitizedCycleError:
    phase: str
    code: str
    message: str
    details: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SanitizedCycleReport:
    cycle_id: str
    status: Literal["ok", "error"]
    source_mode: str
    style_hint: str
    requested_style_hint: str | None = None
    timings_ms: dict[str, float] = field(default_factory=dict)
    phase1: SanitizedPhase1Summary | None = None
    phase2: SanitizedPhase2Summary | None = None
    phase3: list[SanitizedPhase3ReferenceSummary] = field(default_factory=list)
    phase3b: list[SanitizedPhase3ReferenceSummary] = field(default_factory=list)
    phase4: list[SanitizedPhase4Summary] = field(default_factory=list)
    phase5: list[SanitizedPhase5Summary] = field(default_factory=list)
    error: SanitizedCycleError | None = None
