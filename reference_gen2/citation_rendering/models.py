from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CitationRenderResult:
    """Sanitized rendered citation for report display."""

    text: str
    html: str
    style: str = "apa-standard"
    locale: str = "nl-NL"
    warnings: list[str] = field(default_factory=list)
    partial: bool = False
