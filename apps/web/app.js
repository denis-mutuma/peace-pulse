const state = {
  offline: localStorage.getItem("peacepulse-offline") === "true",
  role: localStorage.getItem("peacepulse-role") || "community",
  incidents: [],
};

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
  const content_base64 = await readAsDataUrl(file);
  await api("/api/evidence", {
    method: "POST",
    body: {
      filename: file.name,
      mime_type: file.type || "application/octet-stream",
      content_base64,
      sync_allowed: form.elements.sync_allowed.checked,
    },
  });
  form.reset();
  await loadEvidence();
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
  const status = await api("/api/sync/status");
  $("#syncStatus").textContent = `${status.pending || 0} pending, ${status.synced || 0} synced`;
}

async function runSync() {
  await api("/api/sync/run", { method: "POST", body: {} });
  await loadSync();
}

async function refreshAll() {
  await Promise.allSettled([loadIncidents(), loadEvidence(), loadResources(), loadRumors(), loadSync(), checkHub()]);
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
      $$(".tab").forEach((item) => item.classList.remove("active"));
      $$(".view").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      $(`#${tab.dataset.view}`).classList.add("active");
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

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

bind();
applyRole();
updateQueueCount();
updateTextCount();
refreshAll();
