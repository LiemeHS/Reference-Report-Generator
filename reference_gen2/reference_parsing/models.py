from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Mapping, TypeAlias

from reference_gen2.reference_segmentation.models import ReferenceStyleHint

ParserBackendName: TypeAlias = Literal["anystyle"]
CTypeName: TypeAlias = Literal[
    "journal_article",
    "webpage",
    "report",
    "book",
    "book_chapter",
    "conference_paper",
    "thesis",
    "software",
    "dataset",
    "newspaper_article",
    "unknown",
]
ClassificationStageName: TypeAlias = Literal["pre_parse", "post_parse"]
MatchTargetName: TypeAlias = Literal["crossref", "openlibrary", "none", "multiple"]
RecoveryStatusName: TypeAlias = Literal["unchanged", "attached_backward", "blocked"]


@dataclass(frozen=True)
class ParsedName:
    family: str | None = None
    given: str | None = None
    literal: str | None = None


@dataclass(frozen=True)
class AccessMetadata:
    accessed_date_text: str | None = None
    accessed_date_iso: str | None = None
    retrieval_phrase: str | None = None
    source_url: str | None = None
    source_text: str | None = None


@dataclass(frozen=True)
class ParsedReferenceData:
    type: str | None = None
    author: list[ParsedName] = field(default_factory=list)
    editor: list[ParsedName] = field(default_factory=list)
    title: list[str] = field(default_factory=list)
    container_title: list[str] = field(default_factory=list)
    publisher: list[str] = field(default_factory=list)
    institution: list[str] = field(default_factory=list)
    organization: list[str] = field(default_factory=list)
    collection_title: list[str] = field(default_factory=list)
    date: list[str] = field(default_factory=list)
    issued_year: str | None = None
    volume: list[str] = field(default_factory=list)
    issue: list[str] = field(default_factory=list)
    pages: list[str] = field(default_factory=list)
    doi: list[str] = field(default_factory=list)
    url: list[str] = field(default_factory=list)
    identifier: list[str] = field(default_factory=list)
    location: list[str] = field(default_factory=list)
    genre: list[str] = field(default_factory=list)
    note: list[str] = field(default_factory=list)
    access: AccessMetadata | None = None
    raw_tags: dict[str, list[str]] | None = None


@dataclass(frozen=True)
class ClassificationSignalBundle:
    has_url: bool = False
    has_doi: bool = False
    has_retrieval_clause: bool = False
    has_volume: bool = False
    has_issue: bool = False
    has_pages: bool = False
    has_scholarly_container: bool = False
    has_org_author: bool = False
    has_report_term: bool = False
    has_chapter_marker: bool = False
    has_editor_marker: bool = False
    has_book_container: bool = False
    has_conference_term: bool = False
    has_thesis_term: bool = False
    has_dataset_term: bool = False
    has_software_marker: bool = False
    has_news_term: bool = False


@dataclass(frozen=True)
class ClassificationResult:
    ctype: CTypeName
    trace: list[str] = field(default_factory=list)
    signals_used: ClassificationSignalBundle | None = None
    classification_stage: ClassificationStageName = "pre_parse"


@dataclass(frozen=True)
class CTypeProfile:
    ctype: CTypeName
    expected_fields: list[str] = field(default_factory=list)
    optional_fields: list[str] = field(default_factory=list)
    suspicious_fields: list[str] = field(default_factory=list)
    override_signals: list[str] = field(default_factory=list)
    required_signals: list[str] = field(default_factory=list)
    supporting_signals: list[str] = field(default_factory=list)
    contradictory_signals: list[str] = field(default_factory=list)
    allowed_repairs: list[str] = field(default_factory=list)
    allowed_reclassification_targets: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class MatchPreparation:
    eligible_for_db_match: bool = False
    match_target: MatchTargetName = "none"
    lookup_key_fields: dict[str, list[str]] = field(default_factory=dict)
    lookup_query_fields: dict[str, list[str]] = field(default_factory=dict)
    lookup_confidence_basis: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReportBasis:
    why_this_type: list[str] = field(default_factory=list)
    why_matchable_or_not: list[str] = field(default_factory=list)
    missing_fields_for_match: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedReferenceResult:
    reference_id: str
    raw_reference: str
    normalized_reference: str
    parsed_data: ParsedReferenceData | None
    warnings: list[str] = field(default_factory=list)
    parser_backend: ParserBackendName = "anystyle"
    style_hint_used: ReferenceStyleHint = "unknown"
    parser_model_used: str | None = "default"
    ctype: CTypeName = "unknown"
    classification_trace: list[str] = field(default_factory=list)
    pre_classification: ClassificationResult | None = None
    post_classification: ClassificationResult | None = None
    parse_profile_used: str | None = None
    repair_profile_used: str | None = None
    match_preparation: MatchPreparation | None = None
    report_basis: ReportBasis | None = None
    recovery_status: RecoveryStatusName = "unchanged"
    recovery_trace: list[str] = field(default_factory=list)
    recovery_source_indices: list[int] = field(default_factory=list)
    absorbed_reference_ids: list[str] = field(default_factory=list)


class ReferenceParsingError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 422,
        details: Mapping[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = dict(details or {})
