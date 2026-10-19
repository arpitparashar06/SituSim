// SituSim home page: a boot screen, a small playable network, the scenario log,
// the "how it works" folders, and joining the exercise.

function $(id) { return document.getElementById(id); }

function escapeHtml(text) {
  return String(text === null || text === undefined ? "" : text)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function storageGet(key, where) {
  try { return (where || localStorage).getItem(key) || ""; } catch (e) { return ""; }
}
function storageSet(key, value, where) {
  try { (where || localStorage).setItem(key, value); } catch (e) { }
}

let toastTimer = null;
function showToast(text) {
  const toast = $("toast");
  toast.textContent = text;
  toast.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(function () { toast.classList.remove("show"); }, 2600);
}

const reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// ============================================================
// Boot screen (once per visit), then the hero comes in
// ============================================================

const BOOT_LINES = [
  "> loading scenario: grey lantern",
  "> seeding the random delays",
  "> jamming one radio, for realism",
  "> opening the exercise",
];

function scramble(element, text, duration) {
  const chars = "01▒░█<>/";
  const start = performance.now();
  function step(now) {
    const p = Math.min(1, (now - start) / duration);
    const shown = Math.floor(p * text.length);
    let out = text.slice(0, shown);
    for (let i = shown; i < text.length; i++) {
      out += text[i] === " " ? " " : chars[Math.floor(Math.random() * chars.length)];
    }
    element.textContent = out;
    if (p < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function showHero() {
  document.body.classList.add("ready");
  // The highlighter and the red-pen strike go on once the lines are in.
  setTimeout(function () { $("hero").classList.add("is-visible"); }, 700);
}

function runBoot() {
  const boot = $("boot");
  const alreadyBooted = storageGet("situsim-booted", sessionStorage) === "1";
  if (alreadyBooted || reduceMotion) {
    showHero();
    return;
  }
  boot.classList.remove("hidden");
  document.body.style.overflow = "hidden";
  const lines = boot.querySelectorAll(".boot-line");
  const timers = [];
  for (let i = 0; i < BOOT_LINES.length; i++) {
    timers.push(setTimeout(function () { scramble(lines[i], BOOT_LINES[i], 280); }, i * 220));
  }
  timers.push(setTimeout(function () { $("bootBar").style.width = "100%"; }, 300));
  let pct = 0;
  const counter = setInterval(function () {
    pct = Math.min(100, pct + 4);
    $("bootPct").textContent = pct;
    if (pct >= 100) clearInterval(counter);
  }, 36);

  let finished = false;
  function finish() {
    if (finished) return;
    finished = true;
    for (const t of timers) clearTimeout(t);
    clearInterval(counter);
    storageSet("situsim-booted", "1", sessionStorage);
    boot.classList.add("leaving");
    document.body.style.overflow = "";
    setTimeout(showHero, 250);
    setTimeout(function () { boot.classList.add("hidden"); }, 850);
    window.removeEventListener("keydown", onKey);
  }
  function onKey(event) {
    if (event.key === "Escape" || event.key === "Tab") finish();
  }
  window.addEventListener("keydown", onKey);
  timers.push(setTimeout(finish, 1500));
}
runBoot();

// The blue grid follows the pointer like a torch.
const hero = $("hero");
hero.addEventListener("pointermove", function (event) {
  if (reduceMotion) return;
  const box = hero.getBoundingClientRect();
  hero.style.setProperty("--mx", (event.clientX - box.left) + "px");
  hero.style.setProperty("--my", (event.clientY - box.top) + "px");
});

// Stickers can be dragged anywhere and stay where they are dropped.
for (const sticker of document.querySelectorAll(".fling")) {
  let startX = 0;
  let startY = 0;
  let baseX = 0;
  let baseY = 0;
  sticker.addEventListener("pointerdown", function (event) {
    sticker.setPointerCapture(event.pointerId);
    sticker.classList.add("dragging");
    startX = event.clientX;
    startY = event.clientY;
    baseX = parseFloat(sticker.style.getPropertyValue("--dx")) || 0;
    baseY = parseFloat(sticker.style.getPropertyValue("--dy")) || 0;
  });
  sticker.addEventListener("pointermove", function (event) {
    if (!sticker.classList.contains("dragging")) return;
    sticker.style.setProperty("--dx", (baseX + event.clientX - startX) + "px");
    sticker.style.setProperty("--dy", (baseY + event.clientY - startY) + "px");
  });
  function drop() { sticker.classList.remove("dragging"); }
  sticker.addEventListener("pointerup", drop);
  sticker.addEventListener("pointercancel", drop);
}

// ============================================================
// The playable demo: two sources, four trainees, all in the browser
// ============================================================

const DEMO_ROLES = [
  { id: "GTL", abbr: "GL", label: "Ground lead" },
  { id: "ISR", abbr: "AIR", label: "Air / ISR" },
  { id: "EW", abbr: "EW", label: "Cyber / EW" },
  { id: "COMMS", abbr: "COM", label: "Comms" },
];
const DEMO_SOURCES = [
  { id: "UAV", label: "UAV feed", feeds: ["GTL", "ISR", "EW", "COMMS"] },
  { id: "PATROL", label: "Ground patrol", feeds: ["GTL", "ISR", "EW", "COMMS"] },
];

const demo = {
  time: 0,                 // seconds since the demo started
  links: { GTL: "UP", ISR: "UP", EW: "UP", COMMS: "UP" },
  packets: [],
  arrivals: [],
  newest: { GTL: null, ISR: null, EW: null, COMMS: null },   // observed time of the freshest report received
  lost: { GTL: 0, ISR: 0, EW: 0, COMMS: 0 },
  counter: 0,
  nextReport: 0.6,
  sendNow: false,
  fog: 35,
};

const graph = createNetGraph($("demoGraph"), {
  onNodeClick: function (kind, id) {
    if (kind !== "role") return;
    // Clicking a trainee cuts their radio, clicking again brings it back.
    if (demo.links[id] === "UP") demo.links[id] = "DOWN";
    else demo.links[id] = "UP";
    let cut = 0;
    for (const r of DEMO_ROLES) if (demo.links[r.id] === "DOWN") cut++;
    $("demoHelp").textContent = cut === 0
      ? "Click a trainee to cut their radio. Drag the fog and watch their pictures drift apart."
      : cut + " radio" + (cut === 1 ? " is" : "s are") + " cut. Reports pile up at the break until you click again.";
  },
});

$("fog").addEventListener("input", function () {
  demo.fog = parseInt($("fog").value, 10);
  $("fogOut").textContent = demo.fog + "%";
});

$("sendNow").addEventListener("click", function () { demo.sendNow = true; });

function demoTick(dt) {
  demo.time = demo.time + dt;

  // A source observes something and sends it to every trainee.
  if (demo.time >= demo.nextReport || demo.sendNow) {
    demo.sendNow = false;
    demo.nextReport = demo.time + 1.3 + Math.random() * 0.8;
    const source = DEMO_SOURCES[demo.counter % DEMO_SOURCES.length];
    demo.counter = demo.counter + 1;
    for (const to of source.feeds) {
      // More fog = longer and more uneven delays, and more messages lost.
      const delay = 0.9 + Math.random() * (0.5 + demo.fog / 18);
      const lost = Math.random() < demo.fog / 260;
      demo.packets.push({
        id: "P" + demo.counter + to,
        from: source.id, from_type: "source", to: to, via: "net",
        status: "PENDING", started: demo.time, arrive: demo.time + delay,
        lostAt: lost ? 0.35 + Math.random() * 0.4 : null,
        observed: demo.time,
      });
    }
  }

  // Move every message along.
  const keep = [];
  demo.arrivals = [];
  for (const p of demo.packets) {
    const span = p.arrive - p.started;
    p.progress = Math.min(1, (demo.time - p.started) / span);
    if (p.lostAt !== null && p.progress >= p.lostAt) {
      demo.lost[p.to] = demo.lost[p.to] + 1;
      continue;     // lost in the fog
    }
    if (p.progress >= 1) {
      if (demo.links[p.to] === "DOWN") {
        p.status = "HELD";    // waits at the cut link
        keep.push(p);
        continue;
      }
      demo.arrivals.push({ id: p.id, to: p.to });
      if (demo.newest[p.to] === null || p.observed > demo.newest[p.to]) demo.newest[p.to] = p.observed;
      continue;
    }
    keep.push(p);
  }
  // Held messages flood in when the link comes back.
  for (const p of keep) {
    if (p.status === "HELD" && demo.links[p.to] !== "DOWN") {
      p.status = "PENDING";
      p.started = demo.time;
      p.arrive = demo.time + 0.4;
      p.lostAt = null;
    }
  }
  demo.packets = keep;
}

function demoModel() {
  const packets = [];
  for (const p of demo.packets) {
    packets.push({ id: p.id, from: p.from, from_type: "source", to: p.to, via: "net", kind: "report",
      status: p.status, progress: p.progress || 0, eta: Math.max(0, p.arrive - demo.time) });
  }
  const roles = [];
  for (const r of DEMO_ROLES) {
    roles.push({ id: r.id, abbr: r.abbr, label: r.label, link: demo.links[r.id], exposure: 0, threshold: 90, online: true });
  }
  return { speed: 1, sources: DEMO_SOURCES.map(function (s) { return { id: s.id, label: s.label, state: "ONLINE", feeds: s.feeds }; }),
    roles: roles, packets: packets, arrivals: demo.arrivals, selected: null };
}

function drawPictures() {
  let html = "";
  for (const r of DEMO_ROLES) {
    const newest = demo.newest[r.id];
    const age = newest === null ? null : Math.max(0, demo.time - newest);
    let cls = "pic";
    if (demo.links[r.id] === "DOWN") cls += " cut";
    else if (age !== null && age > 6) cls += " stale";
    let held = 0;
    for (const p of demo.packets) if (p.to === r.id && p.status === "HELD") held++;
    const ageText = age === null ? "nothing yet" : age.toFixed(0) + " s old";
    let what = "freshest report";
    if (demo.links[r.id] === "DOWN") what = held + " waiting at the break";
    else if (demo.lost[r.id] > 0) what = demo.lost[r.id] + " lost in the fog";
    html += '<div class="' + cls + '"><div class="who">' + escapeHtml(r.label) + '</div><div class="age">' + ageText + '</div><div class="what">' + escapeHtml(what) + '</div></div>';
  }
  $("pictures").innerHTML = html;
}

let lastFrame = performance.now();
let lastPictures = 0;
function demoLoop(now) {
  const dt = Math.min(0.1, (now - lastFrame) / 1000);
  lastFrame = now;
  demoTick(reduceMotion ? dt * 0.5 : dt);
  graph.update(demoModel());
  if (now - lastPictures > 250) {
    lastPictures = now;
    drawPictures();
  }
  requestAnimationFrame(demoLoop);
}
requestAnimationFrame(demoLoop);

// ============================================================
// How it works: three folders that take turns
// ============================================================

const tabs = document.querySelectorAll(".folder-tab");
const folderBody = $("folderBody");
let activeFolder = 0;
let cycleTimer = null;
let userPicked = false;
let foldersInView = false;

function openFolder(i, animate) {
  activeFolder = i;
  for (const tab of tabs) {
    const on = parseInt(tab.dataset.i, 10) === i;
    tab.classList.toggle("on", on);
    tab.classList.remove("cycling");
    tab.setAttribute("aria-selected", on ? "true" : "false");
  }
  for (const panel of folderBody.querySelectorAll(".fb")) {
    panel.classList.toggle("hidden", parseInt(panel.dataset.i, 10) !== i);
  }
  folderBody.style.setProperty("--tone", tabs[i].style.getPropertyValue("--tone"));
  folderBody.style.setProperty("--ox", ((i + 0.5) * 100 / 3).toFixed(1) + "%");
  if (animate && !reduceMotion) {
    folderBody.classList.remove("opening");
    void folderBody.offsetWidth;     // restart the animation
    folderBody.classList.add("opening");
  }
  scheduleCycle();
}

function scheduleCycle() {
  clearTimeout(cycleTimer);
  if (userPicked || !foldersInView || reduceMotion) return;
  const tab = tabs[activeFolder];
  void tab.offsetWidth;
  tab.classList.add("cycling");
  cycleTimer = setTimeout(function () { openFolder((activeFolder + 1) % tabs.length, true); }, 6000);
}

for (const tab of tabs) {
  tab.addEventListener("click", function () {
    userPicked = true;
    openFolder(parseInt(tab.dataset.i, 10), true);
  });
}

if ("IntersectionObserver" in window) {
  const watcher = new IntersectionObserver(function (entries) {
    for (const entry of entries) {
      const wasInView = foldersInView;
      foldersInView = entry.isIntersecting;
      if (foldersInView && !wasInView) openFolder(activeFolder, true);
      if (!foldersInView) {
        clearTimeout(cycleTimer);
        for (const tab of tabs) tab.classList.remove("cycling");
      }
    }
  }, { threshold: 0.35 });
  watcher.observe($("folders"));
}

// ============================================================
// Joining the exercise, the scenario log and past reviews
// ============================================================

const ABBR = { GTL: "GL", ISR: "AIR", EW: "EW", COMMS: "COM" };
const ROLE_TONES = { GTL: "#ff5a1f", ISR: "#9bd7ff", EW: "#ff8fb1", COMMS: "#3ae88a" };
const ROLE_TILT = { GTL: -1.4, ISR: 1.2, EW: 1, COMMS: -1.2 };
const nameInput = $("name");
nameInput.value = storageGet("situsim-name");

function join(role) {
  const name = nameInput.value.trim();
  if (name === "") {
    showToast("Enter your name first, then pick a badge.");
    const field = nameInput.closest(".name-field");
    field.classList.remove("shake");
    void field.offsetWidth;
    field.classList.add("shake");
    nameInput.focus();
    return;
  }
  storageSet("situsim-name", name);
  location.href = "/trainee?role=" + role + "&name=" + encodeURIComponent(name);
}

$("joinCta").addEventListener("click", function () {
  setTimeout(function () { nameInput.focus({ preventScroll: true }); }, 500);
});

function drawLog(script) {
  let html = "";
  for (let copy = 0; copy < 2; copy++) {
    for (const line of script) {
      html += '<span><span class="t">' + escapeHtml(line.t) + '</span><span>' + escapeHtml(line.text) + '</span></span>';
    }
  }
  $("logTrack").innerHTML = html;
  $("logCount").textContent = script.length + " scripted events";
}

function drawRoles(roles) {
  const holder = $("roleCards");
  holder.innerHTML = "";
  for (let i = 0; i < roles.length; i++) {
    const r = roles[i];
    const wrap = document.createElement("div");
    wrap.className = "badge-wrap";
    wrap.style.setProperty("--r", (ROLE_TILT[r.role] || 0) + "deg");
    const number = String(i + 1).padStart(2, "0");
    let html = '<button class="badge" type="button" style="--tone: ' + (ROLE_TONES[r.role] || "#ffe85c") + '">'
      + '<span class="badge-hole" aria-hidden="true"></span>'
      + '<span class="badge-id">role_' + number + ' · ' + ABBR[r.role].toLowerCase() + '</span>'
      + '<span class="badge-num" aria-hidden="true">' + number + '</span>'
      + '<span class="badge-title">' + escapeHtml(r.title) + '</span>'
      + '<span class="badge-brief">' + escapeHtml(r.brief) + '</span>';
    if (r.online) html += '<span class="badge-taken">' + escapeHtml(r.name) + ' is playing this role</span>';
    html += '<span class="badge-foot"><span class="badge-path">roles/' + r.role.toLowerCase() + '/</span>'
      + (r.online
        ? '<span class="stamp" style="--r: -6deg">taken</span>'
        : '<span class="stamp" style="--r: -6deg; --stamp: #0d7a43">open</span>')
      + '</span></button>';
    wrap.innerHTML = html;
    wrap.querySelector(".badge").addEventListener("click", function () { join(r.role); });
    holder.appendChild(wrap);
  }
}

function drawSessions(info) {
  const list = $("sessions");
  list.innerHTML = "";
  if (info.sessions.length === 0) {
    list.innerHTML = '<li class="empty">No exercises run yet. Start one from the instructor console and its review appears here when it ends.</li>';
    return;
  }
  const tones = { ENDED: "#3ae88a", RUNNING: "#ffe85c", PAUSED: "#ffb86b", FROZEN: "#9bd7ff" };
  for (const s of info.sessions) {
    const li = document.createElement("li");
    li.style.setProperty("--tone", tones[s.status] || "#fffdf7");
    li.innerHTML = '<a class="commit" href="/aar/' + encodeURIComponent(s.session_id) + '">'
      + '<span class="commit-top"><span class="commit-hash">' + escapeHtml(s.session_id) + '</span>'
      + '<span class="commit-title">' + escapeHtml(s.scenario) + '</span>'
      + '<span class="tag ' + (s.status === "ENDED" ? "ok" : "late") + '">' + escapeHtml(s.status.toLowerCase()) + '</span></span>'
      + '<span class="commit-meta">' + escapeHtml(s.created) + ' · ' + escapeHtml(s.length.replace("T+", "")) + ' long · '
      + s.decisions + ' decision' + (s.decisions === 1 ? "" : "s") + ' · ' + s.events + ' events</span>'
      + '<span class="commit-go">open review</span></a>';
    list.appendChild(li);
  }
  const first = document.createElement("li");
  first.className = "initial";
  first.textContent = "every review starts as an empty log";
  list.appendChild(first);
}

function drawLatest(info) {
  if (info.sessions.length === 0) return;
  const s = info.sessions[0];
  $("latestCard").href = "/aar/" + encodeURIComponent(s.session_id);
  $("latestTitle").textContent = s.scenario;
  $("latestMeta").innerHTML = '<b>' + escapeHtml(s.session_id) + '</b> · ' + escapeHtml(s.created) + ' · '
    + s.decisions + ' decision' + (s.decisions === 1 ? "" : "s");
}

async function load() {
  try {
    const response = await fetch("/api/info");
    const info = await response.json();
    $("disclaimer").textContent = info.disclaimer;
    $("exerciseLine").textContent = "This exercise: " + info.scenario + ", " + info.area + ".";
    $("scenarioSlug").textContent = info.scenario.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    $("joinUrl").textContent = info.join_url;
    drawLog(info.script || []);
    drawRoles(info.roles);
    drawSessions(info);
    drawLatest(info);
  } catch (error) {
    showToast("Could not reach the SituSim server. Is it running?");
  }
}

load();
