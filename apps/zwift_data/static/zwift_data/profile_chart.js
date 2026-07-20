"use strict";

// Shared elevation-profile chart used by /routes (elevation + hover) and /simulate
// (elevation + speed overlay). profileSVG(profile, route, width, sim?, sim2?, segments?)
// returns { svg, meta }; wireHover(body, meta) adds the crosshair + distance/elev/grade
// [/speed/segment] tooltip. `sim`/`sim2` (route_sim / optimal-pacing traces) draw speed
// lines on a right km/h axis. `segments` (route_segments rows) shade sprint/KOM/climb
// zones on the profile; wireSegToggle() shows/hides them.

function gradeColor(g) {
  if (g < -1) return "#3f7fbf";
  if (g < 3) return "#6cc551";
  if (g < 6) return "#f2c744";
  if (g < 9) return "#ef9f27";
  if (g < 12) return "#e2622f";
  return "#d03b3b";
}

// segment-band colours (match the .sbadge palette on the segments page)
const SEG_COLOR = {
  sprint: "#3fb950",
  kom: "#d0424f",
  climb: "#ef9f27",
  lap: "#4aa8ff",
  segment: "#9aa7b3",
};
const segDisplayName = (s) => s.name || `Segment ${s.id}`;

function niceStep(target, steps) {
  for (const s of steps) if (target <= s) return s;
  return steps[steps.length - 1];
}

function profileSVG(p, r, width, sim, sim2, segments) {
  const d = p.d;
  const e = p.e;
  const L = d[d.length - 1];
  const n = d.length;
  // viewBox in CSS pixels (1 unit = 1 px) so axis text stays crisp at any container width
  const W = Math.max(320, Math.round(width) || 700);
  const H = Math.round(Math.min(260, Math.max(150, W / 3.4)));
  const ml = 46;
  const mr = sim || sim2 ? 34 : 12; // room for the speed (km/h) right axis when overlaid
  const mt = 12;
  const mb = 26;
  const px0 = ml;
  const px1 = W - mr;
  const py0 = mt;
  const py1 = H - mb;
  const emin = Math.min(...e);
  const emax = Math.max(...e);
  // Show at least ~30 m of vertical range so genuinely flat routes read flat instead of
  // zooming in on a few metres of rollers; centre the data within the window.
  const span = Math.max((emax - emin) * 1.16, 30);
  const mid = (emax + emin) / 2;
  const lo = mid - span / 2;
  const hi = mid + span / 2;
  const X = (m) => px0 + (m / L) * (px1 - px0);
  const Y = (v) => py1 - ((v - lo) / (hi - lo)) * (py1 - py0);
  const spacing = n > 1 ? d[1] - d[0] : 50;
  const win = Math.max(1, Math.round(120 / spacing)); // ~120 m smoothing for colour

  // smoothed gradient per point (drives both the slice colour and the hover readout)
  const grades = new Array(n);
  for (let k = 0; k < n; k++) {
    const a = Math.max(0, k - win);
    const b = Math.min(n - 1, k + win);
    grades[k] = a === b ? 0 : ((e[b] - e[a]) / (d[b] - d[a])) * 100;
  }

  let slices = "";
  for (let k = 0; k < n - 1; k++) {
    const x0 = X(d[k]).toFixed(1);
    const x1 = X(d[k + 1]).toFixed(1);
    const y0 = Y(e[k]).toFixed(1);
    const y1 = Y(e[k + 1]).toFixed(1);
    slices += `<polygon points="${x0},${py1} ${x0},${y0} ${x1},${y1} ${x1},${py1}" fill="${gradeColor(grades[k])}"/>`;
  }
  let line = "";
  for (let k = 0; k < n; k++) line += `${X(d[k]).toFixed(1)},${Y(e[k]).toFixed(1)} `;

  // optional segment zones: a faint full-height band + a solid ribbon at the top edge,
  // coloured by type. Whole-route/lap spans (>60% of the route) are skipped as bands —
  // they'd tint the whole chart — but still ride in the list below and the tooltip.
  let segSvg = "";
  const segSpans = [];
  if (segments && segments.length) {
    let bands = "";
    for (const s of segments) {
      if (s.start_m == null || s.end_m == null) continue;
      const a = Math.max(0, s.start_m);
      const b = Math.min(L, s.end_m);
      if (b <= a) continue;
      const wide = b - a > 0.6 * L;
      const col = SEG_COLOR[s.type] || SEG_COLOR.segment;
      segSpans.push({ id: s.id, name: s.name, type: s.type, a, b });
      if (wide) continue;
      const xa = X(a);
      const w = Math.max(1, X(b) - xa);
      bands += `<rect x="${xa.toFixed(1)}" y="${py0}" width="${w.toFixed(1)}" height="${(py1 - py0).toFixed(1)}" fill="${col}" fill-opacity="0.09"/>`;
      bands += `<rect x="${xa.toFixed(1)}" y="${py0}" width="${Math.max(1.5, w).toFixed(1)}" height="4" fill="${col}" fill-opacity="0.9"/>`;
    }
    if (bands) segSvg = `<g class="rp-segs">${bands}</g>`;
  }

  // Lead-in: the neutral approach ridden before the route proper begins. p.leadin_m is
  // the exact distance where it ends on this profile's own axis (0 = the profile has no
  // lead-in section — some routes' geometry starts at the route banner). Shade it faint
  // grey with a dashed divider so the route proper reads clearly against it.
  const leadinM =
    typeof p.leadin_m === "number" && p.leadin_m > 0 && p.leadin_m < L ? p.leadin_m : 0;
  let leadinSvg = "";
  if (leadinM) {
    const xb = X(leadinM);
    const bandW = xb - px0;
    const lbl =
      bandW >= 48 // keep the ~43px "lead-in" label from overflowing the divider
        ? `<text x="${(px0 + 4).toFixed(1)}" y="${(py0 + 10).toFixed(1)}" class="rp-t rp-leadin-lbl">lead-in</text>`
        : "";
    leadinSvg =
      `<g class="rp-leadin">` +
      `<rect x="${px0}" y="${py0}" width="${bandW.toFixed(1)}" height="${(py1 - py0).toFixed(1)}" fill="#8b98a5" fill-opacity="0.11"/>` +
      `<line x1="${xb.toFixed(1)}" y1="${py0}" x2="${xb.toFixed(1)}" y2="${py1}" stroke="#8b98a5" stroke-opacity="0.75" stroke-width="1" stroke-dasharray="3 3"/>` +
      lbl +
      `</g>`;
  }

  let grid = "";
  const eStep = niceStep((hi - lo) / 5, [10, 20, 25, 50, 100, 200, 250, 500]);
  for (let v = Math.ceil(lo / eStep) * eStep; v < hi; v += eStep) {
    const y = Y(v).toFixed(1);
    grid += `<line x1="${px0}" y1="${y}" x2="${px1}" y2="${y}" stroke="#2d3742" stroke-width="1"/>`;
    grid += `<text x="${px0 - 6}" y="${(+y + 3.5).toFixed(1)}" text-anchor="end" class="rp-t">${Math.round(v)}</text>`;
  }
  const kmStep = niceStep(L / 1000 / 6, [0.5, 1, 2, 5, 10, 20, 50]);
  grid += `<text x="${px0}" y="${py1 + 16}" text-anchor="start" class="rp-t rp-unit">km</text>`;
  for (let km = kmStep; km * 1000 <= L; km += kmStep) {
    const x = X(km * 1000).toFixed(1);
    grid += `<text x="${x}" y="${py1 + 16}" text-anchor="middle" class="rp-t">${km % 1 ? km : km.toFixed(0)}</text>`;
  }

  // optional speed overlay(s) on a right km/h axis: sim = constant power (blue),
  // sim2 = optimal pacing (teal). Both share the axis, scaled to the faster of the two.
  let speedSvg = "";
  let speedByIdx = null;
  let speed2ByIdx = null;
  const okTrace = (s) => s && s.points && s.points.length > 1;
  if (okTrace(sim) || okTrace(sim2)) {
    const all = [];
    if (okTrace(sim)) all.push(...sim.points.map((pt) => pt.v_kmh));
    if (okTrace(sim2)) all.push(...sim2.points.map((pt) => pt.v_kmh));
    const vhi = Math.max(10, Math.ceil(Math.max(...all) / 10) * 10);
    const Ys = (v) => py1 - (v / vhi) * (py1 - py0);
    let axis = "";
    const vStep = niceStep(vhi / 4, [5, 10, 20, 25, 50]);
    for (let v = vStep; v <= vhi - vStep / 2; v += vStep) {
      axis += `<text x="${px1 + 5}" y="${(Ys(v) + 3.5).toFixed(1)}" text-anchor="start" class="rp-t rp-spd">${v}</text>`;
    }
    axis += `<text x="${px1 + 5}" y="${(py0 + 9).toFixed(1)}" text-anchor="start" class="rp-t rp-spd">km/h</text>`;
    const polyOf = (pts, color, w) => {
      let poly = "";
      for (const pt of pts) poly += `${X(pt.d).toFixed(1)},${Ys(pt.v_kmh).toFixed(1)} `;
      return `<polyline points="${poly.trim()}" fill="none" stroke="${color}" stroke-width="${w}" stroke-linejoin="round"/>`;
    };
    const interp = (pts) => {
      const sd = pts.map((pt) => pt.d);
      const sv = pts.map((pt) => pt.v_kmh);
      const arr = new Array(n);
      let j = 0;
      for (let k = 0; k < n; k++) {
        while (j < sd.length - 2 && sd[j + 1] < d[k]) j++;
        const w = sd[j + 1] - sd[j];
        const t = w > 0 ? (d[k] - sd[j]) / w : 0;
        arr[k] = sv[j] + t * (sv[Math.min(j + 1, sv.length - 1)] - sv[j]);
      }
      return arr;
    };
    let lines = "";
    if (okTrace(sim)) {
      lines += polyOf(sim.points, "#4aa8ff", sim2 ? 1.3 : 1.6);
      speedByIdx = interp(sim.points);
    }
    if (okTrace(sim2)) {
      lines += polyOf(sim2.points, "#1baf7a", 1.9);
      speed2ByIdx = interp(sim2.points);
    }
    speedSvg = axis + lines;
  }

  const totalKm = (r.distance_km + r.leadin_km).toFixed(1);
  const stat = `${totalKm} km · +${r.ascent_m} m · avg ${r.avg_gradient_pct}% · max ${p.max_grade_pct}%`;
  const svg = `<svg viewBox="0 0 ${W} ${H}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Elevation profile: ${stat}">
    ${grid}${slices}${leadinSvg}${segSvg}
    <polyline points="${line.trim()}" fill="none" stroke="#e6edf3" stroke-width="1.3" stroke-opacity=".55" stroke-linejoin="round"/>
    ${speedSvg}
    <line class="rp-cross" x1="0" y1="${py0}" x2="0" y2="${py1}" visibility="hidden"/>
    <circle class="rp-dot" r="3.5" visibility="hidden"/>
  </svg>`;
  return {
    svg,
    meta: {
      W, px0, px1, py0, py1, L, lo, hi, spacing, d, e, grades, leadinM,
      speed: speedByIdx, speed2: speed2ByIdx, segments: segSpans,
    },
  };
}

// A 2D map of the route from the decoded WGS84 track (p.lat / p.lon, parallel to p.d).
// Equal-aspect (cos-lat corrected), no basemap. The line is coloured by gradient with the
// same gradeColor + smoothing as the elevation profile, so the map matches the profile; the
// lead-in is drawn dashed, and a green dot marks the start. "" when the route has no geo.
function routeShapeSVG(p, size) {
  const lat = p.lat;
  const lon = p.lon;
  const d = p.d;
  if (!lat || !lon || lat.length < 2) return "";
  const S = size || 240;
  const pad = 12;
  const latMid = (Math.min(...lat) + Math.max(...lat)) / 2;
  const kx = Math.cos((latMid * Math.PI) / 180); // shrink longitude to keep aspect true
  const X = lon.map((v) => v * kx);
  const Y = lat;
  const xmin = Math.min(...X);
  const xmax = Math.max(...X);
  const ymin = Math.min(...Y);
  const ymax = Math.max(...Y);
  const spanx = xmax - xmin || 1e-9;
  const spany = ymax - ymin || 1e-9;
  const scale = Math.min((S - 2 * pad) / spanx, (S - 2 * pad) / spany);
  const ox = (S - spanx * scale) / 2;
  const oy = (S - spany * scale) / 2;
  const px = (v) => ox + (v - xmin) * scale;
  const py = (v) => oy + (ymax - v) * scale; // north up
  const leadinM = typeof p.leadin_m === "number" ? p.leadin_m : 0;
  const pt = (i) => `${px(X[i]).toFixed(1)},${py(Y[i]).toFixed(1)}`;
  // per-point smoothed gradient — identical to profileSVG so the map colours match the
  // elevation profile exactly (gradeColor over a ~120 m window)
  const e = p.e;
  const n = X.length;
  const spacing = n > 1 ? d[1] - d[0] : 50;
  const win = Math.max(1, Math.round(120 / spacing));
  const grades = new Array(n);
  for (let k = 0; k < n; k++) {
    const a = Math.max(0, k - win);
    const b = Math.min(n - 1, k + win);
    grades[k] = a === b ? 0 : ((e[b] - e[a]) / (d[b] - d[a])) * 100;
  }
  // colour each segment by grade; merge consecutive segments sharing a colour + lead-in
  // state into one polyline (clean joins, fewer nodes). Lead-in segments are drawn dashed.
  const runs = [];
  let cur = null;
  for (let i = 0; i < n - 1; i++) {
    const col = gradeColor(grades[i]);
    const lead = leadinM > 0 && d[i] < leadinM;
    if (!cur || cur.col !== col || cur.lead !== lead) {
      cur = { col, lead, pts: [pt(i)] };
      runs.push(cur);
    }
    cur.pts.push(pt(i + 1));
  }
  const lines = runs
    .map(
      (rn) =>
        `<polyline points="${rn.pts.join(" ")}" fill="none" stroke="${rn.col}" stroke-width="2.6" ` +
        `stroke-linejoin="round" stroke-linecap="round"${rn.lead ? ' stroke-dasharray="3 3"' : ""}/>`
    )
    .join("");
  return `<svg viewBox="0 0 ${S} ${S}" class="rshape" role="img" aria-label="Route map coloured by gradient">
    ${lines}
    <circle cx="${px(X[0]).toFixed(1)}" cy="${py(Y[0]).toFixed(1)}" r="4" fill="#3fb950"/>
  </svg>`;
}

// Wire a `.seg-toggle` checkbox (in the chart header) to show/hide the segment bands
// in `body` without a re-render. Sets body.dataset.hideSegs so wireHover mirrors it.
function wireSegToggle(header, body) {
  const cb = header.querySelector(".seg-toggle");
  if (!cb) return;
  const apply = () => {
    body.dataset.hideSegs = cb.checked ? "0" : "1";
    const g = body.querySelector(".rp-segs");
    if (g) g.style.display = cb.checked ? "" : "none";
  };
  cb.addEventListener("change", apply);
  apply();
}

// Crosshair + tooltip: move the mouse over the profile to read distance / elevation /
// gradient (and speed when a sim overlay is present).
function wireHover(body, meta) {
  const svg = body.querySelector("svg");
  if (!svg) return;
  const cross = svg.querySelector(".rp-cross");
  const dot = svg.querySelector(".rp-dot");
  let tip = body.querySelector(".rp-tip");
  if (!tip) {
    tip = document.createElement("div");
    tip.className = "rp-tip";
    body.appendChild(tip);
  }
  const { W, px0, px1, py0, py1, L, lo, hi, d, e, grades, speed, speed2, segments, leadinM } = meta;

  // narrowest (most specific) segment covering distance m, or null
  const segAt = (m) => {
    let best = null;
    for (const s of segments || []) {
      if (m >= s.a && m <= s.b && (!best || s.b - s.a < best.b - best.a)) best = s;
    }
    return best;
  };

  // nearest sample to a distance m in the (ascending, possibly non-uniform) d[] array
  const nearest = (m) => {
    let a = 0;
    let b = d.length - 1;
    while (a < b) {
      const mid = (a + b) >> 1;
      if (d[mid] < m) a = mid + 1;
      else b = mid;
    }
    return a > 0 && Math.abs(d[a - 1] - m) <= Math.abs(d[a] - m) ? a - 1 : a;
  };

  const move = (ev) => {
    const rect = svg.getBoundingClientRect();
    if (!rect.width) return;
    const scale = rect.width / W; // viewBox px -> display px
    const vbx = (ev.clientX - rect.left) / scale;
    const cx = Math.max(px0, Math.min(px1, vbx));
    const m = ((cx - px0) / (px1 - px0)) * L;
    const i = nearest(m);
    const X = px0 + (d[i] / L) * (px1 - px0);
    const Y = py1 - ((e[i] - lo) / (hi - lo)) * (py1 - py0);
    cross.setAttribute("x1", X);
    cross.setAttribute("x2", X);
    cross.setAttribute("visibility", "visible");
    dot.setAttribute("cx", X);
    dot.setAttribute("cy", Y);
    dot.setAttribute("visibility", "visible");
    const g = grades[i];
    let html =
      `<b>${(d[i] / 1000).toFixed(2)}</b> km · <b>${Math.round(e[i])}</b> m · ` +
      `<b>${g >= 0 ? "+" : "−"}${Math.abs(g).toFixed(1)}</b>%`;
    if (speed && speed2) {
      html += ` · <b>${Math.round(speed[i])}</b>/<b>${Math.round(speed2[i])}</b> km/h`;
    } else if (speed) {
      html += ` · <b>${Math.round(speed[i])}</b> km/h`;
    }
    if (body.dataset.hideSegs !== "1") {
      const s = segAt(m);
      if (s) html += ` · <b class="rp-seg" style="color:${SEG_COLOR[s.type] || SEG_COLOR.segment}">${segDisplayName(s)}</b>`;
    }
    // key off the snapped sample d[i] (same value shown as the distance) so the tag and the
    // readout never disagree within a half-sample of the boundary
    if (leadinM && d[i] <= leadinM) html += ` · <b class="rp-leadin-tip">lead-in</b>`;
    tip.innerHTML = html;
    const half = tip.offsetWidth / 2 + 4;
    const svgLeftInBody = rect.left - body.getBoundingClientRect().left; // SVG has no offsetLeft
    const left = Math.max(half, Math.min(body.clientWidth - half, svgLeftInBody + X * scale));
    tip.style.left = `${left}px`;
    tip.style.display = "block";
  };
  const hide = () => {
    cross.setAttribute("visibility", "hidden");
    dot.setAttribute("visibility", "hidden");
    tip.style.display = "none";
  };
  svg.addEventListener("mousemove", move);
  svg.addEventListener("mouseleave", hide);
}
