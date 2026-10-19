// SituSim trainee workspace
// The server sends a full "state" message every tick. This page redraws only
// the parts that changed, so typing, ticked boxes and open panels are never wiped.

const params = new URLSearchParams(location.search);
const ROLE = (params.get("role") || "").toUpperCase();
const NAME = (params.get("name") || "").trim();
if (ROLE === "" || NAME === "") {
  location.href = "/";
}

const ABBR = { GTL: "GL", ISR: "AIR", EW: "EW", COMMS: "COM" };
const GRADE_RELIABILITY = {
  A: "Completely reliable", B: "Usually reliable", C: "Fairly reliable",
  D: "Not usually reliable", E: "Unreliable", F: "Reliability cannot be judged",
};
const GRADE_CREDIBILITY = {
  1: "Confirmed by other sources", 2: "Probably true", 3: "Possibly true",
  4: "Doubtful", 5: "Improbable", 6: "Truth cannot be judged",
};

let ws = null;
let hello = null;
let state = null;
let sessionId = null;
let channel = "radio";
let feedKey = "";
let dpKey = "";
let sentKey = "";
let probeKey = "";
let rosterKey = "";
let readsKey = "";
let mapKey = "";
let forwardDelivery = null;
let openGrader = null;            // report id whose grading panel is open
const gradeDraft = {};            // report id -> {reliability, credibility}
const seenDeliveries = new Set();
let firstFeedDraw = true;
const cited = new Set();          // report ids ticked as evidence
const dpDrafts = {};              // dp_id -> {choice, confidence, rationale}
const draggingRead = {};          // q_id -> true while the slider is held

// ---------- small helpers ----------
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

// How foggy a report is, from 0 (fresh) to 1 (very old).
function fogFor(age) {
  return Math.max(0, Math.min(1, (age - 45) / 240));
}

let toastTimer = null;
function showToast(text) {
  const toast = $("toast");
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 3200);
}

function send(message) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(message));
    return true;
  }
  showToast("Not connected. Reconnecting…");
  return false;
}

// ---------- connection (reconnects by itself) ----------
function connect() {
  const protocol = location.protocol === "https:" ? "wss://" : "ws://";
  ws = new WebSocket(protocol + location.host + "/ws/trainee/" + ROLE + "/" + encodeURIComponent(NAME));

  ws.onmessage = function (event) {
    const msg = JSON.parse(event.data);
    if (msg.type === "hello") {
      if (sessionId !== null && sessionId !== msg.session_id) {
        location.reload();     // the instructor started a new session
        return;
      }
      sessionId = msg.session_id;
      hello = msg;
      drawHello();
    }
    if (msg.type === "state") {
      if (sessionId !== null && msg.session_id !== sessionId) {
        location.reload();
        return;
      }
      state = msg;
      drawState();
    }
    if (msg.type === "toast") {
      showToast(msg.text);
    }
  };

  ws.onclose = function () {
    $("banner").className = "banner waiting";
    $("banner").textContent = "Connection to the exercise lost. Reconnecting…";
    setTimeout(connect, 2000);
  };
}

// ============================================================
// Things that never change during a session
// ============================================================

function drawHello() {
  $("exerciseName").textContent = hello.name;
  $("who").innerHTML = escapeHtml(hello.role_title) + "<small>" + escapeHtml(NAME) + "</small>";
  $("areaName").textContent = hello.area.split(" (")[0];
  $("mission").textContent = hello.mission;
  $("intent").textContent = hello.intent;
  $("roleTitle").textContent = "Your role: " + hello.role_title;
  $("roleBrief").textContent = hello.role_brief;
  $("disclaimer").textContent = hello.disclaimer;
  document.title = "SituSim: " + hello.role_title;

  let options = '<option value="ALL">Everyone</option>';
  for (const r of hello.roles) {
    if (r.role !== ROLE) options += '<option value="' + r.role + '">' + escapeHtml(r.title) + '</option>';
  }
  $("chatTo").innerHTML = options;

  let flares = "";
  for (const signal of hello.flares) {
    flares += '<button class="btn flare-btn" type="button" data-signal="' + escapeHtml(signal) + '">Fire flare: ' + escapeHtml(signal) + '</button>';
  }
  $("flares").innerHTML = flares;
  for (const button of document.querySelectorAll(".flare-btn")) {
    button.addEventListener("click", function () {
      if (send({ type: "flare", signal: button.dataset.signal })) showToast("Flare fired. Everyone will see it, including the other side.");
    });
  }

  drawReadsSkeleton();
  drawChannels();
  drawMapBase();
}

// ============================================================
// Your read: a live probability on each key question
// ============================================================

function drawReadsSkeleton() {
  let html = "";
  for (const q of hello.questions) {
    html += '<div class="read" data-q="' + q.q_id + '">'
      + '<div class="read-q"><span>' + escapeHtml(q.text) + '</span><span class="read-value unset" id="rv-' + q.q_id + '">Not set</span></div>'
      + '<div class="read-track unset" id="rt-' + q.q_id + '">'
      + '<div class="pins" id="pins-' + q.q_id + '"></div>'
      + '<input type="range" min="0" max="100" step="5" value="50" id="rs-' + q.q_id + '" aria-label="' + escapeHtml(q.text) + '">'
      + '</div>'
      + '<div class="read-ends"><span>' + escapeHtml(q.no) + '</span><span>Can\'t tell</span><span>' + escapeHtml(q.yes) + '</span></div>'
      + '</div>';
  }
  $("reads").innerHTML = html;
  for (const q of hello.questions) {
    const slider = $("rs-" + q.q_id);
    slider.addEventListener("input", function () {
      draggingRead[q.q_id] = true;
      showReadValue(q.q_id, parseInt(slider.value, 10), true);
    });
    slider.addEventListener("change", function () {
      draggingRead[q.q_id] = false;
      send({ type: "belief", q_id: q.q_id, value: parseInt(slider.value, 10) });
    });
  }
}

function showReadValue(qId, value, isSet) {
  const label = $("rv-" + qId);
  const track = $("rt-" + qId);
  if (!isSet) {
    label.textContent = "Not set";
    label.classList.add("unset");
    track.classList.add("unset");
    return;
  }
  label.textContent = value + "%";
  label.classList.remove("unset");
  track.classList.remove("unset");
}

function drawReads() {
  const key = JSON.stringify([state.reads, state.team_reads, Math.floor(state.sim_time / 15)]);
  if (key === readsKey) return;
  readsKey = key;
  for (const q of hello.questions) {
    const mine = state.reads[q.q_id];
    if (!draggingRead[q.q_id]) {
      if (mine) {
        $("rs-" + q.q_id).value = mine.value;
        showReadValue(q.q_id, mine.value, true);
      } else {
        showReadValue(q.q_id, 50, false);
      }
    }
    // Teammates' reads, but only the ones that actually reached you.
    let pins = "";
    for (const r of state.team_reads) {
      if (r.q_id !== q.q_id) continue;
      const old = state.sim_time - r.sent_at > 120;
      pins += '<span class="pin' + (old ? " old" : "") + '" style="left:' + r.value + '%" title="' + escapeHtml(r.from_title)
        + ' said ' + r.value + '% at ' + fmt(r.sent_at) + ', it reached you at ' + fmt(r.received_at) + '">'
        + ABBR[r.from_role] + ' ' + r.value + '%</span>';
    }
    $("pins-" + q.q_id).innerHTML = pins;
  }
}

$("shareRead").addEventListener("click", function () {
  if (channel === "flare") {
    showToast("A flare can't carry your read. Pick radio, relay or runner.");
    return;
  }
  if (send({ type: "share_read", to: "ALL", channel: channel })) showToast("Read shared by " + channel + ".");
});

// ============================================================
// Channels: radio, relay, runner, flare
// ============================================================

function channelInfo() {
  const down = state && state.link === "DOWN";
  return [
    { id: "radio", name: "Radio", eta: down ? "No signal" : "Seconds", warn: down,
      what: "Fast, but jamming slows or loses it. Raises your detectability." },
    { id: "relay", name: "Relay", eta: "~" + (hello ? hello.relay_delay : 20) + " s",
      what: "Through the Comms Coordinator. Gets past jamming; Comms does the transmitting." },
    { id: "runner", name: "Runner", eta: "~" + Math.round((hello ? hello.runner_delay : 120) / 60) + " min",
      what: "Always arrives and makes no signal, but it is slow." },
    { id: "flare", name: "Flare", eta: "Instant",
      what: "Three fixed signals only. Everyone sees it, the other side too." },
  ];
}

let channelsKey = "";
function drawChannels() {
  const info = channelInfo();
  const key = JSON.stringify(info) + channel;
  if (key === channelsKey) return;
  channelsKey = key;
  let html = "";
  for (const c of info) {
    html += '<button type="button" role="radio" aria-checked="' + (c.id === channel) + '" class="channel' + (c.id === channel ? " on" : "") + (c.warn ? " warn" : "") + '" data-channel="' + c.id + '">'
      + '<span class="name">' + c.name + '<span class="eta">' + c.eta + '</span></span>'
      + '<span class="what">' + c.what + '</span></button>';
  }
  $("channels").innerHTML = html;
  for (const button of document.querySelectorAll(".channel")) {
    button.addEventListener("click", function () {
      channel = button.dataset.channel;
      drawChannels();
    });
  }
  $("textMode").classList.toggle("hidden", channel === "flare");
  $("flareMode").classList.toggle("hidden", channel !== "flare");
  const hints = {
    radio: "Raises your detectability",
    relay: "Raises the Comms Coordinator's detectability",
    runner: "Silent",
  };
  $("costHint").textContent = hints[channel] || "";
  $("sendBtn").textContent = "Send by " + channel;
  $("shareHint").textContent = channel === "flare" ? "Pick radio, relay or runner to share" : "Goes to everyone by " + channel;
}

$("chatForm").addEventListener("submit", function (event) {
  event.preventDefault();
  const text = $("chatText").value.trim();
  if (text === "") { showToast("Write a message first."); return; }
  const ok = send({ type: "chat", to: $("chatTo").value, text: text, channel: channel });
  if (ok) {
    $("chatText").value = "";
    showToast("Sent by " + channel + ". You'll only know it arrived if someone acknowledges it.");
  }
});

// ============================================================
// The map, with fog where your information is old
// ============================================================

const CELL = 50;
const MARGIN = 40;

function cellCenter(grid) {
  if (!grid || grid.length < 2) return null;
  const col = grid.charCodeAt(0) - 65;
  const row = parseInt(grid.slice(1), 10) - 1;
  if (isNaN(row) || col < 0 || col > 7 || row < 0 || row > 7) return null;
  return { x: MARGIN + col * CELL + CELL / 2, y: MARGIN + row * CELL + CELL / 2, col: col, row: row };
}

function pathThrough(cells) {
  let d = "";
  for (const cell of cells) {
    const c = cellCenter(cell);
    if (c) d += (d === "" ? "M" : " L") + c.x + " " + c.y;
  }
  return d;
}

function drawMapBase() {
  const map = hello.map;
  let svg = '<defs><filter id="fogBlur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="14"/></filter></defs>';
  const hills = [{ x: 330, y: 110, r: 70 }, { x: 110, y: 330, r: 60 }, { x: 380, y: 380, r: 40 }];
  for (const h of hills) {
    for (let k = 0; k < 4; k++) {
      const rx = h.r - k * 15;
      if (rx <= 4) continue;
      svg += '<ellipse cx="' + h.x + '" cy="' + h.y + '" rx="' + rx + '" ry="' + (rx * 0.72) + '" fill="none" stroke="#cfc6b0" stroke-width="1.2"/>';
    }
  }
  for (let i = 0; i <= 8; i++) {
    const p = MARGIN + i * CELL;
    svg += '<line x1="' + MARGIN + '" y1="' + p + '" x2="' + (MARGIN + 8 * CELL) + '" y2="' + p + '" stroke="rgba(47, 107, 255, 0.16)"/>';
    svg += '<line x1="' + p + '" y1="' + MARGIN + '" x2="' + p + '" y2="' + (MARGIN + 8 * CELL) + '" stroke="rgba(47, 107, 255, 0.16)"/>';
  }
  for (let i = 0; i < 8; i++) {
    svg += '<text x="' + (MARGIN + i * CELL + CELL / 2) + '" y="' + (MARGIN - 13) + '" text-anchor="middle" font-size="11.5" font-weight="700" font-family="JetBrains Mono, monospace" fill="#6b6860">' + String.fromCharCode(65 + i) + '</text>';
    svg += '<text x="' + (MARGIN - 15) + '" y="' + (MARGIN + i * CELL + CELL / 2 + 4) + '" text-anchor="middle" font-size="11.5" font-weight="700" font-family="JetBrains Mono, monospace" fill="#6b6860">' + (i + 1) + '</text>';
  }
  svg += '<path d="' + pathThrough(map.river) + '" fill="none" stroke="#9bd7ff" stroke-width="13" stroke-linecap="round" stroke-linejoin="round"/>';
  svg += '<path d="' + pathThrough(map.main_road) + '" fill="none" stroke="#6b6860" stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round"/>';
  svg += '<path d="' + pathThrough(map.alt_road) + '" fill="none" stroke="#6b6860" stroke-width="2.5" stroke-dasharray="6 5" stroke-linecap="round"/>';

  let labels = "";
  for (const f of map.features) {
    const c = cellCenter(f.grid);
    if (!c) continue;
    if (f.kind === "bridge") svg += '<rect x="' + (c.x - 13) + '" y="' + (c.y - 7) + '" width="26" height="14" rx="3" fill="#fffdf7" stroke="#141414" stroke-width="2"/>';
    else if (f.kind === "ford") svg += '<line x1="' + (c.x - 14) + '" y1="' + (c.y - 6) + '" x2="' + (c.x + 14) + '" y2="' + (c.y + 6) + '" stroke="#141414" stroke-width="2.5" stroke-dasharray="3 3"/>';
    else if (f.kind === "friendly") svg += '<circle cx="' + c.x + '" cy="' + c.y + '" r="11" fill="#ff5a1f" stroke="#141414" stroke-width="2.5"/>';
    else if (f.kind === "op") svg += '<path d="M' + c.x + ' ' + (c.y - 10) + ' L' + (c.x + 10) + ' ' + (c.y + 8) + ' L' + (c.x - 10) + ' ' + (c.y + 8) + ' Z" fill="#fffdf7" stroke="#141414" stroke-width="2"/>';
    else if (f.kind === "objective") svg += '<circle cx="' + c.x + '" cy="' + c.y + '" r="10" fill="none" stroke="#141414" stroke-width="2"/><circle cx="' + c.x + '" cy="' + c.y + '" r="3" fill="#141414"/>';
    labels += '<text x="' + c.x + '" y="' + (c.y + 25) + '" text-anchor="middle" font-size="16" font-weight="700" font-family="Caveat, cursive" fill="' + (f.kind === "friendly" ? "#c93c0a" : "#141414") + '" stroke="#faf6ea" stroke-width="4" paint-order="stroke">' + escapeHtml(f.name) + '</text>';
  }
  svg += '<g id="fogLayer" filter="url(#fogBlur)"></g><g id="reportMarkers"></g><g pointer-events="none">' + labels + '</g>';
  $("map").innerHTML = svg;
}

function drawMapLive() {
  const key = state.feed.length + ":" + Math.floor(state.sim_time / 5);
  if (key === mapKey) return;
  mapKey = key;

  // How clear each cell is: fresh reports nearby clear the fog, and it creeps back as they age.
  let fog = "";
  for (let row = 0; row < 8; row++) {
    for (let col = 0; col < 8; col++) {
      let clarity = 0;
      for (const item of state.feed) {
        const c = cellCenter(item.grid);
        if (!c) continue;
        const dist = Math.sqrt((c.col - col) * (c.col - col) + (c.row - row) * (c.row - row));
        const freshness = Math.max(0, 1 - item.age / 300);
        const near = Math.max(0, 1 - dist / 2.4);
        clarity = Math.max(clarity, freshness * near);
      }
      const opacity = 0.82 * (1 - clarity);
      fog += '<rect x="' + (MARGIN + col * CELL - 6) + '" y="' + (MARGIN + row * CELL - 6) + '" width="' + (CELL + 12) + '" height="' + (CELL + 12) + '" fill="#e2dccb" opacity="' + opacity.toFixed(2) + '"/>';
    }
  }
  document.getElementById("fogLayer").innerHTML = fog;
  drawMarkers(state.feed);
}

function drawMarkers(feed) {
  const layer = document.getElementById("reportMarkers");
  if (!layer) return;
  const perCell = {};
  const drawn = new Set();
  let html = "";
  const items = feed.slice().reverse();
  for (const item of items) {
    if (drawn.has(item.report_id)) continue;
    const c = cellCenter(item.grid);
    if (!c) continue;
    drawn.add(item.report_id);
    const n = perCell[item.grid] || 0;
    perCell[item.grid] = n + 1;
    const offsets = [[-13, -13], [13, -13], [-13, 13], [13, 13], [0, 0]];
    const o = offsets[n % offsets.length];
    const x = c.x + o[0];
    const y = c.y + o[1];
    let fill = "#141414";
    let stroke = "#faf6ea";
    let strokeWidth = 2;
    let textFill = "#ffffff";
    if (item.chips.indexOf("Delayed") >= 0) { fill = "#ffb86b"; textFill = "#141414"; }
    if (item.chips.indexOf("Conflicting") >= 0) { stroke = "#e5342b"; strokeWidth = 3.5; }
    const opacity = (1 - fogFor(item.age) * 0.5).toFixed(2);
    html += '<g class="marker" data-rid="' + escapeHtml(item.report_id) + '" style="cursor:pointer" opacity="' + opacity + '">'
      + '<title>' + escapeHtml(item.report_id + ": " + item.text) + '</title>'
      + '<circle cx="' + x + '" cy="' + y + '" r="11" fill="' + fill + '" stroke="' + stroke + '" stroke-width="' + strokeWidth + '"/>'
      + '<text x="' + x + '" y="' + (y + 3.5) + '" text-anchor="middle" font-size="' + (item.report_id.length > 3 ? 7.5 : 9.5) + '" font-weight="700" font-family="JetBrains Mono, monospace" fill="' + textFill + '">' + escapeHtml(item.report_id) + '</text>'
      + '</g>';
  }
  layer.innerHTML = html;
  for (const marker of layer.querySelectorAll(".marker")) {
    marker.addEventListener("click", function () {
      const slip = document.querySelector('.slip[data-rid="' + marker.dataset.rid + '"]');
      if (slip) {
        slip.scrollIntoView({ behavior: "smooth", block: "center" });
        slip.classList.remove("fresh");
        void slip.offsetWidth;
        slip.classList.add("fresh");
      }
    });
  }
}

// ============================================================
// Everything that changes
// ============================================================

function drawState() {
  if (!hello) return;
  $("clock").textContent = state.clock;

  const words = { READY: "Waiting to start", RUNNING: "Running", PAUSED: "Paused", FROZEN: "Frozen", ENDED: "Ended" };
  $("status").className = "pill " + state.status;
  $("statusText").textContent = words[state.status] || state.status;

  // Radio status
  const net = $("netPill");
  if (state.link === "DOWN") {
    net.className = "pill DOWN";
    $("netText").textContent = state.back_in !== null ? "No signal, back in " + span(state.back_in) : "No signal";
  } else if (state.link === "DEGRADED") {
    net.className = "pill DEGRADED";
    $("netText").textContent = "Radio degraded";
  } else {
    net.className = "pill UP";
    $("netText").textContent = "Radio up";
  }

  // Detectability meter
  const max = state.threshold * 1.25;
  const percent = Math.min(100, state.exposure / max * 100);
  $("meterFill").style.width = percent + "%";
  $("meterFill").classList.toggle("hot", state.exposure >= state.threshold * 0.75);
  $("meterLine").style.left = (state.threshold / max * 100) + "%";

  const banner = $("banner");
  if (state.status === "READY") {
    banner.className = "banner waiting";
    banner.textContent = "Waiting for the instructor to start. Set your first read on each question while you wait.";
  } else if (state.located) {
    banner.className = "banner located";
    banner.textContent = "Your transmissions were located. You are relocating, with no radio for " + span(state.back_in || 0) + ". Use a runner if it can't wait.";
  } else if (state.link === "DOWN") {
    banner.className = "banner down";
    banner.textContent = "No signal on your radio. Radio messages in and out are held. The relay and runners still work.";
  } else if (state.link === "DEGRADED") {
    banner.className = "banner degraded";
    banner.textContent = "Your radio is degraded. Expect delays and lost messages.";
  } else if (state.status === "PAUSED") {
    banner.className = "banner waiting";
    banner.textContent = "The instructor has paused the exercise.";
  } else {
    banner.className = "banner hidden";
  }

  drawReads();
  drawChannels();
  drawFeed();
  updateFeedFog();
  drawMapLive();
  drawDecisions();
  drawSent();
  drawRoster();
  drawProbe();

  $("endedModal").classList.toggle("hidden", state.status !== "ENDED");
}

// ---------- the incoming feed ----------
function drawFeed() {
  const feed = state.feed;
  let key = openGrader + "|";
  for (const item of feed) {
    key += item.delivery_id + item.chips.join("") + (item.acked ? "a" : "") + item.grade + "|";
  }
  $("feedCount").textContent = feed.length === 1 ? "1 message" : feed.length + " messages";
  if (key === feedKey) return;
  feedKey = key;

  const box = $("feed");
  if (feed.length === 0) {
    box.innerHTML = '<div class="feed-empty">Nothing has reached you yet. Reports arrive here, not always in order and not always on time.</div>';
    return;
  }

  // Copies of the same report (from the source, and passed on by teammates) become one card.
  const groups = [];
  const byReport = {};
  for (const item of feed.slice().reverse()) {
    const isReport = item.kind === "report" || item.kind === "forward";
    if (isReport && byReport[item.report_id]) {
      byReport[item.report_id].copies.push(item);
      continue;
    }
    const group = { main: item, copies: [] };
    if (isReport) byReport[item.report_id] = group;
    groups.push(group);
  }
  groups.sort(function (a, b) {
    const ta = a.copies.length ? a.copies[a.copies.length - 1].delivered_at : a.main.delivered_at;
    const tb = b.copies.length ? b.copies[b.copies.length - 1].delivered_at : b.main.delivered_at;
    return tb - ta;
  });

  let html = "";
  for (const group of groups) {
    const item = group.main;
    let cls = "slip";
    if (item.chips.indexOf("Delayed") >= 0) cls += " late";
    if (item.chips.indexOf("Conflicting") >= 0) cls += " conflict";
    if (item.kind !== "report") cls += " team";
    let isFresh = !firstFeedDraw && !seenDeliveries.has(item.delivery_id);
    for (const copy of group.copies) if (!firstFeedDraw && !seenDeliveries.has(copy.delivery_id)) isFresh = true;
    if (isFresh) cls += " fresh";
    let unacked = [];
    if (item.ackable) unacked.push(item.delivery_id);
    for (const copy of group.copies) if (copy.ackable) unacked.push(copy.delivery_id);
    let anyAcked = item.acked;
    for (const copy of group.copies) if (copy.acked) anyAcked = true;

    let tags = "";
    for (const chip of item.chips) {
      const tagClass = chip === "Team message" ? "Team" : chip === "Passed on" ? "Passed" : chip;
      tags += '<span class="tag ' + tagClass + '">' + escapeHtml(chip) + '</span>';
    }

    let from = escapeHtml(item.sender);
    if (item.kind === "forward") from = escapeHtml(item.sender) + ' <span class="slip-meta">passed on by ' + escapeHtml(item.from_title) + '</span>';
    let meta = "";
    const domainNames = { LAND: "Land", AIR: "Air", CYBER: "Cyber", EW: "EW" };
    if (item.domain && item.domain !== "TEAM") meta += domainNames[item.domain] || item.domain;
    if (item.grid && item.grid !== "-") meta += (meta ? ", grid " : "Grid ") + item.grid;

    const checked = cited.has(item.report_id) ? " checked" : "";
    const width = Math.min(100, Math.max(3, (item.transit / 180) * 100));
    const canCite = item.kind === "report" || item.kind === "forward";

    html += '<article class="' + cls + '" data-rid="' + escapeHtml(item.report_id) + '" data-source="' + item.source_time + '" style="--fog:' + fogFor(item.age).toFixed(2) + '">'
      + '<div class="content">'
      + '<div class="slip-head">'
      + (canCite
        ? '<label class="slip-id" title="Tick if a decision rests on this report"><input type="checkbox" class="cite" data-rid="' + escapeHtml(item.report_id) + '"' + checked + '>' + escapeHtml(item.report_id) + '</label>'
        : '<span class="slip-id">' + escapeHtml(item.kind === "flare" ? "Flare" : "Team") + '</span>')
      + '<span class="slip-from">' + from + '</span>'
      + (meta ? '<span class="slip-meta">' + escapeHtml(meta) + '</span>' : '')
      + '<span class="slip-tags">' + tags + '</span>'
      + '</div>'
      + '<p class="slip-text">' + escapeHtml(item.text) + '</p>';
    if (item.note) html += '<p class="slip-note">Note from ' + escapeHtml(item.from_title) + ': ' + escapeHtml(item.note) + '</p>';
    if (item.conflicts_with.length > 0) html += '<p class="slip-conflict">Disagrees with ' + escapeHtml(item.conflicts_with.join(", ")) + '. Weigh them before acting.</p>';
    html += '<div class="transit"><span>Observed ' + fmt(item.source_time) + '</span>'
      + '<span class="track" aria-hidden="true"><i style="width:' + width + '%"></i></span>'
      + '<span>Reached you ' + fmt(item.delivered_at) + ', <span class="took">' + span(item.transit) + ' in transit</span></span></div>';
    if (group.copies.length > 0) {
      const parts = [];
      for (const copy of group.copies) {
        parts.push((copy.from_title || copy.sender) + " by " + (copy.via === "net" ? "radio" : copy.via) + " at " + fmt(copy.delivered_at));
      }
      html += '<p class="also"><b>Also reached you ' + group.copies.length + (group.copies.length === 1 ? " more time" : " more times") + ':</b> ' + escapeHtml(parts.join("; ")) + '</p>';
    }
    html += '<div class="slip-actions">';
    if (canCite) html += '<button class="btn quiet small forward" type="button" data-did="' + item.delivery_id + '">Pass on</button>';
    if (unacked.length > 0) html += '<button class="btn quiet small ack" type="button" data-dids="' + unacked.join(",") + '">Acknowledge</button>';
    else if (anyAcked) html += '<span class="tag ok">Acknowledged</span>';
    if (item.gradeable) {
      if (item.grade) html += '<button class="btn ghost small grade-open" type="button" data-rid="' + escapeHtml(item.report_id) + '" title="Your grade: ' + escapeHtml(GRADE_RELIABILITY[item.grade[0]] + ", " + GRADE_CREDIBILITY[item.grade[1]].toLowerCase()) + '"><span class="grade-chip">' + escapeHtml(item.grade) + '</span> Regrade</button>';
      else html += '<button class="btn ghost small grade-open" type="button" data-rid="' + escapeHtml(item.report_id) + '">Grade source</button>';
    }
    html += '</div>';
    if (openGrader === item.report_id) html += graderHtml(item.report_id);
    html += '</div><span class="age-tag">' + span(item.age) + ' old</span></article>';
  }
  box.innerHTML = html;

  for (const item of feed) seenDeliveries.add(item.delivery_id);
  firstFeedDraw = false;

  for (const boxEl of document.querySelectorAll("input.cite")) {
    boxEl.addEventListener("change", function () {
      if (boxEl.checked) cited.add(boxEl.dataset.rid); else cited.delete(boxEl.dataset.rid);
      for (const other of document.querySelectorAll('input.cite[data-rid="' + boxEl.dataset.rid + '"]')) other.checked = boxEl.checked;
      updateEvidenceLines();
    });
  }
  for (const button of document.querySelectorAll("button.forward")) {
    button.addEventListener("click", function () { openForward(button.dataset.did); });
  }
  for (const button of document.querySelectorAll("button.ack")) {
    button.addEventListener("click", function () {
      for (const did of button.dataset.dids.split(",")) {
        send({ type: "ack", delivery_id: did, channel: channel === "flare" ? "radio" : channel });
      }
    });
  }
  for (const button of document.querySelectorAll("button.grade-open")) {
    button.addEventListener("click", function () {
      openGrader = openGrader === button.dataset.rid ? null : button.dataset.rid;
      drawFeed();
    });
  }
  wireGrader();
}

// Fog only changes the look, so update it every tick without redrawing the cards.
function updateFeedFog() {
  for (const slip of document.querySelectorAll(".slip")) {
    const age = state.sim_time - parseInt(slip.dataset.source, 10);
    slip.style.setProperty("--fog", fogFor(age).toFixed(2));
    const tag = slip.querySelector(".age-tag");
    if (tag) tag.textContent = span(age) + " old";
  }
}

// ---------- grading a source (NATO A-F / 1-6) ----------
function graderHtml(reportId) {
  const draft = gradeDraft[reportId] || {};
  let html = '<div class="grader"><div class="row"><span>Source reliability</span><span class="seg">';
  for (const letter of ["A", "B", "C", "D", "E", "F"]) {
    html += '<button type="button" class="' + (draft.reliability === letter ? "on" : "") + '" data-rel="' + letter + '" title="' + GRADE_RELIABILITY[letter] + '">' + letter + '</button>';
  }
  html += '</span></div><div class="row"><span>Information</span><span class="seg">';
  for (const n of ["1", "2", "3", "4", "5", "6"]) {
    html += '<button type="button" class="' + (draft.credibility === n ? "on" : "") + '" data-cred="' + n + '" title="' + GRADE_CREDIBILITY[n] + '">' + n + '</button>';
  }
  html += '</span></div><p class="meaning">';
  if (draft.reliability) html += escapeHtml(GRADE_RELIABILITY[draft.reliability]);
  if (draft.reliability && draft.credibility) html += ", ";
  if (draft.credibility) html += escapeHtml(GRADE_CREDIBILITY[draft.credibility].toLowerCase());
  if (!draft.reliability && !draft.credibility) html += "How reliable is the source, and how believable is this report?";
  html += '</p><div class="row-end"><button class="btn signal small grade-save" type="button" data-rid="' + escapeHtml(reportId) + '">Save grade</button></div></div>';
  return html;
}

function wireGrader() {
  const grader = document.querySelector(".grader");
  if (!grader) return;
  const reportId = openGrader;
  if (!gradeDraft[reportId]) gradeDraft[reportId] = {};
  for (const button of grader.querySelectorAll("[data-rel]")) {
    button.addEventListener("click", function () { gradeDraft[reportId].reliability = button.dataset.rel; feedKey = ""; drawFeed(); });
  }
  for (const button of grader.querySelectorAll("[data-cred]")) {
    button.addEventListener("click", function () { gradeDraft[reportId].credibility = button.dataset.cred; feedKey = ""; drawFeed(); });
  }
  grader.querySelector(".grade-save").addEventListener("click", function () {
    const draft = gradeDraft[reportId];
    if (!draft.reliability || !draft.credibility) { showToast("Pick a letter and a number."); return; }
    if (send({ type: "grade", report_id: reportId, reliability: draft.reliability, credibility: draft.credibility })) {
      openGrader = null;
      showToast("Graded " + reportId + " as " + draft.reliability + draft.credibility + ".");
    }
  });
}

// ---------- decisions ----------
function drawDecisions() {
  const panel = $("decisionPanel");
  let key = "";
  for (const dp of state.open_dps) key += dp.dp_id + (dp.submitted ? "s" : "o") + "|";

  if (key !== dpKey) {
    dpKey = key;
    if (state.open_dps.length === 0) {
      panel.classList.remove("open");
      let history = "";
      if (state.my_decisions.length > 0) {
        history = '<h3 style="margin-top:0.9rem">Your decisions so far</h3><ul class="sent">';
        for (const d of state.my_decisions) {
          history += '<li><span class="what"><strong>' + escapeHtml(d.dp_id) + '</strong> at ' + fmt(d.time) + ': ' + escapeHtml(d.choice) + '</span></li>';
        }
        history += '</ul>';
      }
      panel.innerHTML = '<h2>Decision</h2><p class="muted">No decision is waiting. Keep your read up to date.</p>' + history;
    } else {
      panel.classList.add("open");
      let html = '<h2>Decision needed</h2>';
      for (const dp of state.open_dps) {
        const draft = dpDrafts[dp.dp_id] || { choice: "", confidence: "", rationale: "" };
        html += '<div class="dp-block" data-dp="' + dp.dp_id + '">';
        html += '<div class="dp-time"><strong>' + escapeHtml(dp.dp_id) + '</strong><span class="dp-countdown" id="countdown-' + dp.dp_id + '"></span></div>';
        html += '<div class="dp-bar" aria-hidden="true"><i id="bar-' + dp.dp_id + '"></i></div>';
        html += '<p class="dp-prompt">' + escapeHtml(dp.prompt) + '</p>';
        if (dp.question) {
          let q = null;
          for (const item of hello.questions) if (item.q_id === dp.question) q = item;
          const mine = state.reads[dp.question];
          if (q) html += '<p class="dp-context">Your read on ' + escapeHtml(q.short.toLowerCase()) + ': <strong>' + (mine ? mine.value + "%" : "not set") + '</strong>. Update it if it has changed.</p>';
        }
        if (dp.team) html += '<p class="dp-team">' + (dp.is_team_lead ? "Team decision. You make the final call." : "Team decision. Your recommendation goes on record.") + '</p>';
        if (dp.submitted) {
          html += '<div class="submitted">Submitted: ' + escapeHtml(dp.my_choice) + '</div>';
        } else {
          html += '<div class="options">';
          for (const option of dp.options) {
            const checked = draft.choice === option.id ? " checked" : "";
            html += '<label class="option"><input type="radio" name="choice-' + dp.dp_id + '" value="' + option.id + '"' + checked + '><span>' + escapeHtml(option.label) + '</span></label>';
          }
          html += '</div>';
          html += '<div class="confidence"><span class="small muted">How sure are you?</span><span class="seg" data-dp="' + dp.dp_id + '">';
          for (const level of ["Low", "Medium", "High"]) {
            html += '<button type="button" class="' + (draft.confidence === level ? "on" : "") + '" data-level="' + level + '">' + level + '</button>';
          }
          html += '</span></div>';
          html += '<label class="field" for="rationale-' + dp.dp_id + '">Why? Name the reports you are relying on.</label>';
          html += '<textarea id="rationale-' + dp.dp_id + '" rows="3" maxlength="1500" placeholder="e.g. R4 says the bridge is blocked; R5 conflicts and may be spoofed (R6).">' + escapeHtml(draft.rationale) + '</textarea>';
          html += '<p class="evidence-line" data-dp="' + dp.dp_id + '"></p>';
          html += '<div class="row-end"><button class="btn signal submit-dp" type="button" data-dp="' + dp.dp_id + '">Submit decision</button></div>';
        }
        html += '</div>';
      }
      panel.innerHTML = html;
      wireDecisionPanel();
      updateEvidenceLines();
    }
  }

  for (const dp of state.open_dps) {
    const el = document.getElementById("countdown-" + dp.dp_id);
    const bar = document.getElementById("bar-" + dp.dp_id);
    if (!el) continue;
    const left = dp.deadline - state.sim_time;
    const windowLength = Math.max(1, dp.deadline - dp.opened_at);
    if (left >= 0) {
      el.textContent = span(left) + " left";
      el.classList.remove("over");
    } else {
      el.textContent = "Window passed " + span(-left) + " ago";
      el.classList.add("over");
    }
    if (bar) {
      const percent = Math.max(0, Math.min(100, (left / windowLength) * 100));
      bar.style.width = percent + "%";
      bar.classList.toggle("low", left < windowLength * 0.25);
    }
  }
}

function wireDecisionPanel() {
  for (const block of document.querySelectorAll(".dp-block")) {
    const dpId = block.dataset.dp;
    if (!dpDrafts[dpId]) dpDrafts[dpId] = { choice: "", confidence: "", rationale: "" };
    for (const radio of block.querySelectorAll('input[type="radio"]')) {
      radio.addEventListener("change", function () { dpDrafts[dpId].choice = radio.value; });
    }
    const seg = block.querySelector(".seg");
    if (seg) {
      for (const button of seg.querySelectorAll("button")) {
        button.addEventListener("click", function () {
          dpDrafts[dpId].confidence = button.dataset.level;
          for (const b of seg.querySelectorAll("button")) b.className = "";
          button.className = "on";
        });
      }
    }
    const textarea = block.querySelector("textarea");
    if (textarea) textarea.addEventListener("input", function () { dpDrafts[dpId].rationale = textarea.value; });
    const submit = block.querySelector(".submit-dp");
    if (submit) {
      submit.addEventListener("click", function () {
        const draft = dpDrafts[dpId];
        if (!draft.choice) { showToast("Pick an option first."); return; }
        if (draft.rationale.trim().length < 8) { showToast("Add a short rationale. It is what the review discusses."); return; }
        const ok = send({ type: "decision", dp_id: dpId, choice: draft.choice, rationale: draft.rationale,
          cited: Array.from(cited), confidence: draft.confidence || "Not given" });
        if (ok) {
          cited.clear();
          for (const box of document.querySelectorAll("input.cite")) box.checked = false;
          updateEvidenceLines();
          showToast("Decision submitted.");
        }
      });
    }
  }
}

function updateEvidenceLines() {
  for (const line of document.querySelectorAll(".evidence-line")) {
    if (cited.size === 0) line.textContent = "No reports ticked as evidence yet. Tick them in Incoming.";
    else line.textContent = "Evidence: " + Array.from(cited).join(", ");
  }
}

// ---------- sent and team ----------
function drawSent() {
  let key = "";
  for (const s of state.sent) key += s.delivery_id + (s.ack_received_at === null ? "n" : "y") + "|";
  if (key === sentKey) return;
  sentKey = key;
  const list = $("sent");
  if (state.sent.length === 0) {
    list.innerHTML = '<li class="muted">Nothing sent yet.</li>';
    return;
  }
  let html = "";
  for (const s of state.sent.slice(0, 10)) {
    let what = s.kind === "forward" ? "Passed " + s.report_id + " to " + s.to_title : s.to_title + ': "' + s.text + '"';
    what += " (" + s.channel.toLowerCase() + ")";
    const ack = s.ack_received_at === null
      ? '<span class="ack no">Sent ' + fmt(s.sent_at) + (s.kind === "flare" ? "" : ", not acknowledged yet") + '</span>'
      : '<span class="ack yes">Acknowledged ' + fmt(s.ack_received_at) + '</span>';
    html += '<li><span class="what">' + escapeHtml(what) + '</span>' + ack + '</li>';
  }
  list.innerHTML = html;
}

function drawRoster() {
  let key = "";
  for (const t of state.teammates) key += t.role + t.name + t.online + "|";
  if (key === rosterKey) return;
  rosterKey = key;
  let html = "";
  for (const t of state.teammates) {
    const me = t.role === ROLE;
    html += '<li class="' + (me ? "me" : "") + '"><span>' + escapeHtml(t.title) + (me ? " (you)" : "") + '</span>'
      + '<span class="' + (t.online ? "" : "off") + '">' + (t.online ? escapeHtml(t.name) : "Not connected") + '</span></li>';
  }
  $("roster").innerHTML = html;
}

// ---------- pass on a report ----------
function openForward(deliveryId) {
  let item = null;
  for (const f of state.feed) if (f.delivery_id === deliveryId) item = f;
  if (!item) return;
  forwardDelivery = deliveryId;
  $("forwardWhat").textContent = item.report_id + ": " + item.text;
  let html = "";
  for (const r of hello.roles) {
    if (r.role === ROLE) continue;
    html += '<label class="check"><input type="checkbox" class="fwd-target" value="' + r.role + '"> ' + escapeHtml(r.title) + '</label>';
  }
  $("forwardTargets").innerHTML = html;
  $("forwardNote").value = "";
  const useChannel = channel === "flare" ? "radio" : channel;
  $("forwardChannel").textContent = "Goes by " + useChannel + ". Change the channel under Reach the team.";
  $("forwardModal").classList.remove("hidden");
  const first = document.querySelector(".fwd-target");
  if (first) first.focus();
}

function closeForward() {
  $("forwardModal").classList.add("hidden");
  forwardDelivery = null;
}

$("forwardCancel").addEventListener("click", closeForward);
$("forwardModal").addEventListener("keydown", function (event) { if (event.key === "Escape") closeForward(); });
$("forwardSend").addEventListener("click", function () {
  const targets = [];
  for (const box of document.querySelectorAll(".fwd-target")) if (box.checked) targets.push(box.value);
  if (targets.length === 0) { showToast("Pick at least one teammate."); return; }
  const useChannel = channel === "flare" ? "radio" : channel;
  const ok = send({ type: "forward", delivery_id: forwardDelivery, to_roles: targets, note: $("forwardNote").value, channel: useChannel });
  if (ok) {
    closeForward();
    showToast("Passed on by " + useChannel + ".");
  }
});

// ---------- freeze probe ----------
function drawProbe() {
  const modal = $("probeModal");
  const p = state.probe;
  const key = p ? p.probe_id + (p.answered ? "a" : "q") : "";
  if (key === probeKey) return;
  probeKey = key;
  if (!p) {
    modal.classList.add("hidden");
    return;
  }
  modal.classList.remove("hidden");
  const form = $("probeForm");
  if (p.answered) {
    form.innerHTML = '<div class="submitted">Answers recorded. The exercise continues when everyone has answered.</div>';
    return;
  }
  let html = "";
  for (const q of p.questions) {
    html += '<div class="probe-q"><p>' + escapeHtml(q.text) + '</p>';
    for (const option of q.options) {
      html += '<label><input type="radio" name="' + q.q_id + '" value="' + escapeHtml(option) + '"> ' + escapeHtml(option) + '</label>';
    }
    html += '</div>';
  }
  html += '<div class="modal-actions"><button class="btn signal" type="submit">Submit answers</button></div>';
  form.innerHTML = html;
  form.onsubmit = function (event) {
    event.preventDefault();
    const answers = {};
    let missing = 0;
    for (const q of p.questions) {
      const picked = form.querySelector('input[name="' + q.q_id + '"]:checked');
      if (picked) answers[q.q_id] = picked.value; else missing++;
    }
    if (missing > 0) { showToast("Answer every question. 'I don't know' is a fine answer."); return; }
    send({ type: "probe_answer", probe_id: p.probe_id, answers: answers });
  };
}

connect();
