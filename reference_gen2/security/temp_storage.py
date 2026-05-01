from __future__ import annotations

import os
import pathlib
import tempfile
import time
from contextlib import contextmanager
from typing import Iterator

from reference_gen2.api.settings import UPLOAD_TMP_DIR
from reference_gen2.security.file_validation import StoredUpload, ValidatedUpload


def ensure_upload_tmp_dir() -> pathlib.Path:
    UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)
    return UPLOAD_TMP_DIR


def store_temp_upload(validated: ValidatedUpload, content: bytes) -> StoredUpload:
    suffix = f".{validated.detected_kind}"
    fd, raw_path = tempfile.mkstemp(suffix=suffix, dir=str(ensure_upload_tmp_dir()))
    try:
        with os.fdopen(fd, "wb") as file_obj:
            file_obj.write(content)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(raw_path)
        except FileNotFoundError:
            pass
        raise

    return StoredUpload(
        original_filename=validated.original_filename,
        normalized_filename=validated.normalized_filename,
        detected_kind=validated.detected_kind,
        declared_mime=validated.declared_mime,
        size_bytes=validated.size_bytes,
        temp_path=pathlib.Path(raw_path),
    )


def delete_temp_upload(path: pathlib.Path) -> None:
    last_error: PermissionError | None = None
    for _ in range(20):
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.1)
    if last_error is not None:
        raise last_error


@contextmanager
def temp_upload_context(validated: ValidatedUpload, content: bytes) -> Iterator[StoredUpload]:
    stored = store_temp_upload(validated, content)
    try:
        yield stored
    finally:
        delete_temp_upload(stored.temp_path)
