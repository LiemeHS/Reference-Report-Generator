# Phase 5 Matching and Scoring Design

This document proposes the Phase 5 boundary and scoring policy for
`Reference_Gen2`.

For the current implemented scoring, status, evidence-label, and best-candidate
rules, see [`docs/phase5_decision_policy.md`](./phase5_decision_policy.md).

It is intentionally downstream of the current Phase 1-4 contract in
[`docs/phase_boundaries.md`](./phase_boundaries.md). The goal is to define the
first phase that turns a Phase 4 provisional candidate into a final
report-facing judgement.

## Why Phase 5 Exists

Phase 4 already owns:

- local DB lookup
- candidate normalization
- deterministic provisional ordering
- provisional statuses like `matched_provisional` and `candidate_only`

Phase 4 explicitly does **not** own:

- final confidence scoring and threshold policy
- accepted-match policy for the end-user report
- report/UI state assembly

That makes Phase 5 the correct home for:

- final confidence scoring
- final acceptance or review decisions
- ambiguity handling between the top candidates
- report-facing explanation generation

Phase 5 should therefore be the **decision layer**, not a second retrieval
layer.

## Boundary Summary

### Purpose

Evaluate the best retained Phase 4 candidate in context, compute final match
confidence, decide the user-facing outcome, and emit report-ready evidence for a
static webpage.

### Owns

- final confidence scoring policy
- acceptance and review thresholds
- ambiguity penalties and top-2 comparison logic
- type-specific final verification rules
- report-facing explanation fields derived from Phase 3 and Phase 4 outputs

### Canonical Input

- one `ParsedReferenceResult`
- one `Phase4MatchResult`
- optional Phase 5 runtime config with thresholds and weights

For batch orchestration:

- `phase3`
- optional `phase3b`
- `phase4`

When both `phase3` and `phase3b` are available, Phase 5 should consume the same
reference view that Phase 4 consumed, which in practice means `phase3b` by
default.

### Input Ownership

Phase 5 should consume both public upstream handoffs directly:

- `ParsedReferenceResult` for the input-side parsed reference
- `Phase4MatchResult` for the candidate-side retrieval result

This is cleaner than forcing Phase 4 to carry parser-owned fields forward.
Phase 3 remains the canonical source of parsed input truth, while Phase 4
remains the canonical source of candidate and retrieval truth.

An orchestration-level wrapper such as `Phase5Input` is acceptable for transport
convenience, but it does not change phase ownership.

Embedding the full `ParsedReferenceResult` inside `Phase4MatchResult` is not the
preferred design, because it would blur retrieval ownership and duplicate
parser-owned state.

### Canonical Output

- one `Phase5MatchEvaluation`
- one stable Phase 5 result record per reference

### Allowed Responsibilities

- interpret Phase 4 `best_candidate`, `top_candidates`, `match_signals`, and
  `reasons`
- combine Phase 3 context with Phase 4 candidate evidence
- assign final confidence and final status
- detect ambiguous near-ties between top candidates
- downgrade provisional matches that are structurally suspicious
- upgrade strong `candidate_only` cases when Phase 5 policy says the evidence is
  sufficient
- emit human-readable explanation snippets for the final static report

### Forbidden Responsibilities

- running new DB queries
- changing Phase 4 candidate retrieval order
- reparsing references
- mutating `ParsedReferenceResult`
- citeproc rendering
- static webpage templating itself

## Relationship to the Old Matcher

The old matching notes are still useful conceptually:

- compare input-side normalized metadata against candidate-side normalized
  metadata
- score several evidence dimensions separately
- rank and explain using both numeric score and reason tags
- add penalties when a candidate is structurally suspicious

What changes in the new architecture is **where that logic lives**.

In the old codebase, retrieval and scoring were tightly coupled. In
`Reference_Gen2`, Phase 4 already does retrieval and provisional ordering, so
Phase 5 should consume Phase 4's retained candidates and perform the **final
policy decision**.

That separation is healthier because:

- Phase 4 can stay retrieval-oriented and provider-agnostic
- Phase 5 can evolve scoring policy without changing SQL strategy behavior
- the static report can expose stable evidence fields instead of adapter logic

## Recommended Data Model

The exact class names can change, but the output should look roughly like this:

```python
@dataclass(frozen=True)
class Phase5ScoreBreakdown:
    raw_score: float
    confidence_score: float
    title_score: float
    author_score: float
    year_score: float
    container_score: float
    doi_score: float
    metadata_score: float
    ambiguity_penalty: float
    structure_penalty: float
    type_penalty: float


@dataclass(frozen=True)
class Phase5ReportSignals:
    final_evidence_summary: list[str]
    strengths: list[str]
    concerns: list[str]
    review_flags: list[str]
    evidence_checks: list[Phase5EvidenceCheck]
    top_candidate_gap: float | None = None


@dataclass(frozen=True)
class Phase5MatchEvaluation:
    reference_id: str
    phase4_status: str
    final_status: str
    final_confidence: str
    accepted_candidate: LocalDbCandidate | None
    runner_up_candidate: LocalDbCandidate | None
    score_breakdown: Phase5ScoreBreakdown
    report_signals: Phase5ReportSignals
    reasons: list[str]
    warnings: list[str]
```

## Recommended Final Statuses

Phase 5 should translate Phase 4 provisional results into user-facing outcomes.

Recommended status set:

- `verified`
  - accepted as the final match with high confidence and no major contradiction
- `needs_review`
  - useful candidate exists, but a human should verify it
- `suspicious`
  - evidence is too weak or contradictory to trust the candidate
- `skipped`
  - inherited from unsupported/ineligible earlier phases
- `error`
  - inherited runtime failure

This keeps Phase 4's operational outcomes separate from the end-user judgement.

## Phase 5 Order of Operations

Each Phase 5 evaluation should flow in this order:

1. input capture
2. inherit non-decision outcomes from Phase 4 (`skipped`, `error`) and map Phase 4 `no_match` to `suspicious`
3. select the best and runner-up candidate from Phase 4 retained candidates
4. derive normalized comparable evidence fields from Phase 3 + candidate
5. compute component scores
6. apply type-specific structural checks
7. apply ambiguity penalties based on top-2 distance
8. resolve ambiguity into `verified`, `needs_review`, or `suspicious` instead of emitting ambiguity as the main final status
9. map final confidence score to final status
10. assemble report-facing explanation fields and evidence checks

## Core Scoring Policy

Phase 5 should compute one final confidence score between `0.0` and `1.0`.

The main idea is:

- use Phase 4 `match_signals` as a compact evidence summary
- optionally refine them with direct field comparison from Phase 3 and the
  candidate metadata
- add penalties for ambiguity and structural mismatch
- make the final decision from this Phase 5 score, not directly from Phase 4's
  provisional status

### Proposed Component Weights

These are a good starting point:

```text
title_score      0.30
author_score     0.22
year_score       0.10
container_score  0.10
doi_score        0.18
metadata_score   0.10
```

Then apply penalties:

```text
confidence_score =
    raw_component_score
    - ambiguity_penalty
    - structure_penalty
    - type_penalty
```

Clamp the result into `[0.0, 1.0]`.

### Why These Weights

- title remains the strongest generic signal
- authors are still highly important, but should not dominate DOI-backed cases
- DOI deserves its own strong channel in Phase 5 because an exact DOI match is
  qualitatively different from token overlap
- year and container matter, but should usually confirm rather than decide
- metadata score gives book chapters and journal articles a place to benefit
  from pages/volume/issue support without overfitting the whole system to those
  fields

## Component Definitions

### 1. DOI Score

Map `Phase4MatchSignals.doi_match_type` like this:

- `exact` -> `1.00`
- `equivalent` -> `0.95`
- `none` -> `0.00`
- `mismatch` -> `0.00` and add a major review flag

If the candidate has a DOI mismatch against an otherwise strong text match, the
candidate should almost never become `verified`.

### 2. Title Score

Title remains the primary non-identifier signal.

Base mapping from `Phase4MatchSignals.title_match_strength`:

- `exact_or_near_exact` -> `1.00`
- `strong` -> `0.82`
- `partial` -> `0.58`
- `weak` -> `0.25`
- `none` -> `0.00`

If direct token similarity is later exposed from Phase 4, Phase 5 can use it,
but it should not depend on adapter-private implementation details.

### 3. Author Score

Base mapping from `Phase4MatchSignals.author_match_strength`:

- `strong` -> `1.00`
- `partial` -> `0.60`
- `weak` -> `0.25`
- `none` -> `0.00`

Additional policy:

- if input authors exist and candidate authors exist but overlap is `none`,
  apply an extra structure penalty unless the input is clearly organization-led
- for `book_chapter` matched to a book-level record, missing chapter-author
  overlap should be tolerated more than for journal articles

### 4. Year Score

Map `Phase4MatchSignals.year_match_type` like this:

- `exact` -> `1.00`
- `near` -> `0.65`
- `missing` -> `0.35`
- `mismatch` -> `0.00`

Year mismatch should rarely kill a match by itself, but it should meaningfully
cap confidence.

### 5. Container / Publisher Score

Map `Phase4MatchSignals.container_match` like this:

- `yes` -> `1.00`
- `unknown` -> `0.45`
- `no` -> `0.00`

For books, this means publisher support.
For journal articles, this means journal/container support.
For book chapters, this often means containing book support.

### 6. Metadata Score

This component is type-sensitive.

For `journal_article`, use `Phase4MatchSignals.volume_issue_pages_match` support from:

- volume
- issue
- pages

Map:

- `exact` -> `1.00`
- `partial` -> `0.60`
- `unknown` -> `0.40`
- `mismatch` -> `0.00`

For `book`, metadata score may stay neutral unless edition or publisher-level
evidence is later surfaced publicly.

For `book_chapter`, metadata score should reward chapter pages or chapter DOI
when available, but remain neutral for book-level recovery candidates.

## Structural Penalties

These penalties are the main reason Phase 5 should exist separately from
retrieval.

### Ambiguity Penalty

If the runner-up candidate is too close to the top candidate, subtract a
penalty.

Recommended gap policy:

- gap `>= 0.20` -> no ambiguity penalty
- gap `0.10 - 0.19` -> `0.05`
- gap `0.05 - 0.09` -> `0.12`
- gap `< 0.05` -> `0.20` and likely final status `needs_review` unless contradiction pushes it to `suspicious`

Use the gap between the top two Phase 5 raw scores if available, otherwise the
top two Phase 4 ordering scores as the fallback.

### Structure Penalty

Apply when the candidate is internally suspicious despite decent overlap.

Examples:

- strong title match but DOI mismatch
- strong title match but zero personal-author overlap
- journal article title is strong but not exact, while author support is weak
  or absent (`JOURNAL_TITLE_AUTHOR_TENSION`)
- `book_chapter` input matched only to a book-level record with no chapter-level
  support
- journal article candidate lacking any container support when the input clearly
  has journal metadata

Suggested penalty range:

- minor structural concern: `0.05`
- medium concern: `0.10`
- major concern: `0.18`

### Type Penalty

Apply when the candidate granularity does not align with the input type.

Examples:

- `book_chapter` -> book-level record only
- article-like reference -> book-level candidate

Suggested policy:

- no penalty when record granularity aligns
- `0.08` when mismatch is explainable and still useful
- `0.18` when mismatch makes the result misleading

## Suggested Final Status Thresholds

Recommended initial mapping:

- `verified`
  - confidence `>= 0.85`
  - no major review flags
  - no severe ambiguity
- `needs_review`
  - confidence `0.45 - 0.69`
  - or major structural concern present
  - or title support is only partial despite otherwise plausible evidence
- `suspicious`
  - confidence `< 0.45`
  - or no candidate available
  - or major contradiction is present
  - or `JOURNAL_TITLE_AUTHOR_TENSION` identifies a strong-but-not-exact journal
    title with weak author support

These thresholds should live in `Phase5RuntimeConfig`, not be hard-coded into a
report template.

## Type-Specific Rules

### Journal Articles

Preferred strong pattern:

- exact/equivalent DOI
- strong title
- strong author overlap
- exact year
- journal/container support

Downgrade patterns:

- DOI mismatch
- strong title with no author support
- no journal support when the input clearly contains journal metadata

### Books

Preferred strong pattern:

- strong title
- strong or partial author overlap
- exact year
- publisher support when present

Books can tolerate missing metadata better than journal articles, but should be
penalized more for title vagueness because many books have subtitle variation.

### Book Chapters

This is the most important special case.

Phase 5 should explicitly distinguish between:

- `chapter-level confirmation`
  - chapter title and chapter-level metadata align
- `book-level recovery`
  - the containing book looks right, but the chapter itself is not fully
    confirmed

Recommended policy:

- chapter-level confirmation can reach `verified`
- book-level recovery can reach `verified` when containing-book title, editors,
  and year strongly identify the source
- book-level recovery with weak author/year support should usually become
  `suspicious`

## Report-Facing Output

Phase 5 should prepare data for a static webpage report directly.

That means each result should expose:

- `final_status`
- `final_confidence`
- `confidence_score`
- `accepted_candidate_summary`
- `runner_up_summary` when relevant
- `strengths`
- `concerns`
- `review_flags`
- `why_this_was_accepted`
- `why_this_was_not_verified`

Recommended candidate summary shape:

```python
@dataclass(frozen=True)
class Phase5CandidateSummary:
    record_id: str
    record_type: str
    title: str | None
    authors: list[str]
    issued_year: str | None
    doi: str | None
    container_title: str | None
    publisher: str | None
```

And for the webpage itself, the most useful explanation strings are short,
stable phrases such as:

- `Title and authors align strongly`
- `DOI matched exactly`
- `Year is close but not exact`
- `Matched at book level, chapter-level proof is limited`
- `Second candidate was too close to accept automatically`

## What Phase 5 Should Consume From Phase 4

Phase 5 should rely on Phase 4 public fields only:

- `status`
- `best_candidate`
- `top_candidates`
- `candidates`
- `reasons`
- `warnings`
- `strategy_used`
- `lookup_trace`
- `match_signals`
  - `doi_match_type`
  - `title_match_strength`
  - `author_match_strength`
  - `year_match_type`
  - `container_match`
  - `volume_issue_pages_match`
- `ordering_score`

It should not depend on:

- SQLite table design
- FTS query text
- provider-private ranking logic
- raw adapter rows beyond already exposed candidate metadata

## Recommended Cross-Phase Tweaks

Phase 5 can be implemented using the current public handoff, but two small
upgrades would make it stronger while staying boundary-safe.

### 1. Add Optional Numeric Similarity Fields to Phase 4 Signals

The current categorical strengths are enough to start, but it would help if
Phase 4 optionally exposed numeric support fields such as:

- `title_similarity: float | None`
- `author_overlap_ratio: float | None`
- `year_distance: int | None`

These remain Phase 4-owned public evidence fields, not adapter internals.

### 2. Expose Candidate Granularity Explicitly

For book-chapter handling, it would help if candidates exposed whether the match
is chapter-level or book-level, for example:

- `record_granularity: "article" | "book" | "chapter" | "unknown"`

That lets Phase 5 cap confidence without guessing from `source_table`.

## Batch Runner

The implemented review harness is `scripts/run_phase125_batch.py`.

It produces:

- `.phase125.json`
- `.phase125.md`
- `.phase125.quick.md`

The JSON artifact preserves the existing Phase 1-4 sections and adds:

- `phase5`
- `phase5_timing_summary`
- `timings_ms.phase5`

The user-facing final statuses emitted by Phase 5 are:

- `verified`
- `needs_review`
- `suspicious`
- `skipped`
- `error`

## Proposed Architecture Placement

Recommended ownership split:

- Phase 4
  - retrieve
  - normalize
  - provisionally rank
- Phase 5
  - evaluate
  - accept/downgrade
  - explain for reporting
- Finalization / report assembly
  - sanitize or serialize the Phase 5 results for the eventual static webpage

That keeps responsibilities clean:

- no SQL logic in Phase 5
- no final policy logic in Phase 4
- no decision logic in static report templating

## First Implementation Slice

The lowest-risk implementation order is:

1. introduce Phase 5 models and a `reference_gen2/reference_evaluation`
   package
2. consume `ParsedReferenceResult` + `Phase4MatchResult`
3. implement categorical scoring from existing Phase 4 `match_signals`
4. add top-2 ambiguity handling
5. emit report-facing explanation fields
6. only then decide whether Phase 4 needs small public evidence extensions

This gives us a working Phase 5 without reopening the Phase 4 provider boundary
too early.
