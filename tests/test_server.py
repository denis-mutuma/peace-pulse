import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "api"))

import peacepulse_core as core
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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_unknown_api_get_returns_404(self):
        handler = FakeHandler()
        handler.path = "/api/unknown"

        server.Handler.do_GET(handler)

        self.assertEqual(handler.status, 404)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "Route not found."})

    def test_health_reports_database_status(self):
        with isolated_core_db(self.tmp.name):
            handler = FakeHandler(path="/api/health")

            server.Handler.do_GET(handler)

        self.assertEqual(handler.status, 200)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {
            "ok": True,
            "service": "peacepulse-edge",
            "database": "ok",
            "sync": {"pending": 0, "synced": 0},
        })

    def test_privacy_audit_endpoint_reports_policy(self):
        with isolated_core_db(self.tmp.name):
            handler = FakeHandler(path="/api/privacy/audit")

            server.Handler.do_GET(handler)

        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 200)
        self.assertIn("counts", payload)
        self.assertIn("Raw evidence file bytes.", payload["never_syncs"])

    def test_demo_reset_endpoint_reseeds_data(self):
        with isolated_core_db(self.tmp.name):
            core.create_report({"text": "A temporary report should be removed by reset."})
            handler = FakeHandler(path="/api/demo/reset", body={})

            server.Handler.do_POST(handler)

        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 200)
        self.assertTrue(payload["reset"])
        self.assertEqual(payload["seeded"]["reports"], 2)

    def test_post_report_returns_201_without_raw_text(self):
        with isolated_core_db(self.tmp.name):
            sensitive_text = "Mr. Kamau says call +254 700 000 000 about blocked water access."
            handler = FakeHandler(
                path="/api/reports",
                body={
                    "language": "en",
                    "rough_location": "Main water point",
                    "category_hint": "resource",
                    "text": sensitive_text,
                },
            )

            server.Handler.do_POST(handler)

        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 201)
        self.assertNotIn("text", payload["report"])
        self.assertNotIn(sensitive_text, json.dumps(payload))
        self.assertIn("[redacted-name]", payload["incident"]["redacted_text"])

    def test_post_report_validation_error_returns_400(self):
        with isolated_core_db(self.tmp.name):
            handler = FakeHandler(path="/api/reports", body={"text": "short"})

            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 400)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "Report text must be at least 8 characters."})

    def test_post_report_malformed_json_returns_400(self):
        with isolated_core_db(self.tmp.name):
            handler = FakeHandler(path="/api/reports", raw_body=b'{"text":')

            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 400)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "Request body must be valid JSON."})

    def test_post_report_large_body_returns_400(self):
        with isolated_core_db(self.tmp.name):
            handler = FakeHandler(path="/api/reports", raw_body=b"x" * (server.MAX_JSON_BODY_BYTES + 1))

            server.Handler.do_POST(handler)

        self.assertEqual(handler.status, 400)
        self.assertEqual(json.loads(handler.wfile.getvalue()), {"error": "Request body is too large."})

    def test_status_history_endpoint_returns_events(self):
        with isolated_core_db(self.tmp.name):
            report = core.create_report({"text": "Families are turned away after long water queues."})
            incident = core.triage_report(report["id"])
            patch = FakeHandler(
                path=f"/api/incidents/{incident['id']}/status",
                body={"status": "assigned", "actor_label": "shift lead"},
            )
            server.Handler.do_PATCH(patch)
            handler = FakeHandler(path=f"/api/incidents/{incident['id']}/history")

            server.Handler.do_GET(handler)

        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 200)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["previous_status"], "new")
        self.assertEqual(payload[0]["new_status"], "assigned")
        self.assertEqual(payload[0]["actor_label"], "shift lead")

    def test_incident_notes_endpoint_creates_and_lists_notes(self):
        with isolated_core_db(self.tmp.name):
            report = core.create_report({"text": "Families are turned away after long water queues."})
            incident = core.triage_report(report["id"])
            post = FakeHandler(
                path=f"/api/incidents/{incident['id']}/notes",
                body={"actor_label": "shift lead", "note": "Assign mediation at Block C-12."},
            )
            server.Handler.do_POST(post)
            handler = FakeHandler(path=f"/api/incidents/{incident['id']}/notes")

            server.Handler.do_GET(handler)

        created = json.loads(post.wfile.getvalue())
        listed = json.loads(handler.wfile.getvalue())
        self.assertEqual(post.status, 201)
        self.assertEqual(handler.status, 200)
        self.assertEqual(listed[0]["id"], created["id"])
        self.assertIn("[redacted-location]", listed[0]["note"])

    def test_incident_timeline_endpoint_returns_events(self):
        with isolated_core_db(self.tmp.name):
            report = core.create_report({"text": "Families are turned away after long water queues."})
            incident = core.triage_report(report["id"])
            core.create_incident_note(incident["id"], {"note": "Assign mediation team."})
            handler = FakeHandler(path=f"/api/incidents/{incident['id']}/timeline")

            server.Handler.do_GET(handler)

        payload = json.loads(handler.wfile.getvalue())
        self.assertEqual(handler.status, 200)
        self.assertTrue(any(item["kind"] == "note" for item in payload))
        self.assertTrue(any(item["kind"] == "triage" for item in payload))

    def test_route_alert_endpoints_create_and_list_alerts(self):
        with isolated_core_db(self.tmp.name):
            post = FakeHandler(
                path="/api/routes/alerts",
                body={
                    "route_label": "Clinic route",
                    "rough_location": "East corridor",
                    "alert_type": "caution",
                    "status": "review",
                    "note": "Steward review near Block C-12.",
                },
            )
            server.Handler.do_POST(post)
            handler = FakeHandler(path="/api/routes/status")

            server.Handler.do_GET(handler)

        created = json.loads(post.wfile.getvalue())
        listed = json.loads(handler.wfile.getvalue())
        self.assertEqual(post.status, 201)
        self.assertEqual(handler.status, 200)
        self.assertEqual(listed["alerts"][0]["id"], created["id"])
        self.assertIn("[redacted-location]", listed["alerts"][0]["note"])

    def test_work_opportunity_endpoints_create_and_list_opportunities(self):
        with isolated_core_db(self.tmp.name):
            post = FakeHandler(
                path="/api/work/opportunities",
                body={
                    "title": "Solar charging steward",
                    "skill_category": "solar",
                    "rough_location": "Central market",
                    "verification_status": "steward_checked",
                    "safety_note": "Community steward verified.",
                },
            )
            server.Handler.do_POST(post)
            handler = FakeHandler(path="/api/work/opportunities")

            server.Handler.do_GET(handler)

        created = json.loads(post.wfile.getvalue())
        listed = json.loads(handler.wfile.getvalue())
        self.assertEqual(post.status, 201)
        self.assertEqual(handler.status, 200)
        self.assertEqual(listed[0]["id"], created["id"])
        self.assertEqual(listed[0]["skill_category"], "solar")


class FakeHandler:
    body = server.Handler.body
    static = server.Handler.static
    json = server.Handler.json
    error = server.Handler.error

    def __init__(self, path="/", body=None, raw_body=None):
        self.path = path
        self.status = None
        self.response_headers = []
        raw_body = raw_body if raw_body is not None else json.dumps(body or {}).encode("utf-8")
        if raw_body != b"{}":
            self.headers = {"content-length": str(len(raw_body))}
        else:
            self.headers = {}
        self.rfile = io.BytesIO(raw_body)
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.response_headers.append((key, value))

    def end_headers(self):
        pass


class isolated_core_db:
    def __init__(self, root):
        self.root = Path(root)

    def __enter__(self):
        self.original_data_dir = core.DATA_DIR
        self.original_db_path = core.DB_PATH
        core.DATA_DIR = self.root / "data"
        core.DB_PATH = core.DATA_DIR / "peacepulse.db"
        server.init_db()

    def __exit__(self, exc_type, exc, tb):
        core.DATA_DIR = self.original_data_dir
        core.DB_PATH = self.original_db_path


if __name__ == "__main__":
    unittest.main()
