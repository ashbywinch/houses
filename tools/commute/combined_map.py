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
import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.commute.drive_isochrone import MapAssets, js_safe_json, user_label
from tools.commute.payload_checks import fail

# one colour per layer — the transit layer always takes _COLORS[0], so the
# drive layers cycle through the rest (index 0 is the transit colour).
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]
_DRIVE_COLORS = _COLORS[1:]

logger = logging.getLogger(__name__)

DEFAULT_UNION = Path("data/commute/union.json")
DEFAULT_DRIVE = Path("data/commute/drive_searches.json")
DEFAULT_INTERSECTION = Path("data/commute/intersection.json")
DEFAULT_OUT = Path("data/commute/commute_map.html")
VENDOR_DIR = Path("tools/commute/vendor")


# CSS background images in leaflet.css (layer-control toggle + default marker)
_CSS_IMAGES = ("layers-2x.png", "layers.png", "marker-icon.png")
# JS default icon options (Leaflet resolves these relative to the page, which
# breaks on a static host — embedded here instead)
_JS_ICONS = ("marker-icon.png", "marker-icon-2x.png", "marker-shadow.png")


@dataclass(frozen=True)
class _LeafletPolygon:
    """One polygon entry of a Leaflet layer, serialized into the page."""

    coords: list[tuple[float, float]]
    url: str
    name: str

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape — owned here (coding-standards.md)
        return dict(coords=self.coords, url=self.url, name=self.name)


@dataclass(frozen=True)
class _LeafletLayer:
    """One Leaflet layer config serialized into the map page JS."""

    name: str
    color: str
    polygons: list[_LeafletPolygon]
    fill_opacity: float | None = None
    weight: int | None = None

    # lucidlint: ignore record-shape to_dict IS the serialization boundary — wire shape owned here (coding-standards.md)
    def to_dict(self) -> dict:
        # lucidlint: ignore record-shape to_dict construction mirrors the wire shape — owned here (coding-standards.md)
        d: dict = dict(name=self.name, color=self.color, polygons=[p.to_dict() for p in self.polygons])
        if self.fill_opacity is not None:
            d["fillOpacity"] = self.fill_opacity
        if self.weight is not None:
            d["weight"] = self.weight
        return d


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def build_html(  # lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape
    union: dict,
    drive: dict,
    assets: MapAssets,
    intersection: dict | None = None,
) -> str:
    """The combined map page — deterministic given the payloads and assets.

    ``assets`` carries the vendored Leaflet JS/CSS and the filename → data-URI
    icon map. ``intersection`` (optional) is the all-commutes payload from
    ``intersection.py`` — its polygons get their own top layer.
    """
    transit = [c["outline"] for c in union.get("components", [])]

    drive_by_label: dict[str, list[dict]] = {}
    # lucidlint: ignore loop-pipeline group-by/control-flow — comprehension cannot express
    for s in drive.get("searches", []):
        drive_by_label.setdefault(s["destination"]["label"], []).append(s)

    layers_js = []
    markers_js = []
    if transit:
        transit_layer = _LeafletLayer(
            name="Train: Pimlico & Aldgate",
            color=_COLORS[0],
            polygons=[_LeafletPolygon(coords=outline, url="", name="") for outline in transit],
        )
        layers_js.append(js_safe_json(transit_layer.to_dict()))
    for i, (label, searches) in enumerate(drive_by_label.items(), 1):
        color = _DRIVE_COLORS[(i - 1) % len(_DRIVE_COLORS)]
        # the search NAME is user-controlled (built from the destination label)
        # — HTML-escape it for the popup innerHTML
        polygons = [
            _LeafletPolygon(coords=s["polygon"], url=s["rightmove_url"], name=user_label(s["name"]))
            for s in searches
        ]
        # each polygon carries its own url — never keyed by a serialised
        # polygon string (Python json.dumps(52.0) != JS JSON.stringify(52.0),
        # a whole class of silent popup-loss bugs)
        drive_layer = _LeafletLayer(
            name=f"Drive to {user_label(label)}", color=color, polygons=polygons
        )
        layers_js.append(js_safe_json(drive_layer.to_dict()))
        d = searches[0]["destination"]
        markers_js.append(
            js_safe_json(
                # lucidlint: ignore record-shape Leaflet marker object serialized into the page (coding-standards.md)
                dict(
                    label=user_label(label), lat=d["lat"], lon=d["lon"],
                    url=searches[0]["rightmove_url"],
                )
            )
        )
    if intersection and intersection.get("searches"):
        polygons = [
            _LeafletPolygon(coords=s["polygon"], url=s["rightmove_url"], name=user_label(s["name"]))
            for s in intersection["searches"]
        ]
        intersection_layer = _LeafletLayer(
            name="Where we could live", color="#c90", polygons=polygons,
            fill_opacity=0.25, weight=4,
        )
        layers_js.append(js_safe_json(intersection_layer.to_dict()))

    css = assets.leaflet_css
    for name in _CSS_IMAGES:
        css = css.replace(f"url(images/{name})", f"url({assets.icons[name]})")
    icon_option = {
        "marker-icon.png": "iconUrl",
        "marker-icon-2x.png": "iconRetinaUrl",
        "marker-shadow.png": "shadowUrl",
    }
    icon_opts = ",\n  ".join(f"{icon_option[name]}: '{assets.icons[name]}'" for name in _JS_ICONS)

    html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Commute map</title>
<style>html,body{margin:0;height:100%}#map{height:100%;background:#e8eef4}__CSS__</style></head>
<body><div id="map"></div>
<script>
(function () {
  // debug panel: ?debug=1 shows what the page actually sees — no console needed
  var errors = [];
  window.addEventListener('error', function (e) { errors.push(e.message || String(e.error)); });
  window.addEventListener('unhandledrejection', function (e) { errors.push('rejection: ' + e.reason); });
  if (location.search.indexOf('debug') === -1) { return; }
  var el = document.createElement('div');
  el.style.cssText = 'position:fixed;left:8px;right:8px;bottom:8px;z-index:100000;max-height:45%;overflow:auto;' +
    'background:rgba(0,0,0,.85);color:#fff;font:11px/1.4 monospace;padding:8px;white-space:pre-wrap;' +
    'border-radius:6px;box-sizing:border-box';
  var close = document.createElement('button');
  close.textContent = 'x';
  close.style.cssText = 'position:absolute;top:4px;right:8px;background:none;border:none;color:#fff;' +
    'font-size:14px;cursor:pointer';
  close.onclick = function () { el.remove(); };
  el.appendChild(close);
  var body = document.createElement('div');
  el.appendChild(body);
  document.body.appendChild(el);
  var tileProbe = { loaded: 0, failed: 0 };
  var probe = new Image();
  probe.onload = function () { tileProbe.loaded = 1; refresh(); };
  probe.onerror = function () { tileProbe.failed = 1; refresh(); };
  probe.src = 'https://tile.openstreetmap.org/0/0/0.png';
  function refresh() {
    var tiles = document.querySelectorAll('img.leaflet-tile');
    var broken = 0;
    for (var i = 0; i < tiles.length; i++) { if (tiles[i].complete && tiles[i].naturalWidth === 0) { broken++; } }
    body.textContent = [
      'url: ' + location.href,
      'ua: ' + navigator.userAgent,
      'leaflet: ' + (typeof window.L !== 'undefined' ? 'loaded' : 'MISSING'),
      'polygons: ' + document.querySelectorAll('.leaflet-overlay-pane path').length,
      'markers: ' + document.querySelectorAll('.leaflet-marker-icon').length,
      'tiles: ' + tiles.length + ' (' + broken + ' broken)',
      'tile probe: ' + (tileProbe.loaded ? 'reachable' : tileProbe.failed ? 'FAILED' : 'pending'),
      'errors: ' + (errors.length ? errors.join(' | ') : 'none'),
    ].join('\\n');
  }
  window.addEventListener('load', function () { setTimeout(refresh, 2500); });
  refresh();
})();
</script>
<script>__LEAFLET_JS__</script>
<script>
L.Icon.Default.mergeOptions({
  __ICON_OPTS__
});
const layers = __LAYERS__;
const markers = __MARKERS__;
const map = L.map('map');
const all = [];
for (const l of layers) { for (const item of l.polygons) { for (const p of item.coords) { all.push(p); } } }
if (all.length) { map.fitBounds(L.latLngBounds(all)); }
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  {maxZoom: 18, attribution: '&copy; OpenStreetMap'}).addTo(map);
const overlays = {};
for (const l of layers) {
  const group = L.layerGroup();
  for (const item of l.polygons) {
    const poly = L.polygon(item.coords, {
      color: l.color,
      weight: l.weight || 3,
      fillOpacity: l.fillOpacity || 0.12,
    });
    if (item.url) {
      poly.bindPopup('<b>' + item.name + '</b><br><a href="' + item.url + '" target="_blank">Open on Rightmove</a>');
    }
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
        .replace("__LEAFLET_JS__", assets.leaflet_js)
        .replace("__ICON_OPTS__", icon_opts)
        .replace("__LAYERS__", "[" + ",".join(layers_js) + "]")
        .replace("__MARKERS__", "[" + ",".join(markers_js) + "]")
    )


def write_map(html: str, out_path: str | Path) -> None:
    """Write the map without churning an identical committed artifact —
    atomically (tmp + os.replace): a phone loading the map mid-regeneration
    sees the old or the new file, never a truncated one."""
    out_path = Path(out_path)
    if out_path.exists() and out_path.read_text() == html:
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp.write_text(html)
    os.replace(tmp, out_path)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _load_intersection(path: Path) -> dict | None:
    """Saved intersection payload, or None when absent/unreadable (map renders without it)."""
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        print("The saved all-commutes data is unreadable — showing the map without it.", file=sys.stderr)
        logger.warning("%s unreadable — omitting the intersection layer", path)
        return None



# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _load_intersection_layer(path: Path) -> dict | None:
    """The gold 'Where we could live' layer, degraded to None with a warning
    when absent, empty, or malformed (the map renders without it — but never
    silently, so an empty intersection isn't mistaken for a complete map)."""
    intersection = _load_intersection(path)
    if intersection is None:
        return None
    if not (isinstance(intersection, dict) and intersection.get("searches")):
        print(
            "warning: the 'Where we could live' layer is missing — the saved all-commutes data has no "
            "areas (run 'make commute-intersection' to check)",
            file=sys.stderr,
        )
        logger.warning("%s has no searches — omitting the 'Where we could live' layer", path)
        return None
    if not all(
        isinstance(s, dict) and "polygon" in s and "rightmove_url" in s and "name" in s
        for s in intersection["searches"]
    ):
        # records missing expected keys would crash build_html — degrade
        # to a map without the layer instead of aborting the whole build
        # lucidlint: ignore duplicate-block the two malformed-payload warnings intentionally mirror each other —
        print(
            "warning: the saved all-commutes data is malformed — showing the map without it.",
            file=sys.stderr,
        )
        logger.warning("%s has malformed search records — omitting the 'Where we could live' layer", path)
        return None
    return intersection


@dataclass(frozen=True)
class _RenderedMap:
    """The loaded payloads plus the rendered page — main's report bundle."""

    union: dict
    drive: dict
    html: str


def _build_map(union_path: Path, drive_path: Path, vendor: Path, intersection: dict | None) -> _RenderedMap:
    """Load the payloads + vendor assets and render the page.

    build_html indexes the payloads unconditionally — a structurally wrong
    (but valid-JSON) artifact raises KeyError/TypeError here, which main's
    fail handler reports as a two-tier exit.
    """
    union = json.loads(union_path.read_text())
    drive = json.loads(drive_path.read_text())
    assets = MapAssets(
        leaflet_js=(vendor / "leaflet.js").read_text(),
        leaflet_css=(vendor / "leaflet.css").read_text(),
        icons={name: _data_uri(vendor / name) for name in _CSS_IMAGES + _JS_ICONS},
    )
    html = build_html(
        union,
        drive,
        assets,
        intersection=intersection,
    )
    return _RenderedMap(union=union, drive=drive, html=html)


# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
# lucidlint: ignore record-shape wire-format dict — serialization boundary owns the shape (coding-standards.md)
def _warn_empty_layers(drive: dict, union: dict, drive_path: Path, union_path: Path) -> None:
    """A map missing the drive/union layers must not be mistaken for complete."""
    if isinstance(drive, dict) and not drive.get("searches"):
        print(
            "warning: no drive sheds found — the map shows only the train area (run 'make commute-drive' if "
            "unexpected)",
            file=sys.stderr,
        )
        logger.warning("%s has no searches — drive layers omitted", drive_path)
    # lucidlint: ignore duplicate-block the two empty-layer warnings intentionally mirror each other — one per missing
    if isinstance(union, dict) and not union.get("components"):
        print(
            "warning: no train area found — the map may be incomplete (run 'make commute-searches' if "
            "unexpected)",
            file=sys.stderr,
        )
        logger.warning("%s has no components — train layer omitted", union_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the combined commute isochrone map (offline).")
    parser.add_argument("--union", default=str(DEFAULT_UNION))
    parser.add_argument("--drive", default=str(DEFAULT_DRIVE))
    parser.add_argument("--intersection", default=str(DEFAULT_INTERSECTION))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--vendor", default=str(VENDOR_DIR))
    args = parser.parse_args(argv)

    union_path, drive_path = Path(args.union), Path(args.drive)
    missing = [
        (path, hint)
        for path, hint in ((union_path, "make commute-searches"), (drive_path, "make commute-drive"))
        if not path.exists()
    ]
    if missing:
        hints = list(dict.fromkeys(h for _, h in missing))
        return fail(
            f"Commute data is missing — run {' and '.join(hints)} first.",
            f"combined map inputs not found: {', '.join(str(p) for p, _ in missing)}",
        )
    vendor = Path(args.vendor)
    intersection = _load_intersection_layer(Path(args.intersection))
    try:
        rendered = _build_map(union_path, drive_path, vendor, intersection)
    except (json.JSONDecodeError, OSError, KeyError, TypeError, AttributeError) as e:
        return fail(
            "The commute data or map assets are unreadable — regenerate them with 'make commute-drive'.",
            f"unreadable input for the combined map: {e}",
        )
    _warn_empty_layers(rendered.drive, rendered.union, drive_path, union_path)
    write_map(rendered.html, args.out)
    print(f"combined commute map → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
