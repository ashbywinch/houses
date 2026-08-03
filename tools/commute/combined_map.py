"""Combined commute map — the transit shed + every driving isochrone on one page.

Reads the committed toolchain payloads and emits a single self-contained
Leaflet page (``data/commute/commute_map.html``) with one layer per isochrone:

- the **transit shed** (Pimlico & Aldgate) — the component outlines from
  ``union.json``;
- each **driving destination**'s shed (Dad, Bracknell, …) — the polygons from
  ``drive_searches.json``, with Rightmove links in their popups;
- destination markers.

Fully offline and deterministic (no timestamps): ``make commute-map``
regenerates the same bytes from the committed inputs. The page is meant to be
shared as a link — it is self-contained (Leaflet from CDN, data inline), so it
renders in a phone browser from any static host (e.g. htmlpreview.github.io on
a pushed branch).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

DEFAULT_UNION = Path("data/commute/union.json")
DEFAULT_DRIVE = Path("data/commute/drive_searches.json")
DEFAULT_OUT = Path("data/commute/commute_map.html")

# one colour per layer — transit blue, then a fixed palette for destinations
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]


def build_html(union: dict, drive: dict) -> str:
    """The combined map page — deterministic given the two payloads."""
    transit = [c["outline"] for c in union.get("components", [])]

    # group drive searches by destination, keeping the payload's order
    drive_by_label: dict[str, list[dict]] = {}
    for s in drive.get("searches", []):
        drive_by_label.setdefault(s["destination"]["label"], []).append(s)

    layers_js = []
    markers_js = []
    if transit:
        layers_js.append(
            json.dumps({"name": "Transit shed (Pimlico & Aldgate)", "color": _COLORS[0], "coords": transit, "urls": []})
        )
    for i, (label, searches) in enumerate(drive_by_label.items(), 1):
        color = _COLORS[i % len(_COLORS)]
        coords = [s["polygon"] for s in searches]
        urls = {json.dumps(s["polygon"]): s["rightmove_url"] for s in searches}
        layers_js.append(json.dumps({"name": f"{label} drive", "color": color, "coords": coords, "urls": urls}))
        d = searches[0]["destination"]
        markers_js.append(
            json.dumps({"label": label, "lat": d["lat"], "lon": d["lon"], "url": searches[0]["rightmove_url"]})
        )

    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Commute isochrones</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css">
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>html,body{margin:0;height:100%}#map{height:100%}</style></head>
<body><div id="map"></div>
<script>
const layers = __LAYERS__;
const markers = __MARKERS__;
const map = L.map('map');
const all = layers.flatMap(l => l.coords).flat();
if (all.length) { map.fitBounds(L.latLngBounds(all)); }
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 18, attribution: '&copy; OpenStreetMap'}).addTo(map);
const overlays = {};
for (const l of layers) {
  const group = L.layerGroup();
  for (const coords of l.coords) {
    const poly = L.polygon(coords, {color: l.color, weight: 3, fillOpacity: 0.12});
    const url = l.urls[JSON.stringify(coords)];
    if (url) { poly.bindPopup('<a href="' + url + '" target="_blank">Rightmove search</a>'); }
    poly.addTo(group);
  }
  group.addTo(map);
  overlays[l.name] = group;
}
L.control.layers(null, overlays, {collapsed: false}).addTo(map);
for (const m of markers) {  L.marker([m.lat, m.lon]).addTo(map)
    .bindPopup('<b>' + m.label + '</b><br><a href="' + m.url + '" target="_blank">Rightmove search</a>');
}
</script></body></html>
"""
    return html.replace("__LAYERS__", "[" + ",".join(layers_js) + "]").replace(
        "__MARKERS__", "[" + ",".join(markers_js) + "]"
    )


def write_map(html: str, out_path: str | Path) -> None:
    """Write the map without churning an identical committed artifact."""
    out_path = Path(out_path)
    if out_path.exists() and out_path.read_text() == html:
        return
    out_path.write_text(html)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the combined commute isochrone map (offline).")
    parser.add_argument("--union", default=str(DEFAULT_UNION))
    parser.add_argument("--drive", default=str(DEFAULT_DRIVE))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args(argv)

    union_path, drive_path = Path(args.union), Path(args.drive)
    for path, hint in ((union_path, "make commute-searches"), (drive_path, "make commute-drive")):
        if not path.exists():
            print(f"{path} not found — run '{hint}' first", file=sys.stderr)
            return 1
    union = json.loads(union_path.read_text())
    drive = json.loads(drive_path.read_text())
    html = build_html(union, drive)
    write_map(html, args.out)
    print(f"combined commute map → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
