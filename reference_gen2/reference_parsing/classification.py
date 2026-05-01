from __future__ import annotations

import re
from dataclasses import replace

from reference_gen2.reference_parsing.models import (
    AccessMetadata,
    ClassificationResult,
    ClassificationSignalBundle,
    CTypeName,
    CTypeProfile,
    ParsedName,
    ParsedReferenceData,
)

# Strong structural signals.
_RETRIEVAL_RE = re.compile(
    r"\b(?:geraadpleegd|opgehaald|bekeken|bezocht|retrieved|accessed|geopend)\s+op\b",
    re.IGNORECASE,
)
_DOI_RE = re.compile(r"\b(?:doi:\s*)?(?:https?://(?:dx\.)?doi\.org/)?10\.\d{4,9}/\S+\b", re.IGNORECASE)
_URL_RE = re.compile(r"\b(?:https?://|www\.)\S+\b", re.IGNORECASE)
_VOLUME_ISSUE_RE = re.compile(r"\b\d+\s*\(\s*\d+\s*\)")
_PAGES_RE = re.compile(r"\b(?:pp?\.\s*)?\d+\s*[-–]\s*\d+\b", re.IGNORECASE)
_REPORT_RE = re.compile(
    r"\b(rapport|jaarverslag|brochure|persbericht|discussion paper|white paper|policy brief|working paper)\b",
    re.IGNORECASE,
)
_CONFERENCE_RE = re.compile(
    r"\b(proceedings|proc\.|conference|symposium|workshop|congres|conferentie|summit|paper presented)\b",
    re.IGNORECASE,
)
_THESIS_RE = re.compile(
    r"\b(proefschrift|dissertatie|thesis|dissertation|masterscriptie|masterthesis|scriptie)\b",
    re.IGNORECASE,
)
_DATASET_RE = re.compile(
    r"\b(dataset|data set|databestand|datafile|repository|zenodo|figshare|osf|dataverse)\b",
    re.IGNORECASE,
)
_SOFTWARE_RE = re.compile(
    r"\[(?:generatieve ai|software|computer software|app|mobile app|ai)\]|\b(?:versie|version)\s+\S+",
    re.IGNORECASE,
)
_NEWS_RE = re.compile(
    r"\b(krant|newspaper|nieuws|news|guardian|times|volkskrant|nrc|trouw|telegraaf|parool|bbc)\b",
    re.IGNORECASE,
)
_CHAPTER_RE = re.compile(r"(?:[\"'“”‘’)\],.;:])\s+In:?\s+", re.IGNORECASE)
_EDITOR_RE = re.compile(r"\b(?:Ed\.|Eds\.|red\.|redacteur|editors?)\b", re.IGNORECASE)
_BOOK_CONTAINER_RE = re.compile(r"(?:[\"'“”‘’)\],.;:])\s+In:?\s+.+\b(?:pp?\.)", re.IGNORECASE)
_IN_EDITOR_CONTAINER_RE = re.compile(
    r"(?:[\"'“”‘’)\],.;:])\s+In:?\s+.+?(?:\((?:Ed\.|Eds\.|red\.|reds\.)\)|\bred\.)",
    re.IGNORECASE,
)
_IN_CONTAINER_ONLY_RE = re.compile(r"(?:[\"'“”‘’)\],.;:])\s+In:?\s+.+", re.IGNORECASE)
_SCHOLARLY_CONTAINER_RE = re.compile(
    r"\b(journal|review|quarterly|tijdschrift|annals|transactions|bulletin|letters)\b",
    re.IGNORECASE,
)
_REPORT_CONTAINER_RE = re.compile(
    r"\b(rapport|jaarverslag|brochure|white paper|policy brief|working paper)\b",
    re.IGNORECASE,
)
_EDITED_REPORT_CONTAINER_RE = re.compile(
    r"\b(jaarrapport|jaarrapport|armoede|monitor|verzorgingsstaat|working poor|sociale uitsluiting|poverty monitor)\b",
    re.IGNORECASE,
)
_ORG_AUTHOR_RE = re.compile(
    r"\b("
    r"ministerie|gemeente|provincie|rijks(?:overheid|dienst|instituut)?|"
    r"instituut|bureau|commissie|commission|agency|organisatie|organization|"
    r"association|vereniging|foundation|stichting|universiteit|university|"
    r"college|school|gemeenschap|raad|council|openai|movisie|eurostat"
    r")\b",
    re.IGNORECASE,
)

# Noise patterns produced by weak parser output.
_RETRIEVAL_CONTAINER_NOISE_RE = re.compile(
    r"^(?:geraadpleegd|opgehaald|bekeken|bezocht|retrieved|accessed|geopend)\s+op$",
    re.IGNORECASE,
)
_GENRE_NOISE_RE = {"van"}


C_TYPE_PROFILES: dict[CTypeName, CTypeProfile] = {
    "journal_article": CTypeProfile(
        ctype="journal_article",
        expected_fields=["title", "container_title"],
        optional_fields=["doi", "volume", "issue", "pages", "date"],
        suspicious_fields=["access"],
        override_signals=["has_doi", "has_volume", "has_issue", "has_pages", "has_scholarly_container"],
        required_signals=["has_doi|has_scholarly_volume_issue_pages"],
        supporting_signals=["has_scholarly_container"],
        contradictory_signals=["has_retrieval_url_without_scholarly_signals"],
        allowed_repairs=["normalize_article_type"],
        allowed_reclassification_targets=["conference_paper", "webpage", "report"],
    ),
    "webpage": CTypeProfile(
        ctype="webpage",
        expected_fields=["title", "url"],
        optional_fields=["date", "organization", "access"],
        suspicious_fields=["container_title", "volume", "issue", "pages", "doi"],
        override_signals=["has_retrieval_clause", "has_url"],
        required_signals=["has_retrieval_clause|has_url", "not_has_scholarly_article_signals"],
        supporting_signals=["has_org_author"],
        contradictory_signals=["has_doi", "has_volume", "has_issue", "has_pages"],
        allowed_repairs=["strip_retrieval_noise", "promote_organization_author", "preserve_access"],
        allowed_reclassification_targets=["journal_article", "report", "software"],
    ),
    "report": CTypeProfile(
        ctype="report",
        expected_fields=["title"],
        optional_fields=["organization", "institution", "publisher", "url", "date"],
        suspicious_fields=["volume", "issue", "pages"],
        override_signals=["has_report_term", "has_org_author"],
        required_signals=["has_report_term|has_org_author", "not_has_scholarly_article_signals"],
        supporting_signals=["has_report_container"],
        contradictory_signals=["has_scholarly_article_signals"],
        allowed_repairs=["promote_organization_author", "normalize_report_type"],
        allowed_reclassification_targets=["webpage", "journal_article", "book", "book_chapter"],
    ),
    "book": CTypeProfile(
        ctype="book",
        expected_fields=["title"],
        optional_fields=["publisher", "date", "organization"],
        required_signals=["has_book_fallback"],
        supporting_signals=["has_authorish_source"],
        contradictory_signals=["has_url_only", "has_scholarly_article_signals"],
        allowed_repairs=["none"],
        allowed_reclassification_targets=["report"],
    ),
    "book_chapter": CTypeProfile(
        ctype="book_chapter",
        expected_fields=["title"],
        optional_fields=["container_title", "editor", "pages"],
        suspicious_fields=["doi"],
        override_signals=["has_chapter_marker", "has_editor_marker", "has_book_container"],
        required_signals=["has_chapter_marker", "has_editor_marker|has_book_container|has_edited_volume_pattern"],
        supporting_signals=["has_pages", "has_edited_volume_pattern"],
        contradictory_signals=[],
        allowed_repairs=["preserve_chapter_fields"],
        allowed_reclassification_targets=["book", "report"],
    ),
    "conference_paper": CTypeProfile(
        ctype="conference_paper",
        expected_fields=["title"],
        optional_fields=["container_title", "pages", "doi"],
        override_signals=["has_conference_term"],
        required_signals=["has_conference_term"],
        supporting_signals=["has_doi"],
        contradictory_signals=[],
        allowed_repairs=["preserve_conference_container"],
        allowed_reclassification_targets=["journal_article"],
    ),
    "thesis": CTypeProfile(
        ctype="thesis",
        expected_fields=["title"],
        optional_fields=["institution", "date", "url"],
        required_signals=["has_thesis_term"],
        supporting_signals=["has_url"],
        contradictory_signals=[],
        allowed_repairs=["none"],
        allowed_reclassification_targets=["report", "book"],
    ),
    "software": CTypeProfile(
        ctype="software",
        expected_fields=["title", "url"],
        optional_fields=["organization", "date", "note"],
        suspicious_fields=["container_title", "volume", "issue", "pages", "doi"],
        override_signals=["has_software_marker", "has_url", "has_org_author"],
        required_signals=["has_software_marker", "has_url|has_org_author"],
        supporting_signals=["has_org_author"],
        contradictory_signals=["has_scholarly_article_signals"],
        allowed_repairs=["strip_scholarly_noise", "promote_organization_author", "normalize_software_type"],
        allowed_reclassification_targets=["webpage"],
    ),
    "dataset": CTypeProfile(
        ctype="dataset",
        expected_fields=["title", "url"],
        optional_fields=["organization", "date"],
        required_signals=["has_dataset_term"],
        supporting_signals=["has_url"],
        contradictory_signals=[],
        allowed_repairs=["none"],
        allowed_reclassification_targets=["webpage"],
    ),
    "newspaper_article": CTypeProfile(
        ctype="newspaper_article",
        expected_fields=["title", "container_title"],
        optional_fields=["date", "url"],
        required_signals=["has_news_term", "has_url|has_date_like_source"],
        supporting_signals=[],
        contradictory_signals=["has_doi", "has_volume", "has_issue", "has_pages"],
        allowed_repairs=["none"],
        allowed_reclassification_targets=["webpage"],
    ),
    "unknown": CTypeProfile(
        ctype="unknown",
        allowed_repairs=["none"],
    ),
}


def normalize_reference_for_apa7_nl(raw_reference: str) -> str:
    normalized = " ".join(raw_reference.split())
    normalized = re.sub(
        r"\b(?:geopend|opgehaald|bekeken|bezocht)\s+op\b",
        "Geraadpleegd op",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized.strip()


def extract_classification_signals(
    raw_reference: str,
    parsed: ParsedReferenceData | None = None,
) -> ClassificationSignalBundle:
    raw = raw_reference or ""
    parsed = parsed or ParsedReferenceData()
    container_values = " ".join(_clean_container_values(parsed))
    author_label = raw.split(".")[0]
    org_labels = " ".join(
        parsed.organization
        + parsed.institution
        + [_name_label(name) for name in parsed.author]
    )

    scholarly_container = bool(_SCHOLARLY_CONTAINER_RE.search(container_values)) or bool(
        _SCHOLARLY_CONTAINER_RE.search(raw)
    )
    scholarly_volume_issue_pages = bool(
        parsed.doi
        or (
            (parsed.volume or _VOLUME_ISSUE_RE.search(raw))
            and (parsed.issue or _VOLUME_ISSUE_RE.search(raw))
        )
        or (parsed.pages or _PAGES_RE.search(raw))
    )

    return ClassificationSignalBundle(
        has_url=bool(parsed.url or _URL_RE.search(raw)),
        has_doi=bool(parsed.doi or _DOI_RE.search(raw)),
        has_retrieval_clause=bool(parsed.access and parsed.access.retrieval_phrase) or bool(_RETRIEVAL_RE.search(raw)),
        has_volume=bool(parsed.volume or _VOLUME_ISSUE_RE.search(raw)),
        has_issue=bool(parsed.issue or _VOLUME_ISSUE_RE.search(raw)),
        has_pages=bool(parsed.pages or _PAGES_RE.search(raw)),
        has_scholarly_container=scholarly_container or scholarly_volume_issue_pages,
        has_org_author=bool(parsed.organization or parsed.institution) or bool(_ORG_AUTHOR_RE.search(org_labels or author_label)),
        has_report_term=bool(_REPORT_RE.search(raw)),
        has_chapter_marker=bool(_CHAPTER_RE.search(raw)),
        has_editor_marker=bool(parsed.editor) or bool(_EDITOR_RE.search(raw)),
        has_book_container=bool(parsed.collection_title or parsed.container_title) or bool(
            _BOOK_CONTAINER_RE.search(raw)
            or _IN_EDITOR_CONTAINER_RE.search(raw)
            or _IN_CONTAINER_ONLY_RE.search(raw)
            or bool(parsed.editor and parsed.container_title)
        ),
        has_conference_term=bool(_CONFERENCE_RE.search(raw + " " + container_values)),
        has_thesis_term=bool(_THESIS_RE.search(raw)),
        has_dataset_term=bool(_DATASET_RE.search(raw)),
        has_software_marker=bool(_SOFTWARE_RE.search(raw)),
        has_news_term=bool(_NEWS_RE.search(raw + " " + container_values)),
    )


def classify_reference_pre_parse(raw_reference: str) -> ClassificationResult:
    signals = extract_classification_signals(raw_reference)
    trace: list[str] = []

    # Normative tree order.
    if _has_strong_book_chapter_signals(raw_reference, signals):
        trace.append("BRANCH chapter: chapter marker + edited volume/container pattern -> book_chapter")
        return ClassificationResult("book_chapter", trace, signals, "pre_parse")
    trace.append("skip chapter: missing strong edited-volume chapter pattern")

    if signals.has_thesis_term:
        trace.append("BRANCH thesis: thesis marker -> thesis")
        return ClassificationResult("thesis", trace, signals, "pre_parse")
    trace.append("skip thesis: no thesis marker")

    if signals.has_dataset_term:
        trace.append("BRANCH dataset: dataset/repository marker -> dataset")
        return ClassificationResult("dataset", trace, signals, "pre_parse")
    trace.append("skip dataset: no dataset marker")

    if signals.has_software_marker and (signals.has_url or signals.has_org_author):
        trace.append("BRANCH software: software marker + url/org pattern -> software")
        return ClassificationResult("software", trace, signals, "pre_parse")
    trace.append("skip software: no software marker + url/org pattern")

    if signals.has_conference_term:
        trace.append("BRANCH conference: proceedings/conference marker -> conference_paper")
        return ClassificationResult("conference_paper", trace, signals, "pre_parse")
    trace.append("skip conference: no conference marker")

    if (
        not (
            signals.has_retrieval_clause
            and signals.has_url
            and signals.has_doi
            and not _has_scholarly_article_signals(signals)
        )
        and (signals.has_doi or (_has_scholarly_article_signals(signals)))
    ):
        trace.append("BRANCH scholarly: doi or scholarly volume/issue/pages -> journal_article")
        return ClassificationResult("journal_article", trace, signals, "pre_parse")
    trace.append("skip scholarly: no strong scholarly article signals")

    if signals.has_news_term and (signals.has_url or _has_date_like_source(raw_reference)):
        trace.append("BRANCH news: named news source + article shape -> newspaper_article")
        return ClassificationResult("newspaper_article", trace, signals, "pre_parse")
    trace.append("skip news: no strong news-source signals")

    if (
        signals.has_retrieval_clause
        and signals.has_url
        and not _has_scholarly_article_signals(signals)
    ):
        trace.append("BRANCH webpage-strong: retrieval + url + no scholarly signals -> webpage")
        return ClassificationResult("webpage", trace, signals, "pre_parse")
    trace.append("skip webpage-strong: no strong webpage pattern")

    if _has_strong_report_signals(raw_reference, signals):
        trace.append("BRANCH report-strong: report term or institutional report pattern -> report")
        return ClassificationResult("report", trace, signals, "pre_parse")
    trace.append("skip report-strong: no strong report pattern")

    if signals.has_url and not signals.has_doi and not _has_scholarly_article_signals(signals):
        trace.append("BRANCH webpage-weak: url + no scholarly/article signals -> webpage")
        return ClassificationResult("webpage", trace, signals, "pre_parse")
    trace.append("skip webpage-weak: no weak webpage fallback")

    if _has_weak_book_fallback(raw_reference, signals):
        trace.append("BRANCH book-weak: title/author-date-publisher fallback -> book")
        return ClassificationResult("book", trace, signals, "pre_parse")
    trace.append("skip book-weak: insufficient weak book signals")

    trace.append("BRANCH unknown: no strong branch won -> unknown")
    return ClassificationResult("unknown", trace, signals, "pre_parse")


def classify_reference_post_parse(
    raw_reference: str,
    parsed: ParsedReferenceData | None,
    pre: ClassificationResult,
) -> tuple[ClassificationResult, list[str]]:
    signals = extract_classification_signals(raw_reference, parsed)
    trace = list(pre.trace)
    warnings: list[str] = []

    contradiction = _contradiction_target(pre.ctype, signals, raw_reference, parsed)
    if contradiction is not None and contradiction != pre.ctype:
        trace.append(f"RECLASSIFY post: strong contradiction -> {contradiction}")
        result = ClassificationResult(contradiction, trace, signals, "post_parse")
        warnings.append("classifier_reclassified_post_parse")
    else:
        trace.append(f"KEEP post: no strong contradiction -> {pre.ctype}")
        result = ClassificationResult(pre.ctype, trace, signals, "post_parse")

    if result.ctype == "unknown":
        warnings.append("classifier_unknown_ctype")
    return result, warnings


def repair_parsed_reference_for_ctype(
    parsed: ParsedReferenceData | None,
    raw_reference: str,
    ctype: CTypeName,
) -> ParsedReferenceData | None:
    if parsed is None:
        return None
    if ctype == "webpage":
        return _repair_webpage(parsed, raw_reference)
    if ctype == "software":
        return _repair_software(parsed, raw_reference)
    if ctype == "report":
        return _repair_report(parsed)
    if ctype == "book":
        return _repair_book(parsed)
    if ctype == "book_chapter":
        return _repair_book_chapter(parsed)
    if ctype == "journal_article":
        return _repair_journal_article(parsed)
    return parsed


def profile_for_ctype(ctype: CTypeName) -> CTypeProfile:
    return C_TYPE_PROFILES[ctype]


def _has_scholarly_article_signals(signals: ClassificationSignalBundle) -> bool:
    return signals.has_scholarly_container and (signals.has_volume or signals.has_issue or signals.has_pages)


def _has_strong_book_chapter_signals(
    raw_reference: str,
    signals: ClassificationSignalBundle,
) -> bool:
    if not signals.has_chapter_marker:
        return False
    if signals.has_editor_marker or signals.has_book_container:
        return True
    if _EDITED_REPORT_CONTAINER_RE.search(raw_reference) and signals.has_pages:
        return True
    return False


def _has_strong_report_signals(raw_reference: str, signals: ClassificationSignalBundle) -> bool:
    if _has_strong_book_chapter_signals(raw_reference, signals):
        return False
    return (
        signals.has_report_term
        or (
            signals.has_org_author
            and not _has_scholarly_article_signals(signals)
            and not signals.has_doi
            and (_REPORT_CONTAINER_RE.search(raw_reference) is not None or not signals.has_retrieval_clause)
        )
    )


def _has_weak_book_fallback(raw_reference: str, signals: ClassificationSignalBundle) -> bool:
    if signals.has_url or signals.has_doi or _has_scholarly_article_signals(signals) or _has_strong_book_chapter_signals(raw_reference, signals):
        return False
    return bool(re.search(r"\(\s*(?:19|20)\d{2}", raw_reference) or re.search(r"\b[A-Z][A-Za-z'`\-]+,\s+[A-Z]", raw_reference))


def _has_date_like_source(raw_reference: str) -> bool:
    return bool(re.search(r"\(\s*(?:19|20)\d{2}", raw_reference))


def _contradiction_target(
    ctype: CTypeName,
    signals: ClassificationSignalBundle,
    raw_reference: str,
    parsed: ParsedReferenceData | None = None,
) -> CTypeName | None:
    # Bound reclassification: at most one step and only on strong contradiction.
    if ctype == "webpage":
        if signals.has_doi or _has_scholarly_article_signals(signals):
            return "journal_article"
        if _has_strong_report_signals(raw_reference, signals) and not signals.has_retrieval_clause:
            return "report"
        return None

    if ctype == "journal_article":
        if _has_strong_book_chapter_signals(raw_reference, signals):
            return "book_chapter"
        if _has_doi_book_signals(parsed, signals):
            return "book"
        if signals.has_retrieval_clause and signals.has_url and not signals.has_doi and not _has_scholarly_article_signals(signals):
            return "webpage"
        return None

    if ctype == "report":
        if _has_strong_book_chapter_signals(raw_reference, signals):
            return "book_chapter"
        if signals.has_doi or _has_scholarly_article_signals(signals):
            return "journal_article"
        if signals.has_retrieval_clause and signals.has_url and not signals.has_report_term:
            return "webpage"
        return None

    if ctype == "software":
        if not signals.has_software_marker and signals.has_url and not signals.has_doi and not _has_scholarly_article_signals(signals):
            return "webpage"
        return None

    if ctype == "conference_paper":
        if not signals.has_conference_term and (signals.has_doi or _has_scholarly_article_signals(signals)):
            return "journal_article"
        return None

    if ctype == "book":
        if _has_strong_book_chapter_signals(raw_reference, signals):
            return "book_chapter"
        if _has_strong_report_signals(raw_reference, signals):
            return "report"
        return None

    if ctype == "unknown":
        if _has_parsed_journal_article_shape(parsed, signals):
            return "journal_article"
        return None

    return None


def _has_doi_book_signals(
    parsed: ParsedReferenceData | None,
    signals: ClassificationSignalBundle,
) -> bool:
    if parsed is None or not signals.has_doi:
        return False
    if signals.has_volume or signals.has_issue or signals.has_pages:
        return False
    if parsed.container_title or parsed.collection_title:
        return False
    if not parsed.title or not parsed.issued_year or not parsed.author:
        return False
    if parsed.publisher:
        return True
    return bool(_publisher_values_from_genre(parsed.genre))


def _has_parsed_journal_article_shape(
    parsed: ParsedReferenceData | None,
    signals: ClassificationSignalBundle,
) -> bool:
    if parsed is None:
        return False
    if not parsed.title or not parsed.issued_year or not parsed.author:
        return False
    return bool(
        parsed.doi
        or parsed.container_title
        or parsed.volume
        or parsed.issue
        or parsed.pages
        or _has_scholarly_article_signals(signals)
    )


def _repair_webpage(parsed: ParsedReferenceData, raw_reference: str) -> ParsedReferenceData:
    updated = parsed
    updated = _promote_organization_author(updated)
    container_title = [value for value in _clean_container_values(updated)]
    genre = [value for value in updated.genre if value.strip().casefold() not in _GENRE_NOISE_RE]
    updated = replace(
        updated,
        type="webpage",
        container_title=container_title,
        volume=[],
        issue=[],
        pages=[],
        doi=[],
        genre=genre,
    )
    if updated.access is None and _RETRIEVAL_RE.search(raw_reference):
        phrase_match = re.search(
            r"(Geraadpleegd op\s+.+?,\s+van\s+(https?://\S+|www\.\S+))",
            raw_reference,
            re.IGNORECASE,
        )
        source_url = updated.url[0] if updated.url else None
        updated = replace(
            updated,
            access=AccessMetadata(
                source_url=source_url,
                retrieval_phrase=phrase_match.group(1) if phrase_match else None,
            ),
        )
    return updated


def _repair_software(parsed: ParsedReferenceData, raw_reference: str) -> ParsedReferenceData:
    updated = _repair_webpage(parsed, raw_reference)
    return replace(updated, type="software")


def _repair_report(parsed: ParsedReferenceData) -> ParsedReferenceData:
    updated = _promote_organization_author(parsed)
    return replace(updated, type="report")


def _repair_book(parsed: ParsedReferenceData) -> ParsedReferenceData:
    publisher = list(parsed.publisher)
    for value in _publisher_values_from_genre(parsed.genre):
        if value not in publisher:
            publisher.append(value)
    updated = replace(parsed, type="book", publisher=publisher)
    return _recover_publisher_from_location(updated)


def _repair_book_chapter(parsed: ParsedReferenceData) -> ParsedReferenceData:
    return _recover_publisher_from_location(parsed)


def _repair_journal_article(parsed: ParsedReferenceData) -> ParsedReferenceData:
    return replace(parsed, type="article-journal")


def _promote_organization_author(parsed: ParsedReferenceData) -> ParsedReferenceData:
    if parsed.organization:
        return parsed
    if not parsed.author:
        return parsed
    first = parsed.author[0]
    label = _name_label(first)
    if not label:
        return parsed
    if not _ORG_AUTHOR_RE.search(label):
        return parsed
    organizations = list(parsed.organization)
    if label not in organizations:
        organizations.append(label)
    return replace(parsed, organization=organizations)


def _clean_container_values(parsed: ParsedReferenceData) -> list[str]:
    return [
        value
        for value in (parsed.container_title + parsed.collection_title)
        if value.strip() and not _RETRIEVAL_CONTAINER_NOISE_RE.match(value.strip())
    ]


def _publisher_values_from_genre(values: list[str]) -> list[str]:
    publishers: list[str] = []
    for value in values:
        cleaned = value.strip().strip(".;,")
        if not cleaned:
            continue
        normalized = cleaned.casefold()
        if normalized in {"article", "research article", "original article", "review article"}:
            continue
        word_count = len(re.findall(r"[A-Za-z][A-Za-z'`\-]*", cleaned))
        if 1 <= word_count <= 5:
            publishers.append(cleaned)
    return publishers


def _recover_publisher_from_location(parsed: ParsedReferenceData) -> ParsedReferenceData:
    publishers = list(parsed.publisher)
    locations = list(parsed.location)

    normalized_publishers: list[str] = []
    normalized_locations = list(locations)
    for value in publishers:
        publisher, location = _split_location_publisher_tail(value)
        if publisher and publisher not in normalized_publishers:
            normalized_publishers.append(publisher)
        elif value and value not in normalized_publishers:
            normalized_publishers.append(value)
        if location and location not in normalized_locations:
            normalized_locations.append(location)

    if not normalized_publishers:
        updated_locations: list[str] = []
        for value in normalized_locations:
            publisher, location = _split_location_publisher_tail(value)
            if publisher and publisher not in normalized_publishers:
                normalized_publishers.append(publisher)
            updated_locations.append(location or value)
        normalized_locations = updated_locations

    normalized_locations = [value for value in normalized_locations if value]
    if normalized_publishers == parsed.publisher and normalized_locations == parsed.location:
        return parsed
    return replace(parsed, publisher=normalized_publishers, location=normalized_locations)


def _split_location_publisher_tail(value: str | None) -> tuple[str | None, str | None]:
    cleaned = (value or "").strip().strip(".;")
    if "," not in cleaned:
        return None, None
    parts = [part.strip() for part in cleaned.split(",") if part.strip()]
    if len(parts) < 2:
        return None, None
    publisher = parts[-1]
    location = ", ".join(parts[:-1])
    if not _looks_like_publisher_name(publisher):
        return None, None
    return publisher, location


def _looks_like_publisher_name(value: str) -> bool:
    cleaned = value.strip()
    if not cleaned:
        return False
    word_count = len(re.findall(r"[A-Za-z][A-Za-z'`\-]*", cleaned))
    if not (1 <= word_count <= 6):
        return False
    normalized = cleaned.casefold()
    if normalized in {"uk", "usa", "us", "ca", "ma"}:
        return False
    return True


def _name_label(name: ParsedName) -> str:
    return name.literal or " ".join(part for part in [name.given, name.family] if part).strip()
