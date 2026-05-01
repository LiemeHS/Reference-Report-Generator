"""Phase 5 scoring logic for confidence computation and penalty calculation."""

from __future__ import annotations

from reference_gen2.reference_evaluation.models import Phase5RuntimeConfig
from reference_gen2.reference_evaluation.policy import has_journal_title_author_tension
from reference_gen2.reference_matching.models import (
    LocalDbCandidate,
    Phase4MatchResult,
    Phase4MatchSignals,
)
from reference_gen2.reference_parsing.models import ParsedReferenceResult


def compute_doi_score(match_signals: Phase4MatchSignals, *, source_has_doi: bool = True) -> float:
    """Compute DOI component score from Phase 4 match signals.

    Mapping:
    - exact -> 1.00
    - equivalent -> 0.95
    - none with source DOI -> 0.10
    - none without source DOI -> 1.00 (DOI is optional in APA)
    - mismatch -> 0.00
    """
    doi_signal = match_signals.doi_match_type
    if doi_signal == "exact":
        return 1.00
    elif doi_signal == "equivalent":
        return 0.95
    elif doi_signal == "none":
        if not source_has_doi:
            return 1.00
        return 0.10
    else:
        return 0.00


def compute_title_score(match_signals: Phase4MatchSignals) -> float:
    """Compute title component score from Phase 4 match signals.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Mapping:
    - exact_or_near_exact -> 1.00
    - strong -> 0.82
    - partial -> 0.56
    - weak -> 0.18
    - none -> 0.00
    """
    title_signal = match_signals.title_match_strength
    if title_signal == "exact_or_near_exact":
        return 1.00
    elif title_signal == "strong":
        return 0.82
    elif title_signal == "partial":
        return 0.56
    elif title_signal == "weak":
        return 0.18
    else:
        return 0.00


def compute_author_score(match_signals: Phase4MatchSignals) -> float:
    """Compute author component score from Phase 4 match signals.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Mapping:
    - strong -> 1.00
    - partial -> 0.65
    - weak -> 0.20
    - none -> 0.00
    """
    author_signal = match_signals.author_match_strength
    if author_signal == "strong":
        return 1.00
    elif author_signal == "partial":
        return 0.65
    elif author_signal == "weak":
        return 0.20
    else:
        return 0.00


def compute_year_score(match_signals: Phase4MatchSignals) -> float:
    """Compute year component score from Phase 4 match signals.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Mapping:
    - exact -> 1.00
    - near -> 0.55
    - missing -> 0.20
    - mismatch -> 0.00
    """
    year_signal = match_signals.year_match_type
    if year_signal == "exact":
        return 1.00
    elif year_signal == "near":
        return 0.55
    elif year_signal == "missing":
        return 0.20
    else:  # mismatch
        return 0.00


def compute_container_score(match_signals: Phase4MatchSignals) -> float:
    """Compute container/publisher component score from Phase 4 match signals.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Mapping:
    - yes -> 1.00
    - unknown -> 0.30
    - no -> 0.00
    """
    container_signal = match_signals.container_match
    if container_signal == "yes":
        return 1.00
    elif container_signal == "unknown":
        return 0.30
    else:  # no
        return 0.00


def compute_metadata_score(match_signals: Phase4MatchSignals, ctype: str) -> float:
    """Compute metadata component score from Phase 4 match signals.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Type-sensitive scoring:
    - journal_article: uses volume/issue/pages support
    - book: neutral (0.40) unless edition/publisher evidence available
    - book_chapter: rewards chapter pages or chapter DOI
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Mapping:
    - exact -> 1.00
    - partial -> 0.60
    - unknown -> 0.25
    - mismatch -> 0.00
    """
    metadata_signal = match_signals.volume_issue_pages_match
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    # For non-article types, default to neutral unless explicit signal
    if ctype not in ("journal_article", "book_chapter"):
        if metadata_signal == "unknown":
            return 0.25
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    if metadata_signal == "exact":
        return 1.00
    elif metadata_signal == "partial":
        return 0.60
    elif metadata_signal == "unknown":
        return 0.25
    else:  # mismatch
        return 0.00


def compute_ambiguity_penalty(
    top_candidates: list[LocalDbCandidate],
    config: Phase5RuntimeConfig,
) -> tuple[float, float | None]:
    """Compute ambiguity penalty based on gap between top 2 candidates.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Returns:
        (penalty, gap) where gap is None if < 2 candidates
    """
    if len(top_candidates) < 2:
        return 0.0, None
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    # Use ordering_score from Phase 4 for gap calculation
    top_score = top_candidates[0].ordering_score
    runner_up_score = top_candidates[1].ordering_score
    gap = abs(top_score - runner_up_score)
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    # Apply penalty based on gap thresholds
    if gap >= config.ambiguity_gap_safe:
        return 0.0, gap
    elif gap >= config.ambiguity_gap_minor:
        return config.ambiguity_penalty_minor, gap
    elif gap >= config.ambiguity_gap_moderate:
        return config.ambiguity_penalty_moderate, gap
    else:
        return config.ambiguity_penalty_severe, gap


def compute_structure_penalty(
    parsed: ParsedReferenceResult,
    phase4: Phase4MatchResult,
    config: Phase5RuntimeConfig,
    component_scores: dict[str, float] | None = None,
) -> float:
    """Compute structure penalty for internally suspicious matches.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Penalties applied for:
    - Strong title match but DOI mismatch
    - Strong title match but zero author overlap
    - Strong title with year mismatch
    - Journal article candidate lacking container support
    """
    if not phase4.best_candidate:
        return 0.0
<<<<<<< HEAD
    
    match_signals = phase4.best_candidate.match_signals
    penalty = 0.0
    
=======

    match_signals = phase4.best_candidate.match_signals
    penalty = 0.0

>>>>>>> f727102 (Update public release files)
    # DOI mismatch with strong title is major concern
    if match_signals.doi_match_type == "mismatch" and match_signals.title_match_strength in (
        "exact_or_near_exact",
        "strong",
    ):
        penalty += config.structure_penalty_major

    if has_journal_title_author_tension(parsed, match_signals, component_scores):
        penalty += config.structure_penalty_major
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    # For books, exact title + year + publisher is strong bibliographic support.
    # A missing/misparsed author should stay reviewable, but it is not by itself
    # a structural contradiction.
    has_book_identity_support = (
        parsed.ctype == "book"
        and match_signals.title_match_strength == "exact_or_near_exact"
        and match_signals.year_match_type == "exact"
        and match_signals.container_match == "yes"
    )

    # Strong title but no author overlap (unless org-led)
    if (
        match_signals.title_match_strength in ("exact_or_near_exact", "strong")
        and match_signals.author_match_strength == "none"
        and parsed.ctype not in ("report", "webpage")
        and not has_book_identity_support
    ):
        penalty += config.structure_penalty_medium

    if (
        match_signals.title_match_strength in ("exact_or_near_exact", "strong")
        and match_signals.year_match_type == "mismatch"
    ):
        penalty += config.structure_penalty_medium
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    # Journal article without container support
    if (
        parsed.ctype == "journal_article"
        and match_signals.container_match == "no"
        and parsed.match_preparation
        and parsed.match_preparation.lookup_key_fields.get("container_title")
    ):
        penalty += config.structure_penalty_minor
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    return penalty


def compute_type_penalty(
    parsed: ParsedReferenceResult,
    best_candidate: LocalDbCandidate | None,
    config: Phase5RuntimeConfig,
) -> float:
    """Compute type penalty for granularity mismatch.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Penalties applied when candidate granularity doesn't align with input type:
    - book_chapter -> book-level record only
    - article-like reference -> book-level candidate
    """
    if not best_candidate:
        return 0.0
<<<<<<< HEAD
    
    penalty = 0.0
    
=======

    penalty = 0.0

>>>>>>> f727102 (Update public release files)
    # Book chapter matched to book-level record.
    if parsed.ctype == "book_chapter":
        if best_candidate.record_granularity == "book":
            # Check if we have chapter-level confirmation
            match_signals = best_candidate.match_signals
            has_chapter_support = (
                match_signals.volume_issue_pages_match in ("exact", "partial")
                or match_signals.doi_match_type in ("exact", "equivalent")
            )
            if not has_chapter_support:
                penalty += config.type_penalty_minor
<<<<<<< HEAD
    
    # Article-like reference matched to book record.
    if parsed.ctype == "journal_article" and best_candidate.record_granularity == "book":
        penalty += config.type_penalty_major
    
=======

    # Article-like reference matched to book record.
    if parsed.ctype == "journal_article" and best_candidate.record_granularity == "book":
        penalty += config.type_penalty_major

>>>>>>> f727102 (Update public release files)
    return penalty


def compute_final_score(
    component_scores: dict[str, float],
    penalties: dict[str, float],
    config: Phase5RuntimeConfig,
) -> tuple[float, float]:
    """Compute final confidence score from components and penalties.
<<<<<<< HEAD
    
=======

>>>>>>> f727102 (Update public release files)
    Returns:
        (raw_score, final_score) where final_score is clamped to [0.0, 1.0]
    """
    # Compute weighted raw score
    raw_score = (
        component_scores["title"] * config.title_weight
        + component_scores["author"] * config.author_weight
        + component_scores["year"] * config.year_weight
        + component_scores["container"] * config.container_weight
        + component_scores["doi"] * config.doi_weight
        + component_scores["metadata"] * config.metadata_weight
    )
<<<<<<< HEAD
    
    # Apply penalties
    final_score = raw_score - sum(penalties.values())
    
    # Clamp to [0.0, 1.0]
    final_score = max(0.0, min(1.0, final_score))
    
=======

    # Apply penalties
    final_score = raw_score - sum(penalties.values())

    # Clamp to [0.0, 1.0]
    final_score = max(0.0, min(1.0, final_score))

>>>>>>> f727102 (Update public release files)
    return raw_score, final_score
