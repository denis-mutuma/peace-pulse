import base64
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "api"))

import peacepulse_core as core


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        core.DATA_DIR = root / "data"
        core.DB_PATH = core.DATA_DIR / "peacepulse.db"
        core.EVIDENCE_DIR = core.DATA_DIR / "storage" / "evidence"
        core.init_db()

    def tearDown(self):
        self.tmp.cleanup()

    def test_report_is_triaged_and_redacted(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Mr. Kamau says families are turned away near Block Seven. Call +254 700 000 000.",
            }
        )
        incident = core.triage_report(report["id"])
        self.assertEqual(incident["category"], "resource")
        self.assertIn("[redacted-name]", incident["redacted_text"])
        self.assertIn("[redacted-phone]", incident["redacted_text"])
        self.assertGreaterEqual(incident["severity"], 3)

    def test_report_without_sync_consent_does_not_enqueue_incident_summary(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues.",
                "consent_to_sync": False,
            }
        )

        incident = core.triage_report(report["id"])

        queued = core.rows(
            "SELECT * FROM sync_queue WHERE item_type = ? AND item_id = ?",
            ("incident_summary", incident["id"]),
        )
        self.assertEqual(queued, [])

    def test_report_with_sync_consent_enqueues_incident_summary(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues.",
                "consent_to_sync": True,
            }
        )

        incident = core.triage_report(report["id"])

        queued = core.rows(
            "SELECT * FROM sync_queue WHERE item_type = ? AND item_id = ?",
            ("incident_summary", incident["id"]),
        )
        self.assertEqual(len(queued), 1)

    def test_list_incidents_omits_original_report_text(self):
        sensitive_text = "Mr. Kamau says call +254 700 000 000 about the blocked clinic queue."
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": sensitive_text,
            }
        )
        core.triage_report(report["id"])

        incident = next(item for item in core.list_incidents() if item["report_id"] == report["id"])

        self.assertNotIn("original_text", incident)
        self.assertNotIn(sensitive_text, incident.values())
        self.assertIn("[redacted-name]", incident["redacted_text"])

    def test_list_reports_redacts_text_and_omits_sensitive_metadata(self):
        sensitive_text = "Mr. Kamau says call +254 700 000 000 near Block Seven."
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Block Seven clinic",
                "category_hint": "resource",
                "text": sensitive_text,
                "consent_to_sync": True,
            }
        )

        listed = next(item for item in core.list_reports() if item["id"] == report["id"])

        self.assertNotIn("text", listed)
        self.assertNotIn("rough_location", listed)
        self.assertNotIn("language", listed)
        self.assertNotIn("consent_to_sync", listed)
        self.assertNotIn(sensitive_text, listed.values())
        self.assertIn("[redacted-name]", listed["redacted_text"])
        self.assertIn("[redacted-phone]", listed["redacted_text"])

    def test_evidence_hash_and_custody(self):
        raw = b"demo evidence"
        record = core.create_evidence(
            {
                "filename": "photo.txt",
                "mime_type": "text/plain",
                "content_base64": base64.b64encode(raw).decode("ascii"),
            }
        )
        self.assertEqual(record["size_bytes"], len(raw))
        self.assertEqual(len(record["sha256"]), 64)
        self.assertTrue((Path(self.tmp.name) / record["encrypted_path"]).exists())
        self.assertEqual(len(core.list_evidence()[0]["custody"]), 1)

    def test_resource_anomaly_and_sync(self):
        event = core.create_resource_event(
            {
                "resource_id": "water-point-north",
                "queue_length": 55,
                "flow_rate": 0.2,
                "uptime": 0,
            }
        )
        self.assertIn("pump offline", event["anomaly"])
        self.assertGreater(core.sync_status()["pending"], 0)
        self.assertGreaterEqual(core.run_sync()["synced"], 1)

    def test_resource_status_breaks_same_second_ties(self):
        first = core.create_resource_event(
            {
                "resource_id": "water-point-north",
                "queue_length": 12,
                "flow_rate": 8.5,
                "uptime": 1,
            }
        )
        latest = core.create_resource_event(
            {
                "resource_id": "water-point-north",
                "queue_length": 59,
                "flow_rate": 0.2,
                "uptime": 0,
            }
        )

        status = core.resource_status()

        self.assertEqual(len(status), 1)
        self.assertNotEqual(status[0]["id"], first["id"])
        self.assertEqual(status[0]["id"], latest["id"])

    def test_rumor_clusters(self):
        core.create_rumor(
            {
                "language": "en",
                "rough_location": "North water point",
                "text": "People say aid is being diverted at the water point.",
            }
        )
        clusters = core.list_rumor_clusters()
        self.assertEqual(len(clusters), 1)
        self.assertEqual(clusters[0]["count"], 1)

    def test_rumor_sync_payload_is_redacted(self):
        rumor = core.create_rumor(
            {
                "language": "en",
                "rough_location": "North water point",
                "text": "Mr. Kamau says call +254 700 000 000 about diverted aid.",
            }
        )

        queued = core.rows(
            "SELECT payload FROM sync_queue WHERE item_type = ? AND item_id = ?",
            ("rumor_summary", rumor["id"]),
        )
        payload = json.loads(queued[0]["payload"])

        self.assertIn("[redacted-name]", payload["text"])
        self.assertIn("[redacted-phone]", payload["text"])
        self.assertNotIn("Mr. Kamau", payload["text"])
        self.assertNotIn("+254 700 000 000", payload["text"])


if __name__ == "__main__":
    unittest.main()
