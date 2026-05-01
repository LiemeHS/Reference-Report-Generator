from __future__ import annotations

from io import BytesIO
from pathlib import Path
import tempfile
from typing import Iterable
import zipfile

import pytest
from docx import Document


def _escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf_bytes(pages: Iterable[list[str]]) -> bytes:
    page_lines = list(pages)
    objects: list[str] = []

    font_obj_num = 3 + 2 * len(page_lines)
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{3 + (index * 2)} 0 R" for index in range(len(page_lines)))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_lines)} >>")

    for index, lines in enumerate(page_lines):
        page_obj_num = 3 + (index * 2)
        content_obj_num = page_obj_num + 1
        resources = f"<< /Font << /F1 {font_obj_num} 0 R >> >>"
        objects.append(
            "<< /Type /Page /Parent 2 0 R "
            "/MediaBox [0 0 612 792] "
            f"/Resources {resources} "
            f"/Contents {content_obj_num} 0 R >>"
        )

        text_ops = ["BT", "/F1 12 Tf"] if lines else []
        for line_index, line in enumerate(lines):
            escaped = _escape_pdf_text(line)
            y_position = 720 - (line_index * 18)
            text_ops.append(f"1 0 0 1 72 {y_position} Tm")
            text_ops.append(f"({escaped}) Tj")
        if lines:
            text_ops.append("ET")
        stream = "\n".join(text_ops)
        objects.append(
            f"<< /Length {len(stream.encode('utf-8'))} >>\nstream\n{stream}\nendstream"
        )

    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    output = ["%PDF-1.4\n"]
    offsets: list[int] = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(part.encode("utf-8")) for part in output))
        output.append(f"{index} 0 obj\n{obj}\nendobj\n")

    xref_offset = sum(len(part.encode("utf-8")) for part in output)
    output.append(f"xref\n0 {len(objects) + 1}\n")
    output.append("0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.append(f"{offset:010d} 00000 n \n")
    output.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n"
    )
    return "".join(output).encode("utf-8")


def build_docx_bytes(body_paragraphs: list[tuple[str, str | None]]) -> bytes:
    document = Document()
    for text, style_name in body_paragraphs:
        paragraph = document.add_paragraph(text)
        if style_name:
            paragraph.style = style_name
    buffer = BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def build_docx_like_zip(entries: dict[str, bytes]) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, payload in entries.items():
            zf.writestr(name, payload)
    return buffer.getvalue()


@pytest.fixture
def good_pdf_bytes() -> bytes:
    return build_pdf_bytes(
        [
            ["Intro page"],
            [
                "References",
                "Alpha, A. (2020). Example reference with enough detail for testing.",
                "Beta, B. (2021). Another reference with a DOI https://doi.org/10.1000/test",
                "Gamma, G. (2019). Third reference for bibliography detection coverage.",
            ],
        ]
    )


@pytest.fixture
def good_docx_bytes() -> bytes:
    return build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Alpha, A. (2020). Example reference with enough detail for testing.",
                "Normal",
            ),
            (
                "Beta, B. (2021). Another reference with a DOI https://doi.org/10.1000/test",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage.",
                "Normal",
            ),
        ]
    )


@pytest.fixture
def local_tmp_dir():
    with tempfile.TemporaryDirectory(prefix="reference_gen2_tests_") as temp_dir:
        yield Path(temp_dir)
