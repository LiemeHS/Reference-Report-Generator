# Phase 5 Decision Policy

This document describes the current Phase 5 decision policy. It is meant to
explain how Phase 5 turns a Phase 4 candidate set into report-facing scores,
statuses, evidence labels, and the displayed best candidate.

## Purpose

Phase 5 is the final decision layer for one parsed reference and one Phase 4
match result. Phase 4 may rank a DOI hit first because retrieval is provisional.
Phase 5 decides whether that candidate is actually trustworthy enough for the
report, and may select a better retained candidate when the evidence supports
that.

Phase 5 does not run new database queries and does not change Phase 4 output. It
only chooses how to evaluate and present the retained Phase 4 candidates.

## Candidate Meaning

The report label `Best candidate` means the candidate Phase 5 selected as the
best available database record for this reference. It does not always mean the
candidate is accepted as correct.

- `verified`: the best candidate is accepted with high confidence.
- `needs_review`: the best candidate may be useful, but a human should verify
  it.
- `suspicious`: the available evidence is weak or contradictory. The best
  candidate is shown for audit/debugging, not as an accepted match.
- `skipped` and `error`: inherited non-decision outcomes from Phase 4.

The JSON fields still use names such as `accepted_candidate` and
`accepted_match_display` for compatibility. In current report language, these
fields should be understood as the Phase 5 selected best candidate unless and
until the public schema is renamed.

## Component Scores

Phase 5 receives compact `match_signals` from Phase 4 and maps them to numeric
component scores.

DOI:

- `exact`: `1.00`
- `equivalent`: `0.95`
- `none` when the submitted reference has no DOI: `1.00`
- `none` when the submitted reference has a DOI that was not confirmed:
  `0.10`
- `mismatch`: `0.00`

DOI is optional in APA-style references. A missing submitted DOI is therefore
neutral for scoring even when the database candidate has a DOI. A submitted DOI
that cannot be confirmed, or that contradicts the candidate, remains evidence.

Title:

- `exact_or_near_exact`: `1.00`
- `strong`: `0.82`
- `partial`: `0.56`
- `weak`: `0.18`
- `none`: `0.00`

Author:

- `strong`: `1.00`
- `partial`: `0.65`
- `weak`: `0.20`
- `none`: `0.00`

Year:

- `exact`: `1.00`
- `near`: `0.55`
- `missing`: `0.20`
- `mismatch`: `0.00`

Container or publisher:

- `yes`: `1.00`
- `unknown`: `0.30`
- `no`: `0.00`

Metadata:

- `exact`: `1.00`
- `partial`: `0.60`
- `unknown`: `0.25`
- `mismatch`: `0.00`

Current weights:

- title: `0.36`
- author: `0.25`
- year: `0.12`
- container: `0.09`
- DOI: `0.12`
- metadata: `0.06`

The raw score is the weighted sum. The final confidence score subtracts
ambiguity, structure, and type penalties, then clamps to `0.0` through `1.0`.

## Penalties And Thresholds

Ambiguity uses the Phase 4 ordering-score gap between the top two Phase 5
ordered candidates:

- gap `>= 0.12`: no penalty
- gap `>= 0.08`: minor penalty `0.04`
- gap `>= 0.04`: moderate penalty `0.10`
- gap `< 0.04`: severe penalty `0.18`

Structural penalties currently apply when:

- a strong title has a DOI mismatch,
- a strong title has no author overlap,
- a strong title has a year mismatch,
- a journal article lacks expected container support.

Final confidence names:

- `high`: score `>= 0.82`
- `medium`: score `>= 0.65`
- `low`: score `>= 0.55`
- `none`: score `< 0.55`

Final status thresholds:

- below `0.55`: `suspicious`
- score `>= 0.82` with no major contradiction: `verified`
- close candidates with no major contradiction and score below verified:
  `needs_review`
- partial title evidence with otherwise useful support: `needs_review`
- otherwise: `needs_review`, unless contradictions push the case to
  `suspicious`

## DOI Metadata Conflict

A DOI record has metadata conflict when the candidate DOI exactly or
equivalently matches the submitted DOI, but at least two important metadata
signals contradict or fail to support the input. The current conflict signals
are:

- title is `none` or `weak`,
- author is `none` or `weak`,
- container is `no`,
- year is `mismatch`.

When this happens, Phase 5 emits `DOI_RECORD_METADATA_CONFLICT` and the report
flag `DOI_METADATA_CONFLICT`, unless Phase 5 selects a better candidate instead.

## DOI-Conflict Candidate Override

When the Phase 4 best candidate is a DOI hit with metadata conflict, Phase 5 may
select a different retained candidate as the best candidate.

The alternative must:

- have title support `strong` or `exact_or_near_exact`,
- have author support `partial` or `strong`,
- have DOI signal `none` or `mismatch`,
- beat the conflicted DOI candidate by at least
  `doi_conflict_override_min_confidence_gap`, currently `0.20`.

This rule handles cases where a submitted DOI resolves to the wrong record, but
Phase 4 also retained a strong title/author candidate with a different DOI. The
different DOI is still evidence, so the final status may remain `suspicious`;
the important change is that the report displays the better-supported record as
the best candidate instead of presenting the DOI-only record as if it were the
best match.

When an override is applied:

- `Best candidate` is the stronger title/author candidate,
- `Runner up` is the displaced DOI record,
- reasons include `phase5_doi_conflict_candidate_override`.

## Evidence Labels And Review Flags

Evidence checks are detailed report rows, such as:

- DOI rows with human-facing labels:
  - `DOI_EXTRACTED_FROM_REFERENCE` / `DOI_NOT_EXTRACTED_FROM_REFERENCE`
  - `EXTRACTED_DOI_FOUND_IN_DB` / `EXTRACTED_DOI_NOT_FOUND_IN_DB`
  - `EXTRACTED_DOI_MATCHES_CANDIDATE`,
    `EXTRACTED_DOI_EQUIVALENT_TO_CANDIDATE`,
    `EXTRACTED_DOI_CONTRADICTS_CANDIDATE`, or skipped variants
  - `DOI_RECORD_METADATA_CONFLICT`
- `TITLE_EXACT_OR_NEAR`, `TITLE_STRONG_MATCH`, `TITLE_PARTIAL_MATCH`,
  `TITLE_WEAK_MATCH`, `TITLE_NO_MATCH`
- `AUTHOR_STRONG_MATCH`, `AUTHOR_PARTIAL_MATCH`, `AUTHOR_WEAK_MATCH`,
  `AUTHOR_NO_MATCH`
- `YEAR_EXACT_MATCH`, `YEAR_NEAR_MATCH`, `YEAR_MISMATCH`,
  `YEAR_NOT_CONFIRMED`
- `CONTAINER_CONFIRMED`, `CONTAINER_MISMATCH`, `CONTAINER_NOT_CONFIRMED`
- `METADATA_EXACT_MATCH`, `METADATA_PARTIAL_MATCH`, `METADATA_MISMATCH`,
  `METADATA_NOT_CONFIRMED`
- `AMBIGUOUS_TOP_CANDIDATES`, `STRUCTURAL_CONTRADICTION`,
  `BOOK_LEVEL_RECOVERY`, `TYPE_GRANULARITY_MISMATCH`

Book/chapter granularity decisions use Phase 4's public
`record_granularity` field (`article`, `book`, `chapter`, or `unknown`).
Phase 5 should not infer this policy from SQL table names or strategy labels.
For book chapters recovered only at containing-book level, strong book title,
editor, and year evidence may produce a `verified` result. In that case
`BOOK_LEVEL_RECOVERY` is evidence context, not a review flag: the containing
book identity was verified from book-level evidence, while chapter-level title
confirmation was unavailable.

Review flags are sparse alert labels derived from warning/failing evidence
checks. Current user-facing flags include:

- `DOI_METADATA_CONFLICT`
- `DOI_MISMATCH`
- `YEAR_MISMATCH`
- `AUTHOR_MISMATCH`
- `TITLE_AUTHOR_TENSION`
- `CONTAINER_MISMATCH`
- `AMBIGUOUS_TOP_CANDIDATES`
- `BOOK_LEVEL_RECOVERY`
- `WEAK_TITLE_EVIDENCE`
- `STRUCTURAL_CONCERN`
