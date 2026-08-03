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
import html
import json
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_UNION = Path("data/commute/union.json")
DEFAULT_DRIVE = Path("data/commute/drive_searches.json")
DEFAULT_INTERSECTION = Path("data/commute/intersection.json")
DEFAULT_OUT = Path("data/commute/commute_map.html")
VENDOR_DIR = Path("tools/commute/vendor")

# one colour per layer — the transit layer always takes _COLORS[0], so the
# drive palette excludes it (a drive shed must never look like the train shed)
_COLORS = ["#e33", "#3a3", "#e80", "#a3a", "#0aa"]
_DRIVE_COLORS = _COLORS[1:]


def _js_safe_json(obj) -> str:
    """JSON safe to embed inside an HTML <script> element.

    json.dumps does not escape ``<``/``>``/``&``, so a user-controlled label
    like ``</script><script>…`` would terminate the script element. Escape
    them as unicode escapes (still valid JSON; JS decodes them back).
    """
    return json.dumps(obj).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def _user_label(label: str) -> str:
    """HTML-escape user-controlled labels: they render via innerHTML in the
    layer control and marker popups."""
    return html.escape(label)

# CSS background images in leaflet.css (layer-control toggle + default marker)
_CSS_IMAGES = ("layers-2x.png", "layers.png", "marker-icon.png")
# JS default icon options (Leaflet resolves these relative to the page, which
# breaks on a static host — embedded here instead)
_JS_ICONS = ("marker-icon.png", "marker-icon-2x.png", "marker-shadow.png")


def _data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def build_html(
    union: dict,
    drive: dict,
    *,
    leaflet_js: str,
    leaflet_css: str,
    icons: dict[str, str],
    intersection: dict | None = None,
) -> str:
    """The combined map page — deterministic given the payloads and assets.

    ``icons`` maps a filename (e.g. ``marker-icon.png``) to a data URI.
    ``intersection`` (optional) is the all-commutes payload from
    ``intersection.py`` — its polygons get their own top layer.
    """
    transit = [c["outline"] for c in union.get("components", [])]

    drive_by_label: dict[str, list[dict]] = {}
    for s in drive.get("searches", []):
        drive_by_label.setdefault(s["destination"]["label"], []).append(s)

    layers_js = []
    markers_js = []
    if transit:
        layers_js.append(
            _js_safe_json(
                {
                    "name": "Train: Pimlico & Aldgate",
                    "color": _COLORS[0],
                    "polygons": [{"coords": outline, "url": "", "name": ""} for outline in transit],
                }
            )
        )
    for i, (label, searches) in enumerate(drive_by_label.items(), 1):
        color = _DRIVE_COLORS[(i - 1) % len(_DRIVE_COLORS)]
        # the search NAME is user-controlled (built from the destination label)
        # — HTML-escape it for the popup innerHTML
        polygons = [
            {"coords": s["polygon"], "url": s["rightmove_url"], "name": _user_label(s["name"])} for s in searches
        ]
        # each polygon carries its own url — never keyed by a serialised
        # polygon string (Python json.dumps(52.0) != JS JSON.stringify(52.0),
        # a whole class of silent popup-loss bugs)
        layers_js.append(
            _js_safe_json({"name": f"Drive to {_user_label(label)}", "color": color, "polygons": polygons})
        )
        d = searches[0]["destination"]
        markers_js.append(
            _js_safe_json(
                {
                    "label": _user_label(label),
                    "lat": d["lat"],
                    "lon": d["lon"],
                    "url": searches[0]["rightmove_url"],
                }
            )
        )
    if intersection and intersection.get("searches"):
        polygons = [
            {"coords": s["polygon"], "url": s["rightmove_url"], "name": _user_label(s["name"])}
            for s in intersection["searches"]
        ]
        layers_js.append(
            _js_safe_json(
                {
                    "name": "Where we could live",
                    "color": "#c90",
                    "polygons": polygons,
                    "fillOpacity": 0.25,
                    "weight": 4,
                }
            )
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


def _fail(user_message: str, dev_detail: str) -> int:
    """Two-tier fail-fast exit (docs/coding-standards.md): a plain-language
    stderr line plus a logger.warning with the exact resolution."""
    print(user_message, file=sys.stderr)
    logger.warning(dev_detail)
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the combined commute isochrone map (offline).")
    parser.add_argument("--union", default=str(DEFAULT_UNION))
    parser.add_argument("--drive", default=str(DEFAULT_DRIVE))
    parser.add_argument("--intersection", default=str(DEFAULT_INTERSECTION))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--vendor", default=str(VENDOR_DIR))
    args = parser.parse_args(argv)

    union_path, drive_path = Path(args.union), Path(args.drive)
    for path, hint in ((union_path, "make commute-searches"), (drive_path, "make commute-drive")):
        if not path.exists():
            return _fail(
                f"Commute data is missing — run '{hint}' first.",
                f"combined map input {path} not found (run '{hint}')",
            )
    vendor = Path(args.vendor)
    intersection_path = Path(args.intersection)
    intersection = None
    if intersection_path.exists():
        try:
            intersection = json.loads(intersection_path.read_text())
        except (json.JSONDecodeError, OSError):
            print("The saved all-commutes data is unreadable — showing the map without it.", file=sys.stderr)
            logger.warning("%s unreadable — omitting the intersection layer", intersection_path)
    try:
        union = json.loads(union_path.read_text())
        drive = json.loads(drive_path.read_text())
        leaflet_js = (vendor / "leaflet.js").read_text()
        leaflet_css = (vendor / "leaflet.css").read_text()
        icons = {name: _data_uri(vendor / name) for name in _CSS_IMAGES + _JS_ICONS}
    except (json.JSONDecodeError, OSError) as e:
        return _fail(
            "The commute data or map assets are unreadable — regenerate them with 'make commute-drive'.",
            f"unreadable input for the combined map: {e}",
        )
    html = build_html(
        union,
        drive,
        leaflet_js=leaflet_js,
        leaflet_css=leaflet_css,
        icons=icons,
        intersection=intersection,
    )
    write_map(html, args.out)
    print(f"combined commute map → {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
