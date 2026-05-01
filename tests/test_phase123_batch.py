from __future__ import annotations

import json
import zipfile
from io import BytesIO
from pathlib import Path

import pytest

from tests.conftest import build_docx_bytes

from scripts.run_phase123_batch import _real_anystyle_executable, main, process_document


_HYPERLINK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)


def _add_external_hyperlinks(content: bytes, targets: list[str]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(content)) as src, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        rel_lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ]
        for index, target in enumerate(targets, start=1):
            rel_lines.append(
                f'<Relationship Id="rId{index}" Type="{_HYPERLINK_REL_TYPE}" Target="{target}" TargetMode="External"/>'
            )
        rel_lines.append("</Relationships>")
        dst.writestr("word/_rels/document.xml.rels", "".join(rel_lines).encode("utf-8"))
    return buffer.getvalue()


def test_process_document_writes_json_markdown_and_quick_outputs(
    monkeypatch, local_tmp_dir: Path
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article. "
                "Extra stable detail for harness testing output generation.",
                "Normal",
            ),
            (
                "Doe, J. (2021). Another title. Other Journal, 3(1), 5-10. https://doi.org/10.5678/other. "
                "Additional stable detail for harness testing output generation.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "sample.docx"
    input_path.write_bytes(
        _add_external_hyperlinks(
            docx_bytes,
            [
                "https://doi.org/10.1234/test.article",
                "https://example.org/reference",
            ],
        )
    )

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)

    payload = process_document(input_path, output_dir, style_hint="apa7_nl")

    json_path = output_dir / "sample.phase123.json"
    md_path = output_dir / "sample.phase123.md"
    quick_md_path = output_dir / "sample.phase123.quick.md"

    assert json_path.exists()
    assert md_path.exists()
    assert quick_md_path.exists()
    assert payload["phase1"] is not None
    assert payload["phase2"] is not None
    assert payload["phase3"] is not None
    assert payload["phase3b"] is not None
    assert payload["status"] == "ok"

    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written["cycle_id"].startswith("cycle_")
    assert written["phase1"]["upload_kind"] == "docx"
    assert written["style_hint"] == "apa7_nl"
    assert written["phase2"]["reference_count"] >= 1
    assert len(written["phase3"]) == len(written["phase3b"])
    assert "phase3b" in written
    assert "ctype" in written["phase3"][0]
    assert "recovery_status" in written["phase3b"][0]
    assert "input_file" not in written
    assert "raw_reference" not in json.dumps(written, ensure_ascii=False)
    assert "sample.docx" not in json.dumps(written, ensure_ascii=False)

    markdown = md_path.read_text(encoding="utf-8")
    assert "## Phase 1" in markdown
    assert "## Phase 2" in markdown
    assert "## Phase 3" in markdown
    assert "Final CType" in markdown
    assert "sample.docx" not in markdown

    quick_markdown = quick_md_path.read_text(encoding="utf-8")
    assert "# Sanitized Quick Glance:" in quick_markdown
    assert "## Reference ref_0001" in quick_markdown
    assert "- Final Type:" in quick_markdown
    assert "- Recovery Status:" in quick_markdown
    assert "- Match Eligible:" in quick_markdown
    assert "```json" not in quick_markdown
    assert "- Raw:" not in quick_markdown
    assert "sample.docx" not in quick_markdown


def test_process_document_rich_output_remains_available_explicitly(
    monkeypatch, local_tmp_dir: Path
):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. "
                "Extra stable detail for rich output harness testing.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "rich.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MIN_CHARS", 20)
    monkeypatch.setattr("reference_gen2.bibliography_detection.service.BIB_MAX_CHARS", 100000)

    payload = process_document(
        input_path,
        output_dir,
        style_hint="apa7_nl",
        rich_output=True,
    )

    assert payload["input_file"].endswith("rich.docx")
    assert payload["phase1"]["bibliography"]["text"]
    assert payload["phase3"][0]["raw_reference"]


def test_main_accepts_single_input_file(monkeypatch, local_tmp_dir: Path):
    executable = _real_anystyle_executable()
    if executable is None:
        pytest.skip("AnyStyle CLI is not available for batch harness testing.")

    input_dir = local_tmp_dir / "input"
    output_dir = local_tmp_dir / "output"
    input_dir.mkdir()
    output_dir.mkdir()

    docx_bytes = build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article. "
                "Extra stable detail for harness testing output generation.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail.",
                "Normal",
            ),
        ]
    )
    input_path = input_dir / "single.docx"
    input_path.write_bytes(docx_bytes)

    monkeypatch.setenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", executable)

    exit_code = main(
        [
            str(input_path),
            "--output-dir",
            str(output_dir),
            "--style-hint",
            "apa7_nl",
        ]
    )

    assert exit_code == 0
    assert (output_dir / "single.phase123.json").exists()
    assert (output_dir / "single.phase123.md").exists()
    assert (output_dir / "single.phase123.quick.md").exists()


def test_quick_glance_report_surfaces_type_mismatch_without_full_json():
    payload = {
        "input_file": "manual_tests/input/pdftest2.pdf",
        "status": "ok",
        "error": None,
        "phase3": [
            {
                "raw_reference": (
                    "Vrooman, C. en S. Hoff (2004) The Feminisation of Poverty – Women as a Risk Group. "
                    "In: C. Vrooman en S. Hoff (red.) The Poor Side of the Netherlands. "
                    "Results from the Dutch poverty monitor 1997-2003. Den Haag: scp/cbs, 93-110."
                ),
                "ctype": "journal_article",
                "parser_backend": "anystyle",
                "warnings": ["parser_missing_identifier"],
                "parsed_data": {
                    "type": "article-journal",
                    "author": [
                        {"family": "Vrooman", "given": "C.", "literal": None},
                        {"family": "Hoff", "given": "S.", "literal": None},
                    ],
                    "editor": [
                        {"family": "Vrooman", "given": "C.", "literal": None},
                        {"family": "Hoff", "given": "S.", "literal": None},
                    ],
                    "title": ["The Feminisation of Poverty – Women as a Risk Group"],
                    "container_title": [
                        "The Poor Side of the Netherlands. Results from the Dutch poverty monitor 1997-2003"
                    ],
                    "publisher": ["scp/cbs"],
                    "institution": [],
                    "organization": [],
                    "date": ["2004"],
                    "volume": [],
                    "issue": [],
                    "pages": ["93-110"],
                    "doi": [],
                    "url": [],
                },
            }
        ],
    }

    from scripts.run_phase123_batch import _quick_glance_report

    quick_markdown = _quick_glance_report(payload)

    assert "Vrooman, C. en S. Hoff" in quick_markdown
    assert "- Final Type: `journal_article`" in quick_markdown
    assert "- AnyStyle Type: `article-journal`" in quick_markdown
    assert "- Editors: Vrooman, C.; Hoff, S." in quick_markdown
    assert "- Container: The Poor Side of the Netherlands." in quick_markdown
    assert "```json" not in quick_markdown
