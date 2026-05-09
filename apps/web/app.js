const state = {
  offline: localStorage.getItem("peacepulse-offline") === "true",
  role: localStorage.getItem("peacepulse-role") || "community",
  incidents: [],
  lastSyncAt: localStorage.getItem("peacepulse-last-sync") || "",
  demoLog: JSON.parse(localStorage.getItem("peacepulse-demo-log") || "[]"),
};

const MAX_EVIDENCE_BYTES = 2_000_000;
const EVIDENCE_MIME_PREFIXES = ["image/", "audio/", "text/", "application/pdf"];
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));

async function api(path, options = {}) {
  if (state.offline && options.method && options.method !== "GET") {
    const error = new Error("Queued locally while offline.");
    error.queueable = true;
    throw error;
  }
  let response;
  try {
    response = await fetch(path, {
      headers: { "content-type": "application/json" },
      ...options,
      body: options.body ? JSON.stringify(options.body) : undefined,
    });
  } catch (cause) {
    const error = new Error("Hub unreachable. Report queued locally.");
    error.queueable = true;
    error.cause = cause;
    throw error;
  }
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || "Request failed.");
    error.status = response.status;
    throw error;
  }
  return payload;
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function setResult(text) {
  $("#reportResult").textContent = text;
}

function setDashboardResult(text) {
  $("#dashboardResult").textContent = text;
}

function setDemoResult(text) {
  $("#demoResult").textContent = text;
}

function queue() {
  return JSON.parse(localStorage.getItem("peacepulse-report-queue") || "[]").map((item) => {
    if (item.payload) return item;
    return {
      id: newLocalId(),
      queued_at: new Date().toISOString(),
      payload: item,
    };
  });
}

function saveQueue(items) {
  localStorage.setItem("peacepulse-report-queue", JSON.stringify(items));
  updateQueueCount();
}

function updateQueueCount() {
  const items = queue();
  $("#queueCount").textContent = `${items.length} pending browser item${items.length === 1 ? "" : "s"}`;
  $("#queueList").innerHTML = items.slice(0, 5).map((item) => `
    <p><strong>${escapeHtml(item.payload.category_hint || "report")}</strong> queued ${new Date(item.queued_at).toLocaleString()}</p>
  `).join("");
}

function newLocalId() {
  return `local_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function updateTextCount() {
  const text = $("#reportForm").elements.text.value;
  $("#textCount").textContent = `${text.length} / 2000 characters`;
}

async function submitReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formData(form);
  try {
    const result = await api("/api/reports", { method: "POST", body: payload });
    setResult(`Submitted and triaged as ${result.incident.category} with severity ${result.incident.severity}.`);
    form.reset();
    form.elements.text.value = "";
    updateTextCount();
    await refreshAll();
  } catch (error) {
    if (error.queueable) {
      saveQueue([...queue(), { id: newLocalId(), queued_at: new Date().toISOString(), payload }]);
    }
    setResult(error.message);
  }
}

const demoPayloads = {
  report: {
    language: "en",
    rough_location: "North water point",
    category_hint: "resource",
    text: "There is tension at the main water point because some families are being turned away after long queues.",
  },
  evidence: {
    filename: "water-point-note.txt",
    mime_type: "text/plain",
    content_base64: btoa("Steward note: queue pressure and allegations of favoritism need mediation review."),
    sync_allowed: true,
  },
  resource: {
    resource_id: "water-point-north",
    queue_length: 58,
    flow_rate: 0.3,
    uptime: 0,
    maintenance_note: "Pump inspection requested during mediation review",
  },
  rumor: {
    language: "en",
    rough_location: "North water point",
    text: "People say aid is being diverted before it reaches the water point.",
    response_notes: "Verify through service desk and publish a non-identifying update.",
  },
};

const demoActions = {
  async report() {
    const result = await api("/api/reports", { method: "POST", body: demoPayloads.report });
    return `Report triaged as ${result.incident.category} with severity ${result.incident.severity}.`;
  },
  async evidence() {
    const result = await api("/api/evidence", { method: "POST", body: demoPayloads.evidence });
    return `Evidence hashed ${result.sha256.slice(0, 16)}... and stored locally.`;
  },
  async resource() {
    const result = await api("/api/sensor-events", { method: "POST", body: demoPayloads.resource });
    return `Resource anomaly recorded: ${result.anomaly}.`;
  },
  async rumor() {
    const result = await api("/api/rumors", { method: "POST", body: demoPayloads.rumor });
    return `Rumor cluster queued with severity ${result.severity}.`;
  },
};

async function runDemoStep(action) {
  const button = $(`[data-demo-action="${action}"]`);
  button.disabled = true;
  setDemoResult("Running scenario step...");
  try {
    const message = await demoActions[action]();
    addDemoLog(action, message);
    setDemoResult(message);
    await refreshAll();
  } catch (error) {
    setDemoResult(error.message);
  } finally {
    button.disabled = false;
  }
}

function addDemoLog(action, message) {
  state.demoLog = [
    {
      action,
      message,
      created_at: new Date().toISOString(),
    },
    ...state.demoLog,
  ].slice(0, 8);
  localStorage.setItem("peacepulse-demo-log", JSON.stringify(state.demoLog));
  renderDemoLog();
}

function resetDemoLog() {
  state.demoLog = [];
  localStorage.removeItem("peacepulse-demo-log");
  setDemoResult("");
  renderDemoLog();
}

function renderDemoLog() {
  $("#demoLog").innerHTML = state.demoLog.map((item) => `
    <p><strong>${escapeHtml(item.action)}</strong> ${escapeHtml(item.message)}<br>${new Date(item.created_at).toLocaleString()}</p>
  `).join("") || `<p class="empty">Run the scenario steps to build the demo story.</p>`;
}

async function flushQueue() {
  const items = queue();
  const remaining = [];
  let accepted = 0;
  let rejected = 0;
  for (const item of items) {
    try {
      await api("/api/reports", { method: "POST", body: item.payload });
      accepted += 1;
    } catch (error) {
      if (error.queueable) {
        remaining.push(item);
      } else {
        rejected += 1;
      }
    }
  }
  saveQueue(remaining);
  if (remaining.length) {
    setResult(`${accepted} sent, ${rejected} rejected, ${remaining.length} still queued.`);
  } else if (rejected) {
    setResult(`${accepted} sent, ${rejected} rejected by the hub.`);
  } else {
    setResult(`${accepted} queued report${accepted === 1 ? "" : "s"} sent to the hub.`);
  }
  await refreshAll();
}

function renderIncidents(items) {
  const status = $("#statusFilter").value;
  const category = $("#categoryFilter").value;
  const minSeverity = Number($("#severityFilter").value || 1);
  const filtered = items.filter((item) =>
    (!status || item.status === status) &&
    (!category || item.category === category) &&
    item.severity >= minSeverity
  );
  if (!filtered.length) {
    $("#incidentGrid").innerHTML = `<p class="empty">No incidents match the current filters.</p>`;
    return;
  }
  $("#incidentGrid").innerHTML = filtered.map((item) => `
    <article class="card">
      <h3>${item.category.replaceAll("_", " ")}</h3>
      <span class="badge risk">Severity ${item.severity}</span>
      <span class="badge">${item.status}</span>
      <span class="badge">${Math.round(item.confidence * 100)}% confidence</span>
      <p>${escapeHtml(item.redacted_text)}</p>
      <p><strong>Cluster:</strong> ${escapeHtml(item.cluster_key)}</p>
      <p><strong>Public update:</strong> ${escapeHtml(item.public_update)}</p>
      <select data-status="${item.id}">
        ${["new", "assigned", "in_progress", "resolved"].map((status) => `<option ${status === item.status ? "selected" : ""}>${status}</option>`).join("")}
      </select>
    </article>
  `).join("");
  $$("[data-status]").forEach((select) => {
    select.addEventListener("change", async () => {
      try {
        await api(`/api/incidents/${select.dataset.status}/status`, { method: "PATCH", body: { status: select.value } });
        setDashboardResult("Incident status updated.");
        await loadIncidents();
      } catch (error) {
        setDashboardResult(error.message);
      }
    });
  });
}

async function loadIncidents() {
  state.incidents = await api("/api/incidents");
  renderIncidents(state.incidents);
}

async function uploadEvidence(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.elements.file.files[0];
  if (!file) return;
  try {
    validateEvidenceFile(file);
    const content_base64 = await readAsDataUrl(file);
    await api("/api/evidence", {
      method: "POST",
      body: {
        filename: file.name,
        mime_type: file.type,
        content_base64,
        sync_allowed: form.elements.sync_allowed.checked,
      },
    });
    $("#evidenceResult").textContent = "Evidence uploaded, hashed, and stored locally.";
    form.reset();
    await loadEvidence();
    if (state.role === "coordinator") await loadSync().catch(() => {});
  } catch (error) {
    $("#evidenceResult").textContent = error.message;
  }
}

function validateEvidenceFile(file) {
  if (file.size > MAX_EVIDENCE_BYTES) {
    throw new Error("Evidence file must be 2 MB or smaller.");
  }
  if (!EVIDENCE_MIME_PREFIXES.some((prefix) => file.type.startsWith(prefix))) {
    throw new Error("Use an image, audio file, text file, or PDF.");
  }
}

async function loadEvidence() {
  const items = await api("/api/evidence");
  $("#evidenceList").innerHTML = items.map((item) => `
    <article class="card">
      <h3>${escapeHtml(item.filename)}</h3>
      <span class="badge">${item.size_bytes} bytes</span>
      <span class="badge">${item.sync_allowed ? "sync allowed" : "local only"}</span>
      <p><strong>SHA-256:</strong> ${escapeHtml(item.sha256.slice(0, 24))}...</p>
      <p>${item.custody.map((event) => escapeHtml(event.action)).join("<br>")}</p>
    </article>
  `).join("") || `<p class="empty">No evidence records yet.</p>`;
}

async function simulateSensor() {
  const queue_length = Math.floor(10 + Math.random() * 58);
  const flow_rate = Number((Math.random() * 9).toFixed(1));
  const uptime = flow_rate < 1.2 ? 0 : 1;
  await api("/api/sensor-events", {
    method: "POST",
    body: {
      resource_id: "water-point-north",
      queue_length,
      flow_rate,
      uptime,
      maintenance_note: uptime ? "" : "Pump inspection requested",
    },
  });
  await loadResources();
}

async function loadResources() {
  const items = await api("/api/resources/status");
  $("#resourceGrid").innerHTML = items.map((item) => `
    <article class="card">
      <h3>${escapeHtml(item.resource_id)}</h3>
      <span class="badge ${item.anomaly === "normal" ? "" : "risk"}">${escapeHtml(item.anomaly)}</span>
      <p>Queue: ${item.queue_length}</p>
      <p>Flow: ${item.flow_rate}</p>
      <p>Uptime: ${item.uptime ? "online" : "offline"}</p>
      <p>${escapeHtml(item.maintenance_note || "No maintenance note")}</p>
    </article>
  `).join("") || `<p class="empty">No resource events yet.</p>`;
}

async function submitRumor(event) {
  event.preventDefault();
  await api("/api/rumors", { method: "POST", body: formData(event.currentTarget) });
  event.currentTarget.reset();
  await loadRumors();
}

async function loadRumors() {
  const clusters = await api("/api/rumors/clusters");
  $("#rumorGrid").innerHTML = clusters.map((cluster) => `
    <article class="card">
      <h3>${escapeHtml(cluster.cluster_key)}</h3>
      <span class="badge risk">Max severity ${cluster.max_severity}</span>
      <span class="badge">${cluster.count} report${cluster.count === 1 ? "" : "s"}</span>
      ${cluster.items.map((item) => `<p>${escapeHtml(item.redacted_text)}<br><strong>Response:</strong> ${escapeHtml(item.response_notes || "Needs steward review")}</p>`).join("")}
    </article>
  `).join("") || `<p class="empty">No rumor clusters yet.</p>`;
}

async function loadSync() {
  const [health, resources, preview] = await Promise.all([
    api("/api/health"),
    api("/api/resources/status"),
    api("/api/sync/preview"),
  ]);
  const latestResource = resources.find((item) => item.anomaly !== "normal") || resources[0];
  $("#healthHub").textContent = health.ok ? "Online" : "Check";
  $("#healthDatabase").textContent = health.database || "Unknown";
  $("#syncStatus").textContent = `${health.sync.pending || 0} pending / ${health.sync.synced || 0} synced`;
  $("#healthResource").textContent = latestResource ? latestResource.anomaly : "No events";
  $("#healthLastSync").textContent = state.lastSyncAt ? new Date(state.lastSyncAt).toLocaleString() : "Not run";
  renderSyncPreview(preview);
}

async function runSync() {
  const result = await api("/api/sync/run", { method: "POST", body: {} });
  if (result.synced > 0) {
    state.lastSyncAt = new Date().toISOString();
    localStorage.setItem("peacepulse-last-sync", state.lastSyncAt);
  }
  await loadSync();
}

function renderSyncPreview(items) {
  if (!items.length) {
    $("#syncPreview").innerHTML = `<p class="empty">No sync records yet.</p>`;
    return;
  }
  $("#syncPreview").innerHTML = items.map((item) => `
    <article class="card syncItem">
      <div>
        <h3>${escapeHtml(item.item_type.replaceAll("_", " "))}</h3>
        <span class="badge">${escapeHtml(item.status)}</span>
        <span class="badge">${new Date(item.created_at).toLocaleString()}</span>
      </div>
      <dl>
        ${Object.entries(item.summary).map(([key, value]) => `
          <div>
            <dt>${escapeHtml(key.replaceAll("_", " "))}</dt>
            <dd>${escapeHtml(value ?? "not set")}</dd>
          </div>
        `).join("")}
      </dl>
      <p>Payload keys: ${item.payload_keys.map(escapeHtml).join(", ")}</p>
    </article>
  `).join("");
}

async function refreshAll() {
  await Promise.allSettled([loadIncidents(), loadEvidence(), loadResources(), loadRumors(), checkHub()]);
  if (state.role === "coordinator") await loadSync().catch(() => {});
}

async function checkHub() {
  try {
    if (state.offline) throw new Error("offline");
    await api("/api/health");
    $("#apiStatus").textContent = "Hub online";
  } catch {
    $("#apiStatus").textContent = "Offline demo mode";
  }
  $("#offlineToggle").textContent = state.offline ? "Go online" : "Go offline";
}

function readAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result);
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

function applyRole() {
  $("#roleSelect").value = state.role;
  $$("[data-role]").forEach((item) => {
    const required = item.dataset.role;
    const visible = state.role === required || (required === "steward" && state.role === "coordinator");
    item.hidden = !visible;
  });
  const activeTab = $(".tab.active");
  if (activeTab?.hidden) activateView("report");
  if (state.role === "coordinator") loadSync().catch(() => {});
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function bind() {
  $$(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      if (tab.hidden) return;
      activateView(tab.dataset.view);
    });
  });
  $("#offlineToggle").addEventListener("click", () => {
    state.offline = !state.offline;
    localStorage.setItem("peacepulse-offline", state.offline);
    checkHub();
  });
  $("#roleSelect").addEventListener("change", () => {
    state.role = $("#roleSelect").value;
    localStorage.setItem("peacepulse-role", state.role);
    applyRole();
  });
  $("#reportForm").addEventListener("submit", submitReport);
  $("#flushQueue").addEventListener("click", flushQueue);
  $("#refreshDashboard").addEventListener("click", refreshAll);
  $("#evidenceForm").addEventListener("submit", uploadEvidence);
  $("#simulateSensor").addEventListener("click", simulateSensor);
  $("#rumorForm").addEventListener("submit", submitRumor);
  $("#runSync").addEventListener("click", runSync);
  $("#resetDemo").addEventListener("click", resetDemoLog);
  $$("[data-demo-action]").forEach((button) => {
    button.addEventListener("click", () => runDemoStep(button.dataset.demoAction));
  });
  $("#reportForm").addEventListener("input", () => {
    setResult("");
    updateTextCount();
  });
  ["#statusFilter", "#categoryFilter", "#severityFilter"].forEach((selector) => {
    $(selector).addEventListener("change", () => {
      setDashboardResult("");
      renderIncidents(state.incidents);
    });
  });
}

function activateView(view) {
  $$(".tab").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === view));
  if (view === "sync") loadSync().catch(() => {});
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

bind();
applyRole();
updateQueueCount();
updateTextCount();
renderDemoLog();
refreshAll();
