# Phase Boundary Contract

This document is the normative architecture contract for the phase boundaries in
`Reference_Gen2`.

If this document conflicts with an implementation detail elsewhere, this
document defines the intended boundary unless and until the contract is updated
explicitly.

## Purpose

The boundary contract exists to keep the pipeline modular:

- Phase 1 owns document-path intake and bibliography extraction
- Phase 2 owns safe reference-list text intake and segmentation into raw
  reference strings
- Phase 3 owns per-reference parsing, classification, and repair
- Phase 4 owns local DB lookup, candidate normalization, and provisional match
  ordering
- Phase 5 owns final scoring, selected-candidate decisions, and semantic
  evidence labels
- Finalization owns sanitization and Phase 6A candidate citation rendering
- Phase 6 owns static HTML rendering from sanitized data only

Downstream work may consume upstream public outputs, but it must not depend on
upstream internals unless the contract is intentionally expanded.

## Phase Map

| Phase | Owns | Current Runtime State |
| --- | --- | --- |
| Phase 1 | upload validation, extraction, bibliography detection | implemented |
| Phase 2 | safe reference-list text intake and raw reference boundary segmentation | implemented |
| Phase 3 | parsing, classification, repair, match preparation | implemented |
| Phase 3b | bounded adjacent-reference recovery | implemented |
| Phase 4 | local DB lookup and provisional candidate ordering | implemented |
| Phase 5 | final scoring, selected candidate, report evidence | implemented |
| Finalization | raw-to-sanitized report boundary | implemented |
| Phase 6A | sanitized candidate citation rendering | implemented |
| Phase 6 | static sanitized HTML report rendering | implemented |
| Phase 7 | ephemeral static report delivery lifecycle and thin HTTP adapter | implemented v1 |

Runtime application layers such as upload/status endpoints, frontend UI,
workers, and Docker deployment are outside the Phase 1-6 contract until their
interfaces are explicitly added.

## Runtime Vs Tooling

Runtime Phase 4 consumes a local search database through its public provider
contract. Database ingest scripts such as `scripts/ingest_full_fast.py` build or
refresh that database offline. They may define search schema/indexing choices,
but they are not runtime phase dependencies and must not be imported by runtime
packages.

## Where To Change Things

- Parsing, source-type classification, parser repair, or match-prep fields:
  change Phase 3 in `reference_gen2/reference_parsing`.
- Reference lookup, local DB provider behavior, provisional candidate ordering,
  or lookup traces: change Phase 4 in `reference_gen2/reference_matching`.
- Final status, confidence, selected-candidate policy, evidence checks, or
  scoring labels: change Phase 5 in `reference_gen2/reference_evaluation`.
- Sanitized report payload shape or candidate citation rendering handoff: change
  finalization or Phase 6A, not the HTML renderer.
- Static report layout, filters, visual states, or print/basic report behavior:
  change Phase 6 in `reference_gen2/report_generation`.
- Static report delivery IDs, session-owned TTL retention, re-check sessions,
  upload/text endpoints, frontend, workers, and Docker: add
  application/deployment layers after the current phase contract instead of
  folding them into Phase 1-6.

## Phase 1

### Purpose

Safely accept a document, extract text, and isolate the
bibliography/reference-list section.

### Owns

- `reference_gen2/document_intake`
- `reference_gen2/document_extraction`
- `reference_gen2/extractors`
- `reference_gen2/bibliography_detection`
- `reference_gen2/security`
- `reference_gen2/services/document_pipeline.py`
- `reference_gen2/pipeline_models.py`

### Canonical Input

- uploaded PDF or DOCX bytes
- filename
- declared MIME type

### Canonical Output

- upload metadata
- `DocumentExtraction`
- `BibliographySection`
- `Phase1ReportContext`

### Allowed Responsibilities

- secure file validation and temp storage
- PDF and DOCX text extraction
- bibliography heading detection
- bibliography section slicing
- PDF-only intra-unit bibliography start trimming after a detected heading
- PDF-only stripping of obvious bibliography-page boilerplate or running headers
- optional PDF boundary/layout hints for downstream segmentation
- Phase 1 metadata/report context

### Forbidden Responsibilities

- reference boundary segmentation
- bibliographic field parsing
- source-type classification
- record matching
- citation rendering

### Known Extension Points

- extraction metadata needed for bibliography detection or segmentation
- additional safe upload validation
- stronger bibliography heading detection

Any extension must remain Phase 1-owned and must not embed parsing or
classification concerns.

For PDF specifically, Phase 1 may expose coarse public layout hints that help
Phase 2 reason about likely block boundaries. These hints must remain
segmentation-oriented abstractions rather than extractor-private geometry.

Phase 1 may also refine the bibliography start inside a PDF heading page when a
single extracted page unit contains both pre-bibliography prose and the opening
reference lines. This remains a bibliography-detection concern, not a
segmentation concern.

## Phase 2

### Purpose

Split a detected bibliography/reference-list section into individual raw
reference strings.

### Owns

- `reference_gen2/reference_segmentation`

### Canonical Input

- `BibliographySection`
- `DocumentExtraction`
- plain pasted reference-list text through the Phase 2 text-intake entrypoint
- optional `style_hint`

### Canonical Output

- `SegmentationResult`

### Allowed Responsibilities

- bibliography-list normalization for segmentation
- plain-text reference-list intake checks for non-file paste input
- segmentation into one raw reference string per item
- segmentation warnings and profile metadata
- use public PDF boundary/layout hints from `DocumentExtraction` when present

### Forbidden Responsibilities

- bibliographic field parsing
- source-type classification
- record matching
- scoring
- formatting or rendering

### Known Extension Points

- style-aware segmentation profiles
- stronger layout-sensitive segmentation
- segmentation-specific normalization metadata
- profile-to-style metadata for orchestration-side auto mode
- stricter plain-text paste validation, as long as it remains field-agnostic

Any extension must stop at raw reference boundaries. Phase 2 must not emit
parsed fields or source-type labels.

Pasted text does not go through Phase 1 because Phase 1 owns file/document
intake for PDF and DOCX. Public pasted text enters through Phase 7 request
security, then the Phase 2 text-intake guard validates length, strips markup,
rejects unsafe control characters, normalizes line endings, and passes inert
plain text into segmentation.

For PDF specifically, Phase 2 may use only the documented public PDF layout
hints on `DocumentExtraction`. It must not depend on raw coordinates,
pdfplumber-specific objects, temp files, uploaded bytes, or extractor-private
grouping logic.

### Phase 2 Order Of Operations

Phase 2 processes one bibliography section in this order:

1. input capture
2. bibliography-text normalization
3. segmentation-profile selection from `style_hint`
4. record inferred `profile_used` for downstream `auto` style routing
5. optional PDF hint lookup construction
6. line-by-line buffer scan
7. explicit marker handling for numbered or bulleted starts
8. candidate-start detection for possible new references
9. continuation and tail-fragment veto checks
10. boundary confirmation using current-buffer completeness plus PDF layout hints
11. buffer flush or append
12. result assembly with warnings and profile metadata

Phase 2 remains a boundary finder only. It does not parse fields, infer final
source types, or use parser output as runtime input.

### Phase 2 Decision Tree

The normative v1 decision tree for Phase 2 segmentation is:

```text
Start bibliography text
|
|-- Normalize text
|     - normalize whitespace and pasted boundaries
|     - repair safe PDF-only inline author/year glue
|     - preserve DOCX paragraph boundaries
|
|-- Select segmentation profile
|     - use style hint to choose conservative vs permissive start detection
|
|-- Build optional PDF hint lookup
|     - use only public coarse hints like new block, gap, indentation change
|
|-- For each line
|     |
|     |-- Blank line?
|     |     |
|     |     |-- No active buffer
|     |     |     => skip
|     |     |
|     |     |-- PDF next line looks like tail fragment of current reference?
|     |     |     => keep buffer open
|     |     |
|     |     |-- Current buffer looks complete and next line looks like new reference?
|     |     |     => flush buffer
|     |     |
|     |     |-- Otherwise
|     |           => usually flush, but warn if layout is ambiguous
|     |
|     |-- Explicit list marker?
|     |     => flush current buffer and start a new reference
|     |
|     |-- Candidate new-reference start?
|     |     |
|     |     |-- PDF tail-fragment veto fires?
|     |     |     Examples:
|     |     |     - journal/container led metadata line
|     |     |     - DOI-only / URL-only / retrieval-only line
|     |     |     - lowercase conjunction/title continuation
|     |     |     - metadata-heavy volume/issue/pages/article-number line
|     |     |     => attach backward to current buffer
|     |     |
|     |     |-- DOCX hard paragraph split allowed?
|     |     |     => flush and start new buffer
|     |     |
|     |     |-- PDF split allowed?
|     |     |     Requires:
|     |     |     - credible new-reference start
|     |     |     - current buffer looks complete enough
|     |     |     - no tail-fragment veto
|     |     |     - public PDF layout hints support the break
|     |     |     => flush and start new buffer
|     |     |
|     |     |-- Conservative/ambiguous case
|     |           => keep attaching and emit segmentation warning
|     |
|     |-- Not a candidate start
|           => append to current buffer
|
|-- End of lines
|     => flush final buffer
|
|-- Emit SegmentationResult
|     - raw reference strings
|     - warnings
|     - style/profile metadata
```

### Phase 2 Heuristic Categories

Phase 2 may use shallow, segmentation-owned evidence only:

- start signals
  - author-led start
  - org/year start
  - style-permitted title-led start
  - numbered or bulleted list markers

- continuation signals
  - DOI-only or URL-only line
  - retrieval/access clause
  - journal/container metadata continuation
  - chapter/report continuation such as `In: ...`
  - lowercase conjunction/title continuation

- buffer-completeness signals
  - current buffer already has author/year/title-like shape
  - current buffer already has terminal metadata such as DOI, URL, or volume/pages
  - current buffer ends like a plausible finished reference

- PDF-only boundary signals
  - new block
  - gap category
  - indentation change
  - relative block strength vs the current buffer state

These are boundary heuristics only. They must remain weaker than parsing and
must not be treated as bibliographic field truth.

### Recovery Layer

Split-reference recovery is currently implemented as a separate bounded layer
after core Phase 3 parsing. In practice this is a `Phase 3b` step:

- it consumes Phase 2 segmented references indirectly through Phase 3 outputs
- it evaluates only adjacent suspicious pairs
- it allows one bounded attach-backward and reparse attempt
- it records why recovery was accepted, blocked, or skipped
- it remains separate from core Phase 2 segmentation and core Phase 3 parsing

The boundary rule remains:

- Phase 2 owns primary reference-boundary decisions
- Phase 3 owns single-reference parsing, classification, and repair
- Phase 3b owns bounded parser-informed adjacent recovery only

Phase 3b must not become a general segmentation engine. It may not:

- resplit one parsed result into multiple references
- join non-adjacent references
- perform recursive multi-hop recovery loops
- write back parser-informed recovery semantics into Phase 2 APIs

## Phase 3

### Purpose

Parse one segmented raw reference string into structured fields and apply
Phase 3-owned classification and repair logic.

### Owns

- `reference_gen2/reference_parsing`
- the AnyStyle adapter and parser orchestration
- project-owned `ctype` interpretation
- Dutch APA repair and classification harnesses

### Canonical Input

- `list[str]` raw segmented references
- optional `style_hint`

### Canonical Output

- `list[ParsedReferenceResult]`
- one stable Phase 3 result record per segmented reference, ready for later
  matching and reporting handoff

### Allowed Responsibilities

- single-reference cleanup and normalization
- pre-parse classification signals
- AnyStyle invocation and mapping
- Phase 3-owned `ctype` classification
- post-parse repair and validation
- match-preparation derivation for later phases
- report-basis explanation derivation for later phases
- warnings, traces, parser metadata, and classification metadata

### Forbidden Responsibilities

- document intake
- bibliography detection
- segmentation
- parse-driven reattachment or segmentation repair of adjacent references
- matching
- scoring
- formatting or rendering

### Known Extension Points

- custom parser-model selection
- Dutch APA classification and repair profiles
- stronger post-parse validation
- Phase 3 review/debug trace output

`ctype`, classification traces, parse profiles, and repair profiles are Phase 3
outputs. They must not be back-projected into Phase 1 or Phase 2 APIs unless
the upstream contract is expanded explicitly.

### Phase 3 Order Of Operations

Each segmented reference should flow through Phase 3 in this order:

1. input capture
2. raw normalization
3. signal extraction
4. pre-classification
5. parse strategy selection
6. field parsing
7. post-parse validation
8. bounded reclassification
9. per-`ctype` repair
10. match-prep derivation
11. result assembly

Phase 3 prepares references for matching and later reporting. It does not
perform database lookup, scoring, citeproc rendering, or report UI assembly.

## Phase 3b

### Purpose

Review adjacent Phase 3 results and recover a small class of likely oversplit
references by attaching the right item backward to the left item and reparsing.

### Owns

- bounded adjacent-pair recovery inside `reference_gen2/reference_parsing`
- recovery status and trace metadata attached to recovered parse results

### Canonical Input

- `list[ParsedReferenceResult]` from core Phase 3
- optional `style_hint`

### Canonical Output

- `list[ParsedReferenceResult]`
- one downstream-facing recovered view of the Phase 3 results

### Allowed Responsibilities

- evaluate adjacent suspicious pairs only
- use Phase 3 parser warnings and parsed-field shape as recovery evidence
- merge `left.raw_reference + right.raw_reference` into one candidate
- rerun normal Phase 3 parsing on the merged candidate
- accept the merge only when the reparsed result is clearly healthier
- record `unchanged`, `attached_backward`, or `blocked`

### Forbidden Responsibilities

- segmentation of the bibliography section from raw document text
- non-adjacent joining
- recursive or repeated recovery passes
- splitting one parsed item into two or more references
- document intake, bibliography detection, DB matching, or rendering

### Known Extension Points

- stricter adjacent-pair evidence policies
- additional bounded recovery classes
- richer recovery trace output for manual review

### Phase 3b Order Of Operations

Phase 3b processes the Phase 3 result list in this order:

1. iterate left-to-right over adjacent pairs
2. evaluate whether the pair matches an allowed attach pattern
3. if yes, build a merged raw-reference candidate
4. rerun normal Phase 3 parsing on the merged candidate
5. compare merged health against the two separate results
6. emit `attached_backward` if the merged result is clearly better
7. emit `blocked` if the pair was reviewed but the merge was not proven helpful
8. otherwise emit `unchanged`

Current recovery classes are intentionally narrow:

- chapter head + orphan `In ...` tail
- article or book-like head + metadata-only tail

Phase 3b is parser-informed, but it is still not a replacement for Phase 2.
Its job is to recover a bounded set of oversplits more safely than Phase 2 can.
Any reference-head or metadata-tail checks used by Phase 3b are Phase 3-owned
recovery heuristics. They must not import Phase 2 segmentation heuristics,
splitters, profiles, or normalization internals.

### Phase 3 Output Contract

`ParsedReferenceResult` is the public single-reference handoff for downstream
phases. At minimum it should contain:

- `reference_id`
- `raw_reference`
- `normalized_reference`
- `style_hint_used`
- `parser_backend`
- `parser_model_used`
- `pre_classification`
- `post_classification`
- final `ctype`
- `classification_trace`
- `parsed_data`
- `parse_profile_used`
- `repair_profile_used`
- `warnings`
- `match_preparation`
- `report_basis`
- `raw_tags` preserved inside `parsed_data` when available
- `recovery_status`
- `recovery_trace`
- `recovery_source_indices`
- `absorbed_reference_ids`

Phase 3 output must not include:

- database match results
- citeproc-rendered reference strings
- report UI state
- persisted user overrides
- frontend-only interaction metadata

When recovery is enabled, two views may exist:

- `phase3`
  - the raw single-reference parse result for each Phase 2 segment
- `phase3b`
  - the recovered downstream-facing result list after bounded adjacent repair

Later phases should consume `phase3b` by default when available, while keeping
`phase3` available for audit and debugging.

### Phase 3 Match-Prep Policy

Phase 3 marks whether a reference is eligible for database matching and which
target family later phases should use.

- `journal_article` -> eligible, `crossref`
- `book` -> eligible, `openlibrary`
- `book_chapter` -> eligible, `openlibrary`
- `thesis` -> not eligible by default unless later DB support is added
- `conference_paper` -> not eligible by default unless later DB support is added
- `report` -> not eligible by default
- `webpage` -> not eligible
- `software` -> not eligible
- `dataset` -> not eligible
- `newspaper_article` -> not eligible
- `unknown` -> not eligible

`match_preparation` is Phase 3-owned handoff data only. Later phases may use
it, but they must not recreate parser internals or re-open AnyStyle raw output
to infer the intended match target.

## Phase 4

### Purpose

Match one parsed reference against the local catalog and return a normalized
candidate set plus a provisional best match.

### Owns

- `reference_gen2/reference_matching`
- local DB provider/adapter selection inside Phase 4 runtime config
- Phase 4-owned candidate, lookup-trace, and match-result models

### Canonical Input

- one `ParsedReferenceResult`
- Phase 4 runtime config containing either:
  - `local_db_path`
  - or a Phase 4 provider implementation

For batch orchestration, Phase 4 may also consume:

- `phase3`
- optional `phase3b`

When both are available, Phase 4 should consume `phase3b` by default.

### Canonical Output

- `Phase4MatchResult`
- one stable Phase 4 result record per input reference

At minimum the public Phase 4 result should contain:

- `reference_id`
- `input_summary`
- `attempted`
- `strategy_used`
- `lookup_trace`
- `candidates`
- `best_candidate`
- `status`
- `reasons`
- `warnings`
- `timings_ms`

See [`docs/phase4_output_interpretation.md`](./phase4_output_interpretation.md)
for the field-by-field meaning of `status`, `strategy_used`, `reasons`,
`match_signals`, and `lookup_trace`.

### Allowed Responsibilities

- consume Phase 3 `match_preparation` as the canonical lookup handoff
- validate whether the reference is eligible for local DB matching
- normalize DOI, title, author, year, and container/publisher lookup inputs
- run DOI-first exact or equivalent local DB lookup
- run deterministic fallback search strategies based on final Phase 3 `ctype`
- normalize DB rows into Phase 4-owned candidate records
- return a full retained candidate set and one provisional best candidate
- emit lookup traces, timings, skip reasons, and adapter/runtime errors

### Forbidden Responsibilities

- document intake, bibliography detection, segmentation, or parsing
- reconstructing parser internals from raw AnyStyle output
- mutating `ParsedReferenceResult`
- external API verification
- final confidence scoring and threshold policy
- citeproc rendering, formatting, or frontend UI state assembly

### Known Extension Points

- additional supported local-record families
- stronger candidate normalization and deduplication
- alternate local DB providers behind the same Phase 4 service contract
- richer review/debug trace output

### Phase 4 Order Of Operations

Each Phase 4 lookup should flow in this order:

1. input capture
2. Phase 3 eligibility and support check
3. normalized lookup-input derivation from `match_preparation`
4. DOI-first lookup when a DOI is present
5. type-specific fallback retrieval when DOI does not yield a usable hit
6. candidate normalization into Phase 4-owned records
7. deterministic provisional ordering
8. result assembly with trace data, reasons, warnings, and timings

Phase 4 performs retrieval and provisional ordering only. It does not own final
confidence scoring, acceptance thresholds, or rendering.

### Phase 4 Strategy Policy

Phase 4 lookup strategy is driven by final Phase 3 `ctype` and
`match_preparation`, not by raw parser tags.

Supported v1 types:

- `journal_article`
  - DOI-first local lookup
  - fallback `title + author + year`
  - on DOI miss, a protected selective recall band may run with longer title
    prefixes plus year constraints so downstream Phase 5 can evaluate plausible
    candidates
  - relaxed `title + year` and title-only fallback when needed
- `book`
  - DOI or identifier lookup when present
  - fallback `title + author + year`
- `book_chapter`
  - fallback `chapter title + book title + author + year`
  - broader book-title fallback when needed

Initially unsupported by default:

- `thesis`
- `conference_paper`
- `report`
- `webpage`
- `software`
- `dataset`
- `newspaper_article`
- `unknown`

Unsupported types should return explicit Phase 4 skip diagnostics rather than
ad hoc matching behavior.

### Phase 4 Adapter Rule

The public Phase 4 service contract must remain schema-agnostic. SQLite table
names, FTS query shape, DOI index checks, and row-to-record mapping belong
inside a local DB provider/adapter.

Phase 4 callers may depend on:

- service inputs and outputs
- candidate/result field names
- lookup-trace semantics

Phase 4 callers must not depend on:

- concrete SQLite table names
- FTS implementation details
- adapter-private raw row structure beyond the explicitly exposed candidate
  metadata field

### Phase 3 Source-Type Decision Tree

The normative v1 decision tree for Phase 3 source-type classification is:

```text
Start
|
|-- Has chapter markers?
|     - raw reference contains "In ..."
|     - and has editor marker or book-container/page-range pattern
|     => book_chapter
|
|-- Has thesis markers?
|     - thesis/dissertation/proefschrift/scriptie terms
|     => thesis
|
|-- Has dataset markers?
|     - dataset/repository/Zenodo/Figshare/OSF-like terms
|     => dataset
|
|-- Has software markers?
|     - version cue and/or bracketed medium like [software], [app], [Generatieve AI]
|     - plus URL or org/product pattern
|     => software
|
|-- Has conference markers?
|     - proceedings/conference/symposium/workshop/congres/conferentie terms
|     => conference_paper
|
|-- Has strong scholarly article signals?
|     - DOI
|       OR
|     - scholarly container + volume/issue/pages pattern
|     => journal_article
|
|-- Has news-source signals?
|     - named news outlet/newspaper term
|     - plus date and title/article shape
|     => newspaper_article
|
|-- Has strong webpage signals?
|     - retrieval clause + URL
|     - and no DOI
|     - and no strong scholarly signals
|     => webpage
|
|-- Has strong report signals?
|     - report-like title term
|       OR
|     - institutional author/publisher
|     - and no strong scholarly signals
|     => report
|
|-- Has weak webpage fallback?
|     - URL present
|     - and no scholarly/article signals
|     => webpage
|
|-- Has weak book fallback?
|     - title + author/date or publisher
|     - and no web/scholarly/report-specific signals
|     => book
|
|-- Otherwise
|     => unknown
```

Tie-break rules:

- `book_chapter` outranks `book`
- `thesis` outranks `report` and `book`
- `dataset` outranks `webpage` when repository/dataset signals are explicit
- `software` outranks `webpage` when version/media/product cues are explicit
- `conference_paper` outranks `journal_article` only when conference/proceedings signals are explicit
- `journal_article` outranks `webpage` when DOI or volume/issue/pages are present
- `webpage` outranks `report` when retrieval+URL is explicit and there is no strong report container/publisher signal
- `report` outranks `webpage` when institutional author/publisher and report title cues are explicit, even if a URL exists
- `unknown` outranks any weak guess if only one weak signal is present

## Public Handoffs

### Phase 1 -> Phase 2

Public handoff:

- `BibliographySection`
- `DocumentExtraction`

Guaranteed:

- bibliography section text and indices
- extraction source kind and text units
- optional PDF layout hints with coarse boundary semantics only
- extraction warnings and stats

When present, the public PDF hint field is `DocumentExtraction.pdf_layout_hints`.
It is limited to segmentation-oriented abstractions such as line text, page
index, coarse gap category, indentation change, and whether a line begins a new
block. It must not be treated as a raw geometry API.

Intentionally omitted:

- raw upload bytes after Phase 1 completion
- temp-file paths as a downstream dependency
- extractor-private logic and internal helper state
- raw PDF geometry, font metadata, bounding boxes, and backend-private parser state

Phase 2 must not assume:

- access to upload internals
- extractor implementation details beyond the public model fields

### Phase 2 -> Phase 3

Public handoff:

- `list[str]` segmented raw references
- optional `style_hint`
- `profile_used` and segmentation warnings/profile metadata

Guaranteed:

- one raw reference string per segmented item
- segmentation warnings/profile metadata on the segmentation result

Intentionally omitted:

- bibliography detection internals
- segmentation rule internals
- page/paragraph provenance beyond what is already represented publicly in
  Phase 2 outputs

Phase 3 must not assume:

- access to segmentation internals
- access to Phase 1 temp storage or raw upload bytes
- access to upstream implementation details not present in the public handoff

### Phase 7 Auto-Style Resolution Rule

When the user submits `style_hint="unknown"` (Auto):

- Phase 2 receives the request unchanged and records segmentation metadata, including
  `profile_used`.
- Phase 7 resolves a downstream style for Phase 3 parsing and final citation
  rendering from high-confidence orchestration-level cues.
- Strong numeric segmentation still maps to Vancouver:
  `numeric_profile -> vancouver`.
- Strong APA author-year list cues may map Auto to `apa7_en`.
- Strong APA author-year list cues with Dutch APA locale evidence may map Auto
  to `apa7_nl`. Locale evidence is limited to shallow cues such as a Dutch
  bibliography heading (`Literatuurlijst`, `Bibliografie`, `Bronnenlijst`) or
  Dutch retrieval wording (`Geraadpleegd op`, `Opgehaald op`, `Bekeken op`).
- Medium-confidence author-year cues are retained as detection metadata but do
  not change downstream parser/render style.
- If no strong cue is present, Phase 7 keeps downstream style as `unknown`
  (auto/default render fallback).

This keeps phase ownership stable: Phase 2 stays a boundary finder, and only the
orchestration layer resolves how auto input affects parser/render behavior.

## Handoff Matrix

| Producer | Consumer | Payload Shape | Guaranteed | Intentionally Omitted | Downstream Must Not Assume |
| --- | --- | --- | --- | --- | --- |
| Phase 1 | Phase 2 | `BibliographySection` + `DocumentExtraction` | bibliography slice, extraction context, warnings, stats, optional coarse PDF layout hints | raw upload bytes, temp-file lifecycle, raw geometry, extractor-private state | upload internals, hidden extraction details |
| Phase 2 | Phase 3 | `list[str]` raw references + optional `style_hint` + segmentation `profile_used` | one segmented reference per item, segmentation metadata | segmentation internals, bibliography detection internals | direct access to upstream internals |
| Phase 3 | Phase 4 / Phase 5 / later tools | `list[ParsedReferenceResult]` | parsed fields, warnings, parser metadata, final `ctype`, classification trace, `match_preparation`, and `report_basis` | DB results, scoring, citeproc rendering, UI state | citeproc-ready output unless explicitly added |
| Phase 4 | Phase 5 / later tools | `Phase4MatchResult` | normalized candidates, `record_granularity`, provisional best candidate, lookup trace, status, reasons, warnings, timings | final confidence policy, accepted-match threshold, citeproc rendering, UI state | schema-specific SQLite behavior unless explicitly exposed |

## Phase 4 Dependency Rule

Phase 4 may consume only the public Phase 3 handoff plus its own runtime
configuration.

Phase 4 must not depend on:

- Phase 2 segmentation internals
- Phase 1 upload or extraction internals
- AnyStyle raw output or parser-private state
- frontend review state

If Phase 4 cannot be implemented correctly using the existing public Phase 3
handoff, that is a boundary decision and must be handled through the same
cross-phase change policy below.

## Phase 5 Dependency Rule

Phase 5 may consume two public upstream handoffs together plus its own runtime
configuration:

- the public Phase 3 parsed-reference handoff for input-side comparison
- the public Phase 4 provisional match handoff for candidate-side comparison

This is intentional. It does not mean that Phase 4 should proxy, embed, or own
Phase 3 parser state.

Phase 5 should use:

- Phase 3 as the canonical input-side parsed reference
- Phase 4 as the canonical candidate and retrieval result
- Phase 4 `record_granularity` as the public signal for article/book/chapter
  candidate granularity

Phase 5 may also use an orchestration-level wrapper such as `Phase5Input`, but
that wrapper is only a transport container and does not change phase ownership.

Phase 5 must not:

- rerun DB retrieval
- depend on parser internals beyond the public `ParsedReferenceResult`
- depend on Phase 2 segmentation internals
- depend on Phase 1 upload or extraction internals
- require Phase 4 to embed or duplicate Phase 3-owned data

## Finalization, Phase 6A, And Phase 6 Dependency Rules

Finalization is the sanitization boundary. It may consume public Phase 1-5
outputs and produce `SanitizedCycleReport` for report generation.

Phase 6A citation rendering may consume public candidate summary fields plus an
optional final Phase 3 `ctype` supplied by finalization to choose the appropriate
citation shape. It may use public candidate models for title, authors, year,
DOI, container, publisher, volume, issue, pages, and granularity. It must not
score, select candidates, borrow submitted-reference parsed fields, or depend on
Phase 5/report rendering internals.

Phase 6 static report generation may consume only `SanitizedCycleReport` or its
serialized mapping. It may own presentation behavior such as clickable display
links, filtering/search controls, and status styling. It must not contain DOI
scoring policy, matching policy, candidate selection policy, citation rendering
logic, re-check/session behavior, or raw/rich phase internals.

When both sanitized `phase3` and `phase3b` are present, Phase 6 must use
`phase3b` as the downstream-facing reference row set. Sanitized `phase3` remains
available for audit/debugging only and must not be appended as additional report
cards, because Phase 3b may have absorbed one or more raw Phase 3 items into a
single recovered reference.

## Phase 3 Dependency Rule

Phase 3 may consume only the public Phase 2 handoff plus its own runtime
configuration.

Phase 3 must not depend on:

- Phase 1 temp files or upload storage details
- raw upload bytes
- extractor implementation details not present in `DocumentExtraction`
- bibliography-detection internals
- segmentation heuristics or splitter internals beyond public Phase 2 results
- Phase 2 profiles or normalization internals

If Phase 3 cannot be implemented correctly with the existing public handoff,
that is a boundary decision and must be handled through the cross-phase change
policy below.

## Cross-Phase Change Policy

Upstream boundary changes are allowed for Phase 3 or Phase 4 only when all of
the following are true:

1. The downstream phase cannot be implemented correctly using the existing public handoff.
2. The required upstream change is minimal and boundary-safe.
3. The public handoff contract is updated in this document.
4. The rationale explains why the change belongs upstream instead of being
   handled inside the downstream phase.

Required change note for any such proposal:

- problem statement
- why the current handoff is insufficient
- exact new field or guarantee being added
- why the change belongs in Phase 1 or Phase 2
- downstream impact and compatibility notes

Default rule:

- if the need can be satisfied inside the downstream phase, it stays there

## Database Ingest Tooling

`scripts/ingest_full_fast.py` and related ingest scripts are database-build
tooling for the local search database consumed by Phase 4. They may shape the
offline search schema and indexes, but they are not runtime phase dependencies
and must not become an import path for Phase 4, Phase 5, finalization, or report
generation.

## Non-Contract Internals

The following are not stable handoff contracts unless explicitly documented as
public:

- extraction heuristics
- bibliography-detection heuristics
- segmentation heuristics
- temp storage paths and lifecycle
- parser backend implementation details
- AnyStyle-specific raw model behavior

Downstream phases may observe these indirectly during debugging, but they may
not treat them as stable integration contracts.

## Examples

### Acceptable Upstream Change

Add a minimal public extraction metadata field needed by segmentation or parsing
when that information cannot be reconstructed correctly downstream and is safe
to expose as part of the model contract.

### Unacceptable Upstream Change

Make Phase 3 read extractor temp paths, access raw upload bytes directly, or
rely on bibliography-detection helper internals that are not part of the public
model contract.

## Review Checklist

Use this checklist for any architecture-affecting change:

- Does this change alter a phase input/output contract?
- Does this make a downstream phase depend on upstream internals?
- Could this stay inside the owning phase instead?
- If upstream expansion is proposed, is the change minimal and documented here?
- Are public handoff guarantees and omissions still clear after the change?

## Future Guardrails

Lightweight contract tests should later lock:

- Phase 1 public output shape
- Phase 2 public output shape
- Phase 3 consumption of only public Phase 2 fields

Those tests are not defined in this document, but this contract should guide
their eventual scope.
