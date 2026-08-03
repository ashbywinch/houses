"""Combined commute map — the transit shed + every driving isochrone on one page.

Reads the committed toolchain payloads and emits a single **fully
self-contained** Leaflet page (``data/commute/commute_map.html``) with one
layer per isochrone:

- the **transit shed** (Pimlico & Aldgate) — the component outlines from
  ``union.json``;
- each **driving destination**'s shed (Dad, Bracknell, …) — the polygons from
  ``drive_searches.json``, with Rightmove links in their popups;
- destination markers.

Why self-contained: the page is meant to be shared as a phone link from a
static host. Public HTML-preview services (e.g. htmlpreview.github.io) serve
the file through a proxy that **drops external CDN scripts** — so Leaflet is
vendored (``tools/commute/vendor/``) and inlined, and every icon is embedded
as a data URI. The same file works from file://, any static host, or GitHub
Pages. Fully offline and deterministic (no timestamps, committed inputs):
``make commute-map`` regenerates the same bytes.
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path

DEFAULT_UNION = Path("data/commute/union.json")
DEFAULT_DRIVE = Path("data/commute/drive_searches.json")
DEFAULT_OUT = Path("data/commute/commute_map.html")
VENDOR_DIR = Path("tools/commute/vendor")

# one colour per layer — transit blue, then a fixed palette for destinations
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]

# CSS background images in leaflet.css (layer-control toggle + default marker)
_CSS_IMAGES = ("layers-2x.png", "layers.png", "marker-icon.png")
# JS default icon options (Leaflet resolves these relative to the page, which
# breaks on a static host — embedded here instead)
_JS_ICONS = ("marker-icon.png", "marker-icon-2x.png", "marker-shadow.png")


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def build_html(union: dict, drive: dict, *, leaflet_js: str, leaflet_css: str, icons: dict[str, str]) -> str:
    """The combined map page — deterministic given the payloads and assets.

    ``icons`` maps a filename (e.g. ``marker-icon.png``) to a data URI.
    """
    transit = [c["outline"] for c in union.get("components", [])]

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

    css = leaflet_css
    for name in _CSS_IMAGES:
        css = css.replace(f"url(images/{name})", f"url({icons[name]})")
    icon_option = {
        "marker-icon.png": "iconUrl",
        "marker-icon-2x.png": "iconRetinaUrl",
        "marker-shadow.png": "shadowUrl",
    }
    icon_opts = ",\n  ".join(f"{icon_option[name]}: '{icons[name]}'" for name in _JS_ICONS)

    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Commute isochrones</title>
<style>__CSS__</style></head>
<body><div id="map"></div>
<script>__LEAFLET_JS__</script>
<script>
L.Icon.Default.mergeOptions({
  __ICON_OPTS__
});
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
for (const m of markers) {
  L.marker([m.lat, m.lon]).addTo(map)
    .bindPopup('<b>' + m.label + '</b><br><a href="' + m.url + '" target="_blank">Rightmove search</a>');
}
</script></body></html>
"""
    return (
        html.replace("__CSS__", css)
        .replace("__LEAFLET_JS__", leaflet_js)
        .replace("__ICON_OPTS__", icon_opts)
        .replace("__LAYERS__", "[" + ",".join(layers_js) + "]")
        .replace("__MARKERS__", "[" + ",".join(markers_js) + "]")
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
    parser.add_argument("--vendor", default=str(VENDOR_DIR))
    args = parser.parse_args(argv)

    union_path, drive_path = Path(args.union), Path(args.drive)
    for path, hint in ((union_path, "make commute-searches"), (drive_path, "make commute-drive")):
        if not path.exists():
            print(f"{path} not found — run '{hint}' first", file=sys.stderr)
            return 1
    vendor = Path(args.vendor)
    icons = {name: _data_uri(vendor / name) for name in _CSS_IMAGES + _JS_ICONS}
    html = build_html(
        json.loads(union_path.read_text()),
        json.loads(drive_path.read_text()),
        leaflet_js=(vendor / "leaflet.js").read_text(),
        leaflet_css=(vendor / "leaflet.css").read_text(),
        icons=icons,
    )
    write_map(html, args.out)
    print(f"combined commute map → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
