// SituSim instructor console
// The server pushes the full exercise state every tick over a WebSocket.
// Buttons call the instructor API with fetch().

const ROLES = [
  { role: "GTL", title: "Ground Team Lead", abbr: "GL" },
  { role: "ISR", title: "Air / ISR Monitor", abbr: "AIR" },
  { role: "EW", title: "Cyber / EW Officer", abbr: "EW" },
  { role: "COMMS", title: "Comms Coordinator", abbr: "COM" },
];
const GAP_NAMES = { sound: "Sound read", info: "Information gap", reasoning: "Reasoning gap", lucky: "Ahead of evidence", unset: "No read yet" };
const GAP_TAG = { sound: "ok", info: "late", reasoning: "bad", lucky: "signal", unset: "" };

let ws = null;
let state = null;
let sessionId = null;
let selected = null;            // "role:GTL" or "source:ISR-UAV"
let inspectorDraft = { state: null, duration: 60 };
let configDirty = false;
let matrixKey = "";
let decisionsKey = "";
let logKey = "";
let scriptKey = "";
let peopleKey = "";
let conflictsKey = "";
let pictureKey = "";
let reportOptionsKey = "";
let probeOptionsKey = "";
let questionsKey = "";
let railKey = "";
let fogKey = "";

function $(id) { return document.getElementById(id); }

function escapeHtml(text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmt(t) {
  t = Math.max(0, Math.floor(t));
  return "T+" + String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0");
}

function span(seconds) {
  seconds = Math.max(0, Math.round(seconds));
  if (seconds < 60) return seconds + " s";
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? m + " min" : m + " min " + s + " s";
}

function roleTitle(role) {
  for (const r of ROLES) if (r.role === role) return r.title;
  return role;
}

let toastTimer = null;
function showToast(text) {
  const toast = $("toast");
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 3000);
}

async function api(path, body) {
  try {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    const data = await response.json();
    if (response.status === 401) {
      askPasscode();
      return false;
    }
    if (!data.ok) {
      showToast(data.error || "That didn't work.");
      return false;
    }
    return true;
  } catch (error) {
    showToast("Can't reach the server.");
    return false;
  }
}

// ---------- connection ----------
function connect() {
  const protocol = location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(protocol + location.host + "/ws/instructor");
  ws.onmessage = function (event) {
    const msg = JSON.parse(event.data);
    if (msg.type !== "state") return;
    if (sessionId !== msg.session_id) {
      sessionId = msg.session_id;
      configDirty = false;
      matrixKey = decisionsKey = logKey = scriptKey = conflictsKey = pictureKey = reportOptionsKey = railKey = "";
      closeInspector();
    }
    state = msg;
    draw();
  };
  ws.onclose = function (event) {
    if (event.code === 4401) {
      // The server wants the instructor passcode before it shares anything.
      $("statusText").textContent = "Locked";
      askPasscode();
      return;
    }
    $("status").className = "pill READY";
    $("statusText").textContent = "Reconnecting";
    setTimeout(connect, 2000);
  };
}

// ---------- instructor passcode (only on a hosted copy) ----------
function askPasscode() {
  $("passcodeModal").classList.remove("hidden");
  $("passcodeInput").focus();
}

$("passcodeForm").addEventListener("submit", async function (event) {
  event.preventDefault();
  $("passcodeError").textContent = "";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode: $("passcodeInput").value }),
    });
    const data = await response.json();
    if (!data.ok) {
      $("passcodeError").textContent = data.error || "That passcode is not right.";
      $("passcodeInput").select();
      return;
    }
    $("passcodeModal").classList.add("hidden");
    connect();
  } catch (error) {
    $("passcodeError").textContent = "Can't reach the server.";
  }
});

// ============================================================
// The live network
// ============================================================

const graph = createNetGraph($("graph"), {
  onNodeClick: function (kind, id) {
    const key = kind + ":" + id;
    if (selected === key) {
      closeInspector();
      return;
    }
    selected = key;
    inspectorDraft = { state: null, duration: 60 };
    drawGraph();
    drawInspector();
  },
});

function drawGraph() {
  if (!state) return;
  const roles = [];
  for (const t of state.trainees) {
    let abbr = t.role;
    for (const r of ROLES) if (r.role === t.role) abbr = r.abbr;
    roles.push({ id: t.role, abbr: abbr, label: t.short, sub: t.online ? t.name : "Not connected", link: t.link,
      until: t.until, exposure: t.exposure, threshold: state.config.located_threshold, located: t.located, online: t.online });
  }
  const sources = [];
  for (const s of state.sources) {
    sources.push({ id: s.name, label: s.name, state: s.state, until: s.until, feeds: s.feeds });
  }
  graph.update({ speed: state.status === "RUNNING" ? state.config.speed : 0, sources: sources, roles: roles,
    packets: state.packets, arrivals: state.arrivals, selected: selected });
}

function closeInspector() {
  selected = null;
  $("inspector").classList.add("hidden");
  if (state) drawGraph();
}

document.addEventListener("keydown", function (event) {
  if (event.key === "Escape" && selected) closeInspector();
});

function drawInspector() {
  const box = $("inspector");
  if (!selected || !state) {
    box.classList.add("hidden");
    return;
  }
  const parts = selected.split(":");
  const kind = parts[0];
  const id = parts.slice(1).join(":");
  const pos = graph.positionOf(kind, id);
  const wrap = document.querySelector(".net-wrap");
  const width = wrap.clientWidth;
  const height = wrap.clientHeight;
  let left = pos.x * width + (kind === "role" ? -330 : 90);
  if (left < 8) left = 8;
  if (left > width - 308) left = width - 308;
  let top = pos.y * height - 60;
  if (top < 8) top = 8;
  if (top > height - 300) top = Math.max(8, height - 300);
  box.style.left = left + "px";
  box.style.top = top + "px";

  let html = "";
  if (kind === "role") {
    let t = null;
    for (const item of state.trainees) if (item.role === id) t = item;
    if (!t) return;
    const chosen = inspectorDraft.state || t.link;
    html += '<h3>' + escapeHtml(t.title) + '</h3><p class="sub">' + (t.online ? escapeHtml(t.name) : "Not connected") + '</p>';
    html += '<div class="facts">'
      + '<div>Radio<b>' + t.link.toLowerCase() + (t.until !== null ? ", " + span(t.until) + " left" : "") + '</b></div>'
      + '<div>Detectability<b>' + t.exposure + ' / ' + state.config.located_threshold + '</b></div>'
      + '<div>In transit to them<b>' + t.pending + '</b></div>'
      + '<div>Held<b>' + t.held + '</b></div></div>';
    html += '<span class="label">Set their radio</span><span class="seg" id="insState">';
    for (const s of ["UP", "DEGRADED", "DOWN"]) {
      const words = { UP: "Up", DEGRADED: "Degraded", DOWN: "Cut" };
      html += '<button type="button" class="' + (chosen === s ? "on " + s : "") + '" data-state="' + s + '">' + words[s] + '</button>';
    }
    html += '</span>';
    if (chosen !== "UP") {
      html += '<span class="label">For</span><span class="seg" id="insDuration">';
      for (const d of [[30, "30 s"], [60, "1 min"], [120, "2 min"], [0, "Until I restore"]]) {
        html += '<button type="button" class="' + (inspectorDraft.duration === d[0] ? "on" : "") + '" data-duration="' + d[0] + '">' + d[1] + '</button>';
      }
      html += '</span>';
      let preview = "";
      if (chosen === "DOWN") preview = (t.pending > 0 ? t.pending + " message(s) on their way will be held" : "Radio messages to and from them will be held") + ". The relay and runners still get through.";
      else preview = "Their radio messages will take twice as long and more will be lost. They are not told.";
      html += '<p class="preview">' + escapeHtml(preview) + '</p>';
    }
    html += '<div class="actions"><button class="btn ghost small" type="button" id="insClose">Close</button><button class="btn signal small" type="button" id="insApply">Apply</button></div>';
  } else {
    let s = null;
    for (const item of state.sources) if (item.name === id) s = item;
    if (!s) return;
    const chosen = inspectorDraft.state || s.state;
    html += '<h3>' + escapeHtml(s.name) + '</h3><p class="sub">Feeds ' + escapeHtml(s.feeds.map(roleTitle).join(", ") || "nobody yet") + '</p>';
    if (!s.scripted) {
      html += '<p class="preview">This source appeared during the exercise and can\'t be switched off.</p>';
      html += '<div class="actions"><button class="btn ghost small" type="button" id="insClose">Close</button></div>';
    } else {
      html += '<span class="label">Set this source</span><span class="seg" id="insState">';
      for (const st of ["ONLINE", "OFFLINE"]) {
        html += '<button type="button" class="' + (chosen === st ? "on" : "") + '" data-state="' + st + '">' + (st === "ONLINE" ? "Online" : "Offline") + '</button>';
      }
      html += '</span>';
      if (chosen === "OFFLINE") {
        html += '<span class="label">For</span><span class="seg" id="insDuration">';
        for (const d of [[30, "30 s"], [60, "1 min"], [120, "2 min"], [0, "Until I restore"]]) {
          html += '<button type="button" class="' + (inspectorDraft.duration === d[0] ? "on" : "") + '" data-duration="' + d[0] + '">' + d[1] + '</button>';
        }
        html += '</span><p class="preview">Reports this source would send while offline are never sent.</p>';
      }
      html += '<div class="actions"><button class="btn ghost small" type="button" id="insClose">Close</button><button class="btn signal small" type="button" id="insApply">Apply</button></div>';
    }
  }
  box.innerHTML = html;
  box.classList.remove("hidden");

  for (const button of box.querySelectorAll("#insState button")) {
    button.addEventListener("click", function () { inspectorDraft.state = button.dataset.state; drawInspector(); });
  }
  for (const button of box.querySelectorAll("#insDuration button")) {
    button.addEventListener("click", function () { inspectorDraft.duration = parseInt(button.dataset.duration, 10); drawInspector(); });
  }
  $("insClose").addEventListener("click", closeInspector);
  const apply = $("insApply");
  if (apply) {
    apply.addEventListener("click", async function () {
      const duration = inspectorDraft.duration || null;
      let ok;
      if (kind === "role") {
        let t = null;
        for (const item of state.trainees) if (item.role === id) t = item;
        const target = inspectorDraft.state || t.link;
        ok = await api("/api/link", { role: id, state: target, duration: target === "UP" ? null : duration, note: "Instructor inject" });
        if (ok) showToast(roleTitle(id) + ": radio " + target.toLowerCase() + (target !== "UP" && duration ? " for " + span(duration) : "") + ".");
      } else {
        let s = null;
        for (const item of state.sources) if (item.name === id) s = item;
        const target = inspectorDraft.state || s.state;
        ok = await api("/api/sensor", { sensor: id, state: target, duration: target === "OFFLINE" ? duration : null });
        if (ok) showToast(id + " " + target.toLowerCase() + ".");
      }
      if (ok) closeInspector();
    });
  }
}

// ---------- fog, Fog Director, direction finding ----------
$("director").addEventListener("change", function () { api("/api/director", { enabled: $("director").checked }); });
$("df").addEventListener("change", function () { api("/api/df", { active: $("df").checked }); });

function drawFogControls() {
  const key = state.config.fog_level + "|" + state.config.director + "|" + state.df_active;
  $("director").checked = state.config.director;
  $("df").checked = state.df_active;
  if (key === fogKey) return;
  fogKey = key;
  let html = "";
  for (let i = 0; i < state.fog_levels.length; i++) {
    html += '<button type="button" class="' + (state.config.fog_level === i ? "on" : "") + '" data-level="' + i + '" title="' + escapeHtml(state.fog_levels[i]) + '">' + escapeHtml(state.fog_levels[i].replace(" fog", "")) + '</button>';
  }
  $("fogSeg").innerHTML = html;
  for (const button of document.querySelectorAll("#fogSeg button")) {
    button.addEventListener("click", function () { api("/api/config", { fog_level: parseInt(button.dataset.level, 10) }); });
  }
}

// ============================================================
// Live picture: read vs shadow analyst vs truth
// ============================================================

function classify(read, shadow, truth) {
  if (read === null || truth === null) return "unset";
  const traineeRight = read === 50 ? false : (read > 50) === truth;
  const shadowRight = (shadow > 50) === truth;
  if (traineeRight && shadowRight) return "sound";
  if (!traineeRight && !shadowRight) return "info";
  if (!traineeRight && shadowRight) return "reasoning";
  return "lucky";
}

function drawPicture() {
  const key = JSON.stringify(state.trainees.map(function (t) { return [t.picture, t.online, t.name]; }));
  if (key === pictureKey) return;
  pictureKey = key;
  let html = "";
  for (const t of state.trainees) {
    html += '<div class="trainee-pic"><header><h3>' + escapeHtml(t.title) + '</h3><span class="name">' + (t.online ? escapeHtml(t.name) : "Not connected") + '</span></header>';
    for (const p of t.picture) {
      const category = classify(p.read, p.shadow, p.truth);
      let track = '<span class="truth" style="left:' + (p.truth ? 100 : 0) + '%" title="Truth: ' + (p.truth ? "yes" : "no") + '"></span>';
      if (p.read !== null) {
        const a = Math.min(p.read, p.shadow);
        const b = Math.max(p.read, p.shadow);
        if (b - a > 15) track += '<span class="gapline" style="left:' + a + '%;width:' + (b - a) + '%"></span>';
      }
      track += '<span class="shadow" style="left:' + p.shadow + '%" title="Their reports support ' + p.shadow + '%"></span>';
      if (p.read !== null) track += '<span class="read" style="left:' + p.read + '%" title="Their read: ' + p.read + '%"></span>';
      html += '<div class="q-row"><span class="q">' + escapeHtml(p.short) + '</span><span class="q-track">' + track + '</span>'
        + '<span class="tag gap-tag ' + GAP_TAG[category] + '">' + GAP_NAMES[category] + '</span></div>';
    }
    html += '</div>';
  }
  $("picture").innerHTML = html;
}

// ============================================================
// Top bar, timeline and the rest
// ============================================================

$("btnStart").addEventListener("click", function () { api("/api/control", { action: "start" }); });
$("btnPause").addEventListener("click", function () { api("/api/control", { action: "pause" }); });
$("btnResume").addEventListener("click", function () { api("/api/control", { action: "resume" }); });
$("btnEnd").addEventListener("click", function () {
  if (state && state.status !== "ENDED" && !window.confirm("End the exercise now? Open decision points will close.")) return;
  api("/api/control", { action: "end" });
});
$("btnReset").addEventListener("click", function () {
  if (state && state.status !== "READY" && state.status !== "ENDED" && !window.confirm("Start a new session? The current one ends and is kept for review.")) return;
  api("/api/control", { action: "reset" });
});
$("speed").addEventListener("change", function () { api("/api/config", { speed: $("speed").value }); });

function draw() {
  $("scenarioName").textContent = state.scenario_name;
  $("clock").textContent = state.clock;
  const words = { READY: "Ready", RUNNING: "Running", PAUSED: "Paused", FROZEN: "Frozen for probe", ENDED: "Ended" };
  $("status").className = "pill " + state.status;
  $("statusText").textContent = words[state.status] || state.status;

  $("btnStart").disabled = state.status !== "READY";
  $("btnPause").disabled = state.status !== "RUNNING";
  $("btnResume").disabled = state.status !== "PAUSED";
  $("btnEnd").disabled = state.status === "READY" || state.status === "ENDED";
  $("btnAar").href = "/aar/" + state.session_id;

  drawGraph();
  if (selected) drawInspectorFacts();
  drawFogControls();
  drawPicture();
  drawRail();
  drawConfig();
  drawPeople();
  drawMatrix();
  drawConflicts();
  drawDecisions();
  drawScript();
  drawLog();
  drawInjectOptions();

  const c = state.counts;
  const legend = '<span class="cell DELIVERED">✓ Delivered ' + c.DELIVERED + '</span>'
    + '<span class="cell PENDING">In transit ' + c.PENDING + '</span>'
    + '<span class="cell HELD">Held ' + c.HELD + '</span>'
    + '<span class="cell DROPPED">✕ Lost ' + c.DROPPED + '</span>';
  $("matrixLegend").innerHTML = legend;
  $("counts").textContent = c.PENDING + " in flight, " + c.HELD + " held";
  $("fingerprint").textContent = "Fingerprint " + state.fingerprint;
}

// Keep the numbers in an open inspector live, without wiping the choices being made.
function drawInspectorFacts() {
  const box = $("inspector");
  const facts = box.querySelector(".facts");
  if (!facts) return;
  const parts = selected.split(":");
  if (parts[0] !== "role") return;
  for (const t of state.trainees) {
    if (t.role !== parts[1]) continue;
    const b = facts.querySelectorAll("b");
    b[0].textContent = t.link.toLowerCase() + (t.until !== null ? ", " + span(t.until) + " left" : "");
    b[1].textContent = t.exposure + " / " + state.config.located_threshold;
    b[2].textContent = t.pending;
    b[3].textContent = t.held;
  }
}

function drawRail() {
  const duration = Math.max(1, state.duration);
  const key = duration + JSON.stringify(state.upcoming);
  if (key !== railKey) {
    railKey = key;
    $("railEnd").textContent = fmt(duration).slice(2);
    let html = "";
    for (const row of state.upcoming) {
      if (row.action === "END") continue;
      let kind = row.action;
      if (kind === "SENSOR" || kind === "DF") kind = "LINK";
      const left = Math.min(100, (row.seconds / duration) * 100);
      html += '<span class="tick ' + kind + (row.fired ? " fired" : "") + '" style="left:' + left + '%" title="' + escapeHtml(row.at + "  " + row.text) + '"></span>';
    }
    $("railTicks").innerHTML = html;
  }
  const percent = Math.min(100, (state.sim_time / duration) * 100);
  $("railFill").style.width = percent + "%";
  $("railNow").style.left = percent + "%";
}

// ---------- settings ----------
const SLIDERS = [
  { id: "relay_delay", key: "relay_delay", show: function (v) { return span(v); } },
  { id: "runner_delay", key: "runner_delay", show: function (v) { return span(v); } },
  { id: "located_threshold", key: "located_threshold", show: function (v) { return String(v); } },
  { id: "located_penalty", key: "located_penalty", show: function (v) { return span(v); } },
  { id: "delayed_label_after", key: "delayed_label_after", show: function (v) { return span(v); } },
  { id: "stale_after", key: "stale_after", show: function (v) { return span(v); } },
  { id: "duration_in", key: "duration", out: "o_duration", show: function (v) { return span(v); } },
];

for (const s of SLIDERS) {
  const input = $(s.id);
  input.addEventListener("input", function () {
    configDirty = true;
    $(s.out || "o_" + s.id).textContent = s.show(parseFloat(input.value));
    $("configState").textContent = "Not applied yet";
  });
}
for (const id of ["seed", "show_degraded"]) {
  $(id).addEventListener("input", function () { configDirty = true; $("configState").textContent = "Not applied yet"; });
}
$("configForm").addEventListener("submit", async function (event) {
  event.preventDefault();
  const body = { show_degraded_to_trainee: $("show_degraded").checked };
  for (const s of SLIDERS) body[s.key] = parseFloat($(s.id).value);
  if (state && state.status === "READY") body.seed = parseInt($("seed").value, 10);
  const ok = await api("/api/config", body);
  if (ok) {
    configDirty = false;
    $("configState").textContent = "Applied";
    showToast("Settings applied. They affect messages sent from now on.");
  }
});

function drawConfig() {
  const c = state.config;
  const speedText = String(c.speed);
  if (!$("speed").querySelector('option[value="' + speedText + '"]')) {
    $("speed").innerHTML += '<option value="' + speedText + '">' + speedText + '×</option>';
  }
  $("speed").value = speedText;
  if (configDirty) return;
  for (const s of SLIDERS) {
    $(s.id).value = c[s.key];
    $(s.out || "o_" + s.id).textContent = s.show(c[s.key]);
  }
  $("seed").value = c.seed;
  $("seed").disabled = state.status !== "READY";
  $("show_degraded").checked = c.show_degraded_to_trainee;
  $("configState").textContent = "";
}

// ---------- trainees ----------
function drawPeople() {
  $("joinUrl").textContent = state.join_url;
  let key = "";
  for (const t of state.trainees) key += t.role + t.name + t.online + "|";
  if (key === peopleKey) return;
  peopleKey = key;
  let html = "";
  for (const t of state.trainees) {
    html += '<li><span class="name">' + escapeHtml(t.title) + '</span>'
      + (t.online ? '<span class="online">Connected</span>' : '<span class="offline">Not connected</span>')
      + '<span class="muted small">' + (t.name ? escapeHtml(t.name) : "Nobody yet") + '</span><span></span></li>';
  }
  $("people").innerHTML = html;
}

// ---------- who has what ----------
function drawMatrix() {
  const key = JSON.stringify(state.reports);
  if (key === matrixKey) return;
  matrixKey = key;
  const body = $("matrix").querySelector("tbody");
  if (state.reports.length === 0) {
    body.innerHTML = '<tr><td colspan="5" class="muted">No reports yet. Start the exercise and the scenario sends them on its own.</td></tr>';
    return;
  }
  let html = "";
  const rows = state.reports.slice().reverse();
  for (const r of rows) {
    let tags = "";
    if (r.truth === true) tags += '<span class="tag ok">True</span>';
    if (r.truth === false) tags += '<span class="tag bad">False</span>';
    if (r.critical) tags += '<span class="tag signal">Critical</span>';
    if (r.synthetic) tags += '<span class="tag">Added contradiction</span>';
    if (r.conflicts_with) tags += '<span class="tag bad">Conflicts with ' + escapeHtml(r.conflicts_with) + '</span>';
    html += '<tr><td class="rep"><span class="rid">' + escapeHtml(r.report_id) + '</span> <span class="muted small">'
      + escapeHtml(r.sender) + ', ' + r.source_time + '</span><span class="rtext">' + escapeHtml(r.text) + '</span>'
      + '<span class="tags">' + tags + '</span></td>';
    for (const role of ROLES) {
      const cell = r.cells[role.role];
      if (!cell || cell.status === "") html += '<td><span class="cell none">Not sent</span></td>';
      else if (cell.status === "DELIVERED") {
        const late = cell.delay !== null && cell.delay > state.config.delayed_label_after ? " late" : "";
        html += '<td><span class="cell DELIVERED' + late + '">✓ ' + cell.at + '<small>' + span(cell.delay) + ' delay' + (cell.via ? ", " + escapeHtml(cell.via) : "") + '</small></span></td>';
      } else if (cell.status === "PENDING") html += '<td><span class="cell PENDING">In transit</span></td>';
      else if (cell.status === "HELD") html += '<td><span class="cell HELD">Held<small>link cut</small></span></td>';
      else html += '<td><span class="cell DROPPED">✕ Lost</span></td>';
    }
    html += '</tr>';
  }
  body.innerHTML = html;
}

// ---------- contradictions ----------
function drawConflicts() {
  const key = JSON.stringify(state.contradictions);
  if (key === conflictsKey) return;
  conflictsKey = key;
  $("conflictCount").textContent = state.contradictions.length === 0 ? "" : state.contradictions.length + " so far";
  if (state.contradictions.length === 0) {
    $("conflicts").innerHTML = '<p class="muted small">None yet. They appear when two reports disagree.</p>';
    return;
  }
  let html = "";
  for (const c of state.contradictions) {
    const stateClass = c.state === "Resolved" ? "ok" : c.state === "Seen, unresolved" ? "late" : "";
    html += '<div class="conflict"><div class="pair">' + escapeHtml(c.a) + ' <span class="vs">vs</span> ' + escapeHtml(c.b)
      + ' <span class="tag ' + stateClass + '">' + escapeHtml(c.state) + '</span></div>'
      + '<div class="sides"><div><strong>' + escapeHtml(c.a_sender) + '</strong> ' + (c.a_truth === false ? '<span class="tag bad">False</span>' : '') + '<br>' + escapeHtml(c.a_text) + '</div>'
      + '<div><strong>' + escapeHtml(c.b_sender) + '</strong> ' + (c.b_truth === false ? '<span class="tag bad">False</span>' : '') + '<br>' + escapeHtml(c.b_text) + '</div></div>'
      + '<p class="who">';
    if (c.both.length > 0) html += 'Seen both sides: ' + escapeHtml(c.both.map(roleTitle).join(", ")) + '. ';
    else html += 'Nobody has seen both sides yet. ';
    if (c.resolved.length > 0) html += 'Read correctly by ' + escapeHtml(c.resolved.map(roleTitle).join(", ")) + '.';
    html += '</p></div>';
  }
  $("conflicts").innerHTML = html;
}

// ---------- decisions ----------
function drawDecisions() {
  const open = [];
  for (const dp of state.decision_points) {
    if (dp.status === "OPEN") open.push(dp.dp_id + " (" + dp.answered.length + "/" + dp.roles.length + ")");
  }
  $("dpLine").textContent = open.length > 0 ? "Open: " + open.join(", ") : "None open";
  const key = JSON.stringify(state.decisions);
  if (key === decisionsKey) return;
  decisionsKey = key;
  if (state.decisions.length === 0) {
    $("decisions").innerHTML = '<p class="muted small">Decisions appear here the moment they are submitted.</p>';
    return;
  }
  let html = "";
  for (const d of state.decisions) {
    let flags = "";
    if (d.late) flags += '<span class="tag late">After the window</span>';
    for (const pair of d.conflicts) flags += '<span class="tag bad">Saw ' + escapeHtml(pair.join(" vs ")) + '</span>';
    if (d.missing.length > 0) flags += '<span class="tag">Missing ' + escapeHtml(d.missing.join(", ")) + '</span>';
    const pct = d.percent === null ? "N/A" : d.percent + "%";
    html += '<div class="dec"><div><div class="head"><span class="dp">' + escapeHtml(d.dp_id) + '</span>' + escapeHtml(roleTitle(d.role)) + '</div>'
      + '<div class="choice">' + escapeHtml(d.choice) + '</div>'
      + '<div class="meta">' + d.time + ', took ' + span(d.latency) + ', ' + d.visible_count + ' reports in view</div></div>'
      + '<div class="score"><div class="pct">' + pct + '</div><div class="lbl">rubric</div></div>'
      + (d.rationale ? '<div class="rationale">"' + escapeHtml(d.rationale) + '"</div>' : '')
      + (flags ? '<div class="flags">' + flags + '</div>' : '')
      + '</div>';
  }
  $("decisions").innerHTML = html;
}

// ---------- script and log ----------
function drawScript() {
  const key = JSON.stringify(state.upcoming);
  if (key === scriptKey) return;
  scriptKey = key;
  let html = "";
  let nextMarked = false;
  for (const row of state.upcoming) {
    let cls = row.fired ? "fired" : "";
    if (!row.fired && !nextMarked) { cls = "next"; nextMarked = true; }
    html += '<li class="' + cls + '"><span class="t">' + row.at + '</span><span>' + escapeHtml(row.text) + '</span></li>';
  }
  $("script").innerHTML = html;
}

function drawLog() {
  const key = state.log.length > 0 ? state.log[0].id + ":" + state.log.length : "empty";
  if (key === logKey) return;
  logKey = key;
  if (state.log.length === 0) {
    $("log").innerHTML = '<li class="muted">Nothing has happened yet.</li>';
    return;
  }
  let html = "";
  for (const row of state.log.slice(0, 60)) {
    html += '<li><span class="t">' + row.t + '</span><span><span class="eid">E' + row.id + '</span>' + escapeHtml(row.text) + '</span></li>';
  }
  $("log").innerHTML = html;
}

// ---------- inject forms ----------
for (const box of document.querySelectorAll(".role-checks")) {
  let html = "";
  for (const r of ROLES) html += '<label><input type="checkbox" value="' + r.role + '"> ' + escapeHtml(r.title) + '</label>';
  box.innerHTML = html;
}

function checkedRoles(name) {
  const roles = [];
  for (const box of document.querySelectorAll('.role-checks[data-name="' + name + '"] input')) if (box.checked) roles.push(box.value);
  return roles;
}

for (const tab of document.querySelectorAll(".tab")) {
  tab.addEventListener("click", function () {
    for (const t of document.querySelectorAll(".tab")) t.classList.remove("on");
    for (const body of document.querySelectorAll(".tab-body")) body.classList.add("hidden");
    tab.classList.add("on");
    $(tab.dataset.tab).classList.remove("hidden");
  });
}

$("tabReport").addEventListener("submit", async function (event) {
  event.preventDefault();
  const ok = await api("/api/inject/report", {
    text: $("repText").value, sender: $("repSender").value, grid: $("repGrid").value,
    domain: $("repDomain").value, truth: $("repTruth").value === "true",
    to_roles: checkedRoles("repTo"), critical: $("repCritical").checked,
    question: $("repQuestion").value, direction: $("repDirection").value,
  });
  if (ok) { $("repText").value = ""; showToast("Report sent."); }
});

$("tabConflict").addEventListener("submit", async function (event) {
  event.preventDefault();
  const ok = await api("/api/inject/conflict", {
    report_id: $("conTarget").value, text: $("conText").value, sender: $("conSender").value, to_roles: checkedRoles("conTo"),
  });
  if (ok) { $("conText").value = ""; showToast("Contradiction sent."); }
});

$("tabDecision").addEventListener("submit", async function (event) {
  event.preventDefault();
  const ok = await api("/api/inject/decision", {
    prompt: $("decPrompt").value, options: $("decOptions").value.split("\n"), window: $("decWindow").value, roles: checkedRoles("decRoles"),
  });
  if (ok) { $("decPrompt").value = ""; showToast("Decision point opened."); }
});

$("tabProbe").addEventListener("submit", async function (event) {
  event.preventDefault();
  const ok = await api("/api/probe/start", { probe_id: $("probeId").value });
  if (ok) showToast("Clock stopped. Trainee screens are fogged while they answer.");
});
$("probeEnd").addEventListener("click", function () { api("/api/probe/end"); });

function drawInjectOptions() {
  let key = "";
  for (const r of state.reports) key += r.report_id + "|";
  if (key !== reportOptionsKey) {
    reportOptionsKey = key;
    const select = $("conTarget");
    const previous = select.value;
    let html = "";
    for (const r of state.reports) {
      if (r.synthetic) continue;
      html += '<option value="' + escapeHtml(r.report_id) + '">' + escapeHtml(r.report_id + ": " + r.text.slice(0, 60)) + '</option>';
    }
    select.innerHTML = html || '<option value="">No reports yet</option>';
    if (previous) select.value = previous;
  }
  const qKey = JSON.stringify(state.questions);
  if (qKey !== questionsKey) {
    questionsKey = qKey;
    let html = '<option value="">No key question</option>';
    for (const q of state.questions) html += '<option value="' + q.q_id + '">' + escapeHtml(q.short) + '</option>';
    $("repQuestion").innerHTML = html;
  }
  let pkey = "";
  for (const p of state.probes) pkey += p.probe_id + "|";
  if (pkey !== probeOptionsKey) {
    probeOptionsKey = pkey;
    let html = "";
    for (const p of state.probes) html += '<option value="' + escapeHtml(p.probe_id) + '">' + escapeHtml(p.label) + '</option>';
    $("probeId").innerHTML = html;
  }
  $("probeState").textContent = state.probe ? "Running " + state.probe.probe_id + ". Answered so far: " + (state.probe.answered.length > 0 ? state.probe.answered.map(roleTitle).join(", ") : "nobody") + "." : "";
  $("probeEnd").disabled = !state.probe;
}

connect();
