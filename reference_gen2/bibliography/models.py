from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class BibliographySection:
    heading: str | None
    heading_unit_index: int | None
    start_unit_index: int
    end_unit_index: int
    text: str
    warnings: list[str]


class BibliographyDetectionError(Exception):
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
