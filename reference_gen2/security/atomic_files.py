from __future__ import annotations

import os
from pathlib import Path
import tempfile

PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


def ensure_private_dir(path: Path) -> None:
    """Create a directory for private runtime artifacts."""
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(target, PRIVATE_DIR_MODE)
    except OSError:
        pass


def atomic_write_text(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int = PRIVATE_FILE_MODE,
) -> None:
    """Write a private text file atomically within the target directory."""
    _atomic_write(path, content.encode(encoding), mode=mode)


def atomic_write_bytes(path: Path, content: bytes, *, mode: int = PRIVATE_FILE_MODE) -> None:
    """Write a private binary file atomically within the target directory."""
    _atomic_write(path, content, mode=mode)


def _atomic_write(path: Path, content: bytes, *, mode: int) -> None:
    target = Path(path)
    ensure_private_dir(target.parent)
    fd, temp_path = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, target)
        try:
            os.chmod(target, mode)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temp_path)
        except FileNotFoundError:
            pass
        raise
