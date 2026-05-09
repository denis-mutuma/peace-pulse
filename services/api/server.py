from __future__ import annotations

import json
import mimetypes
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from peacepulse_core import (
    ROOT,
    create_incident_note,
    create_evidence,
    create_opportunity,
    create_report,
    create_route_alert,
    create_resource_event,
    create_rumor,
    health_status,
    incident_timeline,
    init_db,
    list_evidence,
    list_incident_notes,
    list_incidents,
    list_opportunities,
    list_rumor_clusters,
    list_status_history,
    public_report,
    privacy_audit,
    reset_demo_data,
    route_status,
    resource_status,
    run_sync,
    sync_preview,
    sync_status,
    triage_report,
    update_incident_status,
)


WEB_ROOT = ROOT / "apps" / "web"
MAX_JSON_BODY_BYTES = 16_384


class Handler(BaseHTTPRequestHandler):
    server_version = "PeacePulse/0.1"

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/health":
                self.json(health_status())
            elif path == "/api/privacy/audit":
                self.json(privacy_audit())
            elif path.endswith("/timeline") and path.startswith("/api/incidents/"):
                incident_id = path.split("/")[3]
                self.json(incident_timeline(incident_id))
            elif path.endswith("/notes") and path.startswith("/api/incidents/"):
                incident_id = path.split("/")[3]
                self.json(list_incident_notes(incident_id))
            elif path.endswith("/history") and path.startswith("/api/incidents/"):
                incident_id = path.split("/")[3]
                self.json(list_status_history(incident_id))
            elif path == "/api/incidents":
                self.json(list_incidents())
            elif path == "/api/evidence":
                self.json(list_evidence())
            elif path == "/api/resources/status":
                self.json(resource_status())
            elif path == "/api/routes/status":
                self.json(route_status())
            elif path == "/api/work/opportunities":
                self.json(list_opportunities())
            elif path == "/api/rumors/clusters":
                self.json(list_rumor_clusters())
            elif path == "/api/sync/status":
                self.json(sync_status())
            elif path == "/api/sync/preview":
                self.json(sync_preview())
            elif path.startswith("/api/"):
                self.error(404, "Route not found.")
            else:
                self.static(path)
        except Exception as exc:
            self.error(500, str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.body()
            if path == "/api/reports":
                report = create_report(body)
                incident = triage_report(report["id"])
                self.json({"report": public_report(report), "incident": incident}, 201)
            elif path.endswith("/notes") and path.startswith("/api/incidents/"):
                incident_id = path.split("/")[3]
                self.json(create_incident_note(incident_id, body), 201)
            elif path == "/api/evidence":
                self.json(create_evidence(body), 201)
            elif path == "/api/sensor-events":
                self.json(create_resource_event(body), 201)
            elif path == "/api/routes/alerts":
                self.json(create_route_alert(body), 201)
            elif path == "/api/work/opportunities":
                self.json(create_opportunity(body), 201)
            elif path == "/api/rumors":
                self.json(create_rumor(body), 201)
            elif path == "/api/sync/run":
                self.json(run_sync())
            elif path == "/api/demo/reset":
                self.json(reset_demo_data())
            else:
                self.error(404, "Route not found.")
        except ValueError as exc:
            self.error(400, str(exc))
        except Exception as exc:
            self.error(500, str(exc))

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self.body()
            if path.endswith("/status") and path.startswith("/api/incidents/"):
                incident_id = path.split("/")[3]
                self.json(update_incident_status(incident_id, body.get("status", ""), body.get("actor_label", "responder")))
            else:
                self.error(404, "Route not found.")
        except ValueError as exc:
            self.error(400, str(exc))
        except Exception as exc:
            self.error(500, str(exc))

    def body(self) -> dict:
        length = int(self.headers.get("content-length") or 0)
        if length == 0:
            return {}
        if length > MAX_JSON_BODY_BYTES:
            raise ValueError("Request body is too large.")
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Request body must be valid JSON.") from exc

    def json(self, payload: object, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def error(self, status: int, message: str) -> None:
        self.json({"error": message}, status)

    def static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        web_root = WEB_ROOT.resolve()
        target = (WEB_ROOT / path.lstrip("/")).resolve()
        if not target.is_relative_to(web_root) or not target.exists() or target.is_dir():
            target = WEB_ROOT / "index.html"
        content = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(str(target))[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"{self.address_string()} - {fmt % args}")


def main() -> None:
    init_db(seed_demo_data=True)
    host = os.environ.get("PEACEPULSE_HOST", "0.0.0.0")
    port = int(os.environ.get("PEACEPULSE_PORT", "8080"))
    print(f"PeacePulse edge hub listening on http://localhost:{port}")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
