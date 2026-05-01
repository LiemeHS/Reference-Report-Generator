from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict

from reference_gen2.reference_matching import (
    Phase4RuntimeConfig,
    SqliteLocalDbProvider,
    match_reference,
)
from reference_gen2.reference_parsing.models import (
    MatchPreparation,
    ParsedName,
    ParsedReferenceData,
    ParsedReferenceResult,
)


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a lightweight read-only Phase 4 probe against a local DB.",
    )
    parser.add_argument("--db-path", required=True, help="Path to the SQLite DB.")
    parser.add_argument(
        "--ctype",
        required=True,
        choices=["journal_article", "book", "book_chapter"],
        help="Phase 4 ctype to probe.",
    )
    parser.add_argument("--doi", default="", help="DOI to test.")
    parser.add_argument("--title", default="", help="Title or chapter title.")
    parser.add_argument(
        "--container-title",
        default="",
        help="Journal title or book title.",
    )
    parser.add_argument("--author", action="append", default=[], help="Author name. Repeatable.")
    parser.add_argument("--editor", action="append", default=[], help="Editor name. Repeatable.")
    parser.add_argument("--year", default="", help="Issued year.")
    parser.add_argument("--pages", default="", help="Pages string.")
    parser.add_argument("--volume", default="", help="Volume string.")
    parser.add_argument("--issue", default="", help="Issue string.")
    parser.add_argument(
        "--provider-only",
        action="store_true",
        help="Only run the direct provider DOI probe and skip full Phase 4 service execution.",
    )
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=5,
        help="Maximum candidate count to retain.",
    )
    return parser


def main() -> int:
    args = _build_arg_parser().parse_args()
    provider = SqliteLocalDbProvider(args.db_path)

    if args.doi:
        started = time.perf_counter()
        try:
            provider_candidates = provider.lookup_by_doi(
                ctype=args.ctype,  # type: ignore[arg-type]
                doi=args.doi,
                max_candidates=args.max_candidates,
            )
        except Exception as exc:
            print(f"Provider DOI probe failed: {type(exc).__name__}: {exc}")
            return 1
        provider_ms = round((time.perf_counter() - started) * 1000, 2)
        print("Direct provider DOI probe")
        print(f"  db_path: {args.db_path}")
        print(f"  ctype: {args.ctype}")
        print(f"  doi: {args.doi}")
        print(f"  elapsed_ms: {provider_ms}")
        print(f"  candidate_count: {len(provider_candidates)}")
        for index, candidate in enumerate(provider_candidates, start=1):
            print(
                f"  [{index}] record_id={candidate.record_id} title={candidate.title!r} doi={candidate.doi!r}"
            )
        print()

    if args.provider_only:
        return 0

    parsed_result = _build_parsed_result(args)
    started = time.perf_counter()
    try:
        result = match_reference(
            parsed_result,
            config=Phase4RuntimeConfig(
                local_db_path=args.db_path,
                max_candidates=args.max_candidates,
            ),
        )
    except Exception as exc:
        print(f"Phase 4 service probe failed: {type(exc).__name__}: {exc}")
        return 1
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)

    print("Full Phase 4 service probe")
    print(f"  elapsed_ms: {elapsed_ms}")
    print(f"  status: {result.status}")
    print(f"  strategy_used: {result.strategy_used}")
    print(f"  reasons: {result.reasons}")
    print(f"  warnings: {result.warnings}")
    print(f"  lookup_trace: {json.dumps(asdict(result.lookup_trace), ensure_ascii=False)}")
    if result.best_candidate is not None:
        print(
            "  best_candidate: "
            + json.dumps(asdict(result.best_candidate), ensure_ascii=False)
        )
    else:
        print("  best_candidate: null")
    print(f"  retained_candidates: {len(result.candidates)}")
    return 0


def _build_parsed_result(args: argparse.Namespace) -> ParsedReferenceResult:
    parsed = ParsedReferenceData(
        author=_names_from_args(args.author),
        editor=_names_from_args(args.editor),
        title=[args.title] if args.title else [],
        container_title=[args.container_title] if args.container_title else [],
        date=[args.year] if args.year else [],
        issued_year=args.year or None,
        pages=[args.pages] if args.pages else [],
        volume=[args.volume] if args.volume else [],
        issue=[args.issue] if args.issue else [],
        doi=[args.doi] if args.doi else [],
    )
    lookup_key_fields: dict[str, list[str]] = {}
    lookup_query_fields: dict[str, list[str]] = {}

    if args.ctype == "journal_article":
        lookup_key_fields = _drop_empty(
            {
                "doi": [args.doi] if args.doi else [],
                "title": [args.title] if args.title else [],
                "author": list(args.author),
                "issued_year": [args.year] if args.year else [],
                "container_title": [args.container_title] if args.container_title else [],
                "volume": [args.volume] if args.volume else [],
                "issue": [args.issue] if args.issue else [],
                "pages": [args.pages] if args.pages else [],
            }
        )
        lookup_query_fields = _drop_empty(
            {
                "title": [args.title] if args.title else [],
                "author": list(args.author),
                "container_title": [args.container_title] if args.container_title else [],
                "issued_year": [args.year] if args.year else [],
            }
        )
    elif args.ctype == "book":
        lookup_key_fields = _drop_empty(
            {
                "title": [args.title] if args.title else [],
                "author": list(args.author),
                "issued_year": [args.year] if args.year else [],
                "publisher": [args.container_title] if args.container_title else [],
                "identifier": [args.doi] if args.doi else [],
            }
        )
        lookup_query_fields = _drop_empty(
            {
                "title": [args.title] if args.title else [],
                "author": list(args.author),
                "issued_year": [args.year] if args.year else [],
                "publisher": [args.container_title] if args.container_title else [],
            }
        )
    elif args.ctype == "book_chapter":
        lookup_key_fields = _drop_empty(
            {
                "chapter_title": [args.title] if args.title else [],
                "book_title": [args.container_title] if args.container_title else [],
                "author": list(args.author),
                "editor": list(args.editor),
                "pages": [args.pages] if args.pages else [],
                "issued_year": [args.year] if args.year else [],
            }
        )
        lookup_query_fields = _drop_empty(
            {
                "chapter_title": [args.title] if args.title else [],
                "book_title": [args.container_title] if args.container_title else [],
                "author": list(args.author),
                "editor": list(args.editor),
                "issued_year": [args.year] if args.year else [],
            }
        )

    return ParsedReferenceResult(
        reference_id="ref_phase4_probe",
        raw_reference="phase4 probe",
        normalized_reference="phase4 probe",
        parsed_data=parsed,
        ctype=args.ctype,
        match_preparation=MatchPreparation(
            eligible_for_db_match=True,
            match_target="crossref" if args.ctype == "journal_article" else "openlibrary",
            lookup_key_fields=lookup_key_fields,
            lookup_query_fields=lookup_query_fields,
        ),
    )


def _names_from_args(values: list[str]) -> list[ParsedName]:
    names: list[ParsedName] = []
    for value in values:
        text = value.strip()
        if not text:
            continue
        if "," in text:
            family, given = [piece.strip() for piece in text.split(",", 1)]
            names.append(ParsedName(family=family or None, given=given or None))
        else:
            parts = text.split()
            if len(parts) >= 2:
                names.append(ParsedName(family=parts[-1], given=" ".join(parts[:-1])))
            else:
                names.append(ParsedName(literal=text))
    return names


def _drop_empty(fields: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        key: value
        for key, value in fields.items()
        if any(item.strip() for item in value if isinstance(item, str))
    }


if __name__ == "__main__":
    raise SystemExit(main())
