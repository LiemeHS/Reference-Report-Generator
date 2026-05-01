from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from reference_gen2.reference_styles import ReferenceStyleHint


@dataclass(frozen=True)
class SegmentationResult:
    reference_list_text: str
    references: list[str]
    warnings: list[str] = field(default_factory=list)
    style_hint_used: ReferenceStyleHint = "unknown"
    profile_used: str = "unknown_profile"


class ReferenceSegmentationError(Exception):
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
