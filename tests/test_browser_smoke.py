import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1] / "services" / "api"))

import peacepulse_core as core
import server


class BrowserSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.chromium = (
            shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
        )
        if not cls.chromium:
            raise unittest.SkipTest("Chromium is required for browser smoke tests.")

        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.original_data_dir = core.DATA_DIR
        cls.original_db_path = core.DB_PATH
        cls.original_evidence_dir = core.EVIDENCE_DIR
        core.DATA_DIR = cls.root / "data"
        core.DB_PATH = core.DATA_DIR / "peacepulse.db"
        core.EVIDENCE_DIR = core.DATA_DIR / "storage" / "evidence"
        core.init_db(seed_demo_data=True)

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

        cls.chrome_dir = cls.root / "chrome-profile"
        cls.chrome_dir.mkdir()
        cls.chrome_port = cls._free_port()
        cls.proc = subprocess.Popen(
            [
                cls.chromium,
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                f"--remote-debugging-port={cls.chrome_port}",
                f"--user-data-dir={cls.chrome_dir}",
                "about:blank",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            ws_url = cls._wait_for_devtools_url()
            cls.browser = DevToolsClient(ws_url)
        except Exception as exc:
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
            cls.httpd.shutdown()
            cls.httpd.server_close()
            cls.tmp.cleanup()
            raise unittest.SkipTest(f"Chromium could not start headless: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "browser"):
            cls.browser.close()
        if hasattr(cls, "proc"):
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
        if hasattr(cls, "httpd"):
            cls.httpd.shutdown()
            cls.httpd.server_close()
        core.DATA_DIR = cls.original_data_dir
        core.DB_PATH = cls.original_db_path
        core.EVIDENCE_DIR = cls.original_evidence_dir
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    @classmethod
    def _wait_for_devtools_url(cls):
        deadline = time.time() + 10
        while time.time() < deadline:
            if cls.proc.poll() is not None:
                raise RuntimeError(f"Chromium exited with status {cls.proc.returncode}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{cls.chrome_port}/json/list", timeout=2) as response:
                    targets = json.loads(response.read().decode("utf-8"))
                for target in targets:
                    if target.get("type") == "page":
                        return target["webSocketDebuggerUrl"]
            except (OSError, urllib.error.URLError):
                pass
            time.sleep(0.05)
        raise TimeoutError("Chromium DevTools endpoint did not become available.")

    @staticmethod
    def _free_port():
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def setUp(self):
        core.reset_demo_data()
        self.navigate("/")
        self.evaluate(
            """
            localStorage.clear();
            sessionStorage.clear();
            """
        )
        self.navigate("/")

    def navigate(self, path):
        self.browser.command("Page.navigate", {"url": f"{self.base_url}{path}"})
        self.evaluate(
            """
            waitForSmoke(() => document.querySelector("#apiStatus")?.textContent === "Hub online")
            """,
            timeout=5,
        )

    def evaluate(self, expression, timeout=3):
        source = SMOKE_HELPERS + "\n" + expression
        return self.browser.evaluate(source, timeout=timeout)

    def test_voice_note_upload_links_evidence_and_keeps_sync_preview_redacted(self):
        result = self.evaluate(
            """
            (async () => {
              const form = document.querySelector("#reportForm");
              form.elements.text.value = "Families are turned away after long water queues near Block C-12.";
              form.elements.category_hint.value = "resource";
              form.elements.voice_sync_allowed.checked = true;
              setFile(form.elements.voice_note, "voice-note.webm", "audio/webm", "demo voice bytes");
              form.requestSubmit();
              await waitForSmoke(() => document.querySelector("#reportResult").textContent.includes("Voice note stored"));

              document.querySelector("#roleSelect").value = "coordinator";
              document.querySelector("#roleSelect").dispatchEvent(new Event("change", { bubbles: true }));
              document.querySelector('[data-view="evidence"]').click();
              await waitForSmoke(() => document.querySelector("#evidenceList").textContent.includes("voice-note.webm"));
              document.querySelector('[data-view="sync"]').click();
              await waitForSmoke(() => document.querySelector("#syncPreview").textContent.includes("evidence record"));

              const preview = await fetch("/api/sync/preview").then((response) => response.json());
              const evidence = await fetch("/api/evidence").then((response) => response.json());
              return {
                result: document.querySelector("#reportResult").textContent,
                evidenceText: document.querySelector("#evidenceList").textContent,
                syncText: document.querySelector("#syncPreview").textContent,
                previewJson: JSON.stringify(preview),
                evidenceJson: JSON.stringify(evidence)
              };
            })()
            """,
            timeout=8,
        )

        self.assertIn("Voice note stored as linked local evidence", result["result"])
        self.assertIn("voice-note.webm", result["evidenceText"])
        self.assertIn("Linked report: rep_", result["evidenceText"])
        self.assertIn("evidence_record", result["previewJson"])
        self.assertNotIn("encrypted_path", result["previewJson"])
        self.assertNotIn("demo voice bytes", result["previewJson"])
        self.assertIn("encrypted_path", result["evidenceJson"])

    def test_offline_queue_keeps_voice_bytes_out_of_browser_queue(self):
        result = self.evaluate(
            """
            (async () => {
              document.querySelector("#offlineToggle").click();
              await waitForSmoke(() => document.querySelector("#apiStatus").textContent === "Offline demo mode");

              const form = document.querySelector("#reportForm");
              form.elements.text.value = "Families need help near the water queue.";
              form.elements.category_hint.value = "resource";
              setFile(form.elements.voice_note, "offline-note.webm", "audio/webm", "offline voice bytes");
              form.requestSubmit();
              await waitForSmoke(() => document.querySelector("#queueCount").textContent.startsWith("1 pending"));

              const queue = JSON.parse(localStorage.getItem("peacepulse-report-queue"));
              const offlineMessage = document.querySelector("#reportResult").textContent;
              document.querySelector("#offlineToggle").click();
              await waitForSmoke(() => document.querySelector("#apiStatus").textContent === "Hub online");
              document.querySelector("#flushQueue").click();
              await waitForSmoke(() => document.querySelector("#queueCount").textContent.startsWith("0 pending"));
              return {
                message: offlineMessage,
                queuedJson: JSON.stringify(queue),
                queueCount: document.querySelector("#queueCount").textContent
              };
            })()
            """,
            timeout=8,
        )

        self.assertIn("Text report queued; voice notes require the hub to be online", result["message"])
        self.assertIn("0 pending browser items", result["queueCount"])
        self.assertIn("Families need help near the water queue", result["queuedJson"])
        self.assertNotIn("offline voice bytes", result["queuedJson"])
        self.assertNotIn("voice_note", result["queuedJson"])

    def test_route_and_work_ui_flows_redact_sensitive_notes(self):
        result = self.evaluate(
            """
            (async () => {
              document.querySelector('[data-view="routes"]').click();
              await waitForSmoke(() => document.querySelector("#servicePointGrid").textContent.includes("Clinic route"));
              const routeForm = document.querySelector("#routeForm");
              routeForm.elements.note.value = "Review after reports near Block C-12 and call +254 700 000 000.";
              routeForm.requestSubmit();
              await waitForSmoke(async () => {
                const status = await fetch("/api/routes/status").then((response) => response.json());
                return status.alerts.some((alert) => alert.note.includes("[redacted-location]"));
              });
              await loadRoutes();

              document.querySelector('[data-view="work"]').click();
              await waitForSmoke(() => document.querySelector("#workGrid").textContent.includes("Water committee assistant"));
              const workForm = document.querySelector("#workForm");
              workForm.elements.safety_note.value = "Checked by steward; call +254 700 000 000 only outside the hub.";
              workForm.requestSubmit();
              await waitForSmoke(async () => {
                const items = await fetch("/api/work/opportunities").then((response) => response.json());
                return items.some((item) => item.safety_note.includes("[redacted-phone]"));
              });
              await loadOpportunities();

              return {
                routeText: document.querySelector("#routeAlertGrid").textContent,
                workText: document.querySelector("#workGrid").textContent
              };
            })()
            """,
            timeout=8,
        )

        self.assertIn("[redacted-location]", result["routeText"])
        self.assertIn("[redacted-phone]", result["workText"])
        self.assertNotIn("+254 700 000 000", result["routeText"])
        self.assertNotIn("+254 700 000 000", result["workText"])


SMOKE_HELPERS = """
function waitForSmoke(predicate, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const tick = async () => {
      try {
        if (await predicate()) {
          resolve(true);
          return;
        }
      } catch (_) {}
      if (Date.now() - started > timeout) {
        reject(new Error("Timed out waiting for browser smoke condition."));
        return;
      }
      setTimeout(tick, 25);
    };
    tick();
  });
}

function setFile(input, filename, mimeType, content) {
  const file = new File([content], filename, { type: mimeType });
  const transfer = new DataTransfer();
  transfer.items.add(file);
  input.files = transfer.files;
}
"""


class DevToolsClient:
    def __init__(self, ws_url):
        self.sock = self._connect(ws_url)
        self.next_id = 1

    def close(self):
        self.sock.close()

    def command(self, method, params=None, timeout=3):
        message_id = self.next_id
        self.next_id += 1
        self._send_json({"id": message_id, "method": method, "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = self._recv_json(deadline - time.time())
            if message.get("id") == message_id:
                if "error" in message:
                    raise AssertionError(message["error"])
                return message.get("result", {})
        raise TimeoutError(f"Timed out waiting for CDP response to {method}.")

    def evaluate(self, expression, timeout=3):
        result = self.command(
            "Runtime.evaluate",
            {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
            timeout=timeout,
        )
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = details.get("text") or "JavaScript evaluation failed"
            value = details.get("exception", {}).get("description") or details.get("exception", {}).get("value")
            raise AssertionError(f"{text}: {value}")
        return result.get("result", {}).get("value")

    def _connect(self, ws_url):
        parsed = urllib.parse.urlparse(ws_url)
        sock = socket.create_connection((parsed.hostname, parsed.port), timeout=5)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        target = parsed.path
        if parsed.query:
            target = f"{target}?{parsed.query}"
        request = (
            f"GET {target} HTTP/1.1\r\n"
            f"Host: {parsed.hostname}:{parsed.port}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n"
        )
        sock.sendall(request.encode("ascii"))
        response = b""
        while b"\r\n\r\n" not in response:
            response += sock.recv(4096)
        if b" 101 " not in response.split(b"\r\n", 1)[0]:
            raise RuntimeError(response.decode("latin1", errors="replace"))
        return sock

    def _send_json(self, payload):
        data = json.dumps(payload).encode("utf-8")
        mask = os.urandom(4)
        if len(data) < 126:
            header = struct.pack("!BB", 0x81, 0x80 | len(data))
        elif len(data) < 65536:
            header = struct.pack("!BBH", 0x81, 0x80 | 126, len(data))
        else:
            header = struct.pack("!BBQ", 0x81, 0x80 | 127, len(data))
        masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
        self.sock.sendall(header + mask + masked)

    def _recv_json(self, timeout):
        self.sock.settimeout(max(timeout, 0.1))
        while True:
            first, second = self._recv_exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._recv_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._recv_exact(8))[0]
            if second & 0x80:
                mask = self._recv_exact(4)
                payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(self._recv_exact(length)))
            else:
                payload = self._recv_exact(length)
            if opcode == 1:
                return json.loads(payload.decode("utf-8"))
            if opcode == 8:
                raise ConnectionError("Chrome closed the DevTools websocket.")

    def _recv_exact(self, size):
        data = b""
        while len(data) < size:
            chunk = self.sock.recv(size - len(data))
            if not chunk:
                raise ConnectionError("DevTools websocket closed.")
            data += chunk
        return data


class QuietHandler(server.Handler):
    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    unittest.main()
