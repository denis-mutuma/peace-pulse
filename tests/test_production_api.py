import json
import os
import sys
import tempfile
import unittest
import asyncio
import hashlib
from pathlib import Path

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

sys.path.append(str(Path(__file__).resolve().parents[1]))

from services.api_prod.app import app
import services.api_prod.app as app_module
from services.api_prod.config import Settings, validate_production_settings
from services.api_prod.db import Base, get_db
from services.api_prod.security import _totp_at, hash_token, sign_hub_payload


class ASGISyncClient:
    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, **kwargs):
        async def run_request():
            transport = httpx.ASGITransport(app=self.app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                return await client.request(method, path, **kwargs)

        return asyncio.run(run_request())

    def get(self, path: str, **kwargs):
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs):
        return self.request("POST", path, **kwargs)

    def put(self, path: str, **kwargs):
        return self.request("PUT", path, **kwargs)

    def patch(self, path: str, **kwargs):
        return self.request("PATCH", path, **kwargs)


class ProductionApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        db_path = Path(self.tmp.name) / "prod.db"
        self.original_evidence_dir = app_module.settings.evidence_storage_dir
        app_module.settings.evidence_storage_dir = Path(self.tmp.name) / "evidence"
        self.engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, autoflush=False, autocommit=False, expire_on_commit=False)

        async def override_db():
            db = self.SessionLocal()
            try:
                yield db
            finally:
                db.close()

        app.dependency_overrides[get_db] = override_db
        self.client = ASGISyncClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        app_module.settings.evidence_storage_dir = self.original_evidence_dir
        self.tmp.cleanup()

    def bootstrap(self):
        response = self.client.post(
            "/api/v1/admin/bootstrap",
            json={
                "organization_name": "Demo Org",
                "site_name": "North Site",
                "site_rough_location": "North zone",
                "admin_email": "admin@example.org",
                "admin_password": "change-this-password",
                "admin_name": "Admin",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def token(self):
        self.bootstrap()
        response = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password", "mfa_code": "000000"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["access_token"]

    def test_bootstrap_login_and_public_report_purges_raw_text(self):
        boot = self.bootstrap()
        sites = self.client.get("/api/v1/public/sites")
        self.assertEqual(sites.status_code, 200, sites.text)
        self.assertEqual(sites.json()[0]["id"], boot["site_id"])

        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password", "mfa_code": "000000"},
        )
        self.assertEqual(login.status_code, 200, login.text)
        me = self.client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {login.json()['access_token']}"})
        self.assertEqual(me.status_code, 200, me.text)
        self.assertIn("org_admin", me.json()["roles"])
        self.assertIn(boot["site_id"], me.json()["site_ids"])
        self.assertFalse(me.json()["mfa_enabled"])

        report = self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={
                "language": "en",
                "rough_location": "North water point",
                "category_hint": "resource",
                "text": "Mr. Kamau says call +254 700 000 000 about Block C-12 water queue tension.",
            },
        )
        self.assertEqual(report.status_code, 201, report.text)
        payload = report.json()
        self.assertIn("[redacted-name]", payload["redacted_text"])
        self.assertIn("[redacted-phone]", payload["redacted_text"])
        self.assertNotIn("+254 700 000 000", json.dumps(payload))

        incidents = self.client.get("/api/v1/incidents", headers={"authorization": f"Bearer {login.json()['access_token']}"})
        self.assertEqual(incidents.status_code, 200, incidents.text)
        self.assertEqual(len(incidents.json()), 1)

    def test_tenant_role_required_for_incidents(self):
        boot = self.bootstrap()
        self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={"text": "Families are turned away after long water queues.", "category_hint": "resource"},
        )

        response = self.client.get("/api/v1/incidents")

        self.assertEqual(response.status_code, 401)

    def test_browser_role_state_does_not_grant_production_access(self):
        boot = self.bootstrap()
        self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={"text": "Families are turned away after long water queues.", "category_hint": "resource"},
        )

        response = self.client.get("/api/v1/incidents", headers={"x-peacepulse-role": "coordinator"})

        self.assertEqual(response.status_code, 401)

    def test_legacy_unversioned_routes_are_not_exposed(self):
        checks = [
            ("GET", "/api/health", None),
            ("POST", "/api/reports", {"text": "Families need help near the water queue."}),
            ("POST", "/api/demo/reset", {}),
            ("GET", "/api/routes/status", None),
            ("GET", "/api/work/opportunities", None),
        ]
        for method, path, payload in checks:
            with self.subTest(path=path):
                if method == "POST":
                    response = self.client.post(path, json=payload)
                else:
                    response = self.client.get(path)
                self.assertEqual(response.status_code, 404, response.text)

    def test_evidence_upload_stores_encrypted_content_and_keeps_sync_metadata_only(self):
        token = self.token()
        content = b"\xff\xd8\xff\xe1\x00\x10Exif\x00\x00private-gps\xff\xdaimage-bytes"
        response = self.client.post(
            "/api/v1/evidence/uploads",
            headers={"authorization": f"Bearer {token}"},
            json={
                "site_id": self.bootstrap_site_id(),
                "filename": "photo.jpg",
                "mime_type": "image/jpeg",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "sync_allowed": True,
            },
        )

        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertIn("object_key", payload)
        self.assertNotIn("content_base64", json.dumps(payload))
        stored = self.client.put(
            payload["upload_url"],
            headers={"authorization": f"Bearer {token}", "content-type": "image/jpeg"},
            content=content,
        )
        self.assertEqual(stored.status_code, 200, stored.text)
        self.assertEqual(stored.json()["storage_status"], "stored")

        encrypted_path = app_module.settings.evidence_storage_dir / f"{payload['object_key']}.enc"
        self.assertTrue(encrypted_path.exists())
        encrypted = encrypted_path.read_bytes()
        self.assertNotEqual(encrypted, content)
        self.assertNotIn(b"Exif", encrypted)

        evidence = self.client.get("/api/v1/evidence", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(evidence.status_code, 200, evidence.text)
        self.assertEqual(evidence.json()[0]["storage_status"], "stored")

        sync_preview = self.client.get("/api/v1/sync/preview", headers={"authorization": f"Bearer {token}"})
        self.assertEqual(sync_preview.status_code, 200, sync_preview.text)
        serialized = json.dumps(sync_preview.json())
        self.assertIn("evidence_record", serialized)
        self.assertNotIn("content_base64", serialized)
        self.assertNotIn(str(encrypted_path), serialized)

    def test_evidence_content_rejects_hash_mismatch(self):
        token = self.token()
        content = b"real evidence bytes"
        response = self.client.post(
            "/api/v1/evidence/uploads",
            headers={"authorization": f"Bearer {token}"},
            json={
                "site_id": self.bootstrap_site_id(),
                "filename": "note.txt",
                "mime_type": "text/plain",
                "size_bytes": len(content),
                "sha256": hashlib.sha256(b"different").hexdigest(),
            },
        )
        self.assertEqual(response.status_code, 201, response.text)

        stored = self.client.put(
            response.json()["upload_url"],
            headers={"authorization": f"Bearer {token}", "content-type": "text/plain"},
            content=content,
        )

        self.assertEqual(stored.status_code, 400, stored.text)
        self.assertIn("hash", stored.text)

    def test_production_modules_are_tenant_scoped_and_redacted(self):
        token = self.token()
        site_id = self.bootstrap_site_id()
        headers = {"authorization": f"Bearer {token}"}

        resource = self.client.post(
            "/api/v1/resources/events",
            headers=headers,
            json={"site_id": site_id, "resource_id": "water-point-north", "queue_length": 52, "flow_rate": 0.4, "uptime": 0},
        )
        self.assertEqual(resource.status_code, 201, resource.text)
        resources = self.client.get("/api/v1/resources/status", headers=headers)
        self.assertEqual(resources.status_code, 200, resources.text)
        self.assertIn("pump offline", resources.json()[0]["anomaly"])

        rumor = self.client.post(
            "/api/v1/rumors",
            headers=headers,
            json={
                "site_id": site_id,
                "rough_location": "North water point",
                "text": "Ms. Amina says call +254 700 000 000 because aid is diverted.",
            },
        )
        self.assertEqual(rumor.status_code, 201, rumor.text)
        clusters = self.client.get("/api/v1/rumors/clusters", headers=headers)
        self.assertEqual(clusters.status_code, 200, clusters.text)
        self.assertIn("[redacted-phone]", json.dumps(clusters.json()))
        self.assertNotIn("+254 700 000 000", json.dumps(clusters.json()))

        route = self.client.post(
            "/api/v1/routes/alerts",
            headers=headers,
            json={
                "site_id": site_id,
                "route_label": "Clinic route",
                "rough_location": "East corridor",
                "alert_type": "blocked",
                "status": "blocked",
                "note": "Review near Block C-12 and call +254 700 000 000.",
            },
        )
        self.assertEqual(route.status_code, 201, route.text)
        route_status = self.client.get("/api/v1/routes/status", headers=headers)
        self.assertEqual(route_status.status_code, 200, route_status.text)
        self.assertIn("[redacted-location]", json.dumps(route_status.json()))

        opportunity = self.client.post(
            "/api/v1/work/opportunities",
            headers=headers,
            json={
                "site_id": site_id,
                "title": "Repair assistant for Mr. Kamau",
                "skill_category": "repair",
                "rough_location": "Central workshop",
                "verification_status": "steward_checked",
                "safety_note": "Do not call +254 700 000 000 in the listing.",
            },
        )
        self.assertEqual(opportunity.status_code, 201, opportunity.text)
        opportunities = self.client.get("/api/v1/work/opportunities", headers=headers)
        self.assertEqual(opportunities.status_code, 200, opportunities.text)
        serialized = json.dumps(opportunities.json())
        self.assertIn("[redacted-name]", serialized)
        self.assertIn("[redacted-phone]", serialized)

    def test_production_incident_timeline_combines_module_context(self):
        boot = self.bootstrap()
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        report = self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={"rough_location": "North water point", "category_hint": "resource", "text": "Families are turned away after long water queues."},
        ).json()
        self.client.post(f"/api/v1/incidents/{report['incident_id']}/notes", headers=headers, json={"note": "Assign mediation near Block C-12."})
        self.client.post(
            "/api/v1/evidence/uploads",
            headers=headers,
            json={
                "site_id": boot["site_id"],
                "linked_report_id": report["id"],
                "filename": "note.txt",
                "mime_type": "text/plain",
                "size_bytes": 10,
                "sha256": "b" * 64,
            },
        )
        self.client.post(
            "/api/v1/resources/events",
            headers=headers,
            json={"site_id": boot["site_id"], "resource_id": "water-point-north", "queue_length": 55, "flow_rate": 0.3, "uptime": 0},
        )
        self.client.post(
            "/api/v1/rumors",
            headers=headers,
            json={"site_id": boot["site_id"], "rough_location": "North water point", "text": "People say aid is diverted near the water point."},
        )

        timeline = self.client.get(f"/api/v1/incidents/{report['incident_id']}/timeline", headers=headers)
        self.assertEqual(timeline.status_code, 200, timeline.text)
        kinds = {item["kind"] for item in timeline.json()}
        self.assertTrue({"triage", "note", "evidence", "resource", "rumor"}.issubset(kinds))
        self.assertNotIn("Block C-12", json.dumps(timeline.json()))

    def test_copilot_runbooks_investigation_and_chat_are_grounded(self):
        boot = self.bootstrap()
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        report = self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={
                "rough_location": "North water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues near Block C-12.",
            },
        ).json()

        runbooks = self.client.get("/api/v1/copilot/runbooks", headers=headers)
        self.assertEqual(runbooks.status_code, 200, runbooks.text)
        self.assertTrue(any(item["id"] == "rb_resource_pressure" for item in runbooks.json()))
        self.assertTrue(all(item["retrieval_method"] == "local_tfidf_cosine" for item in runbooks.json()))

        investigation = self.client.post(f"/api/v1/copilot/incidents/{report['incident_id']}/investigate", headers=headers, json={})
        self.assertEqual(investigation.status_code, 200, investigation.text)
        payload = investigation.json()
        self.assertEqual(payload["incident_id"], report["incident_id"])
        self.assertTrue(payload["recommended_actions"])
        self.assertTrue(payload["citations"])
        self.assertNotIn("Block C-12", json.dumps(payload))

        session = self.client.post(
            "/api/v1/copilot/sessions",
            headers=headers,
            json={"incident_id": report["incident_id"], "title": "Water pressure review"},
        )
        self.assertEqual(session.status_code, 201, session.text)
        reply = self.client.post(
            f"/api/v1/copilot/sessions/{session.json()['id']}/messages",
            headers=headers,
            json={"content": "What should responders do next?"},
        )
        self.assertEqual(reply.status_code, 200, reply.text)
        messages = reply.json()["messages"]
        self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
        self.assertIn("Top recommendation", messages[-1]["content"])
        self.assertTrue(messages[-1]["citations"])

        privacy = self.client.get("/api/v1/privacy/audit", headers=headers)
        self.assertEqual(privacy.status_code, 200, privacy.text)
        self.assertEqual(privacy.json()["counts"]["copilot_sessions"], 1)
        self.assertEqual(privacy.json()["counts"]["copilot_messages"], 2)
        self.assertIn("Copilot chat transcripts", json.dumps(privacy.json()["never_syncs"]))

        sync_preview = self.client.get("/api/v1/sync/preview", headers=headers)
        self.assertEqual(sync_preview.status_code, 200, sync_preview.text)
        self.assertNotIn("What should responders do next?", json.dumps(sync_preview.json()))
        self.assertNotIn("Top recommendation", json.dumps(sync_preview.json()))

    def test_copilot_runbook_editing_and_vector_retrieval(self):
        boot = self.bootstrap()
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        created = self.client.post(
            "/api/v1/copilot/runbooks",
            headers=headers,
            json={
                "title": "Shade Queue Mediation",
                "category": "resource",
                "content": "When shade tents are missing near water queues, assign a steward to separate urgent hydration support from routine queue mediation.",
                "tags": ["shade", "queue", "hydration"],
            },
        )
        self.assertEqual(created.status_code, 201, created.text)

        patched = self.client.patch(
            f"/api/v1/copilot/runbooks/{created.json()['id']}",
            headers=headers,
            json={"content": "When shade tents or hydration points are missing, assign a steward owner and publish a coarse non-identifying service update."},
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertIn("hydration points", patched.json()["content"])

        session = self.client.post(
            "/api/v1/copilot/sessions",
            headers=headers,
            json={"title": "Shade queue review"},
        )
        self.assertEqual(session.status_code, 201, session.text)
        reply = self.client.post(
            f"/api/v1/copilot/sessions/{session.json()['id']}/messages",
            headers=headers,
            json={"content": "What should we do about missing shade tents and hydration points?"},
        )
        self.assertEqual(reply.status_code, 200, reply.text)
        citations = reply.json()["messages"][-1]["citations"]
        self.assertTrue(citations)
        self.assertEqual(citations[0]["document_id"], created.json()["id"])
        self.assertEqual(citations[0]["retrieval_method"], "local_tfidf_cosine")

    def test_mfa_enrollment_then_login_requires_totp(self):
        token = self.token()
        enroll = self.client.post("/api/v1/auth/mfa/enroll", headers={"authorization": f"Bearer {token}"}, json={})
        self.assertEqual(enroll.status_code, 200, enroll.text)
        code = _totp_at(enroll.json()["secret"], int(__import__("time").time() // 30))

        verify = self.client.post(
            "/api/v1/auth/mfa/verify-enrollment",
            headers={"authorization": f"Bearer {token}"},
            json={"code": code},
        )
        self.assertEqual(verify.status_code, 200, verify.text)

        no_mfa = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        self.assertEqual(no_mfa.status_code, 401)
        with_mfa = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password", "mfa_code": code},
        )
        self.assertEqual(with_mfa.status_code, 200, with_mfa.text)

    def test_password_change_invalidates_old_password(self):
        token = self.token()
        changed = self.client.post(
            "/api/v1/auth/change-password",
            headers={"authorization": f"Bearer {token}"},
            json={"current_password": "change-this-password", "new_password": "new-production-password"},
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        old_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        self.assertEqual(old_login.status_code, 401)
        new_login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "new-production-password"},
        )
        self.assertEqual(new_login.status_code, 200, new_login.text)

    def test_production_settings_reject_default_secret_and_missing_bootstrap_token(self):
        with self.assertRaisesRegex(RuntimeError, "JWT_SECRET"):
            validate_production_settings(Settings(env="production", bootstrap_token="boot"))
        with self.assertRaisesRegex(RuntimeError, "BOOTSTRAP_TOKEN"):
            validate_production_settings(Settings(env="production", jwt_secret="not-default"))
        with self.assertRaisesRegex(RuntimeError, "REMOTE_SYNC_URL"):
            validate_production_settings(
                Settings(
                    env="production",
                    jwt_secret="not-default",
                    bootstrap_token="boot",
                    remote_sync_url="https://coordinator.example",
                )
            )

    def test_production_bootstrap_requires_bootstrap_token(self):
        original_env = app_module.settings.env
        original_token = app_module.settings.bootstrap_token
        app_module.settings.env = "production"
        app_module.settings.bootstrap_token = "boot-token"
        try:
            missing = self.client.post(
                "/api/v1/admin/bootstrap",
                json={
                    "organization_name": "Demo Org",
                    "site_name": "North Site",
                    "admin_email": "admin@example.org",
                    "admin_password": "change-this-password",
                },
            )
            self.assertEqual(missing.status_code, 401)
            created = self.client.post(
                "/api/v1/admin/bootstrap",
                headers={"X-Bootstrap-Token": "boot-token"},
                json={
                    "organization_name": "Demo Org",
                    "site_name": "North Site",
                    "admin_email": "admin@example.org",
                    "admin_password": "change-this-password",
                },
            )
            self.assertEqual(created.status_code, 201, created.text)
        finally:
            app_module.settings.env = original_env
            app_module.settings.bootstrap_token = original_token

    def test_frontend_does_not_prefill_default_auth_secrets(self):
        html = (Path(__file__).resolve().parents[1] / "apps" / "web" / "index.html").read_text()
        self.assertNotIn('value="change-this-password"', html)
        self.assertNotIn('value="000000"', html)

    def test_ci_and_deploy_workflows_use_production_checks(self):
        root = Path(__file__).resolve().parents[1]
        ci = (root / ".github" / "workflows" / "ci.yml").read_text()
        deploy = (root / ".github" / "workflows" / "deploy.yml").read_text()
        backup = (root / "infra" / "backup-sqlite.sh").read_text()

        self.assertIn("python -m unittest discover -s tests", ci)
        self.assertIn("node --check apps/web/app.js", ci)
        self.assertIn("/api/v1/health", deploy)
        self.assertIn("PEACEPULSE_JWT_SECRET", deploy)
        self.assertIn("PEACEPULSE_BOOTSTRAP_TOKEN", deploy)
        self.assertIn("--verify", backup)

    def bootstrap_site_id(self):
        # The database is already bootstrapped by token(); fetch the site through a public report failure-free path.
        with self.SessionLocal() as db:
            from services.api_prod.models import Site

            return db.query(Site).first().id

    def test_signed_hub_sync_rejects_local_only_payload_fields(self):
        boot = self.bootstrap()
        payload = {
            "idempotency_key": "batch-0001",
            "items": [
                {"item_type": "incident_summary", "item_id": "inc_1", "payload": {"redacted_text": "[redacted-phone]"}},
                {"item_type": "evidence_record", "item_id": "evd_1", "payload": {"encrypted_path": "data/storage/evidence.bin"}},
            ],
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = sign_hub_payload(hash_token(boot["hub_secret"]), body)

        response = self.client.post(
            f"/api/v1/hubs/{boot['hub_id']}/sync/batches",
            content=body,
            headers={
                "content-type": "application/json",
                "x-hub-id": boot["hub_id"],
                "x-hub-signature": signature,
            },
        )

        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["accepted"], 1)
        self.assertEqual(result["rejected"], 1)

    def test_sync_run_marks_pending_records_synced_and_keeps_history(self):
        boot = self.bootstrap()
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={"rough_location": "North water point", "category_hint": "resource", "text": "Families are turned away after long water queues."},
        )

        preview = self.client.get("/api/v1/sync/preview", headers=headers)
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json())
        self.assertTrue(all(item["status"] == "pending" for item in preview.json()))

        result = self.client.post("/api/v1/sync/run", headers=headers, json={})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertGreaterEqual(result.json()["synced"], 1)
        self.assertEqual(result.json()["pending"], 0)
        self.assertEqual(result.json()["delivery_mode"], "local")
        self.assertEqual(result.json()["delivery_state"], "pushed")

        after = self.client.get("/api/v1/sync/preview", headers=headers)
        self.assertEqual(after.status_code, 200, after.text)
        self.assertEqual(after.json(), [])

        history = self.client.get("/api/v1/sync/history", headers=headers)
        self.assertEqual(history.status_code, 200, history.text)
        self.assertTrue(history.json())
        self.assertTrue(any(item["status"] == "synced" and item["synced_at"] for item in history.json()))

    def test_sync_run_pushes_pending_records_to_remote_coordinator(self):
        boot = self.bootstrap()
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={
                "rough_location": "North water point",
                "category_hint": "resource",
                "text": "Families are turned away after long water queues near Block C-12.",
            },
        )

        original_remote_url = app_module.settings.remote_sync_url
        original_remote_hub_id = app_module.settings.remote_sync_hub_id
        original_remote_hub_secret = app_module.settings.remote_sync_hub_secret
        original_remote_timeout = app_module.settings.remote_sync_timeout_seconds
        app_module.settings.remote_sync_url = "https://coordinator.example"
        app_module.settings.remote_sync_hub_id = "remote-hub-123"
        app_module.settings.remote_sync_hub_secret = "remote-sync-secret"
        app_module.settings.remote_sync_timeout_seconds = 1.5
        captured = {}

        def handler(request):
            captured["path"] = request.url.path
            captured["headers"] = {key.lower(): value for key, value in request.headers.items()}
            captured["body"] = json.loads(request.content.decode("utf-8"))
            expected_signature = sign_hub_payload(hash_token(app_module.settings.remote_sync_hub_secret), request.content)
            self.assertEqual(captured["headers"]["x-hub-id"], app_module.settings.remote_sync_hub_id)
            self.assertEqual(captured["headers"]["x-hub-signature"], expected_signature)
            self.assertNotIn("raw_text", json.dumps(captured["body"]))
            self.assertNotIn("encrypted_path", json.dumps(captured["body"]))
            self.assertNotIn("content_base64", json.dumps(captured["body"]))
            return httpx.Response(
                200,
                json={
                    "batch_id": "sbn_remote_1",
                    "accepted": len(captured["body"]["items"]),
                    "rejected": 0,
                    "results": [{"item_id": item["item_id"], "status": "accepted"} for item in captured["body"]["items"]],
                },
            )

        original_remote_client = app_module.services._remote_sync_client
        app_module.services._remote_sync_client = lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)
        try:
            result = self.client.post("/api/v1/sync/run", headers=headers, json={})
            self.assertEqual(result.status_code, 200, result.text)
            payload = result.json()
            self.assertEqual(payload["delivery_mode"], "remote")
            self.assertEqual(payload["delivery_state"], "pushed")
            self.assertEqual(payload["synced"], 1)
            self.assertEqual(payload["pending"], 0)
            self.assertEqual(captured["path"], f"/api/v1/hubs/{app_module.settings.remote_sync_hub_id}/sync/batches")

            after = self.client.get("/api/v1/sync/preview", headers=headers)
            self.assertEqual(after.status_code, 200, after.text)
            self.assertEqual(after.json(), [])
            history = self.client.get("/api/v1/sync/history", headers=headers)
            self.assertEqual(history.status_code, 200, history.text)
            self.assertTrue(any(item["status"] == "synced" for item in history.json()))
        finally:
            app_module.services._remote_sync_client = original_remote_client
            app_module.settings.remote_sync_url = original_remote_url
            app_module.settings.remote_sync_hub_id = original_remote_hub_id
            app_module.settings.remote_sync_hub_secret = original_remote_hub_secret
            app_module.settings.remote_sync_timeout_seconds = original_remote_timeout

    def test_sync_run_keeps_records_pending_when_remote_push_fails(self):
        boot = self.bootstrap()
        login = self.client.post(
            "/api/v1/auth/login",
            json={"email": "admin@example.org", "password": "change-this-password"},
        )
        headers = {"authorization": f"Bearer {login.json()['access_token']}"}
        self.client.post(
            f"/api/v1/public/sites/{boot['site_id']}/reports",
            json={"rough_location": "North water point", "category_hint": "resource", "text": "Families are turned away after long water queues."},
        )

        original_remote_url = app_module.settings.remote_sync_url
        original_remote_hub_id = app_module.settings.remote_sync_hub_id
        original_remote_hub_secret = app_module.settings.remote_sync_hub_secret
        app_module.settings.remote_sync_url = "https://coordinator.example"
        app_module.settings.remote_sync_hub_id = "remote-hub-123"
        app_module.settings.remote_sync_hub_secret = "remote-sync-secret"

        def handler(_request):
            return httpx.Response(503, json={"detail": "coordinator down"})

        original_remote_client = app_module.services._remote_sync_client
        app_module.services._remote_sync_client = lambda timeout: httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)
        try:
            result = self.client.post("/api/v1/sync/run", headers=headers, json={})
            self.assertEqual(result.status_code, 200, result.text)
            payload = result.json()
            self.assertEqual(payload["delivery_mode"], "remote")
            self.assertEqual(payload["delivery_state"], "failed")
            self.assertEqual(payload["synced"], 0)
            self.assertEqual(payload["pending"], 1)
            self.assertIn("coordinator down", payload["delivery_detail"])

            preview = self.client.get("/api/v1/sync/preview", headers=headers)
            self.assertEqual(preview.status_code, 200, preview.text)
            self.assertEqual(len(preview.json()), 1)
            self.assertEqual(preview.json()[0]["status"], "pending")
        finally:
            app_module.services._remote_sync_client = original_remote_client
            app_module.settings.remote_sync_url = original_remote_url
            app_module.settings.remote_sync_hub_id = original_remote_hub_id
            app_module.settings.remote_sync_hub_secret = original_remote_hub_secret


if __name__ == "__main__":
    unittest.main()
