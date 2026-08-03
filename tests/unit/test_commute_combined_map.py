"""Combined commute map — transit + driving isochrones on one Leaflet page."""

from __future__ import annotations

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


def _html(*, leaflet_js: str = LEAFLET_JS, leaflet_css: str = LEAFLET_CSS, icons: dict[str, str] = ICONS) -> str:
    return build_html(UNION, DRIVE, leaflet_js=leaflet_js, leaflet_css=leaflet_css, icons=icons)


def test_build_html_embeds_all_three_isochrones():
    html = _html()
    # three named layers: transit + two drives
    assert "Transit shed (Pimlico & Aldgate)" in html
    assert "Dad drive" in html
    assert "Bracknell drive" in html
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


def test_build_html_is_deterministic():
    assert _html() == _html()


def test_write_map_does_not_churn_identical(tmp_path):
    out = tmp_path / "commute_map.html"
    write_map("abc", out)
    assert out.read_text() == "abc"
    write_map("abc", out)  # identical → no rewrite
    assert out.read_text() == "abc"
