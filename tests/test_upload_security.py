from __future__ import annotations

import pathlib
import zipfile
from io import BytesIO

import pytest

from reference_gen2.security import (
    ensure_upload_tmp_dir,
    store_temp_upload,
    temp_upload_context,
    validate_upload,
)
from reference_gen2.security.file_validation import UploadValidationError


_HYPERLINK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)
_IMAGE_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
)


def _add_external_relationships(content: bytes, relationships: list[tuple[str, str]]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(BytesIO(content)) as src, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as dst:
        for info in src.infolist():
            dst.writestr(info, src.read(info.filename))
        rel_lines = [
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">',
        ]
        for index, (rel_type, target) in enumerate(relationships, start=1):
            rel_lines.append(
                f'<Relationship Id="rId{index}" Type="{rel_type}" Target="{target}" TargetMode="External"/>'
            )
        rel_lines.append("</Relationships>")
        dst.writestr("word/_rels/document.xml.rels", "".join(rel_lines).encode("utf-8"))
    return buffer.getvalue()


def test_validate_upload_accepts_known_good_pdf(good_pdf_bytes: bytes):
    validated = validate_upload("paper.pdf", "application/pdf", good_pdf_bytes)

    assert validated.detected_kind == "pdf"
    assert validated.normalized_filename == "paper.pdf"


def test_validate_upload_accepts_known_good_docx(good_docx_bytes: bytes):
    validated = validate_upload(
        "paper.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        good_docx_bytes,
    )

    assert validated.detected_kind == "docx"
    assert validated.normalized_filename == "paper.docx"


def test_validate_upload_rejects_invalid_pdf_signature():
    with pytest.raises(UploadValidationError) as exc:
        validate_upload("paper.pdf", "application/pdf", b"not-a-pdf")

    assert exc.value.code == "invalid_signature"


def test_validate_upload_rejects_truncated_pdf():
    with pytest.raises(UploadValidationError) as exc:
        validate_upload("paper.pdf", "application/pdf", b"%PDF-1.4\nbroken")

    assert exc.value.code == "invalid_pdf_container"


def test_validate_upload_accepts_pdf_with_page_tree_but_no_literal_page_objects():
    content = (
        b"%PDF-1.5\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
        b"4 0 obj\n<< /Type /ObjStm /N 1 /First 8 /Length 9 >>\nstream\n3 0\nendstream\nendobj\n"
        b"xref\n0 5\n0000000000 65535 f \n"
        b"trailer\n<< /Root 1 0 R /Size 5 >>\n"
        b"startxref\n123\n%%EOF"
    )

    validated = validate_upload("compressed.pdf", "application/pdf", content)

    assert validated.detected_kind == "pdf"


def test_validate_upload_accepts_real_fixture_pdftest7():
    sample_path = pathlib.Path("manual_tests/input/pdftest7.pdf")
    if not sample_path.exists():
        pytest.skip("Missing fixture: manual_tests/input/pdftest7.pdf")

    validated = validate_upload(
        sample_path.name,
        "application/pdf",
        sample_path.read_bytes(),
    )

    assert validated.detected_kind == "pdf"


def test_validate_upload_rejects_docx_macro_content():
    # Let the real ZIP-based helper build the document-like archive.
    from conftest import build_docx_like_zip

    content = build_docx_like_zip(
        {
            "[Content_Types].xml": b"<Types />",
            "word/document.xml": b"<w:document />",
            "word/vbaProject.bin": b"macro",
        }
    )

    with pytest.raises(UploadValidationError) as exc:
        validate_upload("paper.docx", None, content)

    assert exc.value.code == "suspicious_docx_content"


def test_validate_upload_accepts_docx_with_external_hyperlinks(good_docx_bytes: bytes):
    content = _add_external_relationships(
        good_docx_bytes,
        [
            (_HYPERLINK_REL_TYPE, "https://doi.org/10.1000/test"),
            (_HYPERLINK_REL_TYPE, "https://example.org/reference"),
        ],
    )

    validated = validate_upload("paper.docx", None, content)

    assert validated.detected_kind == "docx"


def test_validate_upload_rejects_docx_with_non_hyperlink_external_relationship(
    good_docx_bytes: bytes,
):
    content = _add_external_relationships(
        good_docx_bytes,
        [
            (_HYPERLINK_REL_TYPE, "https://doi.org/10.1000/test"),
            (_IMAGE_REL_TYPE, "https://example.org/external-image.png"),
        ],
    )

    with pytest.raises(UploadValidationError) as exc:
        validate_upload("paper.docx", None, content)

    assert exc.value.code == "suspicious_docx_content"
    assert exc.value.details["relationship_type"] == _IMAGE_REL_TYPE
    assert exc.value.details["target"] == "https://example.org/external-image.png"


def test_store_temp_upload_creates_tmp_dir_if_missing(
    monkeypatch, local_tmp_dir: pathlib.Path, good_pdf_bytes: bytes
):
    target = local_tmp_dir / "missing" / "uploads"
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", target)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", target)
    validated = validate_upload("paper.pdf", None, good_pdf_bytes)

    stored = store_temp_upload(validated, good_pdf_bytes)

    try:
        assert target.exists()
        assert stored.temp_path.exists()
    finally:
        stored.temp_path.unlink(missing_ok=True)


def test_temp_upload_context_cleans_up_on_success(
    monkeypatch, local_tmp_dir: pathlib.Path, good_pdf_bytes: bytes
):
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", local_tmp_dir)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", local_tmp_dir)
    validated = validate_upload("paper.pdf", None, good_pdf_bytes)

    with temp_upload_context(validated, good_pdf_bytes) as stored:
        path = stored.temp_path
        assert path.exists()

    assert not path.exists()


def test_ensure_upload_tmp_dir_is_idempotent(monkeypatch, local_tmp_dir: pathlib.Path):
    target = local_tmp_dir / "nested" / "uploads"
    monkeypatch.setattr("reference_gen2.api.settings.UPLOAD_TMP_DIR", target)
    monkeypatch.setattr("reference_gen2.security.temp_storage.UPLOAD_TMP_DIR", target)

    created = ensure_upload_tmp_dir()

    assert created == target
    assert created.exists()
