import base64
import json
import sys
import tempfile
import unittest
from unittest.mock import patch
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

    def test_short_report_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Report text must be at least 8 characters."):
            core.create_report({"text": "short"})

    def test_long_report_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Report text must be 2,000 characters or fewer."):
            core.create_report({"text": "x" * 2001})

    def test_invalid_language_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid language."):
            core.create_report({"language": "de", "text": "Families need help at the water queue."})

    def test_invalid_category_hint_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid concern type."):
            core.create_report({"category_hint": "gossip", "text": "Families need help at the water queue."})

    def test_public_report_omits_raw_text(self):
        report = core.create_report({"text": "Families need help at the water queue."})

        public = core.public_report(report)

        self.assertNotIn("text", public)
        self.assertEqual(public["id"], report["id"])

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

    def test_redaction_covers_email_id_and_unit_location(self):
        sensitive_text = "Ms. Amina in Block C-12 shared ID AB123456 and email amina@example.org."
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "North camp",
                "category_hint": "threat",
                "text": sensitive_text,
            }
        )

        incident = core.triage_report(report["id"])

        self.assertIn("[redacted-name]", incident["redacted_text"])
        self.assertIn("[redacted-location]", incident["redacted_text"])
        self.assertIn("[redacted-id]", incident["redacted_text"])
        self.assertIn("[redacted-email]", incident["redacted_text"])
        self.assertNotIn("amina@example.org", incident["redacted_text"])
        self.assertNotIn("Block C-12", incident["redacted_text"])

    def test_incident_status_can_be_updated(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues.",
            }
        )
        incident = core.triage_report(report["id"])

        updated = core.update_incident_status(incident["id"], "in_progress")

        self.assertEqual(updated["status"], "in_progress")

    def test_status_change_creates_history_event(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues.",
            }
        )
        incident = core.triage_report(report["id"])

        core.update_incident_status(incident["id"], "assigned", "shift lead")

        history = core.list_status_history(incident["id"])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["previous_status"], "new")
        self.assertEqual(history[0]["new_status"], "assigned")
        self.assertEqual(history[0]["actor_label"], "shift lead")

    def test_invalid_incident_status_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid status."):
            core.update_incident_status("inc_missing", "closed")

    def test_invalid_status_does_not_create_history_event(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues.",
            }
        )
        incident = core.triage_report(report["id"])

        with self.assertRaisesRegex(ValueError, "Invalid status."):
            core.update_incident_status(incident["id"], "closed")

        self.assertEqual(core.list_status_history(incident["id"]), [])

    def test_incident_note_is_redacted_and_synced(self):
        report = core.create_report(
            {
                "language": "en",
                "rough_location": "Main water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues.",
            }
        )
        incident = core.triage_report(report["id"])

        note = core.create_incident_note(
            incident["id"],
            {
                "actor_label": "water steward",
                "note": "Called Mr. Kamau at +254 700 000 000 and assigned mediation.",
            },
        )

        notes = core.list_incident_notes(incident["id"])
        payload = json.loads(core.rows("SELECT payload FROM sync_queue WHERE item_type = ?", ("incident_note",))[0]["payload"])
        self.assertEqual(notes[0]["id"], note["id"])
        self.assertEqual(notes[0]["actor_label"], "water steward")
        self.assertIn("[redacted-name]", notes[0]["note"])
        self.assertIn("[redacted-phone]", payload["note"])

    def test_incident_note_validation(self):
        with self.assertRaisesRegex(ValueError, "Incident not found."):
            core.create_incident_note("inc_missing", {"note": "Assign mediation team."})

        report = core.create_report({"text": "Families are turned away after long water queues."})
        incident = core.triage_report(report["id"])
        with self.assertRaisesRegex(ValueError, "Note must be at least 4 characters."):
            core.create_incident_note(incident["id"], {"note": "ok"})
        with self.assertRaisesRegex(ValueError, "Note must be 500 characters or fewer."):
            core.create_incident_note(incident["id"], {"note": "x" * 501})

    def test_purge_triaged_report_text_keeps_incident_available(self):
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

        purged = core.purge_triaged_report_text()

        stored = core.rows("SELECT text, redacted_text FROM reports WHERE id = ?", (report["id"],))[0]
        incident = next(item for item in core.list_incidents() if item["report_id"] == report["id"])
        self.assertEqual(purged, 1)
        self.assertEqual(stored["text"], "")
        self.assertNotEqual(stored["redacted_text"], "")
        self.assertIn("[redacted-name]", incident["redacted_text"])

    def test_env_db_path_is_honored(self):
        original_data_dir = core.DATA_DIR
        original_db_path = core.DB_PATH
        original_evidence_dir = core.EVIDENCE_DIR
        env_db = Path(self.tmp.name) / "env" / "peacepulse.db"
        try:
            with patch.dict("os.environ", {"PEACEPULSE_DB_PATH": str(env_db)}):
                core.configure_from_env()
                core.init_db()

            self.assertEqual(core.DB_PATH, env_db)
            self.assertTrue(env_db.exists())
        finally:
            core.DATA_DIR = original_data_dir
            core.DB_PATH = original_db_path
            core.EVIDENCE_DIR = original_evidence_dir

    def test_evidence_hash_and_custody(self):
        raw = b"demo evidence"
        record = core.create_evidence(
            {
                "filename": "photo.jpg",
                "mime_type": "image/jpeg",
                "content_base64": base64.b64encode(raw).decode("ascii"),
                "sync_allowed": True,
            }
        )

        evidence = core.list_evidence()[0]
        queued = core.rows("SELECT payload FROM sync_queue WHERE item_type = ?", ("evidence_record",))

        self.assertEqual(record["sha256"], evidence["sha256"])
        self.assertEqual(record["size_bytes"], len(raw))
        self.assertEqual(len(evidence["custody"]), 1)
        self.assertNotIn("encrypted_path", json.loads(queued[0]["payload"]))

    def test_evidence_rejects_invalid_base64(self):
        with self.assertRaisesRegex(ValueError, "Evidence content must be valid base64."):
            core.create_evidence(
                {
                    "filename": "photo.jpg",
                    "mime_type": "image/jpeg",
                    "content_base64": "not valid base64",
                }
            )

    def test_evidence_rejects_large_files(self):
        raw = b"x" * (core.MAX_EVIDENCE_BYTES + 1)

        with self.assertRaisesRegex(ValueError, "Evidence file must be 2 MB or smaller."):
            core.create_evidence(
                {
                    "filename": "photo.jpg",
                    "mime_type": "image/jpeg",
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                }
            )

    def test_evidence_rejects_unsupported_metadata(self):
        with self.assertRaisesRegex(ValueError, "Evidence filename contains unsupported characters."):
            core.create_evidence(
                {
                    "filename": "bad*name.jpg",
                    "mime_type": "image/jpeg",
                    "content_base64": base64.b64encode(b"demo").decode("ascii"),
                }
            )

        with self.assertRaisesRegex(ValueError, "Evidence file type is not supported."):
            core.create_evidence(
                {
                    "filename": "script.sh",
                    "mime_type": "application/x-sh",
                    "content_base64": base64.b64encode(b"demo").decode("ascii"),
                }
            )

    def test_sync_preview_exposes_redacted_summaries(self):
        sensitive_text = "Mr. Kamau says call +254 700 000 000 about blocked water access."
        report = core.create_report({"category_hint": "resource", "text": sensitive_text})
        core.triage_report(report["id"])
        core.create_evidence(
            {
                "filename": "photo.jpg",
                "mime_type": "image/jpeg",
                "content_base64": base64.b64encode(b"demo evidence").decode("ascii"),
                "sync_allowed": True,
            }
        )

        preview = core.sync_preview()
        serialized = json.dumps(preview)

        self.assertGreaterEqual(len(preview), 2)
        self.assertNotIn(sensitive_text, serialized)
        self.assertNotIn("encrypted_path", serialized)
        self.assertIn("[redacted-name]", serialized)
        self.assertIn("payload_keys", preview[0])

    def test_resource_anomaly_and_status(self):
        event = core.create_resource_event(
            {
                "resource_id": "water-point-north",
                "queue_length": 55,
                "flow_rate": 0.2,
                "uptime": 0,
                "maintenance_note": "pump inspection requested",
            }
        )

        status = core.resource_status()

        self.assertIn("pump offline", event["anomaly"])
        self.assertEqual(status[0]["id"], event["id"])
        self.assertEqual(status[0]["maintenance_note"], "pump inspection requested")

    def test_rumor_cluster_payload_is_redacted(self):
        rumor = core.create_rumor(
            {
                "language": "en",
                "rough_location": "North water point",
                "text": "Ms. Amina says call +254 700 000 000 because aid is diverted.",
            }
        )

        clusters = core.list_rumor_clusters()
        payload = json.loads(core.rows("SELECT payload FROM sync_queue WHERE item_type = ?", ("rumor_summary",))[0]["payload"])

        self.assertEqual(clusters[0]["count"], 1)
        self.assertIn("[redacted-name]", clusters[0]["items"][0]["redacted_text"])
        self.assertNotIn("text", payload)
        self.assertIn("[redacted-phone]", payload["redacted_text"])

    def test_run_sync_marks_pending_items_synced(self):
        report = core.create_report({"text": "Families need help at the water queue."})
        core.triage_report(report["id"])

        result = core.run_sync()

        self.assertGreaterEqual(result["synced"], 1)
        self.assertEqual(core.sync_status()["pending"], 0)


if __name__ == "__main__":
    unittest.main()
