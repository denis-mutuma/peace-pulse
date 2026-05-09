const state = {
  offline: localStorage.getItem("peacepulse-offline") === "true",
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

function queue() {
  return JSON.parse(localStorage.getItem("peacepulse-report-queue") || "[]");
}

function saveQueue(items) {
  localStorage.setItem("peacepulse-report-queue", JSON.stringify(items));
  updateQueueCount();
}

function updateQueueCount() {
  $("#queueCount").textContent = `${queue().length} pending browser item${queue().length === 1 ? "" : "s"}`;
}

async function submitReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = formData(form);
  payload.consent_to_sync = form.elements.consent_to_sync.checked;
  try {
    const result = await api("/api/reports", { method: "POST", body: payload });
    setResult(`Submitted and triaged as ${result.incident.category} with severity ${result.incident.severity}.`);
    await refreshAll();
  } catch (error) {
    if (error.queueable) {
      saveQueue([...queue(), payload]);
    }
    setResult(error.message);
  }
}

async function flushQueue() {
  const items = queue();
  const remaining = [];
  let rejected = 0;
  for (const item of items) {
    try {
      await api("/api/reports", { method: "POST", body: item });
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
    setResult("Some reports are still queued.");
  } else if (rejected) {
    setResult(`${rejected} queued report${rejected === 1 ? "" : "s"} rejected by the hub.`);
  } else {
    setResult("Queued reports sent to the hub.");
  }
  await refreshAll();
}

function renderIncidents(items) {
  $("#incidentGrid").innerHTML = items.map((item) => `
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
      await api(`/api/incidents/${select.dataset.status}/status`, { method: "PATCH", body: { status: select.value } });
      await loadIncidents();
    });
  });
}

async function loadIncidents() {
  renderIncidents(await api("/api/incidents"));
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
      <p><strong>SHA-256:</strong> ${item.sha256.slice(0, 24)}...</p>
      <p>${item.custody.map((event) => escapeHtml(event.action)).join("<br>")}</p>
    </article>
  `).join("");
}

async function simulateSensor() {
  const queue_length = Math.floor(10 + Math.random() * 58);
  const flow_rate = Number((Math.random() * 9).toFixed(1));
  const uptime = flow_rate < 1.2 ? 0 : 1;
  await api("/api/sensor-events", {
    method: "POST",
    body: { resource_id: "water-point-north", queue_length, flow_rate, uptime },
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
    </article>
  `).join("");
}

async function submitRumor(event) {
  event.preventDefault();
  await api("/api/rumors", { method: "POST", body: formData(event.currentTarget) });
  await loadRumors();
}

async function loadRumors() {
  const clusters = await api("/api/rumors/clusters");
  $("#rumorGrid").innerHTML = clusters.map((cluster) => `
    <article class="card">
      <h3>${escapeHtml(cluster.cluster_key)}</h3>
      <span class="badge risk">Max severity ${cluster.max_severity}</span>
      <span class="badge">${cluster.count} report${cluster.count === 1 ? "" : "s"}</span>
      ${cluster.items.map((item) => `<p>${escapeHtml(item.text)}</p>`).join("")}
    </article>
  `).join("");
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
  $("#reportForm").addEventListener("submit", submitReport);
  $("#flushQueue").addEventListener("click", flushQueue);
  $("#refreshDashboard").addEventListener("click", refreshAll);
  $("#evidenceForm").addEventListener("submit", uploadEvidence);
  $("#simulateSensor").addEventListener("click", simulateSensor);
  $("#rumorForm").addEventListener("submit", submitRumor);
  $("#runSync").addEventListener("click", runSync);
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

bind();
updateQueueCount();
refreshAll();
