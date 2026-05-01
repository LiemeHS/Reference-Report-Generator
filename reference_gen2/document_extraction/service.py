from __future__ import annotations

import multiprocessing
from multiprocessing.context import BaseContext
from multiprocessing.process import BaseProcess
import queue
import time
from typing import Callable

from reference_gen2.api.settings import EXTRACT_TIMEOUT_SEC
from reference_gen2.extractors.docx_extractor import extract_docx_text
from reference_gen2.extractors.models import DocumentExtraction, ExtractionError
from reference_gen2.extractors.pdf_extractor import extract_pdf_text
from reference_gen2.security.file_validation import StoredUpload


_EXTRACTION_PROCESS_JOIN_TIMEOUT_SEC = 1.0
_EXTRACTION_QUEUE_POLL_TIMEOUT_SEC = 0.1


def _dispatch(upload: StoredUpload) -> Callable[[StoredUpload], DocumentExtraction]:
    if upload.detected_kind == "pdf":
        return extract_pdf_text
    if upload.detected_kind == "docx":
        return extract_docx_text
    raise ExtractionError(
        "extraction_failed",
        f"Unsupported upload kind for extraction: {upload.detected_kind!r}.",
    )


def extract_document_text(upload: StoredUpload) -> DocumentExtraction:
    """Extract document text in a killable child process.

    PDF and DOCX parsers handle untrusted complex formats. A thread timeout can
    return control to the caller only after the worker thread stops. A process
    boundary lets Phase 1 terminate a stuck parser on timeout.
    """

    context = _process_context()
    result_queue: multiprocessing.Queue = context.Queue(maxsize=1)
    process = context.Process(
        target=_extract_document_text_worker,
        args=(upload, result_queue),
        daemon=True,
    )
    process.start()
    try:
        try:
            payload = _wait_for_worker_payload(process, result_queue)
        except queue.Empty as exc:
            _terminate_process(process)
            raise ExtractionError(
                "extraction_timeout",
                "Document extraction did not complete within the time limit.",
            ) from exc

        process.join(timeout=_EXTRACTION_PROCESS_JOIN_TIMEOUT_SEC)
        if process.is_alive():
            _terminate_process(process)
            raise ExtractionError(
                "extraction_timeout",
                "Document extraction did not complete within the time limit.",
            )
        return _decode_worker_payload(payload)
    finally:
        if process.is_alive():
            _terminate_process(process)
        result_queue.close()
        result_queue.join_thread()


def _process_context() -> BaseContext:
    if "fork" in multiprocessing.get_all_start_methods():
        return multiprocessing.get_context("fork")
    return multiprocessing.get_context("spawn")


def _wait_for_worker_payload(
    process: BaseProcess,
    result_queue: multiprocessing.Queue,
) -> object:
    deadline = time.monotonic() + EXTRACT_TIMEOUT_SEC
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise queue.Empty
        try:
            return result_queue.get(
                timeout=min(_EXTRACTION_QUEUE_POLL_TIMEOUT_SEC, remaining)
            )
        except queue.Empty:
            if not process.is_alive():
                process.join(timeout=_EXTRACTION_PROCESS_JOIN_TIMEOUT_SEC)
                raise ExtractionError(
                    "extraction_failed",
                    "Document extraction failed.",
                )


def _extract_document_text_worker(
    upload: StoredUpload,
    result_queue: multiprocessing.Queue,
) -> None:
    try:
        extractor = _dispatch(upload)
        result_queue.put(("ok", extractor(upload)))
    except ExtractionError as exc:
        result_queue.put(("extraction_error", exc.code, exc.message, exc.http_status))
    except Exception as exc:
        result_queue.put(("exception", str(exc)))


def _decode_worker_payload(payload: object) -> DocumentExtraction:
    if not isinstance(payload, tuple) or not payload:
        raise ExtractionError(
            "extraction_failed",
            "Document extraction failed.",
        )
    status = payload[0]
    if status == "ok" and len(payload) == 2:
        result = payload[1]
        if isinstance(result, DocumentExtraction):
            return result
        raise ExtractionError(
            "extraction_failed",
            "Document extraction failed.",
        )
    if status == "extraction_error" and len(payload) == 4:
        raise ExtractionError(
            str(payload[1]),
            str(payload[2]),
            int(payload[3]),
        )
    if status == "exception" and len(payload) == 2:
        raise ExtractionError(
            "extraction_failed",
            f"Document extraction failed: {payload[1]}",
        )
    raise ExtractionError(
        "extraction_failed",
        "Document extraction failed.",
    )


def _terminate_process(process: BaseProcess) -> None:
    process.terminate()
    process.join(timeout=_EXTRACTION_PROCESS_JOIN_TIMEOUT_SEC)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=_EXTRACTION_PROCESS_JOIN_TIMEOUT_SEC)
