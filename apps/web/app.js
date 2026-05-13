const state = {
  offline: localStorage.getItem("peacepulse-offline") === "true",
  accessToken: localStorage.getItem("peacepulse-access-token") || "",
  staff: JSON.parse(localStorage.getItem("peacepulse-staff") || "null"),
  publicSites: JSON.parse(localStorage.getItem("peacepulse-public-sites") || "[]"),
  selectedSiteId: localStorage.getItem("peacepulse-site-id") || "",
  incidents: [],
  resources: [],
  copilotSessionId: localStorage.getItem("peacepulse-copilot-session") || "",
  lastSyncAt: localStorage.getItem("peacepulse-last-sync") || "",
  demoLog: JSON.parse(localStorage.getItem("peacepulse-demo-log") || "[]"),
};

const MAX_EVIDENCE_BYTES = 2_000_000;
const EVIDENCE_MIME_PREFIXES = ["image/", "audio/", "text/", "application/pdf"];
const GUIDED_REPORTS = {
  resource: {
    location: "North water point",
    text: "There is tension at the main water point because some families are being turned away.",
  },
  threat: {
    location: "Clinic route",
    text: "People feel unsafe near the clinic route after dark and want steward review.",
  },
  corruption: {
    location: "Distribution desk",
    text: "Community members are worried that aid is not reaching people fairly.",
  },
  rumor: {
    location: "North water point",
    text: "People say aid is being diverted before it reaches the water point.",
  },
  unsafe_route: {
    location: "East corridor",
    text: "The route to the clinic feels unsafe and needs a service-point update.",
  },
  work_exploitation: {
    location: "Central market",
    text: "A work opportunity may be unsafe or exploitative and needs steward review.",
  },
};
const RISK_PATTERNS = [
  { label: "phone number", pattern: /(?:\+?\d[\d\s().-]{7,}\d)/i },
  { label: "email address", pattern: /\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b/i },
  { label: "ID-like number", pattern: /\b(?:id|passport|permit|card)\s*[#: -]?\s*[a-z0-9-]{5,}\b/i },
  { label: "exact block or unit", pattern: /\b(?:block|unit|house|tent|room)\s+[a-z]?-?\d+\b/i },
  { label: "person name", pattern: /\b(?:mr|mrs|ms|miss|dr|sheikh|pastor)\.?\s+[A-Z][a-z]+\b/ },
];
const PHRASEBOOK = {
  en: {
    samples: [
      "There is tension at the main water point because some families are being turned away.",
      "People say aid is being diverted before it reaches the water point.",
      "The clinic route feels unsafe after dark.",
    ],
    warning: "Avoid names, exact homes, phone numbers, or details that could expose someone.",
  },
  sw: {
    samples: [
      "Kuna mvutano kwenye kituo cha maji kwa sababu baadhi ya familia zinarudishwa.",
      "Watu wanasema msaada unaelekezwa kwingine kabla ya kufika kwenye kituo cha maji.",
      "Njia ya kliniki inaonekana si salama baada ya giza kuingia.",
    ],
    warning: "Epuka majina, nyumba maalum, namba za simu, au taarifa zinazoweza kumtambulisha mtu.",
  },
  fr: {
    samples: [
      "Il y a des tensions au point d'eau car certaines familles sont refoulees.",
      "Des personnes disent que l'aide est detournee avant d'arriver au point d'eau.",
      "La route vers la clinique semble dangereuse apres la tombee de la nuit.",
    ],
    warning: "Evitez les noms, domiciles exacts, numeros de telephone ou details identifiants.",
  },
  ar: {
    samples: [
      "هناك توتر عند نقطة المياه لأن بعض العائلات يتم إرجاعها.",
      "يقول الناس إن المساعدات يتم تحويلها قبل وصولها إلى نقطة المياه.",
      "الطريق إلى العيادة يبدو غير آمن بعد حلول الظلام.",
    ],
    warning: "تجنبوا الأسماء أو المنازل الدقيقة أو أرقام الهاتف أو أي تفاصيل تكشف الهوية.",
  },
};
const VIEW_TITLES = {
  access: "Production Access",
  dashboard: "Incidents",
  demo: "Guided Scenario",
  evidence: "Evidence Locker",
  privacy: "Privacy Audit",
  report: "Anonymous Report",
  resources: "Resource Monitor",
  routes: "Routes And Services",
  rumors: "RumorShield",
  sync: "Hub Sync",
  work: "FairWork Board",
  copilot: "Copilot",
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
    const headers = { "content-type": "application/json", ...(options.headers || {}) };
    if (options.auth && state.accessToken) {
      headers.authorization = `Bearer ${state.accessToken}`;
    }
    response = await fetch(path, {
      ...options,
      headers,
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
    const error = new Error(payload.detail || payload.error || "Request failed.");
    error.status = response.status;
    if (error.status === 401 && options.auth) {
      saveSession("", null);
    }
    throw error;
  }
  return payload;
}

async function v1(path, options = {}) {
  return api(`/api/v1${path}`, options);
}

async function staffApi(path, options = {}) {
  if (!state.accessToken) {
    const error = new Error("Sign in to use staff tools.");
    error.authRequired = true;
    throw error;
  }
  return api(`/api/v1${path}`, { ...options, auth: true });
}

function formData(form) {
  return Object.fromEntries(new FormData(form).entries());
}

function reportPayload(form) {
  const payload = formData(form);
  delete payload.voice_note;
  delete payload.voice_sync_allowed;
  return payload;
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

function currentSiteId() {
  return state.selectedSiteId || state.publicSites[0]?.id || "";
}

function hasStaffAccess() {
  return Boolean(state.accessToken && state.staff);
}

function hasRole(...roles) {
  return Boolean(state.staff?.roles?.some((role) => roles.includes(role)));
}

function hasCoordinatorAccess() {
  return hasRole("coordinator", "org_admin", "system_admin");
}

function saveSession(token, staff = null) {
  state.accessToken = token || "";
  state.staff = staff;
  if (state.accessToken) {
    localStorage.setItem("peacepulse-access-token", state.accessToken);
  } else {
    localStorage.removeItem("peacepulse-access-token");
  }
  if (staff) {
    localStorage.setItem("peacepulse-staff", JSON.stringify(staff));
  } else {
    localStorage.removeItem("peacepulse-staff");
  }
  if (staff) {
    const siteId = staff.site_ids?.[0] || state.selectedSiteId;
    if (siteId) saveSelectedSite(siteId);
  }
  renderAccessState();
  applyRole();
}

function savePublicSites(sites) {
  state.publicSites = sites;
  localStorage.setItem("peacepulse-public-sites", JSON.stringify(sites));
  if (!state.selectedSiteId && sites[0]) saveSelectedSite(sites[0].id);
  renderPublicSites();
}

function saveSelectedSite(siteId) {
  state.selectedSiteId = siteId || "";
  if (state.selectedSiteId) {
    localStorage.setItem("peacepulse-site-id", state.selectedSiteId);
  } else {
    localStorage.removeItem("peacepulse-site-id");
  }
  renderPublicSites();
}

function renderPublicSites() {
  const select = $("#publicSiteSelect");
  if (!select) return;
  if (!state.publicSites.length) {
    select.innerHTML = `<option value="">No active sites</option>`;
    return;
  }
  select.innerHTML = state.publicSites.map((site) => `
    <option value="${escapeHtml(site.id)}" ${site.id === state.selectedSiteId ? "selected" : ""}>${escapeHtml(site.name)} - ${escapeHtml(site.rough_location)}</option>
  `).join("");
}

function renderAccessState() {
  $("#accessPanel").hidden = false;
  $("#logoutStaff").hidden = !hasStaffAccess();
  $("#mfaForm").hidden = !hasStaffAccess();
  const summary = $("#sessionSummary");
  if (hasStaffAccess()) {
    summary.textContent = `Signed in as ${state.staff.email} (${state.staff.roles.join(", ")}). MFA ${state.staff.mfa_enabled ? "enabled" : "not enrolled"}.`;
  } else {
    summary.textContent = "Anonymous reporting is open when the production API is reachable; staff tools require sign in.";
  }
}

async function loadProductionContext() {
  try {
    await v1("/health");
    const sites = await v1("/public/sites");
    savePublicSites(sites);
  } catch {
  }
  renderAccessState();
  applyRole();
}

async function bootstrapTenant(event) {
  event.preventDefault();
  try {
    const payload = formData(event.currentTarget);
    const bootstrapToken = payload.bootstrap_token || "";
    delete payload.bootstrap_token;
    const result = await v1("/admin/bootstrap", {
      method: "POST",
      body: payload,
      headers: bootstrapToken ? { "X-Bootstrap-Token": bootstrapToken } : {},
    });
    saveSelectedSite(result.site_id);
    await loadProductionContext();
    $("#sessionSummary").textContent = `Tenant created. Hub id ${result.hub_id}; sign in with the admin account.`;
  } catch (error) {
    $("#sessionSummary").textContent = error.message;
  }
}

async function loginStaff(event) {
  event.preventDefault();
  try {
    const result = await v1("/auth/login", { method: "POST", body: formData(event.currentTarget) });
    state.accessToken = result.access_token;
    const staff = await v1("/auth/me", { auth: true });
    saveSession(result.access_token, staff);
    await refreshAll();
  } catch (error) {
    $("#sessionSummary").textContent = error.message;
  }
}

async function logoutStaff() {
  if (state.accessToken) {
    await v1("/auth/logout", { method: "POST", body: {}, auth: true }).catch(() => {});
  }
  saveSession("", null);
  activateView("report");
}

async function startMfaEnrollment() {
  try {
    const result = await staffApi("/auth/mfa/enroll", { method: "POST", body: {} });
    $("#mfaSecret").value = result.secret;
    $("#sessionSummary").textContent = "Add the secret to an authenticator app, then verify the current code.";
  } catch (error) {
    $("#sessionSummary").textContent = error.message;
  }
}

async function verifyMfaEnrollment(event) {
  event.preventDefault();
  try {
    await staffApi("/auth/mfa/verify-enrollment", { method: "POST", body: formData(event.currentTarget) });
    const staff = await v1("/auth/me", { auth: true });
    saveSession(state.accessToken, staff);
    $("#sessionSummary").textContent = "MFA enrollment verified.";
  } catch (error) {
    $("#sessionSummary").textContent = error.message;
  }
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
  renderDashboardMetrics();
}

function newLocalId() {
  return `local_${Date.now()}_${Math.random().toString(16).slice(2)}`;
}

function updateTextCount() {
  const text = $("#reportForm").elements.text.value;
  $("#textCount").textContent = `${text.length} / 2000 characters`;
  updateRiskWarning();
}

function sensitiveDetailLabels(text) {
  return RISK_PATTERNS.filter((item) => item.pattern.test(text)).map((item) => item.label);
}

function updateRiskWarning() {
  const labels = sensitiveDetailLabels($("#reportForm").elements.text.value);
  const target = $("#riskWarning");
  target.hidden = labels.length === 0;
  target.innerHTML = labels.length ? `
    <strong>Check before sending:</strong>
    This report may include ${escapeHtml(labels.join(", "))}. Remove identifying details unless they are essential.
  ` : "";
}

function applyGuidedReport(category) {
  const preset = GUIDED_REPORTS[category];
  if (!preset) return;
  const form = $("#reportForm");
  form.elements.category_hint.value = category;
  form.elements.rough_location.value = preset.location;
  form.elements.text.value = preset.text;
  setResult("Guided report loaded. Add only non-identifying details before submitting.");
  updateTextCount();
}

function renderPhrasebook() {
  const language = $("#reportLanguage").value;
  const entry = PHRASEBOOK[language] || PHRASEBOOK.en;
  $("#phrasebook").innerHTML = `
    <p>${escapeHtml(entry.warning)}</p>
    <div>
      ${entry.samples.map((sample) => `<button class="secondary" type="button" data-sample="${escapeHtml(sample)}">${escapeHtml(sample)}</button>`).join("")}
    </div>
  `;
  $$("[data-sample]").forEach((button) => {
    button.addEventListener("click", () => {
      $("#reportForm").elements.text.value = button.dataset.sample;
      updateTextCount();
    });
  });
}

async function submitReport(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const payload = reportPayload(form);
  const voiceFile = form.elements.voice_note.files[0];
  try {
    if (voiceFile) validateVoiceNote(voiceFile);
    const siteId = currentSiteId();
    if (!siteId) {
      throw new Error("Bootstrap or select a production site before submitting a report.");
    }
    const result = await v1(`/public/sites/${siteId}/reports`, { method: "POST", body: payload });
    let voiceMessage = "";
    if (voiceFile) {
      if (hasStaffAccess()) {
        try {
          await uploadVoiceNote(result.id, voiceFile, form.elements.voice_sync_allowed.checked);
          voiceMessage = " Voice-note metadata stored as linked production evidence.";
        } catch (voiceError) {
          voiceMessage = ` Voice-note metadata was not stored: ${voiceError.message}`;
        }
      } else {
        voiceMessage = " Voice-note metadata requires staff sign-in; the text report was submitted.";
      }
    }
    const incident = result.incident || result;
    setResult(`Submitted and triaged as ${incident.category} with severity ${incident.severity}.${voiceMessage}`);
    form.reset();
    form.elements.text.value = "";
    updateTextCount();
    await refreshAll();
  } catch (error) {
    if (error.queueable) {
      saveQueue([...queue(), { id: newLocalId(), queued_at: new Date().toISOString(), payload }]);
      if (voiceFile) {
        setResult(`${error.message} Text report queued; voice notes require the hub to be online.`);
        return;
      }
    }
    setResult(error.message);
  }
}

function validateVoiceNote(file) {
  if (file.size > MAX_EVIDENCE_BYTES) {
    throw new Error("Voice note must be 2 MB or smaller.");
  }
  if (!file.type.startsWith("audio/")) {
    throw new Error("Voice note must be an audio file.");
  }
}

async function uploadVoiceNote(reportId, file, syncAllowed) {
  const sha256 = await fileSha256(file);
  await staffApi("/evidence/uploads", {
    method: "POST",
    body: {
      site_id: currentSiteId(),
      filename: file.name || "voice-note.webm",
      mime_type: file.type,
      size_bytes: file.size,
      sha256,
      linked_report_id: reportId,
      sync_allowed: syncAllowed,
    },
  });
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
    const siteId = currentSiteId();
    const result = await v1(`/public/sites/${siteId}/reports`, { method: "POST", body: demoPayloads.report });
    return `Report triaged as ${result.category} with severity ${result.severity}.`;
  },
  async evidence() {
    const siteId = currentSiteId();
    const text = "Steward note: queue pressure and allegations of favoritism need mediation review.";
    const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
    const sha256 = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
    const result = await staffApi("/evidence/uploads", {
      method: "POST",
      body: {
        site_id: siteId,
        filename: demoPayloads.evidence.filename,
        mime_type: demoPayloads.evidence.mime_type,
        size_bytes: text.length,
        sha256,
        sync_allowed: true,
      },
    });
    return `Evidence metadata created for ${result.object_key}.`;
  },
  async resource() {
    const result = await staffApi("/resources/events", { method: "POST", body: { ...demoPayloads.resource, site_id: currentSiteId() } });
    return `Resource anomaly recorded: ${result.anomaly}.`;
  },
  async rumor() {
    const result = await staffApi("/rumors", { method: "POST", body: { ...demoPayloads.rumor, site_id: currentSiteId() } });
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

async function clearDemoState() {
  try {
    localStorage.removeItem("peacepulse-report-queue");
    state.demoLog = [];
    localStorage.removeItem("peacepulse-demo-log");
    updateQueueCount();
    renderDemoLog();
    setDemoResult("Local demo log and browser queue cleared. Production records remain unchanged.");
    await refreshAll();
  } catch (error) {
    setDemoResult(error.message);
  }
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
      const siteId = currentSiteId();
      if (!siteId) {
        throw new Error("No production site selected.");
      }
      await v1(`/public/sites/${siteId}/reports`, { method: "POST", body: item.payload });
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
  renderDashboardMetrics();
  const status = $("#statusFilter").value;
  const category = $("#categoryFilter").value;
  const minSeverity = Number($("#severityFilter").value || 1);
  const filtered = items.filter((item) =>
    (!status || item.status === status) &&
    (!category || item.category === category) &&
    item.severity >= minSeverity
  ).sort((a, b) => b.severity - a.severity || new Date(b.created_at) - new Date(a.created_at));
  if (!filtered.length) {
    $("#incidentGrid").innerHTML = `<p class="empty">No incidents match the current filters.</p>`;
    return;
  }
  $("#incidentGrid").innerHTML = filtered.map((item) => `
    <article class="incidentCard">
      <div class="incidentMain">
        <div class="incidentTopline">
          <div class="incidentTitle">
            <h3>${escapeHtml(item.category.replaceAll("_", " "))}</h3>
            <div class="badgeRow">
              <span class="badge risk">Severity ${item.severity}</span>
              <span class="badge">${escapeHtml(item.status.replaceAll("_", " "))}</span>
              <span class="badge">${Math.round(item.confidence * 100)}% confidence</span>
            </div>
          </div>
          <span class="badge">${new Date(item.created_at).toLocaleDateString()}</span>
        </div>
        <p class="incidentText">${escapeHtml(item.redacted_text)}</p>
        <div class="incidentMeta">
          <span class="badge">Cluster ${escapeHtml(item.cluster_key)}</span>
          <span class="badge">Site ${escapeHtml(item.site_id)}</span>
        </div>
        <p class="incidentText"><strong>Public update:</strong> ${escapeHtml(item.public_update)}</p>
        <div class="noteList" data-notes="${item.id}"></div>
        <div class="timeline" data-timeline-list="${item.id}"></div>
      </div>
      <div class="incidentActions">
        <label>Status
          <select data-status="${item.id}">
            ${["new", "assigned", "in_progress", "resolved"].map((status) => `<option value="${status}" ${status === item.status ? "selected" : ""}>${status.replaceAll("_", " ")}</option>`).join("")}
          </select>
        </label>
        <form class="noteForm" data-note="${item.id}">
          <input name="note" placeholder="Add mediation note" maxlength="500" required />
          <button type="submit">Add</button>
        </form>
        <button class="secondary" type="button" data-timeline="${item.id}">Show timeline</button>
      </div>
    </article>
  `).join("");
  $$("[data-status]").forEach((select) => {
    select.addEventListener("change", async () => {
      try {
        await staffApi(`/incidents/${select.dataset.status}/status`, { method: "PATCH", body: { status: select.value } });
        setDashboardResult("Incident status updated.");
        await loadIncidents();
      } catch (error) {
        setDashboardResult(error.message);
      }
    });
  });
  loadVisibleNotes(filtered);
  bindTimelineButtons();
}

function renderDashboardMetrics() {
  const target = $("#dashboardMetrics");
  if (!target) return;
  const openIncidents = state.incidents.filter((item) => item.status !== "resolved").length;
  const highSeverity = state.incidents.filter((item) => item.status !== "resolved" && item.severity >= 4).length;
  const resourceAnomalies = state.resources.filter((item) => item.anomaly && item.anomaly !== "normal").length;
  const queuedReports = queue().length;
  target.innerHTML = [
    ["Open incidents", openIncidents],
    ["High severity", highSeverity],
    ["Resource anomalies", resourceAnomalies],
    ["Queued reports", queuedReports],
  ].map(([label, value]) => `
    <div class="metricTile">
      <span>${label}</span>
      <strong>${value}</strong>
    </div>
  `).join("");
}

async function loadIncidents() {
  if (!hasStaffAccess()) {
    state.incidents = [];
    renderDashboardMetrics();
    $("#incidentGrid").innerHTML = `<p class="empty">Sign in to view production incidents.</p>`;
    return;
  }
  state.incidents = await staffApi("/incidents");
  renderIncidents(state.incidents);
}

async function loadVisibleNotes(items) {
  await Promise.all(items.map(async (item) => {
    const target = $(`[data-notes="${item.id}"]`);
    if (!target) return;
    try {
      const notes = await staffApi(`/incidents/${item.id}/notes`);
      target.innerHTML = notes.slice(0, 3).map((note) => `
        <p><strong>${escapeHtml(note.actor_label || "responder")}</strong>: ${escapeHtml(note.note)}</p>
      `).join("") || `<p class="empty">No mediation notes yet.</p>`;
    } catch {
      target.innerHTML = `<p class="empty">Notes unavailable.</p>`;
    }
  }));
  bindNoteForms();
}

function bindNoteForms() {
  $$("[data-note]").forEach((form) => {
    if (form.dataset.bound) return;
    form.dataset.bound = "true";
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      const note = form.elements.note.value.trim();
      try {
        await staffApi(`/incidents/${form.dataset.note}/notes`, { method: "POST", body: { note } });
        form.reset();
        setDashboardResult("Mediation note added.");
        await loadIncidents();
      } catch (error) {
        setDashboardResult(error.message);
      }
    });
  });
}

function bindTimelineButtons() {
  $$("[data-timeline]").forEach((button) => {
    if (button.dataset.bound) return;
    button.dataset.bound = "true";
    button.addEventListener("click", async () => {
      const target = $(`[data-timeline-list="${button.dataset.timeline}"]`);
      const expanded = target.dataset.expanded === "true";
      if (expanded) {
        target.dataset.expanded = "false";
        target.innerHTML = "";
        button.textContent = "Show timeline";
        return;
      }
      try {
        const items = await staffApi(`/incidents/${button.dataset.timeline}/timeline`);
        target.dataset.expanded = "true";
        target.innerHTML = items.map((item) => `
          <div>
            <span class="badge">${escapeHtml(item.kind)}</span>
            <strong>${escapeHtml(item.title)}</strong>
            <p>${escapeHtml(item.detail)}</p>
          </div>
        `).join("");
        button.textContent = "Hide timeline";
      } catch (error) {
        target.innerHTML = `<p class="empty">${escapeHtml(error.message)}</p>`;
      }
    });
  });
}

async function uploadEvidence(event) {
  event.preventDefault();
  const form = event.currentTarget;
  const file = form.elements.file.files[0];
  if (!file) return;
  try {
    validateEvidenceFile(file);
    const siteId = currentSiteId();
    if (!siteId) throw new Error("Select a production site before adding evidence metadata.");
    const sha256 = await fileSha256(file);
    const record = await staffApi("/evidence/uploads", {
      method: "POST",
      body: {
        site_id: siteId,
        filename: file.name,
        mime_type: file.type,
        size_bytes: file.size,
        sha256,
        sync_allowed: form.elements.sync_allowed.checked,
      },
    });
    $("#evidenceResult").textContent = `Evidence metadata created for ${record.object_key}.`;
    form.reset();
    await loadEvidence();
    if (hasCoordinatorAccess()) await loadSync().catch(() => {});
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
  if (!hasStaffAccess()) {
    $("#evidenceList").innerHTML = `<p class="empty">Sign in to view production evidence metadata.</p>`;
    return;
  }
  const items = await staffApi("/evidence");
  $("#evidenceList").className = "dataList";
  $("#evidenceList").innerHTML = items.map((item) => `
    <article class="dataRow">
      <div class="dataRowMain">
        <h3>${escapeHtml(item.filename)}</h3>
        <p><strong>SHA-256:</strong> ${escapeHtml(item.sha256.slice(0, 24))}...</p>
        <p>${item.linked_report_id ? `Linked report: ${escapeHtml(item.linked_report_id)}` : "Unlinked evidence record"}</p>
        <p>Object key: ${escapeHtml(item.object_key)}</p>
      </div>
      <div class="dataRowMeta">
        <span class="badge">${item.size_bytes} bytes</span>
        <span class="badge ${item.sync_allowed ? "ok" : ""}">${item.sync_allowed ? "sync allowed" : "local only"}</span>
      </div>
    </article>
  `).join("") || `<p class="empty">No evidence records yet.</p>`;
}

async function simulateSensor() {
  const queue_length = Math.floor(10 + Math.random() * 58);
  const flow_rate = Number((Math.random() * 9).toFixed(1));
  const uptime = flow_rate < 1.2 ? 0 : 1;
  const body = {
    site_id: currentSiteId(),
    resource_id: "water-point-north",
    queue_length,
    flow_rate,
    uptime,
    maintenance_note: uptime ? "" : "Pump inspection requested",
  };
  if (!body.site_id) throw new Error("Select a production site before simulating a resource event.");
  await staffApi("/resources/events", { method: "POST", body });
  await loadResources();
}

async function loadResources() {
  if (!hasStaffAccess()) {
    state.resources = [];
    renderDashboardMetrics();
    $("#resourceGrid").innerHTML = `<p class="empty">Sign in to view production resources.</p>`;
    return;
  }
  const items = await staffApi("/resources/status");
  state.resources = items;
  renderDashboardMetrics();
  $("#resourceGrid").className = "dataList";
  $("#resourceGrid").innerHTML = items.map((item) => `
    <article class="dataRow">
      <div class="dataRowMain">
        <h3>${escapeHtml(item.resource_id)}</h3>
        <p>${escapeHtml(item.maintenance_note || "No maintenance note")}</p>
      </div>
      <div class="dataRowMeta">
        <span class="badge ${item.anomaly === "normal" ? "ok" : "risk"}">${escapeHtml(item.anomaly)}</span>
        <span class="badge">Queue ${item.queue_length}</span>
        <span class="badge">Flow ${item.flow_rate}</span>
        <span class="badge">${item.uptime ? "online" : "offline"}</span>
      </div>
    </article>
  `).join("") || `<p class="empty">No resource events yet.</p>`;
}

async function submitRouteAlert(event) {
  event.preventDefault();
  try {
    const body = formData(event.currentTarget);
    body.site_id = currentSiteId();
    const alert = await staffApi("/routes/alerts", { method: "POST", body });
    $("#routeResult").textContent = `Route alert added for ${alert.route_label}.`;
    event.currentTarget.reset();
    await loadRoutes();
    if (hasCoordinatorAccess()) await loadSync().catch(() => {});
  } catch (error) {
    $("#routeResult").textContent = error.message;
  }
}

async function loadRoutes() {
  if (!hasStaffAccess()) {
    $("#servicePointGrid").innerHTML = `<p class="empty">Sign in to view production routes.</p>`;
    $("#routeAlertGrid").innerHTML = "";
    return;
  }
  const status = await staffApi("/routes/status");
  $("#servicePointGrid").innerHTML = status.service_points.map((point) => `
    <article class="routeTile ${point.status}">
      <span>${escapeHtml(point.kind)}</span>
      <strong>${escapeHtml(point.label)}</strong>
      <p>${escapeHtml(point.rough_location)}</p>
      <em>${escapeHtml(point.status)}</em>
    </article>
  `).join("");
  $("#routeAlertGrid").className = "dataList";
  $("#routeAlertGrid").innerHTML = status.alerts.map((alert) => `
    <article class="dataRow">
      <div class="dataRowMain">
        <h3>${escapeHtml(alert.route_label)}</h3>
        <p><strong>Area:</strong> ${escapeHtml(alert.rough_location)}</p>
        <p>${escapeHtml(alert.note || "No steward note")}</p>
      </div>
      <div class="dataRowMeta">
        <span class="badge ${alert.status === "open" ? "ok" : "risk"}">${escapeHtml(alert.status)}</span>
        <span class="badge">${escapeHtml(alert.alert_type.replaceAll("_", " "))}</span>
      </div>
    </article>
  `).join("") || `<p class="empty">No route alerts yet.</p>`;
}

async function submitOpportunity(event) {
  event.preventDefault();
  try {
    const body = formData(event.currentTarget);
    body.site_id = currentSiteId();
    const opportunity = await staffApi("/work/opportunities", { method: "POST", body });
    $("#workResult").textContent = `Opportunity added: ${opportunity.title}.`;
    event.currentTarget.reset();
    await loadOpportunities();
    if (hasCoordinatorAccess()) await loadSync().catch(() => {});
  } catch (error) {
    $("#workResult").textContent = error.message;
  }
}

async function loadOpportunities() {
  if (!hasStaffAccess()) {
    $("#workGrid").innerHTML = `<p class="empty">Sign in to view production opportunities.</p>`;
    return;
  }
  const items = await staffApi("/work/opportunities");
  $("#workGrid").className = "dataList";
  $("#workGrid").innerHTML = items.map((item) => `
    <article class="dataRow">
      <div class="dataRowMain">
        <h3>${escapeHtml(item.title)}</h3>
        <p><strong>Area:</strong> ${escapeHtml(item.rough_location)}</p>
        <p>${escapeHtml(item.safety_note || "No safety note")}</p>
      </div>
      <div class="dataRowMeta">
        <span class="badge">${escapeHtml(item.skill_category)}</span>
        <span class="badge ${item.verification_status === "steward_checked" ? "ok" : "risk"}">${escapeHtml(item.verification_status.replaceAll("_", " "))}</span>
      </div>
    </article>
  `).join("") || `<p class="empty">No opportunities yet.</p>`;
}

function prepareExploitationReport() {
  activateView("report");
  const form = $("#reportForm");
  form.elements.category_hint.value = "work_exploitation";
  form.elements.rough_location.value = "Central market";
  form.elements.text.value = "A work opportunity may be unsafe or exploitative and needs steward review.";
  updateTextCount();
  setResult("Work exploitation report prepared. Add details without names or exact homes, then submit.");
}

async function submitRumor(event) {
  event.preventDefault();
  const body = formData(event.currentTarget);
  body.site_id = currentSiteId();
  await staffApi("/rumors", { method: "POST", body });
  event.currentTarget.reset();
  await loadRumors();
}

async function loadRumors() {
  if (!hasStaffAccess()) {
    $("#rumorGrid").innerHTML = `<p class="empty">Sign in to view production rumor clusters.</p>`;
    return;
  }
  const clusters = await staffApi("/rumors/clusters");
  $("#rumorGrid").className = "dataList";
  $("#rumorGrid").innerHTML = clusters.map((cluster) => `
    <article class="dataRow">
      <div class="dataRowMain">
        <h3>${escapeHtml(cluster.cluster_key)}</h3>
        ${cluster.items.map((item) => `<p>${escapeHtml(item.redacted_text)}<br><strong>Response:</strong> ${escapeHtml(item.response_notes || "Needs steward review")}</p>`).join("")}
      </div>
      <div class="dataRowMeta">
        <span class="badge risk">Max severity ${cluster.max_severity}</span>
        <span class="badge">${cluster.count} report${cluster.count === 1 ? "" : "s"}</span>
      </div>
    </article>
  `).join("") || `<p class="empty">No rumor clusters yet.</p>`;
}

async function loadCopilot() {
  if (!hasStaffAccess()) {
    $("#copilotIncidentSelect").innerHTML = `<option value="">Sign in required</option>`;
    $("#copilotRunbooks").innerHTML = `<p class="empty">Sign in to use Copilot.</p>`;
    $("#copilotInvestigation").innerHTML = "";
    $("#copilotChat").innerHTML = "";
    return;
  }
  if (!state.incidents.length) {
    await loadIncidents().catch(() => {});
  }
  $("#copilotIncidentSelect").innerHTML = state.incidents.map((incident) => `
    <option value="${escapeHtml(incident.id)}">${escapeHtml(incident.category.replaceAll("_", " "))} - severity ${incident.severity}</option>
  `).join("") || `<option value="">No incidents available</option>`;
  const runbooks = await staffApi("/copilot/runbooks");
  $("#copilotRunbooks").innerHTML = runbooks.map((item) => `
    <p><strong>${escapeHtml(item.title)}</strong><br>${escapeHtml(item.category)} · ${escapeHtml(item.tags.join(", "))}</p>
  `).join("") || `<p class="empty">No runbooks yet.</p>`;
  if (state.copilotSessionId) {
    await loadCopilotSession(state.copilotSessionId).catch(() => {
      state.copilotSessionId = "";
      localStorage.removeItem("peacepulse-copilot-session");
    });
  }
}

async function investigateCopilot(event) {
  event.preventDefault();
  const incidentId = $("#copilotIncidentSelect").value;
  if (!incidentId) {
    $("#copilotResult").textContent = "Select an incident first.";
    return;
  }
  try {
    const result = await staffApi(`/copilot/incidents/${incidentId}/investigate`, { method: "POST", body: {} });
    renderCopilotInvestigation(result);
    $("#copilotResult").textContent = `Investigation ready for ${result.incident_id}.`;
  } catch (error) {
    $("#copilotResult").textContent = error.message;
  }
}

function renderCopilotInvestigation(result) {
  $("#copilotInvestigation").innerHTML = `
    <article class="card syncItem">
      <div>
        <h3>${escapeHtml(result.summary)}</h3>
        <span class="badge">${result.verification.passed ? "verified" : "review"}</span>
      </div>
      <dl>
        <div><dt>Hypotheses</dt><dd>${result.hypotheses.map(escapeHtml).join("<br>")}</dd></div>
        <div><dt>Actions</dt><dd>${result.recommended_actions.map(escapeHtml).join("<br>")}</dd></div>
        <div><dt>Trace</dt><dd>${result.agent_trace.map(escapeHtml).join("<br>")}</dd></div>
      </dl>
      <p><strong>Citations:</strong> ${result.citations.map((item) => escapeHtml(item.title)).join(", ") || "none"}</p>
    </article>
  `;
}

async function newCopilotSession() {
  const incidentId = $("#copilotIncidentSelect").value || null;
  const session = await staffApi("/copilot/sessions", {
    method: "POST",
    body: { incident_id: incidentId, title: incidentId ? `Incident ${incidentId}` : "PeacePulse copilot session" },
  });
  state.copilotSessionId = session.id;
  localStorage.setItem("peacepulse-copilot-session", session.id);
  renderCopilotSession(session);
}

async function sendCopilotMessage(event) {
  event.preventDefault();
  if (!state.copilotSessionId) {
    await newCopilotSession();
  }
  const session = await staffApi(`/copilot/sessions/${state.copilotSessionId}/messages`, {
    method: "POST",
    body: formData(event.currentTarget),
  });
  event.currentTarget.reset();
  state.copilotSessionId = session.id;
  localStorage.setItem("peacepulse-copilot-session", session.id);
  renderCopilotSession(session);
}

async function loadCopilotSession(sessionId) {
  const session = await staffApi(`/copilot/sessions/${sessionId}`);
  renderCopilotSession(session);
}

function renderCopilotSession(session) {
  $("#copilotChat").innerHTML = session.messages.map((message) => `
    <article class="card syncItem">
      <div>
        <h3>${escapeHtml(message.role)}</h3>
        <span class="badge">${new Date(message.created_at).toLocaleString()}</span>
      </div>
      <p>${escapeHtml(message.content)}</p>
      ${message.citations.length ? `<p><strong>Citations:</strong> ${message.citations.map((item) => escapeHtml(item.title)).join(", ")}</p>` : ""}
    </article>
  `).join("") || `<p class="empty">Start a Copilot chat session.</p>`;
}

async function loadPrivacyAudit() {
  if (!hasStaffAccess()) {
    $("#privacyCounts").innerHTML = `<p class="empty">Sign in to view the production privacy audit.</p>`;
    $("#privacyLocal").innerHTML = "";
    $("#privacySyncs").innerHTML = "";
    $("#privacyNever").innerHTML = "";
    return;
  }
  const audit = await staffApi("/privacy/audit");
  $("#privacyCounts").innerHTML = Object.entries(audit.counts).map(([key, value]) => `
    <div class="metricTile">
      <span>${escapeHtml(key.replaceAll("_", " "))}</span>
      <strong>${escapeHtml(value)}</strong>
    </div>
  `).join("");
  $("#privacyLocal").innerHTML = policyList(audit.local_only);
  $("#privacySyncs").innerHTML = policyList(audit.syncs);
  $("#privacyNever").innerHTML = policyList(audit.never_syncs);
}

function policyList(items) {
  return `<ul class="policyList">${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</ul>`;
}

async function loadSync() {
  if (!hasStaffAccess()) {
    $("#syncPreview").innerHTML = `<p class="empty">Sign in to view production sync records.</p>`;
    return;
  }
  const [health, preview] = await Promise.all([
    v1("/health"),
    staffApi("/sync/preview"),
  ]);
  $("#healthHub").textContent = health.ok ? "Online" : "Check";
  $("#healthDatabase").textContent = health.database || "Unknown";
  $("#syncStatus").textContent = "Hub sync API ready";
  $("#healthResource").textContent = "Production v1";
  $("#healthLastSync").textContent = state.lastSyncAt ? new Date(state.lastSyncAt).toLocaleString() : "Not run";
  renderSyncPreview(preview);
}

async function runSync() {
  const result = await staffApi("/sync/run", { method: "POST", body: {} });
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
  await Promise.allSettled([loadIncidents(), loadEvidence(), loadResources(), loadRoutes(), loadOpportunities(), loadRumors(), loadCopilot(), loadPrivacyAudit(), checkHub()]);
  if (hasCoordinatorAccess()) await loadSync().catch(() => {});
}

async function checkHub() {
  try {
    if (state.offline) throw new Error("offline");
    await v1("/health");
    $("#apiStatus").textContent = "Production API online";
  } catch {
    $("#apiStatus").textContent = "Production API offline";
  }
  $("#offlineToggle").textContent = state.offline ? "Go online" : "Go offline";
}

async function fileSha256(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function applyRole() {
  $$("[data-role]").forEach((item) => {
    const required = item.dataset.role;
    const visible = hasStaffAccess() && (required === "steward" || hasCoordinatorAccess());
    item.hidden = !visible;
  });
  const activeTab = $(".tab.active");
  if (activeTab?.hidden) activateView("report");
  if (hasCoordinatorAccess()) loadSync().catch(() => {});
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
  $("#bootstrapForm").addEventListener("submit", bootstrapTenant);
  $("#loginForm").addEventListener("submit", loginStaff);
  $("#logoutStaff").addEventListener("click", logoutStaff);
  $("#startMfa").addEventListener("click", startMfaEnrollment);
  $("#mfaForm").addEventListener("submit", verifyMfaEnrollment);
  $("#publicSiteSelect").addEventListener("change", () => {
    saveSelectedSite($("#publicSiteSelect").value);
  });
  $("#reportForm").addEventListener("submit", submitReport);
  $("#flushQueue").addEventListener("click", flushQueue);
  $("#refreshDashboard").addEventListener("click", refreshAll);
  $("#evidenceForm").addEventListener("submit", uploadEvidence);
  $("#simulateSensor").addEventListener("click", simulateSensor);
  $("#routeForm").addEventListener("submit", submitRouteAlert);
  $("#refreshRoutes").addEventListener("click", loadRoutes);
  $("#workForm").addEventListener("submit", submitOpportunity);
  $("#refreshWork").addEventListener("click", loadOpportunities);
  $("#reportExploitation").addEventListener("click", prepareExploitationReport);
  $("#rumorForm").addEventListener("submit", submitRumor);
  $("#refreshCopilot").addEventListener("click", loadCopilot);
  $("#copilotInvestigateForm").addEventListener("submit", investigateCopilot);
  $("#newCopilotSession").addEventListener("click", newCopilotSession);
  $("#copilotChatForm").addEventListener("submit", sendCopilotMessage);
  $("#runSync").addEventListener("click", runSync);
  $("#refreshPrivacy").addEventListener("click", loadPrivacyAudit);
  $("#resetDemo").addEventListener("click", resetDemoLog);
  $("#clearDemoState").addEventListener("click", clearDemoState);
  $$("[data-demo-action]").forEach((button) => {
    button.addEventListener("click", () => runDemoStep(button.dataset.demoAction));
  });
  $$("[data-guided-report]").forEach((button) => {
    button.addEventListener("click", () => applyGuidedReport(button.dataset.guidedReport));
  });
  $("#reportForm").addEventListener("input", () => {
    setResult("");
    updateTextCount();
  });
  $("#reportLanguage").addEventListener("change", renderPhrasebook);
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
  $("#viewTitle").textContent = VIEW_TITLES[view] || "PeacePulse Hub";
  if (view === "sync") loadSync().catch(() => {});
  if (view === "privacy") loadPrivacyAudit().catch(() => {});
  if (view === "routes") loadRoutes().catch(() => {});
  if (view === "work") loadOpportunities().catch(() => {});
  if (view === "copilot") loadCopilot().catch(() => {});
}

if ("serviceWorker" in navigator) {
  navigator.serviceWorker.register("/sw.js").catch(() => {});
}

async function init() {
  bind();
  renderPublicSites();
  renderAccessState();
  await loadProductionContext();
  applyRole();
  updateQueueCount();
  updateTextCount();
  renderPhrasebook();
  renderDemoLog();
  refreshAll();
}

init();
