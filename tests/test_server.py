import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "api"))

import server


class StaticFileTests(unittest.TestCase):
    def test_static_rejects_adjacent_prefix_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web_root = root / "web"
            web_root.mkdir()
            (web_root / "index.html").write_bytes(b"index")
            adjacent = root / "web-secret"
            adjacent.mkdir()
            (adjacent / "file.txt").write_bytes(b"secret")

            original_web_root = server.WEB_ROOT
            server.WEB_ROOT = web_root
            try:
                handler = FakeHandler()
                handler.static("/../web-secret/file.txt")
            finally:
                server.WEB_ROOT = original_web_root

        self.assertEqual(handler.status, 200)
        self.assertEqual(handler.wfile.getvalue(), b"index")


class ApiRouteTests(unittest.TestCase):
    def test_unknown_api_get_returns_404(self):
        handler = FakeHandler()
        handler.path = "/api/evidence"

        server.Handler.do_GET(handler)

        self.assertEqual(handler.status, 404)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "Route not found."})


class FakeHandler:
    static = server.Handler.static
    json = server.Handler.json
    error = server.Handler.error

    def __init__(self):
        self.headers = []
        self.status = None
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers.append((key, value))

    def end_headers(self):
        pass


if __name__ == "__main__":
    unittest.main()
