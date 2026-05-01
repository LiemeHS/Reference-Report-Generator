from __future__ import annotations

from pathlib import Path

from reference_gen2.security.atomic_files import atomic_write_text


def test_atomic_write_text_creates_and_overwrites_target(tmp_path: Path):
    target = tmp_path / "nested" / "payload.txt"

    atomic_write_text(target, "first")
    atomic_write_text(target, "second")

    assert target.read_text(encoding="utf-8") == "second"
    assert list(target.parent.glob("*.tmp")) == []
