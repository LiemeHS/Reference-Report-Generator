from __future__ import annotations

import io
import pathlib
import re
import zipfile
from xml.etree import ElementTree
from dataclasses import dataclass
from typing import Literal, Mapping

from reference_gen2.api.settings import (
    DOCX_REJECT_EMBEDDED_OBJECTS,
    DOCX_REJECT_EXTERNAL_RELATIONSHIPS,
    DOCX_REJECT_MACROS,
    PDF_MAX_OBJECTS,
    PDF_MAX_OBJECTS_PER_MB,
    PDF_MAX_STREAMS,
    UPLOAD_ALLOWED_EXTENSIONS,
    UPLOAD_MAX_BYTES,
)

UploadKind = Literal["pdf", "docx"]

_PDF_MIME_TYPES = {"application/pdf"}
_DOCX_MIME_TYPES = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
_GENERIC_MIME_TYPES = {"application/octet-stream"}
_DOCX_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
_DOCX_MAX_ENTRY_COUNT = 1000
_PDF_TRAILER_SCAN_BYTES = 4096
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_RISKY_FILENAME_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
_DOCX_MACRO_ENTRY_RE = re.compile(r"(^|/)(vbaproject\.bin|vbadata\.xml)$", re.I)
_DOCX_EMBEDDED_ENTRY_RE = re.compile(r"(^|/)(embeddings|activeX|oleObject)", re.I)
_OOXML_HYPERLINK_REL_TYPE = (
    "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
)


@dataclass(frozen=True)
class ValidatedUpload:
    original_filename: str
    normalized_filename: str
    detected_kind: UploadKind
    declared_mime: str | None
    size_bytes: int


@dataclass(frozen=True)
class StoredUpload(ValidatedUpload):
    temp_path: pathlib.Path


class UploadValidationError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        http_status: int = 422,
        details: Mapping[str, object] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = dict(details or {})


def sanitize_filename(filename: str) -> str:
    """Return a safe filename for logging/display without trusting the original path."""
    cleaned = _CONTROL_CHARS_RE.sub("", filename or "")
    cleaned = _RISKY_FILENAME_CHARS_RE.sub("_", cleaned)
    cleaned = cleaned.strip()
    return cleaned[:255]


def sniff_upload_kind(content: bytes) -> UploadKind | None:
    if content.startswith(b"%PDF-"):
        return "pdf"
    if zipfile.is_zipfile(io.BytesIO(content)):
        return "docx"
    return None


def validate_pdf_container(content: bytes) -> None:
    """Validate a PDF upload with lightweight structural checks."""
    if not content.startswith(b"%PDF-"):
        raise UploadValidationError(
            "invalid_signature",
            "Uploaded file content does not match the expected file signature.",
        )

    trailer = content[-_PDF_TRAILER_SCAN_BYTES:]
    if b"%%EOF" not in trailer:
        raise UploadValidationError(
            "invalid_pdf_container",
            "PDF upload is missing a valid end-of-file marker.",
        )

    has_page_object = b"/Type /Page" in content or b"/Type/Page" in content
    has_page_tree = (
        b"/Type /Pages" in content
        or b"/Type/Pages" in content
        or b"/Pages" in content
    )
    if not has_page_object and not has_page_tree:
        raise UploadValidationError(
            "invalid_pdf_container",
            "PDF upload does not appear to contain a page tree or page objects.",
        )

    object_count = content.count(b" obj")
    if object_count > PDF_MAX_OBJECTS:
        raise UploadValidationError(
            "suspicious_pdf_structure",
            "PDF contains too many objects to inspect safely.",
        )

    stream_count = content.count(b"stream")
    if stream_count > PDF_MAX_STREAMS:
        raise UploadValidationError(
            "suspicious_pdf_structure",
            "PDF contains too many streams to inspect safely.",
        )

    size_mb = max(len(content) / (1024 * 1024), 0.01)
    if (object_count / size_mb) > PDF_MAX_OBJECTS_PER_MB:
        raise UploadValidationError(
            "suspicious_pdf_structure",
            "PDF object density exceeds the safe inspection threshold.",
        )


@dataclass(frozen=True)
class _ExternalRelationship:
    source_file: str
    rel_type: str
    target: str


def _docx_external_relationships(zf: zipfile.ZipFile) -> list[_ExternalRelationship]:
    relationships: list[_ExternalRelationship] = []
    for info in zf.infolist():
        name = info.filename.replace("\\", "/")
        if not name.endswith(".rels"):
            continue
        try:
            payload = zf.read(info).decode("utf-8", errors="ignore")
            root = ElementTree.fromstring(payload)
        except (KeyError, ElementTree.ParseError, UnicodeDecodeError):
            continue

        for elem in root.iter():
            target_mode = elem.attrib.get("TargetMode", "")
            if target_mode.lower() == "external":
                relationships.append(
                    _ExternalRelationship(
                        source_file=name,
                        rel_type=elem.attrib.get("Type", ""),
                        target=elem.attrib.get("Target", ""),
                    )
                )
    return relationships


def validate_docx_container(content: bytes) -> None:
    """Validate that the content is a safe DOCX-like ZIP container."""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            entries = zf.infolist()
            if len(entries) > _DOCX_MAX_ENTRY_COUNT:
                raise UploadValidationError(
                    "invalid_docx_container",
                    "DOCX contains too many ZIP entries.",
                )

            names = {info.filename for info in entries}
            required = {"[Content_Types].xml", "word/document.xml"}
            if not required.issubset(names):
                raise UploadValidationError(
                    "invalid_docx_container",
                    "DOCX is missing required Word document entries.",
                )

            total_uncompressed = 0
            for info in entries:
                normalized = info.filename.replace("\\", "/")
                if normalized.startswith("/"):
                    raise UploadValidationError(
                        "invalid_docx_container",
                        "DOCX contains an absolute ZIP entry path.",
                    )

                parts = normalized.split("/")
                if len(parts) > 1 and parts[0].endswith(":"):
                    raise UploadValidationError(
                        "invalid_docx_container",
                        "DOCX contains a drive-qualified ZIP entry path.",
                    )
                for part in parts[:-1]:
                    if part in {"", ".", ".."}:
                        raise UploadValidationError(
                            "invalid_docx_container",
                            "DOCX contains an unsafe ZIP entry path.",
                        )

                if DOCX_REJECT_MACROS and _DOCX_MACRO_ENTRY_RE.search(normalized):
                    raise UploadValidationError(
                        "suspicious_docx_content",
                        "DOCX contains macro-enabled content and was rejected.",
                    )

                if DOCX_REJECT_EMBEDDED_OBJECTS and _DOCX_EMBEDDED_ENTRY_RE.search(
                    normalized
                ):
                    raise UploadValidationError(
                        "suspicious_docx_content",
                        "DOCX contains embedded object content and was rejected.",
                    )

                total_uncompressed += info.file_size
                if total_uncompressed > _DOCX_MAX_UNCOMPRESSED_BYTES:
                    raise UploadValidationError(
                        "invalid_docx_container",
                        "DOCX expands to an unsafe uncompressed size.",
                    )

                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > 100:
                        raise UploadValidationError(
                            "invalid_docx_container",
                            "DOCX contains a suspiciously compressed ZIP entry.",
                        )

            bad_entry = zf.testzip()
            if bad_entry is not None:
                raise UploadValidationError(
                    "invalid_docx_container",
                    f"DOCX ZIP integrity check failed for entry {bad_entry!r}.",
                )

            external_relationships = _docx_external_relationships(zf)
            disallowed_relationships = [
                relationship
                for relationship in external_relationships
                if relationship.rel_type != _OOXML_HYPERLINK_REL_TYPE
            ]
            if DOCX_REJECT_EXTERNAL_RELATIONSHIPS and disallowed_relationships:
                first = disallowed_relationships[0]
                raise UploadValidationError(
                    "suspicious_docx_content",
                    "DOCX contains disallowed external relationships and was rejected.",
                    details={
                        "source_file": first.source_file,
                        "relationship_type": first.rel_type,
                        "target": first.target,
                        "disallowed_external_relationship_count": len(
                            disallowed_relationships
                        ),
                        "allowed_external_hyperlink_count": len(
                            external_relationships
                        )
                        - len(disallowed_relationships),
                    },
                )
    except UploadValidationError:
        raise
    except zipfile.BadZipFile as exc:
        raise UploadValidationError(
            "invalid_docx_container",
            "DOCX upload is not a valid ZIP container.",
        ) from exc
    except OSError as exc:
        raise UploadValidationError(
            "invalid_docx_container",
            "DOCX upload could not be safely inspected.",
        ) from exc


def _normalized_declared_mime(declared_mime: str | None) -> str | None:
    normalized = (declared_mime or "").strip().lower()
    return normalized or None


def _canonical_filename(sanitized_filename: str, kind: UploadKind) -> str:
    ext = f".{kind}"
    stem = pathlib.PurePath(sanitized_filename).stem.strip() or "upload"
    return f"{stem}{ext}"


def validate_upload(
    filename: str, declared_mime: str | None, content: bytes
) -> ValidatedUpload:
    sanitized_filename = sanitize_filename(filename)
    if not sanitized_filename:
        raise UploadValidationError(
            "empty_filename", "Upload filename is empty after sanitization."
        )

    size_bytes = len(content or b"")
    if size_bytes == 0:
        raise UploadValidationError("empty_file", "Upload file is empty.")
    if size_bytes > UPLOAD_MAX_BYTES:
        raise UploadValidationError(
            "file_too_large",
            f"Upload exceeds the maximum allowed size of {UPLOAD_MAX_BYTES} bytes.",
        )

    suffix = pathlib.PurePath(sanitized_filename).suffix.lower()
    if suffix not in UPLOAD_ALLOWED_EXTENSIONS:
        raise UploadValidationError(
            "unsupported_extension",
            "Only .pdf and .docx uploads are supported.",
        )

    detected_kind = sniff_upload_kind(content)
    expected_kind: UploadKind = "pdf" if suffix == ".pdf" else "docx"
    if detected_kind != expected_kind:
        raise UploadValidationError(
            "invalid_signature",
            "Uploaded file content does not match the expected file signature.",
        )

    if expected_kind == "pdf":
        validate_pdf_container(content)
    if expected_kind == "docx":
        validate_docx_container(content)

    normalized_mime = _normalized_declared_mime(declared_mime)
    if normalized_mime and normalized_mime not in _GENERIC_MIME_TYPES:
        allowed_mimes = _PDF_MIME_TYPES if expected_kind == "pdf" else _DOCX_MIME_TYPES
        if normalized_mime not in allowed_mimes:
            raise UploadValidationError(
                "mime_mismatch",
                "Declared MIME type does not match the uploaded file type.",
            )

    return ValidatedUpload(
        original_filename=sanitized_filename,
        normalized_filename=_canonical_filename(sanitized_filename, expected_kind),
        detected_kind=expected_kind,
        declared_mime=normalized_mime,
        size_bytes=size_bytes,
    )
