from __future__ import annotations

from pathlib import Path

import pytest

from scripts.run_phase123_batch import _real_anystyle_executable
from scripts.run_phase124_batch import process_document
from reference_gen2.reference_matching.provider import normalize_text


def test_pdftest4_vancouver_matches_known_localdb_sources(tmp_path):
    sample_path = Path("manual_tests/input/pdftest4_vancouver.pdf")
    db_path = Path("/data/db.sqlite")
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest4_vancouver.pdf")
    if not db_path.exists():
        pytest.skip("Missing local DB: /data/db.sqlite")
    if not _real_anystyle_executable():
        pytest.skip("AnyStyle executable is not available")

    payload = process_document(
        sample_path,
        tmp_path,
        db_path=str(db_path),
        style_hint="vancouver",
        relaxed=True,
    )

    phase3 = payload.get("phase3b") or payload.get("phase3") or []
    phase4 = payload.get("phase4") or []
    assert payload["status"] == "ok"
    assert len(phase3) >= 21
    assert len(phase4) == len(phase3)

    matched = [result for result in phase4 if result.get("status") == "matched_provisional"]
    assert len(matched) >= 13

    by_title = {}
    for parsed, match in zip(phase3, phase4):
        parsed_data = parsed.get("parsed_data") or {}
        title = normalize_text(" ".join(parsed_data.get("title") or []))
        by_title[title] = match

    _assert_best_doi(
        by_title,
        "instructions to authors for case reporting are limited",
        "10.1186/1472-6920-4-4",
    )
    _assert_best_doi(
        by_title,
        "authorship criteria for scientific papers",
        "10.1590/s1678-97412004000400002",
    )
    _assert_best_doi(
        by_title,
        "jama editor stresses authors need to disclose financial ties",
        "10.1136/bmj.333.7564.370-c",
    )


def _assert_best_doi(by_title: dict[str, dict], title_fragment: str, doi: str) -> None:
    match = next(
        (value for key, value in by_title.items() if normalize_text(title_fragment) in key),
        None,
    )
    assert match is not None, f"Missing parsed reference for {title_fragment!r}"
    assert match.get("status") == "matched_provisional"
    best = match.get("best_candidate") or {}
    assert best.get("doi") == doi
