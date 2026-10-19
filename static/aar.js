// SituSim After-Action Review: timeline filters, hover read-outs on the small
// charts, the "see what they saw" player, and the optional AI summary button.

function escapeHtml(text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function fmt(t) {
  t = Math.max(0, Math.floor(t));
  return "T+" + String(Math.floor(t / 60)).padStart(2, "0") + ":" + String(t % 60).padStart(2, "0");
}

const GAP_NAMES = { sound: "Sound read", info: "Information gap", reasoning: "Reasoning gap", lucky: "Ahead of the evidence", unset: "No read" };
const CHANNEL = { net: "radio", relay: "relay", runner: "runner", flare: "flare" };

const replayText = document.getElementById("replayData").textContent;
const replay = replayText && replayText !== "null" ? JSON.parse(replayText) : null;

// The latest value at or before time t, from a list of [time, value] steps.
function valueAt(steps, t) {
  let value = null;
  for (const step of steps || []) {
    if (step[0] <= t) value = step[1];
  }
  return value;
}

// Index of the last sample time at or before t.
function indexAt(t) {
  let index = 0;
  for (let i = 0; i < replay.times.length; i++) {
    if (replay.times[i] <= t) index = i;
  }
  return index;
}

function classify(read, shadow, truth) {
  if (read === null || read === undefined || truth === null || truth === undefined) return "unset";
  const traineeRight = read === 50 ? false : (read > 50) === truth;
  const shadowRight = (shadow > 50) === truth;
  if (traineeRight && shadowRight) return "sound";
  if (!traineeRight && !shadowRight) return "info";
  if (!traineeRight && shadowRight) return "reasoning";
  return "lucky";
}

function roleData(roleId) {
  for (const r of replay.roles) if (r.role === roleId) return r;
  return null;
}

// What one role believed about one question at time t, against the shadow analyst and the truth.
function pictureAt(roleId, qId, t) {
  const role = roleData(roleId);
  const i = indexAt(t);
  const read = role ? valueAt(role.reads[qId], t) : null;
  const shadowSeries = (replay.shadow[roleId] || {})[qId] || [];
  const shadow = shadowSeries.length > i ? shadowSeries[i] : null;
  const truthSeries = replay.truth[qId] || [];
  const truth = truthSeries.length > i ? truthSeries[i] === 1 : null;
  return { read: read, shadow: shadow, truth: truth, category: classify(read, shadow, truth) };
}

// ============================================================
// Timeline filters
// ============================================================

const filterButtons = document.querySelectorAll("#filters button");
for (const button of filterButtons) {
  button.addEventListener("click", function () {
    for (const b of filterButtons) b.classList.remove("on");
    button.classList.add("on");
    const cat = button.dataset.cat;
    for (const row of document.querySelectorAll("#timeline li")) {
      row.style.display = cat === "all" || row.dataset.cat === cat ? "" : "none";
    }
  });
}

// ============================================================
// Small charts: one crosshair moves across all of them, the tooltip
// explains the moment under the pointer, a click replays it
// ============================================================

const tip = document.getElementById("tip");
const minis = document.querySelectorAll("figure.mini");
const CHART_W = 300;
const PAD = 4;

function timeFromPointer(svg, clientX) {
  const box = svg.getBoundingClientRect();
  const x = (clientX - box.left) / box.width * CHART_W;
  const fraction = Math.max(0, Math.min(1, (x - PAD) / (CHART_W - 2 * PAD)));
  return { t: fraction * replay.end, x: PAD + fraction * (CHART_W - 2 * PAD) };
}

function showCross(x) {
  for (const figure of minis) {
    const line = figure.querySelector(".cross");
    if (!line) continue;
    line.setAttribute("x1", x);
    line.setAttribute("x2", x);
    line.classList.remove("hidden");
  }
}

function hideCross() {
  for (const figure of minis) {
    const line = figure.querySelector(".cross");
    if (line) line.classList.add("hidden");
  }
  if (tip) tip.classList.add("hidden");
}

if (replay && tip) {
  for (const figure of minis) {
    const svg = figure.querySelector("svg");
    svg.addEventListener("pointermove", function (event) {
      const where = timeFromPointer(svg, event.clientX);
      showCross(where.x);
      const role = roleData(figure.dataset.role);
      let question = figure.dataset.q;
      for (const q of replay.questions) if (q.q_id === figure.dataset.q) question = q.short;
      const p = pictureAt(figure.dataset.role, figure.dataset.q, where.t);
      tip.innerHTML = '<b>' + fmt(where.t) + '</b> · ' + escapeHtml(role ? role.title : "") + '<br>'
        + escapeHtml(question) + '<br>'
        + '<span class="tr">their read: ' + (p.read === null ? "not set" : p.read + "%") + '</span><br>'
        + '<span class="ts">reports supported: ' + (p.shadow === null ? "-" : Math.round(p.shadow) + "%") + '</span><br>'
        + '<span class="tt">truth: ' + (p.truth === null ? "-" : (p.truth ? "yes" : "no")) + '</span><br>'
        + GAP_NAMES[p.category].toLowerCase() + (document.getElementById("player") ? " · click to replay" : "");
      tip.classList.remove("hidden");
      let left = event.clientX + 16;
      if (left + 230 > window.innerWidth) left = event.clientX - 236;
      tip.style.left = left + "px";
      tip.style.top = (event.clientY + 14) + "px";
    });
    svg.addEventListener("pointerleave", hideCross);
    svg.addEventListener("click", function (event) {
      if (!document.getElementById("player")) return;
      const where = timeFromPointer(svg, event.clientX);
      selectRole(figure.dataset.role);
      setTime(where.t);
      stopPlaying();
      document.getElementById("player").scrollIntoView({ behavior: "smooth", block: "center" });
    });
  }
}

// ============================================================
// See what they saw: rebuild one trainee's screen at any moment
// ============================================================

const player = document.getElementById("player");
let playRole = null;
let playT = 0;
let playSpeed = 20;
let playing = false;
let lastFrame = 0;
let feedKey = "";

function selectRole(roleId) {
  playRole = roleId;
  for (const b of document.querySelectorAll("#playerRoles button")) {
    b.classList.toggle("on", b.dataset.role === roleId);
  }
  feedKey = "";
  drawPlayer();
}

function setTime(t) {
  playT = Math.max(0, Math.min(replay.end, t));
  document.getElementById("playTime").value = Math.round(playT);
  drawPlayer();
}

function stopPlaying() {
  playing = false;
  document.getElementById("playBtn").textContent = "Play";
}

function drawPlayer() {
  if (!player || !playRole) return;
  const t = playT;
  const role = roleData(playRole);
  document.getElementById("playClock").textContent = fmt(t);

  // Their reads at this moment
  const link = valueAt(role.links, t) || "UP";
  let located = false;
  for (const l of role.located || []) {
    if (l <= t && t < l + 60) located = true;
  }
  const linkText = { UP: "Radio up", DEGRADED: "Radio degraded", DOWN: "Radio down" };
  let html = '<p class="pv-title">their read on each question</p>'
    + '<span class="pill pv-link ' + link + '"><span class="dot"></span>' + linkText[link] + (located ? ", located" : "") + '</span>';
  for (const q of replay.questions) {
    const p = pictureAt(playRole, q.q_id, t);
    html += '<div class="pv-q"><div class="pv-q-head"><span>' + escapeHtml(q.short) + '</span>'
      + (p.read === null ? '<span class="v unset">not set</span>' : '<span class="v">' + p.read + '%</span>') + '</div>'
      + '<div class="pv-track">'
      + (p.truth !== null ? '<span class="pv-truth" style="left: ' + (p.truth ? 100 : 0) + '%" title="Truth"></span>' : '')
      + (p.shadow !== null ? '<span class="pv-shadow" style="left: ' + p.shadow + '%" title="What their reports supported"></span>' : '')
      + (p.read !== null ? '<span class="pv-read" style="left: ' + p.read + '%" title="Their read"></span>' : '')
      + '</div><span class="tag g-' + p.category + '">' + GAP_NAMES[p.category] + '</span></div>';
  }
  document.getElementById("pvReads").innerHTML = html;

  // What had reached them, newest first, with their decisions in between
  const rows = [];
  for (const item of role.items) {
    if (item.at <= t) rows.push({ at: item.at, item: item });
  }
  for (const d of replay.decisions) {
    if (d.role === playRole && d.at !== null && d.at <= t) rows.push({ at: d.at, decision: d });
  }
  const key = playRole + ":" + rows.length;
  if (key === feedKey) return;
  feedKey = key;
  rows.sort(function (a, b) { return b.at - a.at; });
  let feed = '<p class="pv-title">what had reached them by ' + fmt(t) + ' (' + rows.length + ')</p>';
  if (rows.length === 0) feed += '<p class="pv-empty">Nothing had reached them yet.</p>';
  for (const row of rows.slice(0, 40)) {
    if (row.decision) {
      feed += '<div class="pv-item dec"><div class="pv-head"><b>' + escapeHtml(row.decision.dp_id) + '</b><span>' + (row.decision.missed ? 'window closed at ' : 'decided at ') + fmt(row.at) + '</span></div>'
        + '<p>' + (row.decision.missed ? 'No decision before the window closed.' : 'Chose: ' + escapeHtml(row.decision.choice)) + '</p></div>';
      continue;
    }
    const item = row.item;
    let cls = "pv-item";
    if (item.from) cls += " team";
    if (item.truth === false) cls += " false";
    let who = escapeHtml(item.sender);
    if (item.kind === "forward") who = escapeHtml(item.sender) + ", passed on by " + escapeHtml(item.from);
    else if (item.from) who = "from " + escapeHtml(item.from);
    const took = item.at - (item.source_time || item.at);
    feed += '<div class="' + cls + '"><div class="pv-head"><b>' + escapeHtml(item.rid) + '</b><span>' + who + '</span>'
      + '<span>reached them ' + fmt(item.at) + ' by ' + (CHANNEL[item.via] || "radio") + (took > 0 ? ', ' + Math.round(took) + ' s after it was seen' : '') + '</span>'
      + (item.truth === false ? '<span class="tag bad">False</span>' : '') + '</div>'
      + '<p>' + escapeHtml(item.text) + '</p></div>';
  }
  document.getElementById("pvFeed").innerHTML = feed;
}

function playLoop(now) {
  if (playing) {
    const dt = Math.min(0.1, (now - lastFrame) / 1000);
    playT = playT + dt * playSpeed;
    if (playT >= replay.end) {
      playT = replay.end;
      stopPlaying();
    }
    document.getElementById("playTime").value = Math.round(playT);
    drawPlayer();
  }
  lastFrame = now;
  requestAnimationFrame(playLoop);
}

if (player && replay) {
  let tabs = "";
  for (const r of replay.roles) {
    tabs += '<button type="button" data-role="' + r.role + '">' + escapeHtml(r.title) + '</button>';
  }
  document.getElementById("playerRoles").innerHTML = tabs;
  for (const b of document.querySelectorAll("#playerRoles button")) {
    b.addEventListener("click", function () { selectRole(b.dataset.role); });
  }
  document.getElementById("playTime").addEventListener("input", function () {
    stopPlaying();
    setTime(parseFloat(document.getElementById("playTime").value));
  });
  document.getElementById("playBtn").addEventListener("click", function () {
    if (playing) {
      stopPlaying();
      return;
    }
    if (playT >= replay.end) setTime(0);
    playing = true;
    document.getElementById("playBtn").textContent = "Pause";
  });
  for (const b of document.querySelectorAll("#playSpeed button")) {
    b.addEventListener("click", function () {
      for (const other of document.querySelectorAll("#playSpeed button")) other.classList.remove("on");
      b.classList.add("on");
      playSpeed = parseFloat(b.dataset.speed);
    });
  }
  selectRole(replay.roles[0].role);
  // Start on the first decision point, where the review usually begins.
  let firstDecision = null;
  for (const d of replay.decisions) {
    if (d.at !== null && (firstDecision === null || d.at < firstDecision)) firstDecision = d.at;
  }
  setTime(firstDecision !== null ? firstDecision : 0);
  requestAnimationFrame(playLoop);
}

// ============================================================
// Optional AI summary
// ============================================================

const aiButton = document.getElementById("aiButton");
if (aiButton) {
  aiButton.addEventListener("click", async function () {
    const result = document.getElementById("aiResult");
    aiButton.disabled = true;
    aiButton.textContent = "Drafting from the event log…";
    try {
      const response = await fetch("/aar/" + aiButton.dataset.session + "/summary", { method: "POST" });
      const data = await response.json();
      if (data.ok) {
        result.innerHTML = '<div class="ai-text"><span class="label">$ drafted by ' + escapeHtml(data.provider)
          + '. Every sentence was checked against the events it cites.</span>' + escapeHtml(data.text) + '</div>';
        aiButton.classList.add("hidden");
      } else {
        result.innerHTML = '<p class="ai-error">' + escapeHtml(data.reason) + '</p>';
        aiButton.disabled = false;
        aiButton.textContent = "Try again";
      }
    } catch (error) {
      result.innerHTML = '<p class="ai-error">Could not reach the server.</p>';
      aiButton.disabled = false;
      aiButton.textContent = "Try again";
    }
  });
}
