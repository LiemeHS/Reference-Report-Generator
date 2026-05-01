from __future__ import annotations

from html import escape
from html.parser import HTMLParser
import logging
from pathlib import Path
import re
from typing import Any

from reference_gen2.citation_rendering.models import CitationRenderResult
from reference_gen2.reference_matching.models import LocalDbCandidate

logger = logging.getLogger(__name__)

DEFAULT_STYLE = "apa-standard"
DEFAULT_LOCALE = "nl-NL"

_STYLES_DIR = Path(__file__).resolve().parent / "styles"
_STYLE_ALIASES = {
    DEFAULT_STYLE: _STYLES_DIR / "apa-custom.csl",
    "apa7_nl": _STYLES_DIR / "apa-custom.csl",
    "apa7_en": _STYLES_DIR / "apa-custom.csl",
}
_ARTICLE_TABLES = {"search_journal", "search_conference"}
_BOOK_TABLES = {"search_book"}
_CHAPTER_TABLES = {"search_book_chapter"}
_YEAR_RE = re.compile(r"\b(\d{4})\b")
_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)


def render_candidate_citation(
    candidate: LocalDbCandidate | None,
    *,
    reference_ctype: str | None = None,
    style: str = DEFAULT_STYLE,
    locale: str = DEFAULT_LOCALE,
) -> CitationRenderResult | None:
    """Render one matched DB candidate as sanitized Dutch APA report text.

    This Phase 6A boundary consumes candidate summary fields only. It never uses
    submitted-reference parsed author names or initials.
    """

    if candidate is None:
        return None

    csl_json = candidate_to_csl_json(
        candidate,
        reference_ctype=reference_ctype,
    )
    warnings = _warn_missing(csl_json)
    runtime_warnings: list[str] = []
    resolved_style = _resolve_style(style, runtime_warnings)

    try:
        if _is_no_contributor_book(csl_json):
            text, rendered_html = _render_no_contributor_book(csl_json)
        else:
            text, rendered_html = _render_with_citeproc(
                csl_json,
                style=str(resolved_style),
                locale=locale,
            )
    except Exception as exc:
        logger.info(
            "citation_rendering.citeproc_failed style=%s resolved_style=%s locale=%s err=%s",
            style,
            resolved_style,
            locale,
            exc,
        )
        runtime_warnings.append("citeproc renderer unavailable; using fallback formatter")
        text, rendered_html = _render_fallback(csl_json, style=style)

    text = _collapse_repeated_initial_periods(text)
    rendered_html = _collapse_repeated_initial_periods(rendered_html)
    text = _normalize_text(text)
    rendered_html = _sanitize_rendered_html(rendered_html or f"<p>{escape(text)}</p>")
    all_warnings = warnings + runtime_warnings
    return CitationRenderResult(
        text=text,
        html=rendered_html,
        style=style,
        locale=locale,
        warnings=all_warnings,
        partial=bool(warnings or runtime_warnings),
    )


def candidate_to_csl_json(
    candidate: LocalDbCandidate,
    *,
    reference_ctype: str | None = None,
) -> dict[str, Any]:
    csl_type = _candidate_csl_type(candidate, reference_ctype=reference_ctype)
    csl: dict[str, Any] = {
        "id": _safe_id(candidate.record_id),
        "type": csl_type,
    }

    title = _safe_text(candidate.title)
    if title:
        csl["title"] = title

    authors = _contributors_to_csl(candidate.authors, candidate.author_initials)
    if authors:
        csl["author"] = authors
    editors = _contributors_to_csl(candidate.editors, candidate.editor_initials)
    if editors:
        csl["editor"] = editors

    year = _safe_year(candidate.issued_year)
    if year:
        csl["issued"] = {"date-parts": [[int(year)]]}

    doi = _safe_doi(candidate.doi)
    if doi:
        csl["DOI"] = doi

    container = _safe_text(candidate.container_title)
    publisher = _safe_text(candidate.publisher)
    if csl_type == "book":
        if publisher:
            csl["publisher"] = publisher
    else:
        if container:
            csl["container-title"] = container
        if csl_type == "chapter" and publisher:
            csl["publisher"] = publisher

    for csl_key, value in (
        ("volume", candidate.volume),
        ("issue", candidate.issue),
        ("page", candidate.pages),
    ):
        clean = _safe_text(value)
        if clean:
            csl[csl_key] = clean

    return csl


def _candidate_csl_type(candidate: LocalDbCandidate, *, reference_ctype: str | None) -> str:
    table = (candidate.source_table or candidate.record_type or "").strip()
    if table in _BOOK_TABLES or reference_ctype == "book":
        return "book"
    if table in _CHAPTER_TABLES or reference_ctype == "book_chapter":
        return "chapter"
    if table in _ARTICLE_TABLES or reference_ctype == "journal_article":
        return "article-journal"
    return "article-journal"


def _contributors_to_csl(
    names: list[str],
    initials: list[str] | None = None,
) -> list[dict[str, str]]:
    clean_names = [_safe_text(name, max_chars=120) for name in names]
    clean_names = [name for name in clean_names if name]
    rendered: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, author in enumerate(clean_names):
        key = _author_key(author)
        if not key or key in seen:
            continue
        if _looks_like_aggregate_author_text(author, clean_names):
            continue
        seen.add(key)
        contributor = {"family": _title_case_author_name(author)}
        given = _safe_initials(initials[index] if initials and index < len(initials) else None)
        if given:
            contributor["given"] = given
        rendered.append(contributor)
    return rendered


def _warn_missing(csl_json: dict[str, Any]) -> list[str]:
    csl_type = str(csl_json.get("type") or "")
    warnings: list[str] = []
    if not csl_json.get("author"):
        warnings.append("Missing author/organization")
    if not str(csl_json.get("title") or "").strip():
        warnings.append("Missing title")
    if not csl_json.get("issued"):
        warnings.append("Missing year/date")
    if csl_type == "article-journal":
        if not str(csl_json.get("container-title") or "").strip():
            warnings.append("Missing journal title")
        if not (
            str(csl_json.get("volume") or "").strip()
            and str(csl_json.get("issue") or "").strip()
            and str(csl_json.get("page") or "").strip()
        ):
            warnings.append("Missing volume/issue/pages")
    elif csl_type == "book":
        if not str(csl_json.get("publisher") or "").strip():
            warnings.append("Missing publisher")
    elif csl_type == "chapter":
        if not str(csl_json.get("container-title") or "").strip():
            warnings.append("Missing book title")
        if not str(csl_json.get("publisher") or "").strip():
            warnings.append("Missing publisher")
    return warnings


def _resolve_style(style: str, warnings: list[str]) -> str | Path:
    requested = (style or "").strip() or DEFAULT_STYLE
    alias = _STYLE_ALIASES.get(requested)
    if alias is not None:
        if alias.exists():
            return alias
        warnings.append(f'Style "{requested}" is missing; falling back to apa')
        return "apa"
    return requested


def _render_with_citeproc(csl_json: dict[str, Any], *, style: str, locale: str) -> tuple[str, str]:
    from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle, formatter  # type: ignore
    from citeproc.source.json import CiteProcJSON  # type: ignore

    source = CiteProcJSON([csl_json])
    try:
        style_obj = CitationStylesStyle(style, validate=False, locale=locale)
    except TypeError:
        style_obj = CitationStylesStyle(style, validate=False)

    bibliography = CitationStylesBibliography(style_obj, source, formatter.html)
    bibliography.register(Citation([CitationItem(csl_json["id"])]))
    html_items = bibliography.bibliography()
    rendered_html = str(html_items[0]).strip() if html_items else ""

    bibliography_plain = CitationStylesBibliography(style_obj, source, formatter.plain)
    bibliography_plain.register(Citation([CitationItem(csl_json["id"])]))
    text_items = bibliography_plain.bibliography()
    text = str(text_items[0]).strip() if text_items else ""
    return text, rendered_html


def _render_fallback(csl_json: dict[str, Any], *, style: str = DEFAULT_STYLE) -> tuple[str, str]:
    normalized_style = (style or DEFAULT_STYLE).strip().lower()
    if _is_no_contributor_book(csl_json):
        return _render_no_contributor_book(csl_json)
    if normalized_style == "vancouver":
        return _render_vancouver_fallback(csl_json)
    if normalized_style == "mla":
        return _render_mla_fallback(csl_json)
    if normalized_style == "chicago":
        return _render_chicago_fallback(csl_json)
    if normalized_style == "harvard":
        return _render_harvard_fallback(csl_json)
    csl_type = str(csl_json.get("type") or "")
    authors = _fallback_authors(csl_json.get("author") or [])
    editors = _fallback_authors(csl_json.get("editor") or [])
    if not authors and editors:
        authors = f"{editors} (Eds.)"
    year = "n.d."
    try:
        year = str(csl_json["issued"]["date-parts"][0][0])
    except Exception:
        pass
    title = str(csl_json.get("title") or "[No title]").strip()
    container = str(csl_json.get("container-title") or "").strip()
    publisher = str(csl_json.get("publisher") or "").strip()
    volume = str(csl_json.get("volume") or "").strip()
    issue = str(csl_json.get("issue") or "").strip()
    page = str(csl_json.get("page") or "").strip()
    doi = str(csl_json.get("DOI") or "").strip()

    text = f"{authors} ({year}). {title}."
    rendered_html = f"{escape(authors)} ({escape(year)}). "
    if csl_type == "book":
        rendered_html += f"<i>{escape(title)}</i>."
        if publisher:
            text += f" {publisher}."
            rendered_html += f" {escape(publisher)}."
    elif csl_type == "chapter":
        rendered_html += f"{escape(title)}."
        if editors and container:
            text += f" In {editors} (Eds.), {container}."
            rendered_html += f" In {escape(editors)} (Eds.), <i>{escape(container)}</i>."
        elif container:
            text += f" In {container}."
            rendered_html += f" In <i>{escape(container)}</i>."
        if page:
            text += f" (pp. {page})."
            rendered_html += f" (pp. {escape(page)})."
        if publisher:
            text += f" {publisher}."
            rendered_html += f" {escape(publisher)}."
    else:
        rendered_html += f"{escape(title)}."
        if container:
            text += f" {container}"
            rendered_html += f" <i>{escape(container)}</i>"
        if volume:
            text += f", {volume}"
            rendered_html += f", <i>{escape(volume)}</i>"
        if issue:
            text += f"({issue})"
            rendered_html += f"({escape(issue)})"
        if page:
            text += f", {page}"
            rendered_html += f", {escape(page)}"
        if container or volume or issue or page:
            text += "."
            rendered_html += "."

    if doi:
        doi_url = f"https://doi.org/{doi}"
        text += f" {doi_url}"
        rendered_html += (
            f' <a href="{escape(doi_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{escape(doi_url)}</a>"
        )
    return _normalize_text(text), f"<p>{rendered_html}</p>"


def _is_no_contributor_book(csl_json: dict[str, Any]) -> bool:
    return (
        str(csl_json.get("type") or "") == "book"
        and not csl_json.get("author")
        and not csl_json.get("editor")
    )


def _render_no_contributor_book(csl_json: dict[str, Any]) -> tuple[str, str]:
    title = str(csl_json.get("title") or "[No title]").strip()
    year = "n.d."
    try:
        year = str(csl_json["issued"]["date-parts"][0][0])
    except Exception:
        pass
    publisher = str(csl_json.get("publisher") or "").strip()
    doi = str(csl_json.get("DOI") or "").strip()

    text = f"{title}. ({year})."
    rendered_html = f"<i>{escape(title)}</i>. ({escape(year)})."
    if publisher:
        text += f" {publisher}."
        rendered_html += f" {escape(publisher)}."
    if doi:
        doi_url = f"https://doi.org/{doi}"
        text += f" {doi_url}"
        rendered_html += (
            f' <a href="{escape(doi_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
            f"{escape(doi_url)}</a>"
        )
    return _normalize_text(text), rendered_html


def _render_vancouver_fallback(csl_json: dict[str, Any]) -> tuple[str, str]:
    authors = _strip_trailing_period(_fallback_authors(csl_json.get("author") or []))
    year = _fallback_year(csl_json)
    title = str(csl_json.get("title") or "[No title]").strip()
    container = str(csl_json.get("container-title") or "").strip()
    volume = str(csl_json.get("volume") or "").strip()
    issue = str(csl_json.get("issue") or "").strip()
    page = str(csl_json.get("page") or "").strip()
    doi = str(csl_json.get("DOI") or "").strip()
    source = container
    if year:
        source = f"{source}. {year}" if source else year
    if volume:
        source += f";{volume}"
    if issue:
        source += f"({issue})"
    if page:
        source += f":{page}"
    text = _normalize_text(f"{authors}. {title}. {source}.")
    html = f"{escape(authors)}. {escape(title)}. {escape(source)}."
    if doi:
        text, html = _append_doi(text, html, doi)
    return text, f"<p>{html}</p>"


def _render_mla_fallback(csl_json: dict[str, Any]) -> tuple[str, str]:
    authors = _strip_trailing_period(_fallback_authors(csl_json.get("author") or []))
    title = str(csl_json.get("title") or "[No title]").strip()
    container = str(csl_json.get("container-title") or "").strip()
    year = _fallback_year(csl_json)
    publisher = str(csl_json.get("publisher") or "").strip()
    page = str(csl_json.get("page") or "").strip()
    parts = [f'{authors}. "{title}."']
    if container:
        parts.append(f"{container},")
    if publisher:
        parts.append(f"{publisher},")
    if year:
        parts.append(f"{year},")
    if page:
        parts.append(f"pp. {page}.")
    text = _normalize_text(" ".join(parts).rstrip(",") + ".")
    html = escape(text)
    return text, f"<p>{html}</p>"


def _render_chicago_fallback(csl_json: dict[str, Any]) -> tuple[str, str]:
    authors = _strip_trailing_period(_fallback_authors(csl_json.get("author") or []))
    year = _fallback_year(csl_json) or "n.d."
    title = str(csl_json.get("title") or "[No title]").strip()
    container = str(csl_json.get("container-title") or "").strip()
    publisher = str(csl_json.get("publisher") or "").strip()
    tail = container or publisher
    text = _normalize_text(f'{authors}. {year}. "{title}." {tail}.')
    return text, f"<p>{escape(text)}</p>"


def _render_harvard_fallback(csl_json: dict[str, Any]) -> tuple[str, str]:
    authors = _strip_trailing_period(_fallback_authors(csl_json.get("author") or []))
    year = _fallback_year(csl_json) or "n.d."
    title = str(csl_json.get("title") or "[No title]").strip()
    container = str(csl_json.get("container-title") or "").strip()
    publisher = str(csl_json.get("publisher") or "").strip()
    tail = container or publisher
    text = _normalize_text(f"{authors} ({year}) {title}. {tail}.")
    return text, f"<p>{escape(text)}</p>"


def _fallback_year(csl_json: dict[str, Any]) -> str:
    try:
        return str(csl_json["issued"]["date-parts"][0][0])
    except Exception:
        return ""


def _append_doi(text: str, html: str, doi: str) -> tuple[str, str]:
    doi_url = f"https://doi.org/{doi}"
    text = f"{text} {doi_url}"
    html = (
        f'{html} <a href="{escape(doi_url, quote=True)}" target="_blank" rel="noopener noreferrer">'
        f"{escape(doi_url)}</a>"
    )
    return _normalize_text(text), html


def _strip_trailing_period(value: str) -> str:
    return value.rstrip().rstrip(".")


def _fallback_authors(authors: list[dict[str, str]]) -> str:
    names = []
    for author in authors:
        family = str(author.get("family") or "").strip()
        given = str(author.get("given") or "").strip()
        literal = str(author.get("literal") or "").strip()
        if literal:
            names.append(literal)
        elif family and given:
            names.append(f"{family}, {given}")
        elif family:
            names.append(family)
    return ", ".join(names) if names else "[Author missing]"


class _RenderedHtmlSanitizer(HTMLParser):
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
        safe_attrs = ""
        if tag == "a":
            href = ""
            for name, value in attrs:
                if name == "href" and value:
                    href = value.strip()
            if href.startswith(("https://doi.org/", "https://", "http://")):
                safe_attrs = (
                    f' href="{escape(href, quote=True)}"'
                    ' target="_blank" rel="noopener noreferrer"'
                )
        self.parts.append(f"<{tag}{safe_attrs}>")

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
        self.parts.append(escape(data))

    def handle_entityref(self, name: str) -> None:
        if self._discard_depth:
            return
        self.parts.append(f"&{escape(name)};")

    def handle_charref(self, name: str) -> None:
        if self._discard_depth:
            return
        self.parts.append(f"&#{escape(name)};")


def _sanitize_rendered_html(value: str) -> str:
    parser = _RenderedHtmlSanitizer()
    parser.feed(value)
    parser.close()
    rendered = "".join(parser.parts).strip()
    return rendered or "<p></p>"


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").replace("\x00", "").split()).strip()


def _safe_text(value: Any, *, max_chars: int = 500) -> str | None:
    text = _normalize_text(str(value or ""))
    if not text:
        return None
    if len(text) > max_chars:
        return text[: max_chars - 1].rstrip() + "..."
    return text


def _safe_initials(value: Any) -> str | None:
    text = _safe_text(value, max_chars=40)
    return text or None


def _collapse_repeated_initial_periods(value: str) -> str:
    return re.sub(r"(?<=[A-Za-z])\.\.(?=(?:\s|[),<&]))", ".", str(value or ""))


def _safe_year(value: Any) -> str | None:
    match = _YEAR_RE.search(str(value or ""))
    return match.group(1) if match else None


def _safe_doi(value: Any) -> str | None:
    text = _safe_text(value, max_chars=180)
    if not text:
        return None
    return _DOI_PREFIX_RE.sub("", text).strip().strip(".,;")


def _safe_id(value: Any) -> str:
    text = str(value or "candidate").strip() or "candidate"
    return re.sub(r"[^A-Za-z0-9_.:-]+", "_", text)[:160]


def _looks_like_aggregate_author_text(author: str, authors: list[str]) -> bool:
    normalized = _author_key(author)
    if len(normalized.split()) < 2:
        return False
    contained_names = 0
    for other in authors:
        other_normalized = _author_key(other)
        if not other_normalized or other_normalized == normalized:
            continue
        if other_normalized in normalized:
            contained_names += 1
    return contained_names >= 2


def _author_key(author: str) -> str:
    return " ".join(
        "".join(character.lower() if character.isalnum() else " " for character in author).split()
    )


def _title_case_author_name(author: str) -> str:
    particles = {
        "al",
        "bij",
        "da",
        "de",
        "del",
        "den",
        "der",
        "di",
        "du",
        "el",
        "la",
        "le",
        "ten",
        "ter",
        "van",
        "von",
    }
    words: list[str] = []
    for index, word in enumerate(author.split()):
        lower = word.casefold()
        if index > 0 and lower in particles:
            words.append(lower)
            continue
        words.append("-".join(part[:1].upper() + part[1:].lower() for part in word.split("-")))
    return " ".join(words)
