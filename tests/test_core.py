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

    def test_invalid_incident_status_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "Invalid status."):
            core.update_incident_status("inc_missing", "closed")


if __name__ == "__main__":
    unittest.main()
