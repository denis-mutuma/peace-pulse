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


if __name__ == "__main__":
    unittest.main()
