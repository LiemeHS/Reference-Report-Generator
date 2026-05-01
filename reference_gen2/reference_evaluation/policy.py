"""Phase 5 policy predicates shared by scoring and evidence labeling."""

from __future__ import annotations

from reference_gen2.reference_matching.models import Phase4MatchSignals
from reference_gen2.reference_parsing.models import ParsedReferenceResult


def has_journal_title_author_tension(
    parsed: ParsedReferenceResult,
    match_signals: Phase4MatchSignals,
    component_scores: dict[str, float] | None,
) -> bool:
    """Return true for strong-but-not-exact journal titles with weak authorship.

    Exact and near-exact title matches are intentionally excluded. This policy is
    for cases where the title is plausible but different enough that missing or
    weak author evidence should be treated as a structural contradiction.
    """
    if parsed.ctype != "journal_article":
        return False
    if match_signals.title_match_strength != "strong":
        return False
    if match_signals.author_match_strength in {"none", "weak"}:
        return True
    if component_scores is None:
        return False
    return component_scores.get("author", 1.0) <= 0.20
