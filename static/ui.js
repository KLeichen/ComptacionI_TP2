/* ui.js — NEXUS futuristic dashboard client.
   Single all-in-one screen fed by Server-Sent Events. Handles the radial
   gauges, history chart, alert feed, the critical "danger" mode, and the
   slide-in threshold drawer. */

(function () {
  "use strict";

  const RING_CIRC = 2 * Math.PI * 76; // matches r=76 in the SVG / CSS
  const CHART_W = 600;
  const CHART_H = 180;
  const RES = ["cpu", "memory", "disk"];

  let thresholds = { cpu: 80, memory: 85, disk: 90 };
  let staleTimer = null;
  let dirty = {}; // thresholds the user is currently dragging

  // ---- severity --------------------------------------------------------
  function severity(value, limit) {
    if (value >= limit) return "crit";
    if (value >= limit * 0.8) return "warn";
    return "ok";
  }

  // ---- radial gauges ---------------------------------------------------
  function setGauge(res, value, limit) {
    const ring = document.getElementById("ring-" + res);
    ring.style.strokeDashoffset = RING_CIRC * (1 - Math.min(value, 100) / 100);
    document.getElementById("num-" + res).textContent = value.toFixed(0);
    document.getElementById("lim-" + res).textContent = limit;

    const card = document.querySelector('.gauge-card[data-res="' + res + '"]');
    const sev = severity(value, limit);
    card.classList.toggle("is-warn", sev === "warn");
    card.classList.toggle("is-crit", sev === "crit");
  }

  // ---- history chart ---------------------------------------------------
  function renderChart(history, limit, avg) {
    const n = history.length;
    const stepX = n > 1 ? CHART_W / (n - 1) : CHART_W;
    const y = (v) => CHART_H - (Math.min(v, 100) / 100) * CHART_H;

    let pts = "";
    history.forEach((v, i) => {
      pts += (i * stepX).toFixed(1) + "," + y(v).toFixed(1) + " ";
    });
    document.getElementById("chart-line").setAttribute("points", pts.trim());

    const area = pts.trim()
      ? "0," + CHART_H + " " + pts.trim() + " " + CHART_W + "," + CHART_H
      : "";
    document.getElementById("chart-area").setAttribute("points", area);

    const thrY = y(limit);
    const tl = document.getElementById("thr-line");
    tl.setAttribute("y1", thrY); tl.setAttribute("y2", thrY);
    const al = document.getElementById("avg-line");
    al.setAttribute("y1", y(avg)); al.setAttribute("y2", y(avg));
  }

  // ---- critical mode (center danger sign + red lights) -----------------
  function updateCritical(metrics, thr) {
    const breached = RES.filter((r) => metrics[r] >= thr[r]);
    const on = breached.length > 0;
    document.body.classList.toggle("critical", on);
    document.getElementById("danger").classList.toggle("hidden", !on);
    if (on) {
      const names = breached.map((r) => r.toUpperCase()).join(" · ");
      document.getElementById("danger-sub").textContent =
        names + " EXCEEDED THRESHOLD";
    }
  }

  // ---- stale / offline (Option C) --------------------------------------
  function setOffline(off) {
    document.getElementById("offline").classList.toggle("hidden", !off);
    const dot = document.getElementById("dot");
    dot.classList.toggle("on", !off);
    document.getElementById("status-text").textContent = off ? "STALE" : "ONLINE";
    if (off) document.body.classList.remove("critical");
  }

  // ---- apply a full state frame ----------------------------------------
  function applyState(s) {
    if (staleTimer) clearTimeout(staleTimer);
    staleTimer = setTimeout(() => setOffline(true), 4000);

    thresholds = s.thresholds;
    setOffline(!s.online);
    if (!s.online) return;

    RES.forEach((r) => setGauge(r, s.metrics[r], s.thresholds[r]));
    updateCritical(s.metrics, s.thresholds);

    document.getElementById("t-counter").textContent = s.counter;
    document.getElementById("t-time").textContent = s.timestamp;
    document.getElementById("s-counter").textContent = s.counter;
    document.getElementById("s-time").textContent = s.timestamp;
    document.getElementById("s-avg").textContent = s.avg.toFixed(1) + "%";
    document.getElementById("s-max").textContent = s.max.toFixed(1) + "%";
    document.getElementById("s-min").textContent = s.min.toFixed(1) + "%";
    document.getElementById("s-window").textContent = s.window;

    renderChart(s.history, s.thresholds.cpu, s.avg);
    document.getElementById("c-avg").textContent = s.avg.toFixed(1) + "%";
    document.getElementById("c-thr").textContent = s.thresholds.cpu + "%";

    // keep drawer sliders in sync unless the user is dragging
    RES.forEach((r) => {
      const rng = document.getElementById("rng-" + r);
      if (!dirty[r] && document.activeElement !== rng) {
        rng.value = s.thresholds[r];
        document.getElementById("out-" + r).textContent = s.thresholds[r] + "%";
      }
    });
  }

  // ---- alerts feed -----------------------------------------------------
  const feed = document.getElementById("feed");
  function addAlert(a) {
    const empty = feed.querySelector(".feed-empty");
    if (empty) empty.remove();
    const line = document.createElement("div");
    line.className = "feed-line new";
    line.innerHTML =
      '<span class="ts">[' + a.timestamp + "]</span> &#9888; " +
      a.resource + " " + a.value + "% &ge; " + a.threshold + "%";
    feed.insertBefore(line, feed.firstChild);
    while (feed.children.length > 60) feed.removeChild(feed.lastChild);
  }

  // ---- SSE -------------------------------------------------------------
  function connect() {
    const es = new EventSource("/api/stream");
    es.addEventListener("state", (e) => applyState(JSON.parse(e.data)));
    es.addEventListener("alert", (e) => addAlert(JSON.parse(e.data)));
    es.onerror = () => {
      document.getElementById("status-text").textContent = "RECONNECT";
      document.getElementById("dot").classList.remove("on");
    };
  }

  // ---- threshold drawer (Option B) -------------------------------------
  const drawer = document.getElementById("drawer");
  const scrim = document.getElementById("scrim");
  function openDrawer() { drawer.classList.remove("hidden"); scrim.classList.remove("hidden"); }
  function closeDrawer() { drawer.classList.add("hidden"); scrim.classList.add("hidden"); }

  document.getElementById("open-cfg").addEventListener("click", openDrawer);
  document.getElementById("close-cfg").addEventListener("click", closeDrawer);
  scrim.addEventListener("click", closeDrawer);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

  function postConfig(res, value) {
    fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resource: res, value: value }),
    })
      .then((r) => r.json())
      .then((j) => {
        const st = document.getElementById("cfg-status");
        st.textContent = j.ok
          ? "> " + j.resource + " threshold = " + j.value + "% applied live."
          : "! error: " + (j.error || "unknown");
        dirty[res] = false;
      });
  }

  RES.forEach((res) => {
    const rng = document.getElementById("rng-" + res);
    const out = document.getElementById("out-" + res);
    rng.addEventListener("input", () => {
      dirty[res] = true;
      out.textContent = rng.value + "%";
    });
    // commit on release (change) so we don't spam Redis on every pixel
    rng.addEventListener("change", () => postConfig(res, parseFloat(rng.value)));
  });

  // ---- boot ------------------------------------------------------------
  fetch("/api/state").then((r) => r.json()).then(applyState).catch(() => {});
  connect();
})();
