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


def test_build_html_embeds_all_three_isochrones():
    html = build_html(UNION, DRIVE)
    # three named layers: transit + two drives
    assert "Transit shed (Pimlico & Aldgate)" in html
    assert "Dad drive" in html
    assert "Bracknell drive" in html
    # every outline and polygon present as JSON
    for coords in [c["outline"] for c in UNION["components"]] + [s["polygon"] for s in DRIVE["searches"]]:
        assert json_dumps(coords) in html
    # destination markers with Rightmove links
    assert '"label": "Dad"' in html and "https://rm/dad" in html
    assert '"label": "Bracknell"' in html


def test_build_html_is_deterministic():
    assert build_html(UNION, DRIVE) == build_html(UNION, DRIVE)


def test_write_map_does_not_churn_identical(tmp_path):
    out = tmp_path / "commute_map.html"
    write_map("abc", out)
    assert out.read_text() == "abc"
    write_map("abc", out)  # identical → no rewrite
    assert out.read_text() == "abc"


def json_dumps(coords):
    import json

    return json.dumps(coords)
