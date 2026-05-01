from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from reference_gen2.pipeline_models import DocumentInput
from reference_gen2.security.file_validation import StoredUpload, validate_upload
from reference_gen2.security.security_scan import run_upload_security_scan
from reference_gen2.security.temp_storage import store_temp_upload, temp_upload_context


def document_input_from_paste(reference_list: str) -> DocumentInput:
    return DocumentInput(source_mode="paste", reference_list=reference_list)


def receive_upload(filename: str, declared_mime: str | None, content: bytes) -> StoredUpload:
    validated = validate_upload(
        filename=filename,
        declared_mime=declared_mime,
        content=content,
    )
    run_upload_security_scan(validated, content)
    return store_temp_upload(validated=validated, content=content)


@contextmanager
def receive_upload_context(
    filename: str,
    declared_mime: str | None,
    content: bytes,
) -> Iterator[StoredUpload]:
    validated = validate_upload(
        filename=filename,
        declared_mime=declared_mime,
        content=content,
    )
    run_upload_security_scan(validated, content)
    with temp_upload_context(validated, content) as stored:
        yield stored
