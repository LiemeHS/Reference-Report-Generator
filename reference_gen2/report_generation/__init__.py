"""Phase B / Phase 6: static sanitized HTML report generation."""

from .service import ReportGenerationError, StaticReportConfig, generate_html_report, render_html_report

__all__ = [
    "ReportGenerationError",
    "StaticReportConfig",
    "generate_html_report",
    "render_html_report",
]
