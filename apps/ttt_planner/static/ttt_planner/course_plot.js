"use strict";

// The TTT plan's course chart: the route's elevation profile shaded by gradient, the team's
// speed at a steady effort along it, and a readout of every rider's watts at the chosen
// point. The numbers come from the #ttt-course-data JSON in the plan body (built by
// compute._course_chart); the readout's rows, rider names included, are rendered by the
// server, so this script only ever writes numbers and fixed words into the page.
//
// Mouse: hover. Touch: drag sideways. Keyboard: the chart is a slider (arrow keys, Page
// Up/Down, Home/End). It is redrawn after every swap of #ttt-plan-body and on resize,
// keeping the chosen distance.
(function () {
  // Upper bound of each gradient band (%), its colour, and its name in the key.
  var BANDS = [
    [-1, "#5b8fd9", "Down"],
    [3, "#5fb85a", "Flat to 3%"],
    [6, "#e3b23c", "3–6%"],
    [9, "#e8892b", "6–9%"],
    [12, "#d9592b", "9–12%"],
    [Infinity, "#b8323a", "12% and up"],
  ];
  var chosenKm = null;

  function bandColor(g) {
    for (var b = 0; b < BANDS.length; b++) {
      if (g < BANDS[b][0]) return BANDS[b][1];
    }
    return BANDS[BANDS.length - 1][1];
  }

  function niceStep(target, steps) {
    for (var s = 0; s < steps.length; s++) {
      if (target <= steps[s]) return steps[s];
    }
    return steps[steps.length - 1];
  }

  // Index of the point nearest a distance (km is ascending).
  function nearest(km, value) {
    var a = 0;
    var b = km.length - 1;
    while (a < b) {
      var mid = (a + b) >> 1;
      if (km[mid] < value) a = mid + 1;
      else b = mid;
    }
    return a > 0 && Math.abs(km[a - 1] - value) <= Math.abs(km[a] - value) ? a - 1 : a;
  }

  function slope(g) {
    if (Math.abs(g) < 0.5) return "flat";
    return Math.abs(g).toFixed(1) + "% " + (g > 0 ? "up" : "down");
  }

  function drawKey(key) {
    if (!key || key.childElementCount) return;
    BANDS.forEach(function (band) {
      var item = document.createElement("li");
      var swatch = document.createElement("span");
      swatch.className = "cc-swatch";
      swatch.style.background = band[1];
      swatch.setAttribute("aria-hidden", "true");
      item.append(swatch, " " + band[2]);
      key.appendChild(item);
    });
    var item = document.createElement("li");
    var line = document.createElement("span");
    line.className = "cc-line";
    line.setAttribute("aria-hidden", "true");
    item.append(line, " Team speed (kph, right axis)");
    key.appendChild(item);
  }

  // One readout cell: watts on the first line; W/kg, share of FTP and a warning below.
  function fillCell(cell, watts, kg, ftp, opts) {
    cell.replaceChildren();
    if (opts.noPulls) {
      var none = document.createElement("span");
      none.className = "text-base-content/70";
      none.textContent = "no pulls";
      cell.appendChild(none);
      return;
    }
    var main = document.createElement("div");
    main.textContent = watts + " W";
    cell.appendChild(main);
    var parts = [];
    if (opts.wkg && kg) parts.push((watts / kg).toFixed(1) + " W/kg");
    if (ftp) parts.push(Math.round((watts / ftp) * 100) + "% FTP");
    if (parts.length) {
      var sub = document.createElement("div");
      sub.className = "text-xs text-base-content/70";
      sub.textContent = parts.join(" · ");
      cell.appendChild(sub);
    }
    if (opts.flagOverFtp && ftp && watts >= ftp) {
      var badge = document.createElement("span");
      badge.className = "badge badge-warning badge-sm";
      badge.textContent = "over FTP";
      cell.appendChild(badge);
    }
  }

  function draw() {
    var dataEl = document.getElementById("ttt-course-data");
    var host = document.getElementById("ttt-course-plot");
    if (!dataEl || !host) return;
    var data = JSON.parse(dataEl.textContent);
    var km = data.km;
    var elev = data.elev;
    var grade = data.grade;
    var kph = data.kph;
    var n = km.length;
    if (n < 2) return;
    var total = km[n - 1];

    var W = Math.max(300, Math.round(host.clientWidth || 640));
    var H = Math.round(Math.min(240, Math.max(170, W / 3.3)));
    var x0 = 42;
    var x1 = W - 42;
    var y0 = 20; // room above the plot for the axis titles
    var y1 = H - 26;
    var emin = Math.min.apply(null, elev);
    var emax = Math.max.apply(null, elev);
    var span = Math.max((emax - emin) * 1.15, 30);
    var lo = (emax + emin) / 2 - span / 2;
    var hi = lo + span;
    var vmax = Math.max(10, Math.ceil(Math.max.apply(null, kph) / 10) * 10);
    var X = function (k) { return x0 + (k / total) * (x1 - x0); };
    var Ye = function (v) { return y1 - ((v - lo) / (hi - lo)) * (y1 - y0); };
    var Yv = function (v) { return y1 - (v / vmax) * (y1 - y0); };
    var f = function (v) { return v.toFixed(1); };

    var svg = "";
    // Speed grid and its axis on the right; elevation labels on the left.
    var vStep = niceStep(vmax / 4, [5, 10, 20, 25, 50]);
    for (var v = vStep; v <= vmax; v += vStep) {
      svg += '<line class="cc-grid" x1="' + x0 + '" x2="' + x1 + '" y1="' + f(Yv(v)) + '" y2="' + f(Yv(v)) + '"/>';
      svg += '<text class="cc-text" x="' + (x1 + 5) + '" y="' + f(Yv(v) + 4) + '">' + v + "</text>";
    }
    svg += '<text class="cc-text" x="' + (x1 + 5) + '" y="' + (y0 - 9) + '">kph</text>';
    var eStep = niceStep((hi - lo) / 4, [5, 10, 20, 25, 50, 100, 200, 250, 500]);
    for (var e = Math.ceil(lo / eStep) * eStep; e < hi; e += eStep) {
      svg += '<text class="cc-text" text-anchor="end" x="' + (x0 - 5) + '" y="' + f(Ye(e) + 4) + '">' + Math.round(e) + "</text>";
    }
    svg += '<text class="cc-text" text-anchor="end" x="' + (x0 - 5) + '" y="' + (y0 - 9) + '">m</text>';
    var kmStep = niceStep(total / 6, [0.5, 1, 2, 5, 10, 20, 50]);
    svg += '<text class="cc-text" x="' + x0 + '" y="' + (y1 + 17) + '">km</text>';
    for (var k = kmStep; k <= total; k += kmStep) {
      svg += '<text class="cc-text" text-anchor="middle" x="' + f(X(k)) + '" y="' + (y1 + 17) + '">' + (k % 1 ? k : k.toFixed(0)) + "</text>";
    }
    // Lead-in band.
    if (data.leadin_km > 0 && data.leadin_km < total) {
      var xb = X(data.leadin_km);
      svg += '<rect class="cc-leadin" x="' + x0 + '" y="' + y0 + '" width="' + f(xb - x0) + '" height="' + (y1 - y0) + '"/>';
      svg += '<line class="cc-leadin-line" x1="' + f(xb) + '" x2="' + f(xb) + '" y1="' + y0 + '" y2="' + y1 + '"/>';
      if (xb - x0 >= 46) svg += '<text class="cc-text" x="' + (x0 + 4) + '" y="' + (y0 + 11) + '">lead-in</text>';
    }
    // Elevation, shaded by the gradient of each stretch.
    for (var i = 0; i < n - 1; i++) {
      svg += '<polygon fill="' + bandColor(grade[i]) + '" fill-opacity="0.85" points="' +
        f(X(km[i])) + "," + y1 + " " + f(X(km[i])) + "," + f(Ye(elev[i])) + " " +
        f(X(km[i + 1])) + "," + f(Ye(elev[i + 1])) + " " + f(X(km[i + 1])) + "," + y1 + '"/>';
    }
    var elevLine = "";
    var speedLine = "";
    for (var j = 0; j < n; j++) {
      elevLine += f(X(km[j])) + "," + f(Ye(elev[j])) + " ";
      speedLine += f(X(km[j])) + "," + f(Yv(kph[j])) + " ";
    }
    svg += '<polyline class="cc-elev-line" points="' + elevLine + '"/>';
    svg += '<polyline class="cc-speed" points="' + speedLine + '"/>';
    svg += '<line class="cc-cross" y1="' + y0 + '" y2="' + y1 + '"/>';
    svg += '<circle class="cc-dot-elev" r="3.5"/><circle class="cc-dot" r="3.5"/>';
    host.innerHTML = '<svg viewBox="0 0 ' + W + " " + H + '" aria-hidden="true" focusable="false">' + svg + "</svg>";

    var chart = host.querySelector("svg");
    var cross = chart.querySelector(".cc-cross");
    var dotElev = chart.querySelector(".cc-dot-elev");
    var dotSpeed = chart.querySelector(".cc-dot");
    var where = document.getElementById("ttt-course-where");
    var rows = document.querySelectorAll("#ttt-course-readout tbody tr[data-rider-index]");

    host.setAttribute("aria-valuemin", "0");
    host.setAttribute("aria-valuemax", total.toFixed(2));

    var current = 0;
    function choose(i) {
      current = Math.max(0, Math.min(n - 1, i));
      chosenKm = km[current];
      var x = f(X(km[current]));
      cross.setAttribute("x1", x);
      cross.setAttribute("x2", x);
      dotElev.setAttribute("cx", x);
      dotElev.setAttribute("cy", f(Ye(elev[current])));
      dotSpeed.setAttribute("cx", x);
      dotSpeed.setAttribute("cy", f(Yv(kph[current])));
      var text = km[current].toFixed(2) + " km · " + slope(grade[current]) + " · team " + kph[current].toFixed(1) + " kph";
      if (data.leadin_km > 0 && km[current] < data.leadin_km) text += " · lead-in";
      if (where) where.textContent = text;
      host.setAttribute("aria-valuenow", km[current].toFixed(2));
      host.setAttribute("aria-valuetext", text);
      rows.forEach(function (row) {
        var rider = data.riders[Number(row.dataset.riderIndex)];
        if (!rider) return;
        fillCell(row.querySelector(".cc-front"), rider.front[current], rider.kg, rider.ftp, {
          wkg: true, noPulls: !rider.pulls,
        });
        fillCell(row.querySelector(".cc-sit"), rider.sit[current], rider.kg, rider.ftp, { flagOverFtp: true });
      });
    }

    function fromPointer(ev) {
      var rect = chart.getBoundingClientRect();
      if (!rect.width) return;
      var vx = (ev.clientX - rect.left) * (W / rect.width);
      choose(nearest(km, Math.max(0, Math.min(total, ((vx - x0) / (x1 - x0)) * total))));
    }
    chart.addEventListener("pointerdown", fromPointer);
    chart.addEventListener("pointermove", function (ev) {
      if (ev.pointerType === "mouse" || ev.buttons) fromPointer(ev);
    });
    host.onkeydown = function (ev) {
      var steps = { ArrowLeft: -1, ArrowDown: -1, ArrowRight: 1, ArrowUp: 1, PageDown: -10, PageUp: 10 };
      if (ev.key in steps) choose(current + steps[ev.key]);
      else if (ev.key === "Home") choose(0);
      else if (ev.key === "End") choose(n - 1);
      else return;
      ev.preventDefault();
    };

    drawKey(document.getElementById("ttt-course-key"));

    if (chosenKm !== null) {
      choose(nearest(km, chosenKm));
    } else {
      // Start on the steepest stretch of the route proper, where the riders differ most.
      var steepest = nearest(km, data.leadin_km);
      for (var p = steepest; p < n; p++) {
        if (grade[p] > grade[steepest]) steepest = p;
      }
      choose(steepest);
    }
  }

  var resizeTimer = null;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(draw, 150);
  });
  document.body.addEventListener("htmx:afterSettle", function (ev) {
    if (ev.target && ev.target.id === "ttt-plan-body") draw();
  });
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", draw);
  else draw();
})();
