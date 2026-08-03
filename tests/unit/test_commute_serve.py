"""Privacy-safe map server — serves ONLY commute_map.html."""

from __future__ import annotations

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from tools.commute.serve import make_handler


def test_serve_serves_only_the_map(tmp_path):
    """Regression: data/commute holds personal data (destination postcodes,
    raw durations, search payloads) — the LAN server must serve ONLY the map
    file, never a directory listing or any other artifact."""
    (tmp_path / "commute_map.html").write_text("<html>map</html>")
    (tmp_path / "drive_destinations.json").write_text("secret")
    (tmp_path / "drive_isochrone.json").write_text("secret")

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path))
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/commute_map.html")
        assert resp.status == 200
        assert resp.read() == b"<html>map</html>"

        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/")
        assert resp.status == 200
        assert resp.read() == b"<html>map</html>"

        # the map's ?debug=1 diagnostics panel must not 404 on a query string
        resp = urllib.request.urlopen(f"http://127.0.0.1:{port}/commute_map.html?debug=1")
        assert resp.status == 200
        assert resp.read() == b"<html>map</html>"

        for path in ("/drive_destinations.json", "/drive_isochrone.json", "/searches.json"):
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}")
            assert exc.value.code == 404
    finally:
        httpd.shutdown()
