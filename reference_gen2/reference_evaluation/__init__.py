"""Phase 5: Final Match Evaluation and Confidence Scoring.

This package consumes Phase 3 parsed references and Phase 4 match results,
computes final confidence scores, assigns user-facing statuses, and prepares
report-ready evidence for static HTML generation.

Phase 5 owns:
- final confidence scoring policy
- acceptance and review thresholds
- ambiguity penalties and top-2 comparison logic
- type-specific final verification rules
- report-facing explanation fields

Phase 5 does not own:
- database lookup (Phase 4)
- candidate retrieval (Phase 4)
- reference parsing (Phase 3)
- citeproc rendering (Phase 6A)
- HTML templating (Phase 6 / Phase B)
"""

from reference_gen2.reference_evaluation.models import (
    Phase5BatchInput,
    Phase5EvidenceCheck,
    Phase5ConfidenceName,
    Phase5MatchEvaluation,
    Phase5ReportSignals,
    Phase5RuntimeConfig,
    Phase5ScoreBreakdown,
    Phase5StatusName,
)
from reference_gen2.reference_evaluation.service import (
    evaluate_reference,
    evaluate_references,
)

__all__ = [
    "Phase5BatchInput",
    "Phase5EvidenceCheck",
    "Phase5ConfidenceName",
    "Phase5MatchEvaluation",
    "Phase5ReportSignals",
    "Phase5RuntimeConfig",
    "Phase5ScoreBreakdown",
    "Phase5StatusName",
    "evaluate_reference",
    "evaluate_references",
]
