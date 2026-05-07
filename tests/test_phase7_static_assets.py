from __future__ import annotations

from pathlib import Path


STATIC_DIR = Path(__file__).resolve().parents[1] / "reference_gen2" / "api" / "static"


def test_phase7_index_has_separate_open_report_link():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert 'id="submit" type="submit"' in html
    assert 'id="text-submit" type="submit"' in html
    assert 'id="result"' in html
    assert 'id="open-report"' in html
    assert 'id="open-report" href="#" target="_blank" rel="noopener noreferrer"' in html
    assert "Rapport klaar" not in html
    assert "download" not in html
    assert 'id="download-report"' not in html
    assert "Open rapport" in html
    assert "Download rapportbestand" not in html


def test_phase7_frontend_open_report_uses_report_url_directly():
    script = (STATIC_DIR / "phase7.js").read_text(encoding="utf-8")

    assert "URL.createObjectURL(new Blob([html], { type: \"text/html\" }))" not in script
    assert "openReport.target = \"_blank\"" in script
    assert "openReport.rel = \"noopener noreferrer\"" in script
    assert "showResult(current.report_url)" in script
    assert "resetButton(activeButton || submit)" in script
    assert "requestChallengeProof" in script
    assert 'fetch("/challenge"' in script
    assert "challenge_required" in script
    assert "window.crypto.subtle.digest" in script
    assert 'setStatus("Klaar.' not in script
    assert "background: var(--teal)" in (STATIC_DIR / "phase7.css").read_text(encoding="utf-8")
    assert "fetch(reportUrl, { credentials: \"same-origin\" })" not in script
    assert "download" not in script
    assert "reportArchiveName" not in script
    assert "reportFilenameFromHtml" not in script
    assert "button.dataset.reportUrl" not in script
    assert "classList.contains(\"is-ready\")" not in script
    assert "downloadReport" not in script


def test_phase7_index_explains_essential_cookie_without_consent_gate():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    script = (STATIC_DIR / "phase7.js").read_text(encoding="utf-8")

    assert 'id="cookie-notice"' in html
    assert "Alleen noodzakelijke cookies" in html
    assert "reference_gen2_session" in html
    assert "geen analytics" in html
    assert "trackingcookies" in html
    assert "cookies van derden" in html
    assert "Maximaal 1 uur" in html
    assert "Als je cookies blokkeert" in html
    assert "Begrepen" in html
    assert "Meer informatie" in html
    assert "Accepteren" not in html
    assert "Weigeren" not in html
    assert "reference_gen2_cookie_notice_seen" in script
    assert "localStorage.setItem(COOKIE_NOTICE_STORAGE_KEY, \"1\")" in script
    assert "cookieDismiss.addEventListener(\"click\"" in script


def test_phase7_index_uses_user_facing_process_copy():
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    assert "tijdelijk op de server klaargezet" in html
    assert "maximaal 1 uur beschikbaar" in html
    assert "toont per bron" in html
    assert "Stijlherkenning" in html
    assert "referentiestijl te herkennen" in html
    assert "sessie/TTL" not in html
    assert "gesanitiseerde beoordeling" not in html
    assert "controleert de engine" not in html
    assert "segmentatie en parsing" not in html


def test_phase7_frontend_has_specific_upload_failure_messages():
    script = (STATIC_DIR / "phase7.js").read_text(encoding="utf-8")

    assert "page_limit_exceeded" in script
    assert "te veel pagina" in script
    assert "no_extractable_text" in script
    assert "OCR" in script
    assert "bibliography_heading_not_found" in script
