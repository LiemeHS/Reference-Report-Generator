from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_report_generation import _report
from reference_gen2.report_generation import render_html_report


def test_saved_report_artifact_supports_styles_and_filters(tmp_path: Path) -> None:
    """Smoke test local report artifact rendering and interactive behavior."""

    playwright_sync_api = pytest.importorskip("playwright.sync_api")
    sync_playwright = playwright_sync_api.sync_playwright
    html = render_html_report(_report())
    report_path = tmp_path / "reference-report-debug.html"
    report_path.write_text(html, encoding="utf-8")

    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except Exception as exc:
            pytest.skip(f"Playwright browser unavailable: {exc}")

        context = browser.new_context(
            accept_downloads=True,
            viewport={"width": 1360, "height": 980},
        )
        page = context.new_page()
        page.goto(report_path.as_uri())

        root_bg = page.locator(":root").evaluate(
            "el => getComputedStyle(el).getPropertyValue('--bg').trim()"
        )
        assert root_bg in {"#f6f7f9", "rgb(246, 247, 249)", "rgba(246, 247, 249, 1)"}

        card = page.locator("article.reference-card").first
        assert not card.is_hidden()

        page.locator('[data-filter="needs_review"]').click()
        assert card.is_hidden()

        page.locator('[data-filter="all"]').click()
        page.locator("#reportSearch").fill("does-not-exist")
        assert card.is_hidden()

        page.locator("#reportSearch").fill("alpha")
        assert not card.is_hidden()

        page.locator('[data-view="basic"]').click()
        assert page.evaluate("() => document.body.classList.contains('view-basic')")
        page.locator('[data-view="full"]').click()

        with page.expect_download() as download_info:
            page.locator("#saveReportButton").click()
        download = download_info.value
        assert download.suggested_filename.endswith(".html")

        context.close()
        browser.close()
