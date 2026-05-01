from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import asdict

from tests.conftest import build_docx_bytes

from reference_gen2.reference_parsing import parse_references
from reference_gen2.reference_segmentation import segment_references
from reference_gen2.services.document_pipeline import run_phase1_pipeline


def _real_anystyle_executable() -> str | None:
    configured = os.getenv("REFERENCE_GEN2_ANYSTYLE_EXECUTABLE", "").strip()
    if configured and os.path.isfile(configured):
        return configured
    if configured and shutil.which(configured):
        return configured
    discovered = shutil.which("anystyle")
    if discovered:
        return discovered
    candidate = os.path.expanduser("~/.local/share/gem/ruby/3.2.0/bin/anystyle")
    if os.path.isfile(candidate):
        return candidate
    return None


def _sample_docx_bytes() -> bytes:
    return build_docx_bytes(
        [
            ("Introduction", "Heading 1"),
            ("Body content", "Normal"),
            ("References", "Heading 1"),
            (
                "Smith, J. (2020). Some title. Journal Name, 5(2), 10-20. doi:10.1234/test.article. "
                "Extended note for stable bibliography detection coverage in the smoke helper.",
                "Normal",
            ),
            (
                "Doe, J. (2021). Another title. Other Journal, 3(1), 5-10. https://doi.org/10.5678/other. "
                "Additional descriptive text keeps the end-to-end sample comfortably above the Phase 1 minimum.",
                "Normal",
            ),
            (
                "Gamma, G. (2019). Third reference for bibliography detection coverage with enough detail to preserve "
                "stable extraction, segmentation, and parsing output across the full chain.",
                "Normal",
            ),
        ]
    )


def main() -> int:
    executable = _real_anystyle_executable()
    if executable is None:
        print("AnyStyle CLI is not available. Set PATH or REFERENCE_GEN2_ANYSTYLE_EXECUTABLE.")
        return 1

    os.environ["REFERENCE_GEN2_ANYSTYLE_EXECUTABLE"] = executable

    with tempfile.TemporaryDirectory(prefix="reference_gen2_phase123_") as temp_dir:
        os.environ["REFERENCE_GEN2_UPLOAD_TMP_DIR"] = temp_dir

        phase1 = run_phase1_pipeline(
            "phase123.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            _sample_docx_bytes(),
        )
        segmented = segment_references(phase1.bibliography, phase1.extraction)
        parsed = parse_references(segmented.references)

    print("Phase 1")
    print(f"  kind: {phase1.upload.detected_kind}")
    print(f"  heading: {phase1.bibliography.heading}")
    print(f"  bibliography chars: {len(phase1.bibliography.text)}")
    print()

    print("Phase 2")
    for index, reference in enumerate(segmented.references, start=1):
        print(f"  [{index}] {reference}")
    print()

    print("Phase 3")
    for index, result in enumerate(parsed, start=1):
        print(f"  [{index}] warnings={result.warnings}")
        print(
            json.dumps(
                asdict(result),
                indent=2,
                ensure_ascii=False,
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
