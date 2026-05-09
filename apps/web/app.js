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
  try {
    const result = await api("/api/reports", { method: "POST", body: payload });
    setResult(`Submitted and triaged as ${result.incident.category} with severity ${result.incident.severity}.`);
    form.reset();
    form.elements.text.value = "";
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

async function refreshAll() {
  await Promise.allSettled([loadIncidents(), checkHub()]);
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
  $("#reportForm").addEventListener("input", () => setResult(""));
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

bind();
updateQueueCount();
refreshAll();
