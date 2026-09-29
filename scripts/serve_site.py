"""
Preview the events page locally.

Serves `site/public` and `/v1/...` either from a local pipeline output
directory (EVENTRADAR_DATA_DIR, e.g. `.eventradar/public`) or by proxying
the published feed (EVENTRADAR_DATA_URL, never committed). Usage:

  EVENTRADAR_DATA_DIR=.eventradar/public \\
      uv run python scripts/serve_site.py 8788
"""

import os
import sys
import urllib.request
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "site" / "public"


class _Handler(SimpleHTTPRequestHandler):
    """Static files, plus a read-only proxy for /v1/."""

    def do_GET(self) -> None:
        """Serve a file, or proxy feed requests."""
        if not self.path.startswith("/v1/"):
            super().do_GET()
            return
        local = os.environ.get("EVENTRADAR_DATA_DIR")
        if local:
            path = (Path(local) / self.path.split("?")[0].lstrip("/")).resolve()
            if (
                not path.is_relative_to(Path(local).resolve())
                or not path.is_file()
            ):
                self.send_error(404)
                return
            body = path.read_bytes()
            self.send_response(200)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        base = os.environ["EVENTRADAR_DATA_URL"].rstrip("/")
        request = urllib.request.Request(  # noqa: S310
            base + self.path.split("?")[0],
            headers={"User-Agent": "eventradar-preview"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:  # noqa: S310
                body = resp.read()
                self.send_response(resp.status)
                self.send_header("content-type", resp.headers["content-type"])
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        except urllib.error.HTTPError as exc:
            self.send_error(exc.code)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8788
    handler = partial(_Handler, directory=str(ROOT))
    ThreadingHTTPServer(("127.0.0.1", port), handler).serve_forever()
