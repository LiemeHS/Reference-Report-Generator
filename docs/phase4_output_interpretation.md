# Phase 4 Output Interpretation

This document explains how to read Phase 4 results in plain language.

It is the companion to the boundary contract in
[`docs/phase_boundaries.md`](./phase_boundaries.md). That contract defines what
Phase 4 owns; this document explains what the output fields and tags mean when
you inspect a `.phase124.md` or `.phase124.json` artifact.

The examples below are based on current Phase 4 behavior in:

- [`reference_gen2/reference_matching/models.py`](../reference_gen2/reference_matching/models.py)
- [`reference_gen2/reference_matching/service.py`](../reference_gen2/reference_matching/service.py)

## Result Shape

At a high level, each Phase 4 result answers three questions:

1. Did we attempt a local DB lookup?
2. If yes, what query path produced the best candidate?
3. Was the best candidate strong enough to promote to a provisional match?

The main public fields are:

- `status`: final outcome bucket for the reference
- `strategy_used`: the query configuration that surfaced the winning candidate
- `reasons`: summary tags explaining the final result
- `warnings`: non-fatal issues from earlier phases or provider/runtime behavior
- `best_candidate`: the top retained candidate, if any
- `ordering_score`: Phase 4's provisional ranking score for that candidate
- `match_signals`: structured evidence behind the ranking
- `lookup_trace`: the search/debug trace showing what Phase 4 actually tried
- `source_strategy`: candidate-level strategy name; usually matches
  `strategy_used` for the winning candidate
- `source_table`: local DB table that produced the candidate, such as
  `search_journal`, `search_book`, or `search_book_chapter`
- `record_granularity`: public candidate granularity, one of `article`,
  `book`, `chapter`, or `unknown`; downstream phases use this instead of
  guessing from table names or strategy labels

## Status Values

### `matched_provisional`

Phase 4 found a candidate and promoted it to the provisional best match.

In current code, a candidate becomes `matched_provisional` when either:

- it has a DOI exact/equivalent match, or
- its `ordering_score` is at least `0.6`

This is a Phase 4 confidence threshold, not a guarantee that the record is
perfect.

### `candidate_only`

Phase 4 found a plausible candidate, but not enough evidence to promote it to
`matched_provisional`.

This usually means some evidence is strong, but one or more of the following is
missing or weak:

- author overlap
- exact year alignment
- exact container or publisher support
- chapter-level metadata such as pages or a chapter-specific DOI

### `no_match`

Phase 4 attempted lookup strategies but retained no candidate.

Typical signs:

- `attempted: true`
- `strategy_used: null`
- `candidate_count: 0`
- `reasons: ["phase4_no_candidates"]`

### `skipped`

Phase 4 did not attempt a lookup because the reference was not eligible for the
current local DB matching rules.

Typical causes:

- unsupported `ctype`
- ineligible `match_target`
- missing required lookup fields

### `error`

Phase 4 attempted lookup but hit a provider/runtime failure.

This is distinct from `no_match`: `error` means the lookup process itself
failed, not merely that it found no candidates.

No real `error` example was present in the current `manual_tests/output`
artifacts when this document was written, so the mini-example below is
illustrative.

## Promotion Rule

The promotion rule is intentionally simple:

- if there is no retained candidate, the result is `no_match`
- if there is a retained candidate and it has DOI exact/equivalent evidence,
  promote it to `matched_provisional`
- otherwise, if the best candidate has `ordering_score >= 0.6`, promote it to
  `matched_provisional`
- otherwise keep it as `candidate_only`

That means a result can have strong-looking evidence tags like
`title_exact_or_near_exact` and still remain `candidate_only` if the total score
stays below the threshold.

## How To Read `strategy_used`

`strategy_used` is the query configuration that produced the winning candidate.

The names are descriptive and usually follow this pattern:

`<family>_<title-basis>_<optional-extra-fields>_<year-mode>`

Examples:

- `journal_title_year_exact`
- `journal_title_author_year_exact`
- `journal_title6_year_exact_doi_miss`
- `journal_title6_author_year_exact_doi_miss`
- `book_main_title_author_year_exact`
- `book_title2_author_year_exact`
- `chapter_main_title_author_year_near`
- `chapter_book_title_editor_year_near`
- `chapter_book_title_year_near`

Interpretation pieces:

- `journal`, `book`, `chapter`: which search family the config belongs to
- `main_title`: use normalized "main title" terms, often trimmed before subtitle
- `title2` or `title3`: use only the first 2 or 3 title terms
- `book_title`: for a `book_chapter`, search the containing book rather than the
  chapter title
- `author`: include a first-author surname hint in the query
- `editor`: for a `book_chapter` book-level fallback, include editor surname
  terms as the book-author hint
- `exact` or `near`: exact-year filter vs nearby-year tolerance
- `doi_miss`: a protected fallback path that only runs after a DOI lookup
  returned zero hits

For journal articles, `*_doi_miss` strategies are intentionally more
recall-oriented than the normal broad-query guard allows. They still remain
selective: they use a longer title prefix, keep a year constraint, and may add
author or container support. This gives Phase 5 a small candidate set to judge
instead of an immediate empty result when a DOI is wrong or stale.

For `book_chapter`, `chapter_book_title_editor_year_near` usually means:

- Phase 4 could not find a good chapter-level hit first
- it fell back to searching the containing book in `search_book` with editor
  surnames as book-author evidence
- if the parser left an inline editor marker in the book title, Phase 4 stripped
  that marker from the title and recovered editor terms from it
- it accepted a near-year result as the best provisional recovery

`chapter_book_title_year_near` is the broader title-only version of the same
book-level fallback.

## Common Reason Tags

These tags live in `reasons` and `match_reasons`. They summarize what evidence
was found for the best candidate.

### Title Tags

- `title_exact_or_near_exact`: title match is very strong
- `title_strong_match`: title match is strong but not near-exact
- `title_partial_match`: title overlap is real but incomplete
- `title_weak_match`: only weak title overlap was found

### Author Tags

- `author_exact_overlap`: author overlap is strong
- `author_partial_overlap`: some author support exists, but not full overlap
- `author_weak_overlap`: only weak author support exists

### Year Tags

- `year_exact_match`: candidate year matches exactly
- `year_near_match`: candidate year is close enough under the current tolerance
- `year_mismatch`: both years exist but do not match closely

### Container / Publisher Tags

- `container_or_publisher_match`: strong support from journal/container/publisher
- `container_or_publisher_partial_match`: some support exists, but not exact

### Phase 4 Outcome Tags

- `phase4_candidates_found`: at least one candidate was retained
- `phase4_no_candidates`: no candidate survived lookup/ranking
- `phase4_second_candidate_retained`: a second top candidate was also credible
- `phase4_second_candidate_not_credible`: a second candidate existed but was not
  strong enough to retain in the top set
- `phase4_doi_miss_recall_band_entered`: the protected DOI-miss journal fallback
  path actually ran
- `phase4_doi_miss_recall_band_candidates_found`: the DOI-miss recall band
  surfaced plausible candidates
- `phase4_doi_miss_recall_band_exhausted`: the DOI-miss recall band ran but
  still found nothing useful

### Skip / Eligibility Tags

- `phase4_ineligible_ctype`: the reference type is not eligible for Phase 4
- `phase4_unsupported_ctype:<type>`: the current type is unsupported by the
  local DB matcher
- `phase4_missing_field:<field>`: a required lookup field was absent
- `phase4_doi_miss_no_selective_fallback`: a broad journal fallback was blocked
  because the title terms were still too generic

## `match_signals` Glossary

`match_signals` is the structured version of the evidence behind the score.

### `doi_match_type`

- `none`: no DOI evidence was used
- `exact`: DOI matched exactly
- `equivalent`: DOI normalized to the same equivalence key
- `mismatch`: both sides had DOI values but they disagreed

### `title_match_strength`

- `exact_or_near_exact`
- `strong`
- `partial`
- `weak`
- `none`

### `author_match_strength`

- `strong`
- `partial`
- `weak`
- `none`

### `year_match_type`

- `exact`
- `near`
- `mismatch`
- `missing`

### `container_match`

- `yes`: container or publisher evidence supported the candidate
- `no`: container or publisher evidence argued against it
- `unknown`: there was not enough comparable container/publisher information

### `volume_issue_pages_match`

- `exact`
- `partial`
- `mismatch`
- `unknown`

This field is often `unknown` for books because there may be no comparable
volume, issue, or page metadata on the candidate.

## `lookup_trace` Glossary

`lookup_trace` explains what Phase 4 actually did.

The most useful fields are:

- `doi_attempted`: whether Phase 4 tried a DOI-first lookup
- `doi_miss`: whether that DOI-first lookup returned zero hits
- `strategies_attempted`: query configs that actually ran
- `strategies_skipped`: configs that existed but were skipped
- `selected_query_terms`: normalized tokens used for each strategy
- `query_profiles`: strictness labels such as `strict`, `balanced`, `relaxed`
- `year_profiles`: whether each strategy used `exact` or `near` year logic
- `candidate_count`: number of retained candidates after ranking/dedup
- `cascade_stop_reason`: why the search stopped early
- `skipped_reasons`: why lookup was skipped or why configs were skipped
- `timings_ms`: DOI, fallback, and total timing

Common `cascade_stop_reason` values:

- `phase4_stop_strong_candidate_found`: best candidate was already strong enough
- `phase4_stop_title_year_candidate_found`: title + year evidence was good enough
- `phase4_stop_top2_filled`: enough credible top candidates were retained
- `phase4_stop_max_steps_reached`: Phase 4 exhausted the allowed search steps

For DOI-miss journal cases, one useful reading pattern is:

- broad title-only journal fallbacks may still be skipped
- a protected `*_doi_miss` config may then run
- `candidate_count` can be greater than zero even when the final status remains
  `candidate_only`

That is intentional. In these cases Phase 4 is acting as a targeted candidate
generator for later Phase 5 evaluation, not as the final trust decision layer.

## Worked Examples

The examples below are abbreviated from real `manual_tests/output` artifacts
unless marked otherwise.

### 1. Journal Article: Strong `matched_provisional`

Real artifact pattern:

```json
{
  "ctype": "journal_article",
  "status": "matched_provisional",
  "strategy_used": "journal_title_year_exact",
  "best_candidate": {
    "record_id": "search_journal:82400304",
    "title": "Slipping into and out of Poverty: The Dynamics of Spells",
    "source_table": "search_journal",
    "ordering_score": 0.9,
    "match_signals": {
      "title_match_strength": "exact_or_near_exact",
      "author_match_strength": "strong",
      "year_match_type": "exact",
      "container_match": "yes"
    }
  },
  "reasons": [
    "title_exact_or_near_exact",
    "author_exact_overlap",
    "year_exact_match",
    "container_or_publisher_match",
    "phase4_candidates_found"
  ]
}
```

Why it promoted:

- the title is near-exact
- the author signal is strong
- the year is exact
- the journal/container matches
- the score is comfortably above the threshold

### 2. Journal Article: Near-Miss `candidate_only` Example

Illustrative example based on current scoring rules:

```json
{
  "ctype": "journal_article",
  "status": "candidate_only",
  "strategy_used": "journal_title3_year_exact",
  "best_candidate": {
    "source_table": "search_journal",
    "ordering_score": 0.48,
    "match_signals": {
      "title_match_strength": "partial",
      "author_match_strength": "weak",
      "year_match_type": "exact",
      "container_match": "unknown"
    }
  },
  "reasons": [
    "title_partial_match",
    "author_weak_overlap",
    "year_exact_match",
    "phase4_candidates_found"
  ]
}
```

Why it stays `candidate_only`:

- a candidate exists, so this is not `no_match`
- the year supports it
- but title and author evidence are too weak to push the score to `0.6`

### 3. Book: Strong `matched_provisional`

Real artifact pattern:

```json
{
  "ctype": "book",
  "status": "matched_provisional",
  "strategy_used": "book_main_title_author_year_exact",
  "best_candidate": {
    "record_id": "search_book:13514397",
    "title": "Risk society",
    "source_table": "search_book",
    "ordering_score": 0.85,
    "match_signals": {
      "title_match_strength": "exact_or_near_exact",
      "author_match_strength": "strong",
      "year_match_type": "exact",
      "container_match": "yes"
    }
  },
  "reasons": [
    "title_exact_or_near_exact",
    "author_exact_overlap",
    "year_exact_match",
    "container_or_publisher_partial_match",
    "phase4_candidates_found"
  ]
}
```

Why it promoted:

- the main title matched strongly
- author overlap was strong
- year matched exactly
- publisher evidence helped enough to keep the score high

### 4. Book: Ambiguous `candidate_only`

Real artifact pattern:

```json
{
  "ctype": "book",
  "status": "candidate_only",
  "strategy_used": "book_title2_author_year_exact",
  "best_candidate": {
    "record_id": "search_book:25017591",
    "title": "Time and Poverty in Western Welfare States",
    "source_table": "search_book",
    "ordering_score": 0.515,
    "match_signals": {
      "title_match_strength": "partial",
      "author_match_strength": "partial",
      "year_match_type": "exact",
      "container_match": "yes"
    }
  },
  "reasons": [
    "title_partial_match",
    "author_partial_overlap",
    "year_exact_match",
    "container_or_publisher_partial_match",
    "phase4_candidates_found"
  ]
}
```

Why it stayed `candidate_only`:

- the year aligns well
- there is some title and author support
- but title/author evidence is only partial, so the final score stays below `0.6`

### 5. Book Chapter: Book-Backed `candidate_only`

Real artifact pattern:

```json
{
  "ctype": "book_chapter",
  "status": "candidate_only",
  "strategy_used": "chapter_book_title_year_near",
  "best_candidate": {
    "record_id": "search_book:35011728",
    "title": "The Poor Side of the Netherlands",
    "source_table": "search_book",
    "ordering_score": 0.5328,
    "match_signals": {
      "title_match_strength": "exact_or_near_exact",
      "author_match_strength": "none",
      "year_match_type": "near",
      "container_match": "yes",
      "volume_issue_pages_match": "unknown"
    }
  },
  "reasons": [
    "title_exact_or_near_exact",
    "year_near_match",
    "container_or_publisher_partial_match",
    "phase4_candidates_found"
  ]
}
```

Why it stayed `candidate_only`:

- the containing book is a strong recovery target
- the year is close enough, but not exact
- there is no chapter-author overlap on the recovered book record
- it is still a useful candidate, but not strong enough to promote

This is a common pattern for OpenLibrary-backed chapter recovery: the book is
real and relevant, but the candidate is book-level rather than chapter-level.

Phase 4 container/book-title scoring is main-title aware for non-publisher
containers. When a source book title has a subtitle after `:` or after `. `
followed by an uppercase letter or digit, Phase 4 compares both the full title
and the main-title variant. This lets `Effecten van Armoede. Derde Jaarrapport
Armoede en Sociale Uitsluiting` match a candidate titled `Effecten van
armoede`, while ordinary periods in lowercase continuations or abbreviations do
not trigger subtitle boosting.

### 6. `no_match`

Real artifact pattern:

```json
{
  "ctype": "journal_article",
  "status": "no_match",
  "strategy_used": null,
  "lookup_trace": {
    "strategies_attempted": [
      "journal_title_year_exact",
      "journal_title_author_year_exact",
      "journal_title3_year_exact",
      "journal_title_container_year_exact"
    ],
    "candidate_count": 0
  },
  "reasons": [
    "phase4_no_candidates"
  ]
}
```

How to read it:

- Phase 4 did try real strategies
- no candidate survived ranking/retention
- `strategy_used` is `null` because no winning candidate exists

### 7. `skipped`

Real artifact pattern:

```json
{
  "ctype": "report",
  "status": "skipped",
  "attempted": false,
  "strategy_used": null,
  "lookup_trace": {
    "strategies_attempted": [],
    "skipped_reasons": [
      "phase4_ineligible_ctype",
      "phase4_unsupported_ctype:report"
    ]
  },
  "reasons": [
    "phase4_ineligible_ctype",
    "phase4_unsupported_ctype:report"
  ]
}
```

How to read it:

- Phase 4 did not fail
- it also did not search
- the reference was outside the supported local DB matching families

### 8. `error`

Illustrative example:

```json
{
  "status": "error",
  "strategy_used": null,
  "reasons": [
    "phase4_lookup_failed"
  ],
  "warnings": [
    "phase4_lookup_error:OperationalError"
  ]
}
```

How to read it:

- the lookup process itself failed
- this is a runtime/provider problem, not a normal "no candidate found" result

## Practical Reading Tips

- Read `status` first. It tells you which class of outcome you are looking at.
- Then read `strategy_used`. It tells you what search path actually won.
- Then read `reasons` and `match_signals` together. The reason tags are the
  human summary; the signals are the structured detail.
- Use `ordering_score` only as a Phase 4 ranking aid, not as a universal
  confidence metric outside this matcher.
- Use `lookup_trace` when you want to understand why Phase 4 searched the way it
  did or why it stopped early.

## Relationship To Other Docs

- Use [`docs/phase_boundaries.md`](./phase_boundaries.md) for the Phase 4
  boundary contract.
- Use [`docs/book_chapter_search_example.md`](./book_chapter_search_example.md)
  for a deeper walkthrough of book-chapter search behavior.
