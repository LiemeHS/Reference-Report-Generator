from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from reference_gen2.api.settings import (
    ANYSTYLE_ENABLED,
    ANYSTYLE_EXECUTABLE,
    ANYSTYLE_PARSE_ARGS,
    ANYSTYLE_TIMEOUT_SEC,
)
from reference_gen2.reference_parsing.models import ReferenceParsingError


def _command_for_input(input_path: Path) -> list[str]:
    if not ANYSTYLE_ENABLED:
        raise ReferenceParsingError(
            "anystyle_disabled",
            "AnyStyle parsing is disabled by configuration.",
            http_status=503,
        )

    if not ANYSTYLE_EXECUTABLE:
        raise ReferenceParsingError(
            "anystyle_unconfigured",
            "AnyStyle parsing is enabled but no executable is configured.",
            http_status=503,
        )

    return [
        ANYSTYLE_EXECUTABLE,
        *ANYSTYLE_PARSE_ARGS,
        "--stdout",
        "-f",
        "json",
        "parse",
        str(input_path),
        "-",
    ]


def parse_reference_tags(reference_text: str) -> dict[str, Any] | None:
    cleaned = reference_text.strip()
    if not cleaned:
        return None

    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        delete=True,
    ) as handle:
        handle.write(f"{cleaned}\n")
        handle.flush()
        command = _command_for_input(Path(handle.name))

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=ANYSTYLE_TIMEOUT_SEC,
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReferenceParsingError(
                "anystyle_timeout",
                "AnyStyle parsing timed out.",
                http_status=503,
            ) from exc
        except OSError as exc:
            raise ReferenceParsingError(
                "anystyle_execution_failed",
                "AnyStyle could not be executed safely.",
                http_status=503,
            ) from exc

    if result.returncode != 0:
        details: dict[str, Any] = {"returncode": result.returncode}
        stderr = (result.stderr or "").strip()
        if stderr:
            details["stderr"] = stderr
        raise ReferenceParsingError(
            "anystyle_parse_failed",
            "AnyStyle returned a non-zero exit status.",
            http_status=503,
            details=details,
        )

    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ReferenceParsingError(
            "anystyle_invalid_output",
            "AnyStyle returned malformed JSON.",
            http_status=503,
        ) from exc

    if not isinstance(payload, list):
        raise ReferenceParsingError(
            "anystyle_invalid_output",
            "AnyStyle returned an unexpected JSON payload.",
            http_status=503,
        )

    if not payload:
        return None

    first = payload[0]
    if not isinstance(first, dict):
        raise ReferenceParsingError(
            "anystyle_invalid_output",
            "AnyStyle returned an unexpected reference payload.",
            http_status=503,
        )
    return first


def parse_reference_tags_batch(references: list[str]) -> list[dict[str, Any] | None]:
    """
    Parse multiple references in a single AnyStyle subprocess call.
    
    This is significantly faster than calling parse_reference_tags() in a loop
    because it avoids the overhead of spawning a subprocess for each reference.
    
    Args:
        references: List of raw reference strings to parse
        
    Returns:
        List of parsed tag dictionaries (or None for unparseable references),
        in the same order as the input references
    """
    if not references:
        return []
    
    # Filter and track which references are non-empty
    cleaned_refs: list[str] = []
    index_map: list[int] = []  # Maps cleaned_refs index to original references index
    
    for i, ref in enumerate(references):
        cleaned = ref.strip()
        if cleaned:
            cleaned_refs.append(cleaned)
            index_map.append(i)
    
    # If all references are empty, return all None
    if not cleaned_refs:
        return [None] * len(references)
    
    # Write all non-empty references to a single temp file
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        delete=True,
    ) as handle:
        for ref in cleaned_refs:
            handle.write(f"{ref}\n")
        handle.flush()
        command = _command_for_input(Path(handle.name))
        
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=ANYSTYLE_TIMEOUT_SEC * max(1, len(cleaned_refs) // 10),  # Scale timeout
                check=False,
                shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise ReferenceParsingError(
                "anystyle_timeout",
                f"AnyStyle batch parsing timed out for {len(cleaned_refs)} references.",
                http_status=503,
            ) from exc
        except OSError as exc:
            raise ReferenceParsingError(
                "anystyle_execution_failed",
                "AnyStyle could not be executed safely.",
                http_status=503,
            ) from exc
    
    if result.returncode != 0:
        details: dict[str, Any] = {"returncode": result.returncode}
        stderr = (result.stderr or "").strip()
        if stderr:
            details["stderr"] = stderr
        raise ReferenceParsingError(
            "anystyle_parse_failed",
            "AnyStyle batch parsing returned a non-zero exit status.",
            http_status=503,
            details=details,
        )
    
    try:
        payload = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ReferenceParsingError(
            "anystyle_invalid_output",
            "AnyStyle batch parsing returned malformed JSON.",
            http_status=503,
        ) from exc
    
    if not isinstance(payload, list):
        raise ReferenceParsingError(
            "anystyle_invalid_output",
            "AnyStyle batch parsing returned an unexpected JSON payload.",
            http_status=503,
        )
    
    # Map parsed results back to original reference positions
    results: list[dict[str, Any] | None] = [None] * len(references)
    
    for i, original_index in enumerate(index_map):
        if i < len(payload):
            item = payload[i]
            if isinstance(item, dict):
                results[original_index] = item
            # If not a dict, leave as None
    
    return results
