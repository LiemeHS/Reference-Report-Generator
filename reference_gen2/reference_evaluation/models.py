"""Phase 5 data models for final match evaluation and confidence scoring."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from reference_gen2.reference_matching.models import LocalDbCandidate, Phase4MatchResult
from reference_gen2.reference_parsing.models import ParsedReferenceResult

Phase5StatusName = Literal[
    "verified",
    "needs_review",
    "suspicious",
    "skipped",
    "error",
]
Phase5ConfidenceName = Literal["high", "medium", "low", "none"]
Phase5CheckStatus = Literal["pass", "warning", "fail", "not_applicable"]
Phase5FieldComparisonStatus = Literal["match", "partial", "found", "missing", "mismatch"]


@dataclass(frozen=True)
class Phase5FieldComparison:
    """One sanitized source-vs-candidate field comparison for reporting."""

    field_name: str
    label: str
    source_value: str
    found_value: str
    score: float | None
    status: Phase5FieldComparisonStatus


@dataclass(frozen=True)
class Phase5EvidenceCheck:
    """One report-facing Phase 5 evidence check."""

    code: str
    status: Phase5CheckStatus
    summary: str
    label: str = ""


@dataclass(frozen=True)
class Phase5ScoreBreakdown:
    """Detailed breakdown of Phase 5 confidence scoring components."""

    title_score: float
    author_score: float
    year_score: float
    container_score: float
    doi_score: float
    metadata_score: float
    raw_score: float
    ambiguity_penalty: float
    structure_penalty: float
    type_penalty: float
    confidence_score: float


@dataclass(frozen=True)
class Phase5ReportSignals:
    """Report-ready evidence and explanation strings for static HTML generation."""

    strengths: list[str]
    concerns: list[str]
    review_flags: list[str]
    evidence_checks: list[Phase5EvidenceCheck] = field(default_factory=list)
    field_comparisons: list[Phase5FieldComparison] = field(default_factory=list)
    final_evidence_summary: list[str] = field(default_factory=list)
    top_candidate_gap: float | None = None


@dataclass(frozen=True)
class Phase5MatchEvaluation:
    """Final Phase 5 evaluation result for one reference."""

    reference_id: str
    phase4_status: str
    final_status: Phase5StatusName
    final_confidence: Phase5ConfidenceName
    confidence_score: float
    # Compatibility name: this is Phase 5's selected best candidate. It is not
    # necessarily accepted as correct when final_status is needs_review or suspicious.
    accepted_candidate: LocalDbCandidate | None
    runner_up_candidate: LocalDbCandidate | None
    top_candidate_gap: float | None
    score_breakdown: Phase5ScoreBreakdown
    report_signals: Phase5ReportSignals
    reasons: list[str]
    warnings: list[str]


@dataclass(frozen=True)
class Phase5RuntimeConfig:
    """Runtime configuration for Phase 5 evaluation."""

    title_weight: float = 0.36
    author_weight: float = 0.25
    year_weight: float = 0.12
    container_weight: float = 0.09
    doi_weight: float = 0.12
    metadata_weight: float = 0.06

    ambiguity_gap_safe: float = 0.12
    ambiguity_gap_minor: float = 0.08
    ambiguity_gap_moderate: float = 0.04

    ambiguity_penalty_minor: float = 0.04
    ambiguity_penalty_moderate: float = 0.10
    ambiguity_penalty_severe: float = 0.18

    structure_penalty_minor: float = 0.05
    structure_penalty_medium: float = 0.12
    structure_penalty_major: float = 0.24

    type_penalty_minor: float = 0.10
    type_penalty_major: float = 0.22

    verified_threshold: float = 0.82
    needs_review_threshold: float = 0.55

    runner_up_gap_threshold: float = 0.30
    doi_conflict_override_min_confidence_gap: float = 0.20


@dataclass(frozen=True)
class Phase5BatchInput:
    """Convenience wrapper for batch Phase 5 evaluation."""

    parsed_results: list[ParsedReferenceResult]
    phase4_results: list[Phase4MatchResult]
    config: Phase5RuntimeConfig | None = None

    def __post_init__(self) -> None:
        if len(self.parsed_results) != len(self.phase4_results):
            raise ValueError(
                f"parsed_results ({len(self.parsed_results)}) and "
                f"phase4_results ({len(self.phase4_results)}) must have same length"
            )
