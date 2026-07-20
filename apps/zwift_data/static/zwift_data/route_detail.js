"use strict";

// Route-detail charts: fetch the elevation profile + segments for one route and render
// the grade-coloured elevation profile, the VeloViewer-style route map, and the
// segment list. Chart helpers (profileSVG / routeShapeSVG / wireHover / wireSegToggle)
// come from profile_chart.js, loaded first.

const SEG_TYPE_LABEL = { sprint: "Sprint", kom: "KOM", climb: "Climb", lap: "Lap", segment: "Segment" };

async function loadRouteCharts() {
  const root = document.getElementById("zsl-route");
  if (!root) return;
  const route = {
    name: root.dataset.name,
    world_id: Number(root.dataset.worldId),
    name_hash: root.dataset.nameHash,
    distance_km: Number(root.dataset.distanceKm),
    leadin_km: Number(root.dataset.leadinKm),
    ascent_m: Number(root.dataset.ascentM),
    avg_gradient_pct: Number(root.dataset.avgGradient),
  };
  const chartEl = root.querySelector(".route-chart");
  const body = chartEl.querySelector(".rp-body");
  const shapePanel = root.querySelector(".route-shape");
  const shapeBody = root.querySelector(".rs-shape-body");
  const segBody = root.querySelector(".route-segs-body");
  // segment-detail URL built from a "0" reverse so the mount point isn't hardcoded
  const segDetailBase = (root.dataset.segDetailUrl0 || "").replace(/0\/$/, "");

  const hideShape = () => { if (shapePanel) shapePanel.hidden = true; };

  let segments = [];
  try {
    const segRes = await fetch(root.dataset.segmentsUrl);
    if (segRes.ok) segments = (await segRes.json()).segments || [];
  } catch (e) { /* segments are optional */ }

  renderSegments(segBody, segments, segDetailBase);

  try {
    const res = await fetch(root.dataset.profileUrl);
    if (res.status === 404) {
      body.innerHTML = `<span class="rp-msg">No elevation profile available for this route.</span>`;
      hideShape();
      return;
    }
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const p = await res.json();
    const w = body.clientWidth - 8;
    const { svg, meta } = profileSVG(p, route, w, null, null, segments);
    body.innerHTML = svg;
    wireHover(body, meta);
    if (meta.segments && meta.segments.length) {
      const toggle = chartEl.querySelector(".seg-toggle-l");
      if (toggle) toggle.hidden = false;
      wireSegToggle(chartEl, body);
    }
    const mg = chartEl.querySelector(".rp-maxgrade");
    if (mg) mg.textContent = ` · max ${p.max_grade_pct}% grade`;
    const sh = shapeBody && typeof routeShapeSVG === "function" ? routeShapeSVG(p, 240) : "";
    if (sh) shapeBody.innerHTML = sh;
    else hideShape();
  } catch (err) {
    body.innerHTML = `<span class="rp-msg">Couldn't load profile: ${err}</span>`;
    hideShape();
  }
}

function renderSegments(body, segments, detailBase) {
  if (!body) return;
  if (!segments.length) {
    body.innerHTML = `<div class="route-segs-none">No live segments on this route.</div>`;
    return;
  }
  const rows = segments
    .map((s) => {
      const at = s.start_m == null ? "—" : `${(s.start_m / 1000).toFixed(1)}km`;
      const name = s.name || `Segment ${s.id}`;
      const kind = s.type || "segment";
      const type = SEG_TYPE_LABEL[kind] || kind;
      const len = s.length_m == null ? "" : `${s.length_m} m`;
      const grade = s.avg_grade_pct == null ? "" : ` · ${s.avg_grade_pct}%`;
      const pu = s.gives_powerup ? ' <span class="pu" title="Gives a Power-Up">⚡</span>' : "";
      const link = `${detailBase}${encodeURIComponent(s.id)}/`;
      return `<div class="route-seg">
        <span class="rs-at">${at}</span>
        <span class="rs-name"><a href="${link}">${name}</a>${pu}</span>
        <span class="sbadge ${kind}">${type}</span>
        <span class="rs-meta">${len}${grade}</span>
      </div>`;
    })
    .join("");
  body.innerHTML = `<div class="route-segs-list">${rows}</div>`;
}

document.addEventListener("DOMContentLoaded", loadRouteCharts);
