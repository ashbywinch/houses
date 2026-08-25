"""E2E: the combined commute map actually RENDERS — it is not blank.

Regression for the blank-map bug class: a `#map` div without an explicit
height makes Leaflet render into a 0px container (the page looks blank while
the DOM still contains polygons), and a syntax error in an inline script is
silently skipped by the browser. Both slipped past unit tests that only
asserted string presence. This test loads the real generated page in a phone
viewport and asserts it is visible.

Run with:  make test-e2e   (requires: uv run playwright install chromium)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.commute.combined_map import _CSS_IMAGES, _JS_ICONS, VENDOR_DIR, MapAssets, _data_uri, build_html

pytestmark = pytest.mark.e2e

ROOT = Path(__file__).resolve().parents[2]


def _build_map(tmp_path: Path) -> Path:
    union = json.loads((ROOT / "data/commute/union.json").read_text())
    drive = json.loads((ROOT / "data/commute/drive_searches.json").read_text())
    icons = {name: _data_uri(VENDOR_DIR / name) for name in _CSS_IMAGES + _JS_ICONS}
    html = build_html(
        union,
        drive,
        assets=MapAssets(
            leaflet_js=(VENDOR_DIR / "leaflet.js").read_text(),
            leaflet_css=(VENDOR_DIR / "leaflet.css").read_text(),
            icons=icons,
        ),
    )
    path = tmp_path / "commute_map.html"
    path.write_text(html)
    return path


def _page():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch()
    except Exception:  # noqa: BLE001 — missing browser binary is an env issue
        pw.stop()
        pytest.skip("playwright chromium not installed — run 'uv run playwright install chromium'")
    page = browser.new_page(viewport={"width": 390, "height": 844})
    return pw, browser, page


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_commute_map_renders_visible_in_phone_viewport(tmp_path):
    pw, browser, page = _page()
    try:
        page.goto(_build_map(tmp_path).as_uri(), wait_until="load")
        page.wait_for_selector(".leaflet-overlay-pane path", timeout=15000)
        assert page.evaluate("typeof window.L !== 'undefined'")
        # visibility, not DOM presence: a 0-height map div renders nothing
        assert page.evaluate("document.getElementById('map').clientHeight") > 0
        assert page.evaluate("document.querySelectorAll('.leaflet-overlay-pane path').length") >= 3
        assert page.evaluate("document.querySelectorAll('.leaflet-marker-icon').length") >= 2
        # layer control lists all three isochrones
        labels = page.evaluate(
            "Array.from(document.querySelectorAll('.leaflet-control-layers-overlays label span'))"
            ".map(e => e.textContent.trim())"
        )
        assert any("Train: Pimlico & Aldgate" in t for t in labels)
        assert any("Drive to Dad" in t for t in labels)
        assert any("Drive to Bracknell" in t for t in labels)
    finally:
        browser.close()
        pw.stop()


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_commute_map_debug_panel_reports_health(tmp_path):
    """?debug=1 must surface diagnostics on the page itself (no console needed)."""
    pw, browser, page = _page()
    try:
        page.goto(_build_map(tmp_path).as_uri() + "?debug=1", wait_until="load")
        page.wait_for_selector("div[style*='z-index: 100000']", timeout=5000)
        text = page.evaluate("document.body.innerText")
        assert "url:" in text and "leaflet:" in text and "errors:" in text
        assert "MISSING" not in text.split("leaflet:")[1].split("\n")[0]
    finally:
        browser.close()
        pw.stop()


# lucidlint: ignore fakefs deterministic tmp_path test — the house testing standard (no pyfakefs)
def test_drive_polygon_popup_links_to_rightmove(tmp_path):
    """Regression: drive/intersection polygons must have their Rightmove popup
    bound. The lookup keys must match JS JSON.stringify, or popups silently
    never bind (markers still work, so the breakage is invisible)."""
    pw, browser, page = _page()
    try:
        page.goto(_build_map(tmp_path).as_uri(), wait_until="load")
        page.wait_for_selector(".leaflet-overlay-pane path", timeout=15000)
        # a synthetic click ON the Dad polygon element (green #3a3) — this
        # exercises the popup binding, not which layer is topmost under the cursor
        clicked = page.evaluate(
            """() => {
                const p = Array.from(document.querySelectorAll('.leaflet-overlay-pane path'))
                    .find(el => el.getAttribute('stroke') === '#3a3');
                if (!p) return 'no-dad-polygon';
                p.dispatchEvent(new MouseEvent('click', {bubbles: true, clientX: 0, clientY: 0}));
                return 'clicked';
            }"""
        )
        assert clicked == "clicked"
        page.wait_for_selector(".leaflet-popup-content a", timeout=5000)
        href = page.get_attribute(".leaflet-popup-content a", "href")
        assert href is not None and "rightmove.co.uk" in href
        popup_text = page.inner_text(".leaflet-popup-content")
        assert "min drive" in popup_text  # the search name, not an unnamed link
    finally:
        browser.close()
        pw.stop()
