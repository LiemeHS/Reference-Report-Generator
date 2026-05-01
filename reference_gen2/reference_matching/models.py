from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, TypeAlias

from reference_gen2.reference_parsing.models import ParsedReferenceResult

Phase4StatusName: TypeAlias = Literal[
    "matched_provisional",
    "candidate_only",
    "no_match",
    "skipped",
    "error",
]
SupportedPhase4CTypeName: TypeAlias = Literal[
    "journal_article",
    "book",
    "book_chapter",
]
Phase4DoiMatchType: TypeAlias = Literal["none", "exact", "equivalent", "mismatch"]
Phase4StrengthName: TypeAlias = Literal[
    "exact_or_near_exact",
    "strong",
    "partial",
    "weak",
    "none",
]
Phase4YearMatchType: TypeAlias = Literal["exact", "near", "mismatch", "missing"]
Phase4TriState: TypeAlias = Literal["yes", "no", "unknown"]
Phase4MetadataMatchType: TypeAlias = Literal["exact", "partial", "mismatch", "unknown"]
Phase4RecordGranularity: TypeAlias = Literal["article", "book", "chapter", "unknown"]


@dataclass(frozen=True)
class Phase4InputSummary:
    reference_id: str
    ctype: str
    match_target: str
    normalized_doi: str | None = None
    normalized_title: str | None = None
    normalized_year: str | None = None
    normalized_authors: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Phase4MatchSignals:
    doi_match_type: Phase4DoiMatchType = "none"
    title_match_strength: Phase4StrengthName = "none"
    author_match_strength: Phase4StrengthName = "none"
    year_match_type: Phase4YearMatchType = "missing"
    container_match: Phase4TriState = "unknown"
    volume_issue_pages_match: Phase4MetadataMatchType = "unknown"


@dataclass(frozen=True)
class LocalDbCandidate:
    record_id: str
    record_type: str
    record_granularity: Phase4RecordGranularity = "unknown"
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    author_initials: list[str] = field(default_factory=list)
    editors: list[str] = field(default_factory=list)
    editor_initials: list[str] = field(default_factory=list)
    issued_year: str | None = None
    doi: str | None = None
    container_title: str | None = None
    publisher: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    source_table: str | None = None
    source_strategy: str | None = None
    match_signals: Phase4MatchSignals = field(default_factory=Phase4MatchSignals)
    ordering_score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)
    raw_adapter_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Phase4LookupTrace:
    doi_attempted: bool = False
    doi_query_values: list[str] = field(default_factory=list)
    doi_hit_count: int = 0
    doi_miss: bool = False
    doi_hit_quality: str | None = None
    corroboration_triggered: bool = False
    strategies_attempted: list[str] = field(default_factory=list)
    strategies_skipped: list[str] = field(default_factory=list)
    selected_query_terms: dict[str, list[str]] = field(default_factory=dict)
    query_profiles: dict[str, str] = field(default_factory=dict)
    year_profiles: dict[str, str] = field(default_factory=dict)
    candidate_count: int = 0
    second_candidate_retained: bool = False
    second_candidate_rejected_reason: str | None = None
    cascade_stop_reason: str | None = None
    skipped_reasons: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(
        default_factory=lambda: {"doi": 0.0, "fallback": 0.0, "total": 0.0}
    )


@dataclass(frozen=True)
class Phase4MatchResult:
    reference_id: str
    input_summary: Phase4InputSummary
    attempted: bool = False
    strategy_used: str | None = None
    lookup_trace: Phase4LookupTrace = field(default_factory=Phase4LookupTrace)
    candidates: list[LocalDbCandidate] = field(default_factory=list)
    top_candidates: list[LocalDbCandidate] = field(default_factory=list)
    best_candidate: LocalDbCandidate | None = None
    status: Phase4StatusName = "skipped"
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    timings_ms: dict[str, float] = field(
        default_factory=lambda: {"doi": 0.0, "fallback": 0.0, "total": 0.0}
    )


@dataclass(frozen=True)
class Phase4SearchConfig:
    name: str
    title_terms: list[str] = field(default_factory=list)
    author_terms: list[str] = field(default_factory=list)
    container_terms: list[str] = field(default_factory=list)
    fielded_terms: dict[str, list[str]] = field(default_factory=dict)
    target_tables: list[str] = field(default_factory=list)
    year: str | None = None
    year_mode: str = "exact"
    year_window: int = 0
    limit: int = 5
    strictness: str = "strict"
    enabled_by_default: bool = True
    allow_non_fts_fallback: bool = False


class LocalDbProvider(Protocol):
    def lookup_by_doi(
        self,
        *,
        ctype: SupportedPhase4CTypeName,
        doi: str,
        max_candidates: int,
    ) -> list[LocalDbCandidate]:
        ...

    def search_candidates(
        self,
        *,
        ctype: SupportedPhase4CTypeName,
        config: Phase4SearchConfig,
        max_candidates: int,
    ) -> list[LocalDbCandidate]:
        ...


@dataclass(frozen=True)
class Phase4RuntimeConfig:
    local_db_path: str | None = None
    provider: LocalDbProvider | None = None
    max_candidates: int = 5
    prefer_recovered: bool = True
    enable_relaxed_queries: bool = False
    max_fallback_strategies: int = 4
    broad_query_guard_enabled: bool = True
    prefer_distinctive_title_terms: bool = True
    allow_doi_corroboration_search: bool = True
    max_top_candidates: int = 2
    max_corroboration_strategies: int = 2
    second_candidate_min_ordering_score: float = 0.35
    allow_non_fts_scan_fallback: bool = False
    enable_near_year_fallback: bool = True
    near_year_distance: int = 1

    def resolve_provider(self) -> LocalDbProvider:
        if self.provider is not None:
            return self.provider
        if not self.local_db_path:
            raise ValueError("Phase4RuntimeConfig requires local_db_path or provider.")
        from reference_gen2.reference_matching.provider import SqliteLocalDbProvider

        return SqliteLocalDbProvider(self.local_db_path, runtime_config=self)


@dataclass(frozen=True)
class Phase4BatchInput:
    phase3: list[ParsedReferenceResult]
    phase3b: list[ParsedReferenceResult] | None = None
