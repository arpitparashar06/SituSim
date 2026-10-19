// SituSim network picture
// Draws report sources on the left and the four trainees on the right, with
// every message in flight moving along its link. Used by the home page demo
// and the instructor console.
//
//   const graph = createNetGraph(svgElement, { onNodeClick: function (kind, id) { ... } });
//   graph.update(model);   // call whenever new state arrives
//
// model = {
//   speed: 1,                      // simulation speed, for smooth movement
//   sources: [{ id, label, state: "ONLINE"|"OFFLINE", until }],
//   roles:   [{ id, label, sub, link: "UP"|"DEGRADED"|"DOWN", until, exposure, threshold, located, online }],
//   packets: [{ id, from, from_type: "source"|"role", to, via, status: "PENDING"|"HELD", progress, eta }],
//   arrivals: [{ id, to }],
//   selected: "role:GTL" or "source:ISR-UAV" or null
// }

function createNetGraph(svg, options) {
  const W = 800;
  const H = 440;
  const ROLE_POS = {
    GTL: { x: 610, y: 78 },
    ISR: { x: 728, y: 222 },
    EW: { x: 610, y: 366 },
    COMMS: { x: 492, y: 222 },
  };
  const NODE_R = 30;

  svg.setAttribute("viewBox", "0 0 " + W + " " + H);
  svg.innerHTML =
    '<defs>' +
    '  <filter id="ng-wobble" x="-5%" y="-5%" width="110%" height="110%">' +
    '    <feTurbulence baseFrequency="0.02" numOctaves="2" seed="7"/><feDisplacementMap in="SourceGraphic" scale="4"/>' +
    '  </filter>' +
    '</defs>' +
    '<g class="ng-layers" aria-hidden="true">' +
    '  <text x="34" y="16">SOURCES</text><text x="' + (W - 10) + '" y="16" text-anchor="end">TRAINEES</text>' +
    '</g>' +
    '<g class="ng-edges" filter="url(#ng-wobble)"></g><g class="ng-packets"></g><g class="ng-nodes"></g><g class="ng-fx"></g>';
  const edgeLayer = svg.querySelector(".ng-edges");
  const packetLayer = svg.querySelector(".ng-packets");
  const nodeLayer = svg.querySelector(".ng-nodes");
  const fxLayer = svg.querySelector(".ng-fx");

  let model = null;
  let receivedAt = 0;
  let structureKey = "";
  let sourcePos = {};
  let seenArrivals = {};

  function esc(text) {
    return String(text === null || text === undefined ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function placeSources(sources) {
    sourcePos = {};
    const top = 46;
    const bottom = H - 46;
    for (let i = 0; i < sources.length; i++) {
      const y = sources.length === 1 ? H / 2 : top + (bottom - top) * i / (sources.length - 1);
      sourcePos[sources[i].id] = { x: 112, y: y };
    }
  }

  function posOf(kind, id) {
    if (kind === "role") return ROLE_POS[id];
    return sourcePos[id] || { x: 112, y: H / 2 };
  }

  // The curve between two points; sources fan out to the trainees.
  function pathBetween(a, b, kind) {
    if (kind === "source") {
      const midX = (a.x + b.x) / 2;
      return "M" + a.x + " " + a.y + " C " + midX + " " + a.y + ", " + (midX + 40) + " " + b.y + ", " + b.x + " " + b.y;
    }
    return "M" + a.x + " " + a.y + " L " + b.x + " " + b.y;
  }

  // A point part-way along a path, for drawing a moving message.
  function pointOn(a, b, kind, t) {
    if (kind === "source") {
      const midX = (a.x + b.x) / 2;
      const p0 = a;
      const p1 = { x: midX, y: a.y };
      const p2 = { x: midX + 40, y: b.y };
      const p3 = b;
      const u = 1 - t;
      return {
        x: u * u * u * p0.x + 3 * u * u * t * p1.x + 3 * u * t * t * p2.x + t * t * t * p3.x,
        y: u * u * u * p0.y + 3 * u * u * t * p1.y + 3 * u * t * t * p2.y + t * t * t * p3.y,
      };
    }
    return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
  }

  // Stop short of the node's edge so the dot doesn't sit inside the circle.
  function trimmed(a, b) {
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const length = Math.sqrt(dx * dx + dy * dy) || 1;
    return { x: b.x - dx / length * (NODE_R + 6), y: b.y - dy / length * (NODE_R + 6) };
  }

  function linkOf(id) {
    for (const r of model.roles) if (r.id === id) return r.link;
    return "UP";
  }

  function worst(a, b) {
    const order = { UP: 0, DEGRADED: 1, DOWN: 2 };
    return order[a] >= order[b] ? a : b;
  }

  function drawStatic() {
    // Edges: each source to the trainees it feeds, and the team's radio mesh.
    let html = "";
    for (const s of model.sources) {
      const a = posOf("source", s.id);
      for (const to of (s.feeds || [])) {
        const b = ROLE_POS[to];
        if (!b) continue;
        const state = s.state === "OFFLINE" ? "OFF" : linkOf(to);
        html += '<path class="ng-edge src ' + state + '" d="' + pathBetween(a, b, "source") + '"/>';
      }
    }
    const ids = ["GTL", "ISR", "EW", "COMMS"];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) {
        const state = worst(linkOf(ids[i]), linkOf(ids[j]));
        html += '<path class="ng-edge team ' + state + '" d="' + pathBetween(ROLE_POS[ids[i]], ROLE_POS[ids[j]], "role") + '"/>';
      }
    }
    edgeLayer.innerHTML = html;

    // Nodes
    html = "";
    for (const s of model.sources) {
      const p = posOf("source", s.id);
      const selected = model.selected === "source:" + s.id ? " selected" : "";
      html += '<g class="ng-node source ' + s.state + selected + '" data-kind="source" data-id="' + esc(s.id) + '" tabindex="0" role="button" aria-label="' + esc(s.label) + ', ' + s.state.toLowerCase() + '">'
        + '<rect x="' + (p.x - 78) + '" y="' + (p.y - 17) + '" width="156" height="34" rx="17"/>'
        + '<circle class="lamp" cx="' + (p.x - 60) + '" cy="' + p.y + '" r="5"/>'
        + '<text x="' + (p.x - 48) + '" y="' + (p.y + 5) + '">' + esc(s.label) + '</text>';
      if (s.until !== null && s.until !== undefined) {
        html += '<text class="until" x="' + (p.x + 66) + '" y="' + (p.y + 5) + '" text-anchor="end">' + Math.max(0, Math.round(s.until)) + 's</text>';
      }
      html += '</g>';
    }
    for (const r of model.roles) {
      const p = ROLE_POS[r.id];
      if (!p) continue;
      const selected = model.selected === "role:" + r.id ? " selected" : "";
      const circumference = 2 * Math.PI * (NODE_R + 7);
      const threshold = r.threshold || 90;
      const fill = Math.max(0, Math.min(1, (r.exposure || 0) / threshold));
      const hot = fill >= 0.75 ? " hot" : "";
      html += '<g class="ng-node role ' + r.link + (r.located ? " located" : "") + (r.online === false ? " offline" : "") + selected + '" data-kind="role" data-id="' + r.id + '" tabindex="0" role="button" aria-label="' + esc(r.label) + ', link ' + r.link.toLowerCase() + '">'
        + '<circle class="halo" cx="' + p.x + '" cy="' + p.y + '" r="' + (NODE_R + 14) + '"/>'
        + '<circle class="exposure-track" cx="' + p.x + '" cy="' + p.y + '" r="' + (NODE_R + 7) + '"/>'
        + (fill > 0.01 ? '<circle class="exposure' + hot + '" cx="' + p.x + '" cy="' + p.y + '" r="' + (NODE_R + 7) + '" stroke-dasharray="' + (circumference * fill).toFixed(1) + ' ' + circumference.toFixed(1) + '" transform="rotate(-90 ' + p.x + ' ' + p.y + ')"/>' : '')
        + '<g class="core"><circle class="body" cx="' + p.x + '" cy="' + p.y + '" r="' + NODE_R + '"/>'
        + '<text class="abbr" x="' + p.x + '" y="' + (p.y + 5) + '" text-anchor="middle">' + esc(r.abbr || r.id) + '</text></g>'
        + '<text class="label" x="' + p.x + '" y="' + (p.y + NODE_R + 26) + '" text-anchor="middle">' + esc(r.label) + '</text>';
      if (r.sub) {
        html += '<text class="sub" x="' + p.x + '" y="' + (p.y + NODE_R + 42) + '" text-anchor="middle">' + esc(r.sub) + '</text>';
      }
      if (r.until !== null && r.until !== undefined && r.link !== "UP") {
        html += '<g class="countdown"><rect x="' + (p.x + 16) + '" y="' + (p.y - NODE_R - 16) + '" width="46" height="22" rx="11"/>'
          + '<text x="' + (p.x + 39) + '" y="' + (p.y - NODE_R - 1) + '" text-anchor="middle">' + Math.max(0, Math.round(r.until)) + 's</text></g>';
      }
      if (r.located) {
        html += '<g class="badge"><rect x="' + (p.x - 44) + '" y="' + (p.y - NODE_R - 17) + '" width="56" height="22" rx="11"/>'
          + '<text x="' + (p.x - 16) + '" y="' + (p.y - NODE_R - 2) + '" text-anchor="middle">Located</text></g>';
      }
      html += '</g>';
    }
    nodeLayer.innerHTML = html;

    for (const node of nodeLayer.querySelectorAll(".ng-node")) {
      node.addEventListener("click", function () {
        if (options && options.onNodeClick) options.onNodeClick(node.dataset.kind, node.dataset.id, node);
      });
      node.addEventListener("keydown", function (event) {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          if (options && options.onNodeClick) options.onNodeClick(node.dataset.kind, node.dataset.id, node);
        }
      });
    }
  }

  function drawPackets() {
    if (!model) return;
    const elapsed = (performance.now() - receivedAt) / 1000 * (model.speed || 1);
    let html = "";
    const heldCount = {};
    for (const p of model.packets) {
      const fromPos = posOf(p.from_type, p.from);
      const toPos = ROLE_POS[p.to];
      if (!fromPos || !toPos) continue;
      if (p.status === "HELD") {
        // Held messages queue up just outside the node they can't reach.
        const n = heldCount[p.to] || 0;
        heldCount[p.to] = n + 1;
        const angle = Math.PI * (0.75 + 0.18 * n);
        const x = toPos.x + Math.cos(angle) * (NODE_R + 20 + 9 * Math.floor(n / 6));
        const y = toPos.y + Math.sin(angle) * (NODE_R + 20 + 9 * Math.floor(n / 6));
        html += '<circle class="ng-packet held" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="5"/>';
        continue;
      }
      let progress = p.progress;
      if (p.eta > 0 && p.progress < 1) {
        const span = p.eta / (1 - p.progress);
        progress = p.progress + elapsed / span;
      }
      if (progress > 0.98) progress = 0.98;
      if (progress < 0) progress = 0;
      let a = fromPos;
      let b = trimmed(fromPos, toPos);
      let kind = p.from_type === "source" ? "source" : "role";
      if (p.via === "relay" && p.from !== "COMMS" && p.to !== "COMMS") {
        // Relayed traffic travels sender -> Comms -> receiver.
        if (progress < 0.5) {
          b = ROLE_POS.COMMS;
          progress = progress * 2;
        } else {
          a = ROLE_POS.COMMS;
          progress = (progress - 0.5) * 2;
        }
      }
      if (kind === "source") b = toPos;
      const pt = pointOn(a, kind === "source" ? b : b, kind, progress);
      const cls = "ng-packet " + (p.via || "net") + (p.kind === "report" || p.kind === "forward" ? " report" : " team");
      if (p.via === "runner") {
        html += '<rect class="' + cls + '" x="' + (pt.x - 4.5).toFixed(1) + '" y="' + (pt.y - 4.5).toFixed(1) + '" width="9" height="9" rx="2"/>';
      } else {
        html += '<circle class="' + cls + '" cx="' + pt.x.toFixed(1) + '" cy="' + pt.y.toFixed(1) + '" r="' + (p.via === "flare" ? 7 : 5) + '"/>';
      }
    }
    packetLayer.innerHTML = html;
  }

  function flash(arrivals) {
    for (const a of arrivals || []) {
      if (seenArrivals[a.id]) continue;
      seenArrivals[a.id] = true;
      const p = ROLE_POS[a.to];
      if (!p) continue;
      const ring = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      ring.setAttribute("cx", p.x);
      ring.setAttribute("cy", p.y);
      ring.setAttribute("r", NODE_R);
      ring.setAttribute("class", "ng-arrive");
      fxLayer.appendChild(ring);
      setTimeout(function () { ring.remove(); }, 900);
    }
  }

  function frame() {
    drawPackets();
    requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);

  return {
    update: function (next) {
      const firstTime = model === null;
      model = next;
      receivedAt = performance.now();
      const key = JSON.stringify([next.sources, next.roles, next.selected]);
      if (key !== structureKey) {
        structureKey = key;
        placeSources(next.sources);
        drawStatic();
      }
      if (!firstTime) flash(next.arrivals);
      else for (const a of next.arrivals || []) seenArrivals[a.id] = true;
      drawPackets();
    },
    positionOf: function (kind, id) {
      const p = posOf(kind, id);
      return { x: p.x / W, y: p.y / H };
    },
  };
}
