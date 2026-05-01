"""Phase 6A: deterministic citation rendering for report-ready candidates."""

from .models import CitationRenderResult
from .service import render_candidate_citation

__all__ = [
    "CitationRenderResult",
    "render_candidate_citation",
]
