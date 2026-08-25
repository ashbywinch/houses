"""Privacy-safe static server for the commute map.

Serves ONLY ``data/commute/commute_map.html`` (a fully self-contained page —
Leaflet inlined, no external data). Everything else in ``data/commute/`` —
the destination config, raw ORS durations, and search payloads — is NOT
served: binding ``0.0.0.0`` with a directory listing would expose them to any
device on the LAN (guest/company networks included), not just the intended
phone. ``make commute-serve`` uses this instead of ``python -m http.server``.
"""

from __future__ import annotations

import argparse
import http.server
import sys
from pathlib import Path
from typing import override
from urllib.parse import urlsplit

PORT = 8123
MAP_REL = "commute_map.html"


def make_handler(directory: Path) -> type[http.server.SimpleHTTPRequestHandler]:
    """A handler serving ONLY ``commute_map.html`` from ``directory``.

    ``/`` and ``/commute_map.html`` return the map; anything else — including
    directory listings — is a 404. ``SimpleHTTPRequestHandler`` provides the
    ``directory`` attribute the map lookup needs.
    """

    directory_str = str(directory)

    class MapOnlyHandler(http.server.SimpleHTTPRequestHandler):
# lucidlint: ignore detached-method staticmethod would break instantiation/super()
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=directory_str, **kwargs)

        def _map_allowed(self) -> bool:
            # ignore the query string: the map's own ?debug=1 diagnostics
            # panel is a documented flow and must not 404
            return urlsplit(self.path).path in ("/", "/" + MAP_REL)

        def _send_map(self, with_body: bool) -> None:
            if not self._map_allowed():
                self.send_error(404, "only the commute map is served")
                return
            try:
                body = (Path(self.directory) / MAP_REL).read_bytes()
            except OSError:
                self.send_error(404, f"{MAP_REL} not found - run 'make commute-map' first")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if with_body:
                self.wfile.write(body)

        @override
        def do_GET(self) -> None:  # noqa: N802 — http.server API
            self._send_map(with_body=True)

        @override
        def do_HEAD(self) -> None:  # noqa: N802 — http.server API
            # the inherited SimpleHTTPRequestHandler.do_HEAD serves ANY file
            # (confirming existence + sizes of personal-data files) — apply
            # the same map-only check
            self._send_map(with_body=False)

    return MapOnlyHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve only the commute map (privacy-safe).")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--directory", default="data/commute")
    args = parser.parse_args(argv)

    handler = make_handler(Path(args.directory))
    print(f"serving ONLY {MAP_REL} on http://{args.host}:{args.port}/{MAP_REL} (Ctrl-C to stop)")
    sys.stdout.flush()
    http.server.ThreadingHTTPServer((args.host, args.port), handler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
