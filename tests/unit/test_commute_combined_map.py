"""Combined commute map — transit + driving isochrones on one Leaflet page."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from tools.commute.combined_map import build_html, write_map

UNION = {
    "components": [
        {"outline": [[51.5, -0.5], [51.5, -0.4], [51.6, -0.4], [51.6, -0.5]], "rightmove_url": "u1"},
        {"outline": [[52.0, 0.0], [52.0, 0.1], [52.1, 0.1], [52.1, 0.0]], "rightmove_url": "u2"},
    ]
}
DRIVE = {
    "metadata": {"destinations": ["Dad", "Bracknell"], "count": 2},
    "searches": [
        {
            "id": "drive-dad-090",
            "name": "Dad — 90 min drive",
            "polygon": [[51.9, -1.6], [51.9, -1.5], [52.0, -1.5], [52.0, -1.6]],
            "rightmove_url": "https://rm/dad",
            "destination": {"label": "Dad", "postcode": "OX7 5GZ", "lat": 51.94, "lon": -1.55},
            "threshold_min": 90,
        },
        {
            "id": "drive-bracknell-090",
            "name": "Bracknell — 90 min drive",
            "polygon": [[51.4, -0.8], [51.4, -0.7], [51.5, -0.7], [51.5, -0.8]],
            "rightmove_url": "https://rm/bracknell",
            "destination": {"label": "Bracknell", "postcode": "RG12 8YA", "lat": 51.41, "lon": -0.77},
            "threshold_min": 90,
        },
    ],
}
LEAFLET_JS = "var L = window.L || {}; /* leaflet stub */"
LEAFLET_CSS = ".leaflet-container{background:#fff} .leaflet-control-layers-toggle{background:url(images/layers.png)}"
ICONS = {
    "layers.png": "data:image/png;base64,AAA",
    "layers-2x.png": "data:image/png;base64,BBB",
    "marker-icon.png": "data:image/png;base64,CCC",
    "marker-icon-2x.png": "data:image/png;base64,DDD",
    "marker-shadow.png": "data:image/png;base64,EEE",
}


def _html(
    *,
    leaflet_js: str = LEAFLET_JS,
    leaflet_css: str = LEAFLET_CSS,
    icons: dict[str, str] = ICONS,
    intersection: dict | None = None,
) -> str:
    return build_html(
        UNION, DRIVE, leaflet_js=leaflet_js, leaflet_css=leaflet_css, icons=icons, intersection=intersection
    )


def test_build_html_embeds_all_three_isochrones():
    html = _html()
    # three named layers in user language: train + two drives
    assert "Train: Pimlico & Aldgate" in html
    assert "Drive to Dad" in html
    assert "Drive to Bracknell" in html
    # no internal jargon in any user-facing text (title, layers, popups)
    assert "isochrone" not in html.lower()


def test_polygon_popup_urls_attached_directly():
    """Regression: popup URLs are attached to each polygon, never looked up
    by a serialised-polygon key (Python json.dumps(52.0) != JS
    JSON.stringify(52.0) — a whole class of silent popup-loss bugs)."""
    html = _html()
    assert '"url": "https://rm/dad"' in html
    assert '"url": "https://rm/bracknell"' in html
    assert '"url": "https://rm/all"' in _html(intersection=INTERSECTION)
    assert "JSON.stringify" not in html  # no serialisation-key lookup remains
    assert "l.urls" not in html
    # every outline and polygon present as JSON
    import json as _json

    for coords in [c["outline"] for c in UNION["components"]] + [s["polygon"] for s in DRIVE["searches"]]:
        assert _json.dumps(coords) in html
    # destination markers with Rightmove links
    assert '"label": "Dad"' in html and "https://rm/dad" in html
    assert '"label": "Bracknell"' in html


def test_build_html_inlines_leaflet_and_icons():
    html = _html()
    assert "var L = window.L || {};" in html  # leaflet JS inlined, not a CDN <script src>
    assert '<script src=' not in html
    assert '<link rel="stylesheet"' not in html
    # CSS image references replaced with data URIs
    assert "url(images/layers.png)" not in html
    assert "url(data:image/png;base64,AAA)" in html
    # JS default icon options embedded
    assert "iconUrl: 'data:image/png;base64,CCC'" in html
    assert "iconRetinaUrl: 'data:image/png;base64,DDD'" in html


def test_build_html_gives_the_map_a_height():
    # regression: a #map div without an explicit height renders an invisible
    # (blank) map — the page style must survive generation
    html = _html()
    assert "#map{height:100%" in html
    assert "html,body{margin:0;height:100%}" in html


def test_build_html_has_debug_panel_guard():
    html = _html()
    assert "location.search.indexOf('debug')" in html  # ?debug=1 diagnostics
    assert "flatMap" not in html  # ES2019 methods would blank older mobile browsers
    assert ".flat()" not in html
    # the debug panel joins lines with a JS newline ESCAPE — a literal newline
    # inside the string literal is a syntax error the browser silently skips
    assert "join('\\n')" in html
    assert "join('\n')" not in html


def test_inline_scripts_are_valid_javascript(tmp_path):
    """Every generated <script> body must parse as JS.

    Regression for the blank-map bug: a mangled escape (a literal newline in a
    string literal) made the debug script a syntax error, which the browser
    silently skipped while the rest of the page still rendered. String-presence
    tests cannot catch this — only parsing can.
    """

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not installed (frontend toolchain dependency)")
    scripts = re.findall(r"<script>(.*?)</script>", _html(), re.S)
    assert len(scripts) == 3  # debug panel, leaflet, map
    for i, src in enumerate(scripts):
        path = tmp_path / f"script_{i}.js"
        path.write_text(src)
        subprocess.run([node, "--check", str(path)], check=True, capture_output=True)


def test_build_html_is_deterministic():
    assert _html() == _html()


INTERSECTION = {
    "metadata": {"count": 1},
    "searches": [
        {
            "id": "intersection-090",
            "name": "All commutes",
            "polygon": [[51.0, -1.1], [51.0, -1.0], [51.1, -1.0], [51.1, -1.1]],
            "rightmove_url": "https://rm/all",
        }
    ],
}


def test_build_html_adds_intersection_layer():
    html = _html(intersection=INTERSECTION)
    assert "Where we could live" in html
    assert '"url": "https://rm/all"' in html


def test_build_html_without_intersection_has_no_layer():
    assert "Where we could live" not in _html()


def test_write_map_does_not_churn_identical(tmp_path):
    out = tmp_path / "commute_map.html"
    write_map("abc", out)
    assert out.read_text() == "abc"
    write_map("abc", out)  # identical → no rewrite
    assert out.read_text() == "abc"
