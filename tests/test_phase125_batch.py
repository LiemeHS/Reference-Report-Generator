from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tests.conftest import build_docx_bytes

from scripts.run_phase123_batch import _real_anystyle_executable
from scripts.run_phase125_batch import main, process_document


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


def test_process_document_writes_phase125_outputs(monkeypatch, local_tmp_dir: Path):
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

    json_path = output_dir / "sample.phase125.json"
    md_path = output_dir / "sample.phase125.md"
    quick_md_path = output_dir / "sample.phase125.quick.md"

    assert json_path.exists()
    assert md_path.exists()
    assert quick_md_path.exists()
    assert payload["status"] == "ok"
    assert payload["phase5"] is not None
    assert len(payload["phase5"]) >= 1

    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert "phase4" in written
    assert "phase5" in written
    assert "phase5_timing_summary" in written
    assert written["timings_ms"]["phase5"] >= 0.0
    assert written["phase5"][0]["final_status"] in {
        "verified",
        "needs_review",
        "suspicious",
        "skipped",
        "error",
    }
    assert "evidence_checks" in written["phase5"][0]["report_signals"]

    markdown = md_path.read_text(encoding="utf-8")
    assert "## Phase 5" in markdown
    assert "Final Status:" in markdown
    assert "Best Candidate Record ID:" in markdown
    assert "Accepted Record ID:" not in markdown
    assert "Evidence Checks:" in markdown

    quick_markdown = quick_md_path.read_text(encoding="utf-8")
    assert "## Phase 5" in quick_markdown
    assert "- Confidence:" in quick_markdown
    assert "Best Candidate Record ID:" in quick_markdown
    assert "Accepted Record ID:" not in quick_markdown


def test_main_accepts_single_input_file_for_phase125(monkeypatch, local_tmp_dir: Path):
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
    assert (output_dir / "single.phase125.json").exists()
    assert (output_dir / "single.phase125.md").exists()
    assert (output_dir / "single.phase125.quick.md").exists()


def test_process_document_progress_and_reference_scoping(monkeypatch, local_tmp_dir: Path, capsys):
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
    assert "Phase 4 warm-up start" in captured.out
    assert "Phase 5 start references=1" in captured.out
    assert "Phase 5 reference 1/1 start" in captured.out
    assert len(payload["phase4"]) == 1
    assert len(payload["phase5"]) == 1
    assert len(payload["phase5_timing_summary"]) == 1


def test_main_reference_index_runs_only_selected_phase5_reference(monkeypatch, local_tmp_dir: Path):
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
            "--relaxed",
        ]
    )

    assert exit_code == 0
    written = json.loads((output_dir / "indexed.phase125.json").read_text(encoding="utf-8"))
    assert len(written["phase4"]) == 1
    assert len(written["phase5"]) == 1
    assert len(written["phase5_timing_summary"]) == 1


def test_process_document_writes_optional_html_report(monkeypatch, local_tmp_dir: Path):
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
        ]
    )
    input_path = input_dir / "html.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    payload = process_document(
        input_path,
        output_dir,
        db_path=str(db_path),
        style_hint="apa7_nl",
        html_report=True,
    )

    html_path = output_dir / "html.phase125.html"
    html = html_path.read_text(encoding="utf-8")

    assert payload["status"] == "ok"
    assert html_path.exists()
    assert "Statisch opgeschoond rapport" not in html
    assert "Beste gevonden record:" not in html
    assert "Tweede kandidaat: <code>-" not in html
    assert "Verificatiestappen" in html
    assert "ref_0001" in html
    assert "Referentierapport" in html
    assert "Reference Gen2 Report" not in html
    assert "Smith, J. (2020). Some title." in html
    assert "10.1234/test.article" in html
    assert "raw_adapter_data" not in html
    assert "/reports/" not in html
    assert "recheck" not in html.lower()
    assert "session" not in html.lower()
