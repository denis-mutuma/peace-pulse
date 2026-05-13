const state = {
  offline: localStorage.getItem("peacepulse-offline") === "true",
  role: localStorage.getItem("peacepulse-role") || "community",
  productionAvailable: false,
  accessToken: "",
  staff: null,
  publicSites: JSON.parse(localStorage.getItem("peacepulse-public-sites") || "[]"),
  selectedSiteId: localStorage.getItem("peacepulse-site-id") || "",
  incidents: [],
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
  if (!state.productionAvailable) {
    return api(path.replace("/v1", ""), options);
  }
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

function saveSession(token, staff = null) {
  state.accessToken = token || "";
  state.staff = staff;
  localStorage.removeItem("peacepulse-access-token");
  localStorage.removeItem("peacepulse-staff");
  if (staff) {
    const siteId = staff.site_ids?.[0] || state.selectedSiteId;
    if (siteId) saveSelectedSite(siteId);
  } else {
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
    select.innerHTML = `<option value="">Legacy local demo</option>`;
    return;
  }
  select.innerHTML = state.publicSites.map((site) => `
    <option value="${escapeHtml(site.id)}" ${site.id === state.selectedSiteId ? "selected" : ""}>${escapeHtml(site.name)} - ${escapeHtml(site.rough_location)}</option>
  `).join("");
}

function renderAccessState() {
  $("#accessPanel").hidden = !state.productionAvailable;
  $("#roleSelect").hidden = state.productionAvailable;
  $("#logoutStaff").hidden = !hasStaffAccess();
  $("#mfaForm").hidden = !hasStaffAccess();
  const summary = $("#sessionSummary");
  if (!state.productionAvailable) {
    summary.textContent = "Legacy local demo API is active.";
    return;
  }
  if (hasStaffAccess()) {
    summary.textContent = `Signed in as ${state.staff.email} (${state.staff.roles.join(", ")}). MFA ${state.staff.mfa_enabled ? "enabled" : "not enrolled"}.`;
  } else {
    summary.textContent = "Production API is active. Anonymous reporting is open; staff tools require sign in.";
  }
}

async function loadProductionContext() {
  try {
    await v1("/health");
    state.productionAvailable = true;
    const sites = await v1("/public/sites");
    savePublicSites(sites);
    renderAccessState();
    applyRole();
  } catch {
    state.productionAvailable = false;
    renderAccessState();
    applyRole();
  }
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
    if (state.productionAvailable && !siteId) {
      throw new Error("Bootstrap or select a production site before submitting a report.");
    }
    const result = state.productionAvailable
      ? await v1(`/public/sites/${siteId}/reports`, { method: "POST", body: payload })
      : await api("/api/reports", { method: "POST", body: payload });
    let voiceMessage = "";
    if (voiceFile && state.productionAvailable) {
      voiceMessage = " Voice-note bytes require the legacy local evidence locker until anonymous production evidence intake is enabled.";
    } else if (voiceFile) {
      try {
        await uploadVoiceNote(result.report.id, voiceFile, form.elements.voice_sync_allowed.checked);
        voiceMessage = " Voice note stored as linked local evidence.";
      } catch (voiceError) {
        voiceMessage = ` Voice note was not stored: ${voiceError.message}`;
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
  const content_base64 = await readAsDataUrl(file);
  await api("/api/evidence", {
    method: "POST",
    body: {
      filename: file.name || "voice-note.webm",
      mime_type: file.type,
      content_base64,
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

async function resetDemoData() {
  try {
    const result = await api("/api/demo/reset", { method: "POST", body: {} });
    localStorage.removeItem("peacepulse-report-queue");
    state.demoLog = [];
    localStorage.removeItem("peacepulse-demo-log");
    updateQueueCount();
    renderDemoLog();
    setDemoResult(`Demo reset with ${result.seeded.reports} reports and ${result.seeded.incidents} incidents.`);
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
      if (state.productionAvailable && !siteId) {
        throw new Error("No production site selected.");
      }
      if (state.productionAvailable) {
        await v1(`/public/sites/${siteId}/reports`, { method: "POST", body: item.payload });
      } else {
        await api("/api/reports", { method: "POST", body: item.payload });
      }
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
      <form class="noteForm" data-note="${item.id}">
        <input name="note" placeholder="Add mediation note" maxlength="500" required />
        <button type="submit">Add note</button>
      </form>
      <div class="noteList" data-notes="${item.id}"></div>
      <button class="secondary" type="button" data-timeline="${item.id}">Show timeline</button>
      <div class="timeline" data-timeline-list="${item.id}"></div>
    </article>
  `).join("");
  $$("[data-status]").forEach((select) => {
    select.addEventListener("change", async () => {
      try {
        if (state.productionAvailable) {
          await staffApi(`/incidents/${select.dataset.status}/status`, { method: "PATCH", body: { status: select.value } });
        } else {
          await api(`/api/incidents/${select.dataset.status}/status`, { method: "PATCH", body: { status: select.value } });
        }
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

async function loadIncidents() {
  if (state.productionAvailable && !hasStaffAccess()) {
    state.incidents = [];
    $("#incidentGrid").innerHTML = `<p class="empty">Sign in to view production incidents.</p>`;
    return;
  }
  state.incidents = state.productionAvailable ? await staffApi("/incidents") : await api("/api/incidents");
  renderIncidents(state.incidents);
}

async function loadVisibleNotes(items) {
  await Promise.all(items.map(async (item) => {
    const target = $(`[data-notes="${item.id}"]`);
    if (!target) return;
    try {
      const notes = state.productionAvailable ? await staffApi(`/incidents/${item.id}/notes`) : await api(`/api/incidents/${item.id}/notes`);
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
        if (state.productionAvailable) {
          await staffApi(`/incidents/${form.dataset.note}/notes`, { method: "POST", body: { note } });
        } else {
          await api(`/api/incidents/${form.dataset.note}/notes`, {
            method: "POST",
            body: { actor_label: state.role, note },
          });
        }
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
        const items = state.productionAvailable
          ? await staffApi(`/incidents/${button.dataset.timeline}/timeline`)
          : await api(`/api/incidents/${button.dataset.timeline}/timeline`);
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
    if (state.productionAvailable) {
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
    } else {
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
    }
    form.reset();
    await loadEvidence();
    if (state.role === "coordinator" || hasRole("coordinator", "org_admin")) await loadSync().catch(() => {});
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
  if (state.productionAvailable && !hasStaffAccess()) {
    $("#evidenceList").innerHTML = `<p class="empty">Sign in to view production evidence metadata.</p>`;
    return;
  }
  const items = state.productionAvailable ? await staffApi("/evidence") : await api("/api/evidence");
  $("#evidenceList").innerHTML = items.map((item) => `
    <article class="card">
      <h3>${escapeHtml(item.filename)}</h3>
      <span class="badge">${item.size_bytes} bytes</span>
      <span class="badge">${item.sync_allowed ? "sync allowed" : "local only"}</span>
      <p><strong>SHA-256:</strong> ${escapeHtml(item.sha256.slice(0, 24))}...</p>
      <p>${item.linked_report_id ? `Linked report: ${escapeHtml(item.linked_report_id)}` : "Unlinked evidence record"}</p>
      <p>${state.productionAvailable ? `Object key: ${escapeHtml(item.object_key)}` : item.custody.map((event) => escapeHtml(event.action)).join("<br>")}</p>
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
  if (state.productionAvailable) {
    if (!body.site_id) throw new Error("Select a production site before simulating a resource event.");
    await staffApi("/resources/events", { method: "POST", body });
  } else {
    delete body.site_id;
    await api("/api/sensor-events", { method: "POST", body });
  }
  await loadResources();
}

async function loadResources() {
  if (state.productionAvailable && !hasStaffAccess()) {
    $("#resourceGrid").innerHTML = `<p class="empty">Sign in to view production resources.</p>`;
    return;
  }
  const items = state.productionAvailable ? await staffApi("/resources/status") : await api("/api/resources/status");
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

async function submitRouteAlert(event) {
  event.preventDefault();
  try {
    const body = formData(event.currentTarget);
    if (state.productionAvailable) body.site_id = currentSiteId();
    const alert = state.productionAvailable
      ? await staffApi("/routes/alerts", { method: "POST", body })
      : await api("/api/routes/alerts", { method: "POST", body });
    $("#routeResult").textContent = `Route alert added for ${alert.route_label}.`;
    event.currentTarget.reset();
    await loadRoutes();
    if (state.role === "coordinator") await loadSync().catch(() => {});
  } catch (error) {
    $("#routeResult").textContent = error.message;
  }
}

async function loadRoutes() {
  if (state.productionAvailable && !hasStaffAccess()) {
    $("#servicePointGrid").innerHTML = `<p class="empty">Sign in to view production routes.</p>`;
    $("#routeAlertGrid").innerHTML = "";
    return;
  }
  const status = state.productionAvailable ? await staffApi("/routes/status") : await api("/api/routes/status");
  $("#servicePointGrid").innerHTML = status.service_points.map((point) => `
    <article class="routeTile ${point.status}">
      <span>${escapeHtml(point.kind)}</span>
      <strong>${escapeHtml(point.label)}</strong>
      <p>${escapeHtml(point.rough_location)}</p>
      <em>${escapeHtml(point.status)}</em>
    </article>
  `).join("");
  $("#routeAlertGrid").innerHTML = status.alerts.map((alert) => `
    <article class="card">
      <h3>${escapeHtml(alert.route_label)}</h3>
      <span class="badge ${alert.status === "open" ? "" : "risk"}">${escapeHtml(alert.status)}</span>
      <span class="badge">${escapeHtml(alert.alert_type.replaceAll("_", " "))}</span>
      <p><strong>Area:</strong> ${escapeHtml(alert.rough_location)}</p>
      <p>${escapeHtml(alert.note || "No steward note")}</p>
    </article>
  `).join("") || `<p class="empty">No route alerts yet.</p>`;
}

async function submitOpportunity(event) {
  event.preventDefault();
  try {
    const body = formData(event.currentTarget);
    if (state.productionAvailable) body.site_id = currentSiteId();
    const opportunity = state.productionAvailable
      ? await staffApi("/work/opportunities", { method: "POST", body })
      : await api("/api/work/opportunities", { method: "POST", body });
    $("#workResult").textContent = `Opportunity added: ${opportunity.title}.`;
    event.currentTarget.reset();
    await loadOpportunities();
    if (state.role === "coordinator") await loadSync().catch(() => {});
  } catch (error) {
    $("#workResult").textContent = error.message;
  }
}

async function loadOpportunities() {
  if (state.productionAvailable && !hasStaffAccess()) {
    $("#workGrid").innerHTML = `<p class="empty">Sign in to view production opportunities.</p>`;
    return;
  }
  const items = state.productionAvailable ? await staffApi("/work/opportunities") : await api("/api/work/opportunities");
  $("#workGrid").innerHTML = items.map((item) => `
    <article class="card">
      <h3>${escapeHtml(item.title)}</h3>
      <span class="badge">${escapeHtml(item.skill_category)}</span>
      <span class="badge ${item.verification_status === "steward_checked" ? "" : "risk"}">${escapeHtml(item.verification_status.replaceAll("_", " "))}</span>
      <p><strong>Area:</strong> ${escapeHtml(item.rough_location)}</p>
      <p>${escapeHtml(item.safety_note || "No safety note")}</p>
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
  if (state.productionAvailable) body.site_id = currentSiteId();
  if (state.productionAvailable) {
    await staffApi("/rumors", { method: "POST", body });
  } else {
    await api("/api/rumors", { method: "POST", body });
  }
  event.currentTarget.reset();
  await loadRumors();
}

async function loadRumors() {
  if (state.productionAvailable && !hasStaffAccess()) {
    $("#rumorGrid").innerHTML = `<p class="empty">Sign in to view production rumor clusters.</p>`;
    return;
  }
  const clusters = state.productionAvailable ? await staffApi("/rumors/clusters") : await api("/api/rumors/clusters");
  $("#rumorGrid").innerHTML = clusters.map((cluster) => `
    <article class="card">
      <h3>${escapeHtml(cluster.cluster_key)}</h3>
      <span class="badge risk">Max severity ${cluster.max_severity}</span>
      <span class="badge">${cluster.count} report${cluster.count === 1 ? "" : "s"}</span>
      ${cluster.items.map((item) => `<p>${escapeHtml(item.redacted_text)}<br><strong>Response:</strong> ${escapeHtml(item.response_notes || "Needs steward review")}</p>`).join("")}
    </article>
  `).join("") || `<p class="empty">No rumor clusters yet.</p>`;
}

async function loadPrivacyAudit() {
  if (state.productionAvailable && !hasStaffAccess()) {
    $("#privacyCounts").innerHTML = `<p class="empty">Sign in to view the production privacy audit.</p>`;
    $("#privacyLocal").innerHTML = "";
    $("#privacySyncs").innerHTML = "";
    $("#privacyNever").innerHTML = "";
    return;
  }
  const audit = state.productionAvailable ? await staffApi("/privacy/audit") : await api("/api/privacy/audit");
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
  if (state.productionAvailable) {
    if (!hasStaffAccess()) {
      $("#syncPreview").innerHTML = `<p class="empty">Sign in to view production audit and sync records.</p>`;
      return;
    }
    const [health, auditEvents] = await Promise.all([
      v1("/health"),
      hasRole("org_admin", "system_admin") ? staffApi("/audit-events") : Promise.resolve([]),
    ]);
    $("#healthHub").textContent = health.ok ? "Online" : "Check";
    $("#healthDatabase").textContent = health.database || "Unknown";
    $("#syncStatus").textContent = "Hub sync API ready";
    $("#healthResource").textContent = "Production v1";
    $("#healthLastSync").textContent = state.lastSyncAt ? new Date(state.lastSyncAt).toLocaleString() : "Not run";
    $("#syncPreview").innerHTML = auditEvents.length ? auditEvents.map((item) => `
      <article class="card syncItem">
        <h3>${escapeHtml(item.action)}</h3>
        <span class="badge">${escapeHtml(item.subject_type)}</span>
        <span class="badge">${new Date(item.created_at).toLocaleString()}</span>
        <p>${escapeHtml(item.detail || item.subject_id)}</p>
      </article>
    `).join("") : `<p class="empty">No production audit events available for this role.</p>`;
    return;
  }
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
  await Promise.allSettled([loadIncidents(), loadEvidence(), loadResources(), loadRoutes(), loadOpportunities(), loadRumors(), loadPrivacyAudit(), checkHub()]);
  if (state.role === "coordinator" || hasRole("coordinator", "org_admin", "system_admin")) await loadSync().catch(() => {});
}

async function checkHub() {
  try {
    if (state.offline) throw new Error("offline");
    if (state.productionAvailable) {
      await v1("/health");
      $("#apiStatus").textContent = "Production API online";
    } else {
      await api("/api/health");
      $("#apiStatus").textContent = "Hub online";
    }
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

async function fileSha256(file) {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return Array.from(new Uint8Array(digest)).map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

function applyRole() {
  if (state.productionAvailable) {
    if (hasRole("org_admin", "system_admin")) {
      state.role = "coordinator";
    } else if (hasRole("coordinator")) {
      state.role = "coordinator";
    } else if (hasRole("steward")) {
      state.role = "steward";
    } else {
      state.role = "community";
    }
  }
  $("#roleSelect").value = state.role;
  $$("[data-role]").forEach((item) => {
    const required = item.dataset.role;
    const visible = state.productionAvailable
      ? hasStaffAccess() && (required === "steward" || hasRole("coordinator", "org_admin", "system_admin"))
      : state.role === required || (required === "steward" && state.role === "coordinator");
    item.hidden = !visible;
  });
  const activeTab = $(".tab.active");
  if (activeTab?.hidden) activateView("report");
  if (state.role === "coordinator" || hasRole("coordinator", "org_admin", "system_admin")) loadSync().catch(() => {});
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
    if (state.productionAvailable) return;
    state.role = $("#roleSelect").value;
    localStorage.setItem("peacepulse-role", state.role);
    applyRole();
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
  $("#runSync").addEventListener("click", runSync);
  $("#refreshPrivacy").addEventListener("click", loadPrivacyAudit);
  $("#resetDemo").addEventListener("click", resetDemoLog);
  $("#resetDemoData").addEventListener("click", resetDemoData);
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
  if (view === "sync") loadSync().catch(() => {});
  if (view === "privacy") loadPrivacyAudit().catch(() => {});
  if (view === "routes") loadRoutes().catch(() => {});
  if (view === "work") loadOpportunities().catch(() => {});
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
