# Phase Contract Validation Plan

This document is a companion to
[`docs/phase_boundaries.md`](./phase_boundaries.md).

`phase_boundaries.md` defines the intended architecture contract. This plan
describes how to validate that the runtime behavior of Phases 1 through 6 still
follows that contract in practice, not only at the import boundary level.

## Why This Exists

The current boundary tests are useful, but they mostly prove static separation:

- packages do not import disallowed downstream modules
- sanitization boundaries still exist
- report generation still consumes finalized data only

Those checks do not fully prove the more important runtime question:

> Does each phase still behave like an input/output stage when exercised on real
> documents and realistic failure cases?

This plan is for a fuller audit of that runtime contract.

## Validation Goals

The validation pass should answer all of these clearly:

- each phase consumes only its intended public upstream output
- each phase produces a stable public output that can be recorded and replayed
- phase-specific fixes have not leaked responsibilities across phase boundaries
- representative PDFs and edge cases behave the same when phases are run
  independently from recorded upstream outputs
- finalization and Phase 6 still operate only on sanitized contracts

## Target Scope

This validation should cover at least:

- Phase 1: document intake, extraction, bibliography detection
- Phase 2: segmentation
- Phase 3 and 3b: parsing, repair, bounded adjacent-reference recovery
- Phase 4: candidate lookup and provisional ranking
- Phase 5: scoring, final status, evidence labeling
- Finalization and Phase 6/6A: sanitized report contract, citation rendering,
  static report generation

Phase 7 is out of scope except where it orchestrates phases through their
public inputs and outputs.

## Recommended Fixtures

Use a small but representative fixture set rather than a broad random sweep.
The validation set should include:

- `pdftest7.pdf` for parser repair and noisy PDF tails
- `pdftest8.pdf` for bibliography heading selection, book chapters, publisher
  recovery, and metadata normalization
- `pdftest1.pdf` as a stable reference-quality PDF baseline
- one DOCX fixture already used in parser/segmentation tests
- one plain pasted-text input case that starts directly at Phase 2

For each fixture, record the intended reason it is in scope so future audits do
not quietly replace difficult cases with easy ones.

## Validation Workstreams

### 1. Phase Interface Inventory

Write down the canonical runtime input and output for each phase as it exists
today.

For each phase, capture:

- public input types
- public output types
- allowed upstream dependencies
- forbidden extra inputs that would indicate contract drift

This should be derived from the actual code, not only the architecture doc.

### 2. Real Pipeline Trace Capture

For each validation fixture, capture a full run and store a structured trace of:

- Phase 1 output
- Phase 2 output
- Phase 3 output
- Phase 3b output
- Phase 4 output
- Phase 5 output
- finalized sanitized report payload

The trace should preserve enough information to replay downstream phases without
reopening the original document.

### 3. Phase Replay Checks

For each fixture, rerun each downstream phase from captured upstream outputs and
compare results against the original full pipeline run.

Minimum replay checks:

- Phase 2 from captured `BibliographySection` + `DocumentExtraction`
- Phase 3 from captured segmented raw references
- Phase 4 from captured parsed references
- Phase 5 from captured parsed reference + phase 4 result
- Finalization from captured phase outputs
- Phase 6 from finalized sanitized payload only

The key question is whether replayed outputs match the original outputs closely
enough to prove that a phase is not quietly relying on hidden runtime state.

### 4. Hidden-Coupling Audit

Inspect representative code paths for signs of semantic boundary drift, even if
imports are still legal.

Examples to check:

- downstream phases reading extra fields that are not part of the intended
  public contract
- phases relying on raw input text when they should rely on upstream structured
  output
- finalization or report generation depending on raw phase internals instead of
  sanitized data
- phase-specific repairs that reintroduce knowledge owned by a different phase

This is a review task, not only a test task.

### 5. Contract Regression Tests

Add focused tests that enforce runtime contract behavior, not only import
separation.

Priority tests:

- replay tests for `pdftest7` and `pdftest8`
- tests that Phase 6 renders from finalized sanitized payload without needing
  raw phase internals
- tests that Phase 5 evaluation can be executed from phase 3 + phase 4 outputs
  alone
- tests that pasted text enters at Phase 2 and never requires Phase 1 state

These tests should stay small and stable. They should be contract tests, not
full golden snapshots of every field.

## Deliverables

The validation pass should produce:

- one short findings document in `docs/` summarizing whether the runtime phase
  contract still holds
- any new contract tests needed to keep it from regressing
- a list of specific boundary violations, if found, grouped by severity
- a short list of recommended follow-up refactors if hidden coupling is present

If no violations are found, explicitly document that result and note any
remaining blind spots.

## Acceptance Criteria

This plan is complete when all of the following are true:

- representative fixtures have full phase traces
- downstream replay checks pass for Phases 2 through 6
- contract tests exist for at least the highest-risk runtime handoffs
- hidden-coupling review is documented
- any violations are either fixed or written up with concrete follow-up work

## Suggested Order

1. Confirm canonical interfaces from current code
2. Capture traces for the selected fixtures
3. Build replay checks for Phases 2 through 6
4. Run hidden-coupling review on the most complex cases
5. Add contract regression tests
6. Write findings and follow-up items

## Non-Goals

This plan is not intended to:

- redesign the phase architecture
- rewrite all existing tests into golden snapshot tests
- broaden Phase 7 scope
- fix every parsing or matching bug discovered during validation

If the audit reveals bugs, they should be logged separately from the contract
validation itself unless they are direct boundary violations.
