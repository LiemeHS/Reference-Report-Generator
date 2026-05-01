"""Static HTML report generation from finalized sanitized report data."""

from __future__ import annotations

from dataclasses import dataclass
import base64
import hashlib
from html import escape
from html.parser import HTMLParser
from pathlib import Path
import re
from typing import Any

from reference_gen2.finalization import SanitizedCycleReport, serialize_sanitized_report
from reference_gen2.reference_styles import profile_for_reference_style


@dataclass(frozen=True)
class StaticReportConfig:
    """Configuration for standalone sanitized HTML report rendering."""

    title: str = "Reference Report"


class ReportGenerationError(ValueError):
    """Raised when Phase 6 is asked to render unsafe or unsupported data."""


_FORBIDDEN_PAYLOAD_KEYS = {
    "accepted_candidate",
    "adapter_payload",
    "adapter_payloads",
    "authors",
    "best_candidate",
    "bibliography",
    "candidate",
    "candidates",
    "container_title",
    "db_path",
    "doi",
    "input_file",
    "lookup_key_fields",
    "lookup_query_fields",
    "normalized_reference",
    "original_filename",
    "parsed_data",
    "publisher",
    "raw_adapter_data",
    "raw_bibliography_text",
    "raw_reference",
    "reference_list_text",
    "references",
    "rich_payload",
    "runner_up_candidate",
    "text",
    "title",
    "top_candidates",
    "url",
}

_TOP_LEVEL_ALLOWED_KEYS = {
    "cycle_id",
    "error",
    "phase1",
    "phase2",
    "phase3",
    "phase3b",
    "phase4",
    "phase5",
    "source_mode",
    "status",
    "style_hint",
    "requested_style_hint",
    "timings_ms",
}

_STATUS_CLASS_MAP = {
    "candidate-only": "needs-review",
    "candidate_only": "needs-review",
    "error": "error",
    "fail": "suspicious",
    "failed": "suspicious",
    "matched": "verified",
    "matched-provisional": "needs-review",
    "matched_provisional": "needs-review",
    "needs-review": "needs-review",
    "needs_review": "needs-review",
    "no-match": "needs-review",
    "no_match": "needs-review",
    "not-run": "skipped",
    "not_run": "skipped",
    "not-applicable": "skipped",
    "not_applicable": "skipped",
    "n/a": "skipped",
    "ok": "verified",
    "pass": "verified",
    "skipped": "skipped",
    "suspicious": "suspicious",
    "unmatched": "unmatched",
    "verified": "verified",
    "warning": "needs-review",
}

_DOI_TEXT_RE = re.compile(
    r"(?P<doi_url>https?://(?:dx\.)?doi\.org/(?P<doi_from_url>10\.\d{4,9}/[^\s<>'\"]+))"
    r"|(?P<doi_label>doi:\s*(?P<doi_from_label>10\.\d{4,9}/[^\s<>'\"]+))"
    r"|(?P<bare_doi>\b10\.\d{4,9}/[^\s<>'\"]+)",
    re.IGNORECASE,
)
_URL_TEXT_RE = re.compile(r"https?://[^\s<>'\"]+", re.IGNORECASE)


def _report() -> dict[str, Any]:
    """Return a compact sanitized report fixture for cross-file tests.

    This helper is intentionally tiny and deterministic so adapter/reporting tests
    can reuse it as a stable contract sample.
    """
    return {
        "cycle_id": "cycle_abcdefghijklmno",
        "status": "matched",
        "source_mode": "text",
        "style_hint": "apa7_nl",
        "timings_ms": {"total": 3210, "phase3": 1500},
        "phase3": [
            {
                "opaque_reference_id": "r1",
                "display_reference": "Alpha, A. (2020). Example.",
                "ctype": "journal_article",
                "parser_backend": "mock",
                "match_target": "journal_article",
                "match_eligible": True,
            }
        ],
        "phase4": [
            {
                "opaque_reference_id": "r1",
                "status": "matched",
                "best_candidate_display": "Alpha, A. (2020). Example.",
                "candidate_count": 1,
            }
        ],
        "phase5": [
            {
                "opaque_reference_id": "r1",
                "status": "matched",
                "final_status": "matched",
                "final_confidence": "0.92",
                "accepted_record_id": "rec1",
                "accepted_match_display": "Alpha, A. (2020). Example.",
                "accepted_match_render": "Alpha, A. (2020). Example.",
                "runner_up_record_id": None,
                "runner_up_match_display": None,
            }
        ],
    }


def generate_html_report(
    report: SanitizedCycleReport | dict[str, Any],
    output_path: Path | str,
    *,
    config: StaticReportConfig | None = None,
) -> str:
    """Render sanitized static HTML and write it as a local file artifact.

    Returns the rendered HTML so callers can inspect or test it without reading
    the file back from disk. This writer does not host, serve, clean up, or
    manage report lifecycle state; those responsibilities belong to a later
    serving/session layer.
    """
    html = render_html_report(report, config=config)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return html


def render_html_report(
    report: SanitizedCycleReport | dict[str, Any],
    *,
    config: StaticReportConfig | None = None,
) -> str:
    """Render a standalone sanitized static HTML report string.

    Phase 6 accepts the final sanitized report contract only. Raw/rich Phase
    1-5 internals must be finalized and sanitized before reaching this layer.
    """
    config = config or StaticReportConfig()
    payload = _payload(report)
    references = _reference_rows(payload)
    counts = _status_counts(references)
    type_counts = _source_type_counts(references)

    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(config.title)} - {_e(payload.get('cycle_id', ''))}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        '<main class="shell">',
        _render_header(payload, counts, type_counts, len(references), config),
        _render_error(payload),
        _render_document_summary(payload),
        _render_reference_list(references),
        "</main>",
        f"<script>{_JS}</script>",
        "</body>",
        "</html>",
    ]
    return "\n".join(part for part in parts if part)


def report_inline_style_csp_hash() -> str:
    """Return the CSP hash for the report's static offline stylesheet."""
    digest = hashlib.sha256(_CSS.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def report_inline_script_csp_hash() -> str:
    """Return the CSP hash for the report's static offline controls script."""
    digest = hashlib.sha256(_JS.encode("utf-8")).digest()
    return f"'sha256-{base64.b64encode(digest).decode('ascii')}'"


def _payload(report: SanitizedCycleReport | dict[str, Any]) -> dict[str, Any]:
    if isinstance(report, dict):
        _validate_sanitized_payload(report)
        return report
    payload = serialize_sanitized_report(report)
    _validate_sanitized_payload(payload)
    return payload


def _validate_sanitized_payload(payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        raise ReportGenerationError("Phase 6 report input must be a sanitized report mapping.")

    unknown_top_level = set(payload) - _TOP_LEVEL_ALLOWED_KEYS
    if unknown_top_level:
        keys = ", ".join(sorted(unknown_top_level))
        raise ReportGenerationError(f"Unsupported Phase 6 report keys: {keys}")

    for required in ("cycle_id", "status", "source_mode", "style_hint"):
        if required not in payload:
            raise ReportGenerationError(f"Missing sanitized report key: {required}")

    _validate_top_level_timings(payload.get("timings_ms"))
    _reject_forbidden_keys(payload)


def _validate_top_level_timings(value: Any) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        raise ReportGenerationError("timings_ms must be a sanitized timing mapping.")
    for key, timing in value.items():
        key_text = str(key)
        if key_text != "total" and not (key_text.startswith("phase") and key_text[5:].isdigit()):
            raise ReportGenerationError(f"Unsupported timing key: {key_text}")
        if isinstance(timing, bool) or not isinstance(timing, (int, float)):
            raise ReportGenerationError(f"Unsupported timing value for: {key_text}")


def _reject_forbidden_keys(value: Any, path: str = "report") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_text = str(key)
            if (
                key_text in _FORBIDDEN_PAYLOAD_KEYS
                and not _is_safe_metric_bucket(path, key_text)
                and not _is_safe_citation_render_field(path, key_text)
            ):
                raise ReportGenerationError(
                    f"Unsafe raw/rich report field is not allowed in Phase 6: {path}.{key_text}"
                )
            _reject_forbidden_keys(child, f"{path}.{key_text}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_forbidden_keys(child, f"{path}[{index}]")


def _is_safe_metric_bucket(path: str, key: str) -> bool:
    return key == "doi" and path.endswith(".timings_ms")


def _is_safe_citation_render_field(path: str, key: str) -> bool:
    return key == "text" and (
        path.endswith(".accepted_match_render") or path.endswith(".runner_up_match_render")
    )


def _status_counts(references: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        "verified": 0,
        "needs_review": 0,
        "suspicious": 0,
    }
    for row in references:
        status = _filter_status(row.get("final_status"))
        counts[status] += 1
    return counts


def _source_type_counts(references: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in references:
        key = _source_type_key(row.get("ctype"))
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items(), key=lambda item: (_source_type_label(item[0]), item[0])))


def _reference_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    phase3_refs = payload.get("phase3b") or payload.get("phase3") or []
    phase3 = _by_id(phase3_refs)
    phase4 = _by_id(payload.get("phase4") or [])
    phase5 = _by_id(payload.get("phase5") or [])
    ordered_ids: list[str] = []
    for sections in (
        payload.get("phase5") or [],
        payload.get("phase4") or [],
        phase3_refs,
    ):
        for section in sections:
            ref_id = section.get("opaque_reference_id")
            if ref_id and ref_id not in ordered_ids:
                ordered_ids.append(ref_id)

    rows: list[dict[str, Any]] = []
    for index, ref_id in enumerate(ordered_ids, start=1):
        p3 = phase3.get(ref_id, {})
        p4 = phase4.get(ref_id, {})
        p5 = phase5.get(ref_id, {})
        rows.append(
            {
                "index": index,
                "opaque_reference_id": ref_id,
                "display_reference": p3.get("display_reference"),
                "ctype": p3.get("ctype", "unknown"),
                "parser_backend": p3.get("parser_backend", "unknown"),
                "match_target": p3.get("match_target", "none"),
                "match_eligible": p3.get("match_eligible", False),
                "phase4_status": p5.get("phase4_status") or p4.get("status", "not_run"),
                "final_status": p5.get("final_status") or _fallback_status(p4, p3),
                "final_confidence": p5.get("final_confidence", "none"),
                "confidence_score": p5.get("confidence_score"),
                "accepted_record_id": p5.get("accepted_record_id"),
                "runner_up_record_id": p5.get("runner_up_record_id"),
                "accepted_match_display": p5.get("accepted_match_display")
                or p4.get("best_candidate_display"),
                "runner_up_match_display": p5.get("runner_up_match_display"),
                "accepted_match_render": p5.get("accepted_match_render"),
                "runner_up_match_render": p5.get("runner_up_match_render"),
                "best_candidate_display": p4.get("best_candidate_display"),
                "candidate_count": p4.get("candidate_count", 0),
                "strategy_used": p4.get("strategy_used"),
                "review_flags": p5.get("review_flags", []),
                "evidence_checks": p5.get("evidence_checks", []),
                "field_comparisons": p5.get("field_comparisons", []),
                "warnings": _combine_lists(p3.get("warnings"), p4.get("warnings"), p5.get("warnings")),
                "reasons": _combine_lists(p4.get("reasons"), p5.get("reasons")),
                "missing_fields_for_match": p3.get("missing_fields_for_match", []),
                "parsed_fields": p3.get("parsed_fields", {}),
            }
        )
    return rows


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item.get("opaque_reference_id")): item for item in items if item.get("opaque_reference_id")}


def _fallback_status(phase4: dict[str, Any], phase3: dict[str, Any]) -> str:
    if not phase3 and not phase4:
        return "unmatched"
    status = str(phase4.get("status", ""))
    if status in {"skipped", "error"}:
        return status
    if status in {"no_match", "candidate_only"}:
        return "needs_review"
    if status:
        return "needs_review"
    return "skipped" if phase3 and not phase3.get("match_eligible", False) else "unmatched"


def _combine_lists(*values: Any) -> list[str]:
    combined: list[str] = []
    for value in values:
        if not value:
            continue
        for item in value:
            text = str(item)
            if text not in combined:
                combined.append(text)
    return combined


def _render_header(
    payload: dict[str, Any],
    counts: dict[str, int],
    type_counts: dict[str, int],
    total: int,
    config: StaticReportConfig,
) -> str:
    status = str(payload.get("status", "unknown"))
    status_chip = _render_report_status(status)
    cycle_id = payload.get("cycle_id", "-")
    style_label = _report_style_label(payload)
    source_label = _display_label(payload.get("source_mode", "unknown"))
    return f"""
<header class="report-header">
  <div class="title-row">
    <div>
      <p class="eyebrow">Static sanitized report</p>
      <h1>{_e(config.title)}</h1>
      <p class="meta">ID: {_e(cycle_id)} · Style: {_e(style_label)} · Source: {_e(source_label)}</p>
    </div>
    {status_chip}
  </div>
  <div class="header-controls" aria-label="Report search">
    <label class="search-label">
      <span>Search</span>
      <input id="reportSearch" type="search" placeholder="search for specific references">
    </label>
    <div class="view-controls" aria-label="Report view controls">
      <button class="view-toggle" id="saveReportButton" type="button">Save report</button>
      <button class="view-toggle active" id="fullViewToggle" type="button" data-view="full">Full report</button>
      <button class="view-toggle" id="basicViewToggle" type="button" data-view="basic">Basic / print</button>
    </div>
  </div>
  <div class="stats-grid" id="reportFilters" aria-label="Report filters">
    <button class="stat active" type="button" data-filter="all"><span>{total}</span><small>References</small></button>
    <button class="stat good" type="button" data-filter="verified"><span>{counts["verified"]}</span><small>Verified</small></button>
    <button class="stat warn" type="button" data-filter="needs_review"><span>{counts["needs_review"]}</span><small>Needs review</small></button>
    <button class="stat bad" type="button" data-filter="suspicious"><span>{counts["suspicious"]}</span><small>Suspicious</small></button>
  </div>
  {_render_type_filters(type_counts, total)}
  {_render_progress(counts, total)}
</header>
""".strip()


def _render_report_status(status: str) -> str:
    if _status_class(status) != "error":
        return ""
    return f'<div class="status-chip status-error">{_label(status)}</div>'


def _render_progress(counts: dict[str, int], total: int) -> str:
    if total <= 0:
        return ""
    segments = []
    for status in ("verified", "needs_review", "suspicious"):
        width = counts.get(status, 0) / total * 100
        if width <= 0:
            continue
        segments.append(
            f'<meter class="progress-meter status-{_status_class(status)}" min="0" max="{total}" value="{counts.get(status, 0)}">{width:.2f}%</meter>'
        )
    return '<div class="progress" aria-label="Reference status distribution">' + "".join(segments) + "</div>"


def _render_type_filters(type_counts: dict[str, int], total: int) -> str:
    if total <= 0:
        return ""
    buttons = [
        '<button class="type-chip active" type="button" data-type-filter="all">All types</button>'
    ]
    for source_type, count in type_counts.items():
        buttons.append(
            '<button class="type-chip" type="button" '
            f'data-type-filter="{_e(source_type)}">{_e(_source_type_label(source_type))} ({count})</button>'
        )
    return (
        '<div class="type-filter-wrap">'
        '<p class="filter-label">Source type</p>'
        f'<div class="type-filter-grid" id="typeFilters" aria-label="Source type filters">{"".join(buttons)}</div>'
        "</div>"
    )


def _render_error(payload: dict[str, Any]) -> str:
    error = payload.get("error")
    if not error:
        return ""
    details = error.get("details") or {}
    detail_rows = "".join(
        f"<li><span>{_e(key)}</span><code>{_e(value)}</code></li>"
        for key, value in details.items()
    )
    return f"""
<section class="notice error-notice">
  <h2>Error</h2>
  <p><b>{_e(error.get("phase", "unknown"))}</b>: {_e(error.get("code", "unknown_error"))}</p>
  <p>{_e(error.get("message", ""))}</p>
  {f'<ul class="detail-list">{detail_rows}</ul>' if detail_rows else ''}
</section>
""".strip()


def _render_document_summary(payload: dict[str, Any]) -> str:
    phase1 = payload.get("phase1") or {}
    phase2 = payload.get("phase2") or {}
    report = phase1.get("report") or {}
    if not phase1 and not phase2:
        return ""
    timings_ms = payload.get("timings_ms") or {}
    total_ms = timings_ms.get("total") if isinstance(timings_ms, dict) else None
    if total_ms is None:
        total_ms = report.get("extraction_time_ms", 0.0)
    return f"""
<section class="summary-panel">
  <h2>Document Summary</h2>
  <div class="summary-grid">
    <div><span>Upload kind</span><b>{_e(phase1.get("upload_kind", "unknown"))}</b></div>
    <div><span>References detected</span><b>{_e(phase2.get("reference_count", 0))}</b></div>
    <div><span>Total generation time</span><b>{_format_seconds(total_ms)}</b></div>
  </div>
</section>
""".strip()


def _render_reference_list(references: list[dict[str, Any]]) -> str:
    if not references:
        return '<section class="empty-state"><h2>No references</h2><p>No reference summaries were available for this report.</p></section>'
    cards = "\n".join(_render_reference_card(row) for row in references)
    return f'<section class="reference-list" id="referenceList">{cards}<div class="empty-state hidden" id="emptyFiltered"><h2>No matches</h2><p>No references match the current filter.</p></div></section>'


def _render_reference_card(row: dict[str, Any]) -> str:
    status = str(row.get("final_status") or "unmatched")
    filter_status = _filter_status(status)
    source_type = _source_type_key(row.get("ctype"))
    haystack = " ".join(
        str(value)
        for value in (
            row.get("opaque_reference_id"),
            row.get("display_reference"),
            row.get("ctype"),
            row.get("phase4_status"),
            row.get("final_status"),
            row.get("final_confidence"),
            row.get("accepted_record_id"),
            row.get("runner_up_record_id"),
            row.get("accepted_match_display"),
            row.get("runner_up_match_display"),
            _render_text_for_search(row.get("accepted_match_render")),
            _render_text_for_search(row.get("runner_up_match_render")),
            " ".join(row.get("review_flags") or []),
        )
        if value is not None
    ).lower()
    return f"""
<article class="reference-card status-border-{_status_class(filter_status)}" data-status="{_e(filter_status)}" data-source-type="{_e(source_type)}" data-hay="{_e(haystack)}">
  <div class="card-main">
    <div>
      <p class="card-kicker">Reference {row["index"]}</p>
      <p class="source-reference">{_render_display_text(row.get("display_reference") or row.get("opaque_reference_id", "-"))}</p>
    </div>
    <div class="status-chip status-{_status_class(filter_status)}">{_label(filter_status)}</div>
  </div>
  <div class="card-grid">
    <div><span>Confidence</span><b>{_e(row.get("final_confidence", "none"))}</b></div>
    <div><span>Score</span><b>{_score(row.get("confidence_score"))}</b></div>
    <div><span>Type</span><b>{_e(row.get("ctype", "unknown"))}</b></div>
    <div><span>Phase 4</span><b>{_e(row.get("phase4_status", "not_run"))}</b></div>
  </div>
  <meter class="score-meter" min="0" max="1" value="{_score_value(row.get("confidence_score"))}" aria-label="Confidence score">{_score(row.get("confidence_score"))}</meter>
  {_render_next_steps(row, filter_status)}
  {_record_ids(row)}
  {_match_text_block(row)}
  {_source_vs_found_block(row)}
  {_flag_block(row)}
  <details>
    <summary>Evidence checks</summary>
    {_evidence_table(row.get("evidence_checks") or [])}
  </details>
  <details>
    <summary>Reference details</summary>
    {_detail_group("Missing fields", row.get("missing_fields_for_match") or [])}
    {_detail_group("Reasons", row.get("reasons") or [])}
  </details>
</article>
""".strip()


def _record_ids(row: dict[str, Any]) -> str:
    accepted = row.get("accepted_record_id")
    runner = row.get("runner_up_record_id")
    if not accepted and not runner:
        return ""
    return f"""
<div class="record-row">
  <span>Best candidate record: <code>{_e(accepted or "-")}</code></span>
  <span>Runner up: <code>{_e(runner or "-")}</code></span>
</div>
""".strip()


def _render_next_steps(row: dict[str, Any], filter_status: str) -> str:
    if filter_status != "needs_review":
        return ""
    source_type = _source_type_key(row.get("ctype"))
    guidance = {
        "journal_article": (
            "Check the article title, journal name, year, pages, and DOI. "
            "If key fields are missing or abbreviated, correct them before rerunning."
        ),
        "book": (
            "Check the book title, author or editor names, year, edition, and publisher. "
            "Books often need fuller title and publisher details to match cleanly."
        ),
        "book_chapter": (
            "Check both the chapter details and the parent book details: chapter title, book title, editors, page range, and publisher."
        ),
    }.get(
        source_type,
        "Check the title, author names, year, and source details. Add any missing fields before reviewing or rerunning this reference.",
    )
    return (
        '<section class="next-steps">'
        "<h3>Next steps</h3>"
        f"<p>{_e(guidance)}</p>"
        "</section>"
    )


def _match_text_block(row: dict[str, Any]) -> str:
    accepted = row.get("accepted_match_display")
    runner = row.get("runner_up_match_display")
    accepted_render = row.get("accepted_match_render")
    runner_render = row.get("runner_up_match_render")
    if not accepted and not runner and not accepted_render and not runner_render:
        return ""
    accepted_html = _match_citation_item("Best candidate", accepted_render, accepted)
    runner_html = _match_citation_item("Runner up", runner_render, runner)
    return f"""
<section class="match-evidence">
  {accepted_html}
  {runner_html}
</section>
""".strip()


def _match_citation_item(label: str, render: Any, fallback: Any) -> str:
    body = ""
    if isinstance(render, dict):
        rendered_html = str(render.get("html") or "").strip()
        rendered_text = str(render.get("text") or "").strip()
        if rendered_html:
            body = _sanitize_citation_html(rendered_html)
        elif rendered_text:
            body = f"<p>{_render_display_text(rendered_text)}</p>"
    if not body and fallback:
        body = f"<p>{_render_display_text(fallback)}</p>"
    if not body:
        return ""
    return f"<div><span>{_e(label)}</span>{body}</div>"


def _source_vs_found_block(row: dict[str, Any]) -> str:
    comparisons = row.get("field_comparisons") or []
    if comparisons:
        return _field_comparison_block(comparisons)
    return _field_comparison_block(_source_only_comparisons(row))


def _field_comparison_block(comparisons: list[dict[str, Any]]) -> str:
    if not comparisons:
        return ""
    rows = "\n".join(
        f"""
<tr>
  <td>{_e(item.get("label", ""))}</td>
  <td>{_render_display_text(item.get("source_value") or "-")}</td>
  <td>{_render_display_text(item.get("found_value") or "-")}</td>
  <td>{_score(item.get("score"))}</td>
  <td><span class="mini-status status-{_comparison_status_class(item.get("status"))}">{_e(item.get("status", ""))}</span></td>
</tr>
""".strip()
        for item in comparisons
    )
    return f"""
<details open class="field-comparison">
  <summary>Source vs found</summary>
  <div class="table-wrap"><table><thead><tr><th>Field</th><th>Source</th><th>Found</th><th>Score</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>
</details>
""".strip()


def _source_only_comparisons(row: dict[str, Any]) -> list[dict[str, Any]]:
    fields = row.get("parsed_fields") or {}
    if not isinstance(fields, dict):
        return []
    container_label = "Journal"
    source_type = _source_type_key(row.get("ctype"))
    if source_type == "book":
        container_label = "Publisher"
    elif source_type == "book_chapter":
        container_label = "Book"

    rows: list[dict[str, Any]] = []

    def add(label: str, value: Any) -> None:
        if str(value or "").strip():
            rows.append(
                {
                    "label": label,
                    "source_value": value,
                    "found_value": "",
                    "score": None,
                    "status": "n/a",
                }
            )

    add("Title", fields.get("Title"))
    add("Authors", fields.get("Authors") or fields.get("Editors"))
    add("Year", fields.get("Year") or fields.get("Date"))
    add(
        container_label,
        fields.get("Container") or fields.get("Publisher") or fields.get("Institution") or fields.get("Organization"),
    )
    add("DOI", fields.get("DOI"))
    add("Metadata", _metadata_source_text(fields))
    return rows


def _metadata_source_text(fields: dict[str, Any]) -> str:
    parts = []
    for label, key in (("vol", "Volume"), ("issue", "Issue"), ("pages", "Pages")):
        value = str(fields.get(key) or "").strip()
        if value:
            parts.append(f"{label}: {value}")
    return "; ".join(parts)


def _render_text_for_search(render: Any) -> str:
    if not isinstance(render, dict):
        return ""
    return str(render.get("text") or "")


def _flag_block(row: dict[str, Any]) -> str:
    flags = row.get("review_flags") or []
    if not flags:
        return ""
    return f'<div class="flag-row">{_pill_list(flags, "flag")}</div>'


def _evidence_table(checks: list[dict[str, Any]]) -> str:
    if not checks:
        return '<p class="empty-inline">No evidence checks were emitted.</p>'
    rows = "\n".join(
        f"""
<tr>
  <td>{_e(check.get("label") or check.get("code", ""))}</td>
  <td><span class="mini-status status-{_status_class(check.get("status", ""))}">{_e(check.get("status", ""))}</span></td>
  <td>{_e(check.get("summary", ""))}</td>
</tr>
""".strip()
        for check in checks
    )
    return f'<div class="table-wrap"><table><thead><tr><th>Check</th><th>Status</th><th>Summary</th></tr></thead><tbody>{rows}</tbody></table></div>'


def _detail_group(label: str, values: list[str]) -> str:
    content = _pill_list(values, "neutral") if values else '<span class="empty-inline">None</span>'
    return f'<div class="detail-group"><h3>{_e(label)}</h3>{content}</div>'


def _pill_list(values: list[Any], kind: str) -> str:
    return "".join(f'<span class="pill {kind}">{_e(value)}</span>' for value in values)


def _score(value: Any) -> str:
    if value is None:
        return "-"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _e(value)


def _format_seconds(milliseconds: Any) -> str:
    try:
        seconds = float(milliseconds) / 1000.0
    except (TypeError, ValueError):
        seconds = 0.0
    return f"{seconds:.1f} s"


def _report_style_label(payload: dict[str, Any]) -> str:
    resolved = str(payload.get("style_hint") or "unknown")
    requested = str(payload.get("requested_style_hint") or resolved or "unknown")
    resolved_label = _display_style_label(resolved)
    if requested == "unknown" and resolved != "unknown":
        return f"Auto: identified {resolved_label}"
    if requested == "unknown":
        return "Auto"
    return _display_style_label(requested)


def _display_style_label(value: Any) -> str:
    return profile_for_reference_style(str(value or "unknown")).label


def _display_label(value: Any) -> str:
    text = str(value or "unknown").strip().replace("_", " ").replace("-", " ")
    if not text:
        text = "unknown"
    return " ".join(word[:1].upper() + word[1:] for word in text.split())


def _source_type_key(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace("-", "_")
    return text or "unknown"


def _source_type_label(value: Any) -> str:
    normalized = _source_type_key(value)
    labels = {
        "book_chapter": "Book chapter",
        "journal_article": "Journal article",
    }
    return labels.get(normalized, _display_label(normalized))


def _score_value(value: Any) -> str:
    try:
        return f"{max(0.0, min(1.0, float(value))):.2f}"
    except (TypeError, ValueError):
        return "0"


def _render_display_text(value: Any) -> str:
    text = str(value)
    pieces: list[str] = []
    cursor = 0
    for match in _URL_TEXT_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            pieces.append(_render_doi_text(text[cursor:start]))
        matched_url = match.group(0)
        url_text, href, trailing = _strip_url_trailing_punctuation(matched_url)
        pieces.append(
            f'<a href="{_e(href)}" target="_blank" rel="noopener noreferrer">{_e(url_text)}</a>'
        )
        if trailing:
            pieces.append(_e(trailing))
        cursor = end
    if cursor < len(text):
        pieces.append(_render_doi_text(text[cursor:]))
    return "".join(pieces)


def _render_doi_text(text: str) -> str:
    parts: list[str] = []
    cursor = 0
    for match in _DOI_TEXT_RE.finditer(text):
        start, end = match.span()
        if start > cursor:
            parts.append(_e(text[cursor:start]))
        matched_text = match.group(0)
        doi_value = (
            match.group("doi_from_url")
            or match.group("doi_from_label")
            or match.group("bare_doi")
            or ""
        )
        stripped_text, stripped_doi, trailing = _strip_doi_trailing_punctuation(
            matched_text,
            doi_value,
        )
        href = f"https://doi.org/{stripped_doi}"
        parts.append(
            f'<a href="{_e(href)}" target="_blank" rel="noopener noreferrer">{_e(stripped_text)}</a>'
        )
        if trailing:
            parts.append(_e(trailing))
        cursor = end
    if cursor < len(text):
        parts.append(_e(text[cursor:]))
    return "".join(parts)


def _strip_url_trailing_punctuation(url: str) -> tuple[str, str, str]:
    trailing = ""
    while url and url[-1] in ".,;:)":
        trailing = url[-1] + trailing
        url = url[:-1]
    return url, url, trailing


def _strip_doi_trailing_punctuation(text: str, doi: str) -> tuple[str, str, str]:
    trailing = ""
    while doi and doi[-1] in ".,;:)":
        trailing = doi[-1] + trailing
        doi = doi[:-1]
    if trailing and text.endswith(trailing):
        text = text[: -len(trailing)]
    return text, doi, trailing


class _CitationHtmlSanitizer(HTMLParser):
    allowed_tags = {"a", "b", "div", "em", "i", "p", "span", "strong"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._discard_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._discard_depth += 1
            return
        if self._discard_depth:
            return
        if tag not in self.allowed_tags:
            return
        rendered_attrs = ""
        if tag == "a":
            href = ""
            for name, value in attrs:
                if name == "href" and value:
                    href = value.strip()
            if href.startswith(("https://doi.org/", "https://", "http://")):
                rendered_attrs = (
                    f' href="{_e(href)}" target="_blank" rel="noopener noreferrer"'
                )
        self.parts.append(f"<{tag}{rendered_attrs}>")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._discard_depth:
            self._discard_depth -= 1
            return
        if self._discard_depth:
            return
        if tag in self.allowed_tags:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._discard_depth:
            return
        self.parts.append(_e(data))

    def handle_entityref(self, name: str) -> None:
        if self._discard_depth:
            return
        self.parts.append(f"&{_e(name)};")

    def handle_charref(self, name: str) -> None:
        if self._discard_depth:
            return
        self.parts.append(f"&#{_e(name)};")


def _sanitize_citation_html(value: str) -> str:
    parser = _CitationHtmlSanitizer()
    parser.feed(value)
    parser.close()
    rendered = "".join(parser.parts).strip()
    return rendered or "<p></p>"


def _status_class(status: Any) -> str:
    normalized = str(status or "unknown").strip().lower()
    return _STATUS_CLASS_MAP.get(normalized, "unknown")


def _comparison_status_class(status: Any) -> str:
    normalized = str(status or "unknown").strip().lower()
    if normalized == "match":
        return "verified"
    if normalized == "partial":
        return "partial"
    if normalized == "found":
        return "partial"
    if normalized == "missing":
        return "needs-review"
    if normalized == "mismatch":
        return "suspicious"
    return "unknown"


def _filter_status(status: Any) -> str:
    normalized = str(status or "needs_review").strip().lower().replace("-", "_")
    if normalized == "verified":
        return "verified"
    if normalized == "suspicious":
        return "suspicious"
    return "needs_review"


def _label(value: Any) -> str:
    text = str(value or "unknown").replace("_", " ")
    return _e(text[:1].upper() + text[1:])


def _e(value: Any) -> str:
    return escape(str(value), quote=True)


_CSS = """
:root {
  color-scheme: light;
  --bg: #edf2fa;
  --surface: #ffffff;
  --surface-soft: #f5f8fc;
  --border: #d6deeb;
  --text: #192433;
  --muted: #617089;
  --accent: #234d8f;
  --verified: #18834f;
  --verified-bg: #ecf7ee;
  --partial: #445f96;
  --partial-bg: #edf2ff;
  --review: #a45b06;
  --review-bg: #fff4df;
  --suspicious: #b42318;
  --suspicious-bg: #fff1ed;
  --skipped: #667085;
  --skipped-bg: #f2f4f7;
  --error: #8f1d56;
  --error-bg: #fdebf4;
  --shadow: 0 16px 42px rgba(16, 31, 52, 0.08);
  font-family: "Merriweather Sans", "Avenir Next", "Segoe UI", "Fira Sans", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, sans-serif;
}

* { box-sizing: border-box; }
body { margin: 0; background:
  radial-gradient(1000px 380px at 0% -12%, rgba(35, 77, 143, 0.14), transparent 35%),
  radial-gradient(860px 360px at 100% 0%, rgba(24, 131, 79, 0.12), transparent 32%),
  var(--bg); color: var(--text); font-size: 14px; line-height: 1.52; }

.shell { max-width: 1180px; margin: 0 auto; padding: 26px; }
.report-header, .summary-panel, .reference-card, .notice, .empty-state { background: var(--surface); border: 1px solid var(--border); border-radius: 14px; }
.report-header { position: sticky; top: 0; z-index: 2; padding: 18px 18px 16px; box-shadow: var(--shadow); background: linear-gradient(180deg, #fff 0%, #fcfdff 100%); }

.title-row, .card-main { display: flex; align-items: flex-start; justify-content: space-between; gap: 18px; }
h1, h2, h3, p { margin-top: 0; }
h1 { margin-bottom: 6px; font-size: 32px; line-height: 1.1; letter-spacing: -0.012em; }
h2 { font-size: 19px; margin-bottom: 10px; }
h3 { font-size: 12px; margin-bottom: 8px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.05em; }

.eyebrow, .card-kicker { margin: 0 0 5px; color: var(--muted); text-transform: uppercase; font-size: 11px; font-weight: 700; letter-spacing: 0.07em; }
.meta { margin: 0; color: var(--muted); }
.mono, code { font-family: "IBM Plex Mono", ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; }

.stats-grid, .summary-grid, .card-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
.stats-grid { margin-top: 12px; }
.header-controls { margin-top: 16px; display: grid; gap: 12px; grid-template-columns: minmax(0, 1.1fr) auto; align-items: end; }

.stat,
.summary-grid div,
.card-grid div,
.type-chip,
.view-toggle {
  padding: 10px 12px;
  border-radius: 10px;
  border: 1px solid #e6ecf5;
  background: var(--surface-soft);
  color: inherit;
}

.stat { cursor: pointer; font: inherit; text-align: left; }
.stat:hover { border-color: #8f9cb0; background: #edf2f9; }
.stat.active {
  border-color: var(--accent);
  background: linear-gradient(180deg, #f8faff 0%, #eef3ff 100%);
  box-shadow: 0 0 0 1px rgba(35, 77, 143, 0.12) inset;
}
.stat span { display: block; font-size: 20px; font-weight: 800; }
.stat small, .summary-grid span, .card-grid span { display: block; color: var(--muted); font-size: 12px; }
.stat.good span { color: var(--verified); }
.stat.warn span { color: var(--review); }
.stat.bad span { color: var(--suspicious); }

.search-label span { display: block; margin-bottom: 6px; color: var(--muted); font-size: 12px; font-weight: 700; }
input[type="search"] { width: 100%; border: 1px solid var(--border); border-radius: 10px; padding: 10px; font: inherit; color: var(--text); background: #fff; }

.view-controls { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
.view-toggle, .type-chip { cursor: pointer; color: inherit; font: inherit; }
.view-toggle.active, .type-chip.active {
  border-color: var(--accent);
  background: var(--accent);
  color: #fff;
  box-shadow: 0 8px 24px rgba(35, 77, 143, 0.2);
}

.type-filter-wrap { margin-top: 12px; }
.filter-label { margin: 0 0 8px; color: var(--muted); font-size: 12px; font-weight: 700; text-transform: uppercase; letter-spacing: .04em; }
.type-filter-grid { display: flex; flex-wrap: wrap; gap: 8px; }

.flag-row, .warnings-row, .record-row { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.pill, .status-chip, .mini-status { border: 1px solid var(--border); border-radius: 999px; background: #fff; color: var(--muted); }
.status-chip {
  padding: 6px 10px;
  font-size: 12px;
  font-weight: 800;
  white-space: nowrap;
  border-width: 1px;
}

.status-verified { color: var(--verified); background: var(--verified-bg); border-color: #afe3c3; }
.status-partial { color: var(--partial); background: var(--partial-bg); border-color: #bfd0f5; }
.status-needs-review { color: var(--review); background: var(--review-bg); border-color: #f7c67a; }
.status-suspicious { color: var(--suspicious); background: var(--suspicious-bg); border-color: #f5b9ae; }
.status-skipped { color: var(--skipped); background: var(--skipped-bg); border-color: #d0d5dd; }
.status-error { color: var(--error); background: var(--error-bg); border-color: #f6b8d5; }
.status-unknown { color: var(--muted); background: #f7f8fa; border-color: #d0d5dd; }

.summary-panel, .notice { padding: 16px; margin-bottom: 16px; }
.warnings-row { margin-top: 12px; }
.warnings-row > span { color: var(--muted); font-weight: 700; }

.reference-list { display: grid; gap: 12px; }
.reference-card { padding: 16px; border-left-width: 5px; background: #fcfdff; }
.status-border-verified { border-left-color: var(--verified); }
.status-border-needs-review { border-left-color: var(--review); }
.status-border-suspicious { border-left-color: var(--suspicious); }
.status-border-skipped { border-left-color: var(--skipped); }
.status-border-error { border-left-color: var(--error); }
.status-border-unknown { border-left-color: #9aa7bb; }

.source-reference { margin: 0; font-size: 13px; line-height: 1.5; overflow-wrap: anywhere; }
.source-reference a, .text-evidence a, .match-evidence a { color: #2859be; text-decoration-thickness: 1px; text-underline-offset: 2px; }
.card-grid { margin-top: 12px; }
.score-meter { display: block; margin: 12px 0; }
.record-row { margin-bottom: 10px; color: var(--muted); }
.record-row span { background: #f7f8fa; border: 1px solid #e7ebf0; padding: 6px 8px; border-radius: 8px; }

.progress { display: grid; grid-template-columns: repeat(auto-fit, minmax(120px, 1fr)); gap: 8px; margin-top: 14px; }
.progress-meter, .score-meter { width: 100%; height: 10px; border: 0; border-radius: 999px; background: #eceff3; overflow: hidden; }
.progress-meter::-webkit-meter-bar,
.score-meter::-webkit-meter-bar { background: #eceff3; border: 0; border-radius: 999px; }
.progress-meter.status-verified::-webkit-meter-optimum-value,
.score-meter::-webkit-meter-optimum-value { background: var(--verified); }
.progress-meter.status-needs-review::-webkit-meter-optimum-value { background: var(--review); }
.progress-meter.status-suspicious::-webkit-meter-optimum-value { background: var(--suspicious); }
.progress-meter::-moz-meter-bar,
.score-meter::-moz-meter-bar { background: var(--verified); border-radius: 999px; }

.next-steps {
  margin: 10px 0 12px;
  padding: 12px;
  border: 1px solid #f4c47d;
  border-radius: 8px;
  background: linear-gradient(180deg, #fff8ed 0%, #fff3df 100%);
}
.next-steps h3 { margin-bottom: 6px; color: var(--review); }
.next-steps p { margin: 0; color: #7a4b11; }

.text-evidence, .match-evidence {
  margin: 10px 0; padding: 10px 12px; border: 1px solid #e7ebf0; border-radius: 8px; background: #fcfcfd;
}
.text-evidence h3 { margin-bottom: 6px; }
.text-evidence p, .match-evidence p { margin: 0; overflow-wrap: anywhere; }
.match-evidence { display: grid; gap: 10px; }
.match-evidence span { display: block; margin-bottom: 4px; color: var(--muted); font-size: 12px; font-weight: 750; }

.pill { display: inline-flex; padding: 5px 8px; font-size: 12px; margin: 0 6px 6px 0; max-width: 100%; overflow-wrap: anywhere; }
.pill.flag { color: var(--review); background: var(--review-bg); border-color: #f4c47d; }
.pill.warning { color: var(--review); background: var(--review-bg); border-color: #f4c47d; }
.pill.neutral { color: var(--muted); background: #f7f8fa; }

.empty-inline { color: var(--muted); font-style: italic; }
details { margin-top: 10px; border: 1px solid #e7ebf0; border-radius: 8px; overflow: hidden; }
summary { cursor: pointer; padding: 10px 12px; background: #f8fafc; font-weight: 700; color: #344054; }
.detail-group, .table-wrap { padding: 12px; }
table { width: 100%; border-collapse: collapse; font-size: 13px; }
th, td { border-top: 1px solid #e7ebf0; padding: 8px; text-align: left; vertical-align: top; }
th { color: var(--muted); font-size: 12px; }

.mini-status { padding: 3px 7px; font-size: 12px; font-weight: 700; }
.empty-state { padding: 24px; text-align: center; color: var(--muted); }
.hidden { display: none; }

@media (max-width: 760px) {
  .shell { padding: 12px; }
  .report-header { position: static; }
  .title-row, .card-main { flex-direction: column; }
  .header-controls { grid-template-columns: 1fr; }
  .view-controls { justify-content: flex-start; }
}

@media print {
  body { background: #fff; }
  .shell { max-width: none; padding: 0; }
  .header-controls, .type-filter-wrap, script { display: none; }
  .report-header { position: static; box-shadow: none; }
  .reference-card, .summary-panel, .notice { break-inside: avoid; }
  .match-evidence, .field-comparison, details, .record-row, .flag-row, .score-meter { display: none; }
}

.view-basic .match-evidence,
.view-basic .field-comparison,
.view-basic details,
.view-basic .record-row,
.view-basic .flag-row,
.view-basic .score-meter {
  display: none;
}
""".strip()

_JS = """
(() => {
  const cards = Array.from(document.querySelectorAll(".reference-card"));
  const empty = document.getElementById("emptyFiltered");
  const search = document.getElementById("reportSearch");
  const statusButtons = Array.from(document.querySelectorAll("[data-filter]"));
  const typeButtons = Array.from(document.querySelectorAll("[data-type-filter]"));
  const viewButtons = Array.from(document.querySelectorAll("[data-view]"));
  const saveButton = document.getElementById("saveReportButton");
  const state = { status: "all", type: "all", query: "" };

  function setActive(buttons, activeButton) {
    buttons.forEach((button) => {
      const active = button === activeButton;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    });
  }

  function cardMatches(card) {
    const status = card.dataset.status || "";
    const sourceType = card.dataset.sourceType || "";
    const haystack = card.dataset.hay || "";
    if (state.status !== "all" && status !== state.status) {
      return false;
    }
    if (state.type !== "all" && sourceType !== state.type) {
      return false;
    }
    if (state.query && !haystack.includes(state.query)) {
      return false;
    }
    return true;
  }

  function applyFilters() {
    let visible = 0;
    cards.forEach((card) => {
      const matches = cardMatches(card);
      card.classList.toggle("hidden", !matches);
      if (matches) {
        visible += 1;
      }
    });
    if (empty) {
      empty.classList.toggle("hidden", visible !== 0);
    }
  }

  function reportFilename() {
    const title = document.title || "reference-report";
    const safe = title
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 72);
    const date = new Date().toISOString().slice(0, 19).replace(/[-:T]/g, "");
    return `${safe || "reference-report"}-${date}.html`;
  }

  function saveReport() {
    const html = `<!doctype html>\n${document.documentElement.outerHTML}`;
    const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = reportFilename();
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  statusButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.status = button.dataset.filter || "all";
      setActive(statusButtons, button);
      applyFilters();
    });
  });

  typeButtons.forEach((button) => {
    button.addEventListener("click", () => {
      state.type = button.dataset.typeFilter || "all";
      setActive(typeButtons, button);
      applyFilters();
    });
  });

  if (search) {
    search.addEventListener("input", () => {
      state.query = (search.value || "").trim().toLowerCase();
      applyFilters();
    });
  }

  viewButtons.forEach((button) => {
    button.addEventListener("click", () => {
      const view = button.dataset.view || "full";
      document.body.classList.toggle("view-basic", view === "basic");
      setActive(viewButtons, button);
    });
  });

  if (saveButton) {
    saveButton.addEventListener("click", saveReport);
  }
})();
""".strip()
