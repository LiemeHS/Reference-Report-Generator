from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import build_docx_bytes

from scripts.run_phase123_batch import _real_anystyle_executable
from scripts.run_phase124_batch import main, process_document
from scripts.run_phase124_batch import _phase4_triage_summary


def _create_phase4_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE search_journal (
                id INTEGER PRIMARY KEY,
                title TEXT,
                year TEXT,
                doi TEXT,
                journal TEXT,
                volume TEXT,
                issue TEXT,
                pages TEXT,
                author_surnames_text TEXT,
                author_text TEXT
            );
            CREATE UNIQUE INDEX idx_search_journal_doi ON search_journal(doi);
            """
        )
        conn.execute(
            """
            INSERT INTO search_journal (
                id, title, year, doi, journal, volume, issue, pages, author_surnames_text, author_text
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                1,
                "Some title",
                "2020",
                "10.1234/test.article",
                "Journal Name",
                "5",
                "2",
                "10-20",
                "Smith",
                "smith",
            ),
        )
        conn.commit()
    finally:
        conn.close()


def test_process_document_writes_phase124_outputs(
    monkeypatch, local_tmp_dir: Path
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    db_path = local_tmp_dir / "phase4.db"
    _create_phase4_db(db_path)

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "sample.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    payload = process_document(
        input_path,
        output_dir,
        db_path=str(db_path),
        style_hint="apa7_nl",
    )

    json_path = output_dir / "sample.phase124.json"
    md_path = output_dir / "sample.phase124.md"
    quick_md_path = output_dir / "sample.phase124.quick.md"

    assert json_path.exists()
    assert md_path.exists()
    assert quick_md_path.exists()
    assert payload["status"] == "ok"
    assert payload["phase4"] is not None
    assert len(payload["phase4"]) >= 1
    assert payload["phase4"][0]["best_candidate"] is not None

    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["db_path"] == str(db_path)
    assert "phase4" in written
    assert "timings_ms" in written
    assert "phase4_timing_summary" in written
    assert "phase4_triage" in written
    assert written["timings_ms"]["phase1"] >= 0.0
    assert written["timings_ms"]["phase4"] >= 0.0
    assert written["phase4"][0]["status"] in {
        "matched_provisional",
        "candidate_only",
        "no_match",
    }
    assert "top_candidates" in written["phase4"][0]
    assert "timings_ms" in written["phase4_timing_summary"][0]
    assert written["phase4_triage"][0]["bucket"] in {
        "matched_for_phase5_review",
        "scoring_or_weak_evidence",
        "db_coverage_or_lookup_recall",
    }

    markdown = md_path.read_text(encoding="utf-8")
    assert "## Phase 4" in markdown
    assert "Best Candidate:" in markdown

    quick_markdown = quick_md_path.read_text(encoding="utf-8")
    assert "## Phase 4" in quick_markdown
    assert "- Phase 4 Status:" in quick_markdown


def test_main_accepts_single_input_file_for_phase124(
    monkeypatch, local_tmp_dir: Path
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    db_path = local_tmp_dir / "phase4.db"
    _create_phase4_db(db_path)

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "single.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    exit_code = main(
        [
            str(input_path),
            "--db-path",
            str(db_path),
            "--output-dir",
            str(output_dir),
            "--style-hint",
            "apa7_nl",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "single.phase124.json").exists()
    assert (output_dir / "single.phase124.md").exists()
    assert (output_dir / "single.phase124.quick.md").exists()


def test_process_document_progress_and_reference_scoping(
    monkeypatch, local_tmp_dir: Path, capsys
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    db_path = local_tmp_dir / "phase4.db"
    _create_phase4_db(db_path)

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "progress.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    payload = process_document(
        input_path,
        output_dir,
        db_path=str(db_path),
        style_hint="apa7_nl",
        progress=True,
        warm_db=True,
        max_references=1,
    )

    captured = capsys.readouterr()
    assert "Phase 1 start" in captured.out
    assert "Phase 4 warm-up start" in captured.out
    assert "Phase 4 reference 1/1 start" in captured.out
    assert "DONE file=" in captured.out
    assert len(payload["phase4"]) == 1
    assert len(payload["phase4_timing_summary"]) == 1


def test_main_reference_index_runs_only_selected_phase4_reference(
    monkeypatch, local_tmp_dir: Path
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    db_path = local_tmp_dir / "phase4.db"
    _create_phase4_db(db_path)

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "indexed.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    exit_code = main(
        [
            str(input_path),
            "--db-path",
            str(db_path),
            "--output-dir",
            str(output_dir),
            "--style-hint",
            "apa7_nl",
            "--reference-index",
            "1",
        ]
    )

    assert exit_code == 0
    written = json.loads((output_dir / "indexed.phase124.json").read_text(encoding="utf-8"))
    assert len(written["phase4"]) == 1
    assert len(written["phase4_timing_summary"]) == 1


def test_phase4_triage_summary_buckets_parser_and_db_coverage_cases():
    phase3 = [
        {
            "reference_id": "ref_missing_year",
            "ctype": "journal_article",
            "parsed_data": {
                "title": ["Instructions to authors for case reporting are limited"],
                "container_title": ["BMC Medical Education"],
            },
            "match_preparation": {"lookup_key_fields": {"title": ["Instructions"]}},
            "warnings": ["parser_missing_date"],
        },
        {
            "reference_id": "ref_db_gap",
            "ctype": "journal_article",
            "parsed_data": {
                "title": ["Authorship criteria for scientific papers"],
                "issued_year": "2004",
                "container_title": ["Brazilian Journal"],
            },
            "match_preparation": {
                "lookup_key_fields": {
                    "title": ["Authorship criteria for scientific papers"],
                    "issued_year": ["2004"],
                }
            },
            "warnings": [],
        },
    ]
    phase4 = [
        {
            "reference_id": "ref_missing_year",
            "status": "skipped",
            "input_summary": {"ctype": "journal_article"},
            "lookup_trace": {"candidate_count": 0},
            "reasons": ["phase4_missing_field:issued_year"],
        },
        {
            "reference_id": "ref_db_gap",
            "status": "no_match",
            "input_summary": {"ctype": "journal_article"},
            "lookup_trace": {"candidate_count": 0},
            "reasons": ["phase4_no_candidates"],
        },
    ]

    summary = _phase4_triage_summary(phase3, phase4)

    assert summary[0]["bucket"] == "parser_or_extraction"
    assert summary[0]["missing_fields"] == ["issued_year"]
    assert summary[1]["bucket"] == "db_coverage_or_lookup_recall"
    assert summary[1]["title"] == "Authorship criteria for scientific papers"
