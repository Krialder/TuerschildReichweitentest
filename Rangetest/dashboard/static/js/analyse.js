// Analyse-Tab: Aggregat, Heatmap, Timeline, Verteilung, Distanz-Scatter.

let anaRunsCache = [];
let chartAgg, chartTimeline, chartBox, chartCdf, chartScatter;

function fmtSec(s) {
  if (s == null) return "";
  const h = Math.floor(s/3600), m = Math.floor((s%3600)/60), x = s%60;
  return (h?h+"h ":"") + (m?m+"m ":"") + (x||(!h&&!m)?x+"s":"");
}

async function loadAnalyseRuns() {
  $("anaStatus").textContent = "Lade Runs...";
  try {
    const list = await (await fetch("/api/analysis/runs")).json();
    anaRunsCache = list;
    renderAnaRunsList();
    $("anaStatus").textContent = list.length + " Run(s).";
  } catch (e) { $("anaStatus").textContent = "Fehler: " + e; }
}

function renderAnaRunsList() {
  const longMin = parseInt($("anaLongMin").value || "60", 10);
  const longThr = longMin * 60;
  const box = $("anaRuns"); box.innerHTML = "";
  for (const r of anaRunsCache) {
    const isLong = r.duration_s != null && r.duration_s >= longThr;
    const lbl = document.createElement("label");
    lbl.innerHTML = `
      <input type="checkbox" data-rid="${r.id}" ${r.total>0 ? "checked":""}>
      <span><b>#${r.id}</b> ${r.started_at||""}
        ${isLong ? '<span class="badge long">long</span>' : ''}
         ${fmtSec(r.duration_s)}  ${r.total} pkt, PDR ${r.pdr_pct.toFixed(1)}%,
        ${r.max_hops} hops, fw=${r.fw||"?"}, notes: ${(r.notes||"").substring(0,40)}
      </span>`;
    box.appendChild(lbl);
  }
}

function selectedRunIds() {
  return [...$("anaRuns").querySelectorAll("input:checked")]
            .map(i => parseInt(i.dataset.rid, 10));
}

$("anaSelAll").onclick  = () =>
  $("anaRuns").querySelectorAll("input").forEach(i => i.checked = true);
$("anaSelNone").onclick = () =>
  $("anaRuns").querySelectorAll("input").forEach(i => i.checked = false);
$("anaSelLong").onclick = () => {
  const longThr = (parseInt($("anaLongMin").value||"60",10)) * 60;
  $("anaRuns").querySelectorAll("input").forEach(i => {
    const rid = parseInt(i.dataset.rid, 10);
    const r = anaRunsCache.find(x => x.id === rid);
    i.checked = r && r.duration_s != null && r.duration_s >= longThr;
  });
};
$("anaLongMin").onchange = renderAnaRunsList;
$("anaReloadRuns").onclick = loadAnalyseRuns;

function parseIntList(str) {
  if (!str || !str.trim()) return null;
  const out = str.split(",").map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
  return out.length ? out : null;
}
function commonFilters() {
  return {
    run_ids: selectedRunIds(),
    min_duration_s: parseInt($("anaMinDur").value || "0", 10),
    filter_hops: parseIntList($("anaHops").value),
    filter_size: parseIntList($("anaSizes").value),
    filter_dst:  parseIntList($("anaDsts").value),
  };
}

// ---- Aggregat ------------------------------------------------------
async function runAggregate() {
  $("anaStatus").textContent = "lade Aggregat...";
  const body = { ...commonFilters(), group_by: $("anaGroup").value };
  try {
    const r = await fetch("/api/analysis/aggregate", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    drawAggregate(d);
    $("anaStatus").textContent = d.groups.length + " Gruppen.";
  } catch (e) { $("anaStatus").textContent = "Fehler: " + e.message; }
}
function drawAggregate(d) {
  const metric = $("anaMetric").value;
  const labels = d.groups.map(g => String(g.group));
  let datasets;
  if (metric === "pdr_pct") {
    datasets = [
      { label:"PDR %", data: d.groups.map(g => g.pdr_pct),
        backgroundColor:"#3b7a1ecc" },
      { label:"CI low", data: d.groups.map(g => g.pdr_ci_low_pct),
        type:"line", borderColor:"#a23636", backgroundColor:"transparent",
        pointRadius:3, showLine:false },
      { label:"CI high", data: d.groups.map(g => g.pdr_ci_high_pct),
        type:"line", borderColor:"#a23636", backgroundColor:"transparent",
        pointRadius:3, showLine:false },
    ];
  } else if (metric === "rssi_med" || metric === "snr_med") {
    const k = metric.split("_")[0];
    datasets = [
      { label: metric, data: d.groups.map(g => g[`${k}_med`]),
        backgroundColor:"#2c5f8acc" },
      { label: "p10",  data: d.groups.map(g => g[`${k}_p10`]),
        type:"line", borderColor:"#888", borderDash:[3,3], pointRadius:2, showLine:false },
      { label: "p90",  data: d.groups.map(g => g[`${k}_p90`]),
        type:"line", borderColor:"#888", borderDash:[3,3], pointRadius:2, showLine:false },
    ];
  } else if (metric === "lat_med") {
    datasets = [
      { label:"lat median", data: d.groups.map(g => g.lat_med),
        backgroundColor:"#9b59b6cc" },
      { label:"p95", data: d.groups.map(g => g.lat_p95),
        type:"line", borderColor:"#a23636", pointRadius:3, showLine:false },
    ];
  } else {
    datasets = [{ label: metric, data: d.groups.map(g => g[metric]),
                  backgroundColor:"#2c5f8acc" }];
  }
  if (chartAgg) chartAgg.destroy();
  chartAgg = new Chart($("chartAgg").getContext("2d"), {
    type:"bar", data:{ labels, datasets },
    options:{ animation:false, responsive:true, maintainAspectRatio:false,
              plugins:{ tooltip:{ mode:"index", intersect:false } },
              scales: metric === "pdr_pct" ? { y:{ min:0, max:100 } } : {} },
  });
  const cols = ["group","total","ok","pdr_pct","pdr_ci_low_pct","pdr_ci_high_pct",
                "rssi_med","rssi_p10","rssi_p90","snr_med","lat_med","lat_p95",
                "avg_attempts","retry_rate_pct","throughput_bps",
                "longest_fail_streak","fail_reason_top","last_hop_ok_top"];
  let html = `<table><thead><tr>${cols.map(c=>`<th>${c}</th>`).join("")}</tr></thead><tbody>`;
  for (const g of d.groups) {
    html += "<tr>" + cols.map(c => {
      const v = g[c];
      if (v == null) return "<td>–</td>";
      if (typeof v === "number" && !Number.isInteger(v)) return `<td>${v.toFixed(2)}</td>`;
      return `<td>${v}</td>`;
    }).join("") + "</tr>";
  }
  html += "</tbody></table>";
  $("anaTable").innerHTML = html;
  $("anaTable").dataset.csv = cols.join(",") + "\n" + d.groups.map(g =>
    cols.map(c => g[c] == null ? "" : g[c]).join(",")).join("\n");
}
$("anaApply").onclick = runAggregate;
$("anaMetric").onchange = () => { if ($("anaTable").innerHTML) runAggregate(); };
$("anaGroup").onchange  = () => { if ($("anaTable").innerHTML) runAggregate(); };
$("anaExportCsv").onclick = () => {
  const csv = $("anaTable").dataset.csv;
  if (!csv) return alert("Erst Auswertung laden.");
  const blob = new Blob([csv], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `aggregate_${$("anaGroup").value}_${Date.now()}.csv`;
  a.click(); URL.revokeObjectURL(a.href);
};

// ---- Heatmap -------------------------------------------------------
function colorFor(v, lo, hi, metric) {
  if (v == null || isNaN(v)) return "#444";
  const t = (v - lo) / (hi - lo || 1);
  // Bei Latenz ist klein = gut, also Skala umdrehen.
  const tt = Math.max(0, Math.min(1, metric === "lat" ? 1 - t : t));
  const stops = [[0xa2,0x36,0x36],[0xc9,0x7a,0x3a],[0xd8,0xd0,0x40],
                 [0x6a,0xb0,0x4c],[0x3b,0x7a,0x1e]];
  const f = tt * (stops.length - 1);
  const i = Math.floor(f), frac = f - i;
  const a = stops[i], b = stops[Math.min(stops.length - 1, i + 1)];
  const mix = a.map((ai, j) => Math.round(ai + (b[j] - ai) * frac));
  return `rgb(${mix.join(",")})`;
}
async function runHeatmap() {
  $("anaStatus").textContent = "lade Heatmap...";
  const body = {
    run_ids: selectedRunIds(),
    x: $("anaHmX").value, y: $("anaHmY").value, metric: $("anaHmMetric").value,
  };
  try {
    const r = await fetch("/api/analysis/heatmap", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    drawHeatmap(d);
    $("anaStatus").textContent = `Heatmap: ${d.x_labels.length}x${d.y_labels.length}`;
  } catch (e) { $("anaStatus").textContent = "Fehler: " + e.message; }
}
function htmlEl(html) {
  const t = document.createElement("template");
  t.innerHTML = html.trim();
  return t.content.firstChild;
}
function drawHeatmap(d) {
  const flat = d.cells.flat().filter(v => v != null && !isNaN(v));
  if (!flat.length) {
    $("hmGrid").innerHTML = "<i>Keine Daten.</i>";
    $("hmLegend").textContent = "";
    return;
  }
  const lo = Math.min(...flat), hi = Math.max(...flat);
  $("hmLegend").innerHTML = `<b>${d.metric}</b> &nbsp; ${lo.toFixed(1)} `
    + `<span class="legend-bar"></span> ${hi.toFixed(1)}`;
  const grid = $("hmGrid");
  grid.className = "heatmap";
  grid.style.gridTemplateColumns = `auto repeat(${d.x_labels.length}, minmax(34px, 1fr))`;
  grid.innerHTML = "";
  grid.appendChild(htmlEl(`<div class="cell lbl">${d.y}\\${d.x}</div>`));
  d.x_labels.forEach(x => grid.appendChild(htmlEl(`<div class="cell lbl">${x}</div>`)));
  d.y_labels.forEach((y, i) => {
    grid.appendChild(htmlEl(`<div class="cell lbl">${y}</div>`));
    d.cells[i].forEach(v => {
      const txt = v == null ? "" : (Number.isInteger(v) ? v : v.toFixed(1));
      const bg = colorFor(v, lo, hi, d.metric);
      const tip = v == null ? "" : `${d.metric}=${txt}`;
      grid.appendChild(htmlEl(
        `<div class="cell" style="background:${bg}" title="${tip}">${txt}</div>`));
    });
  });
}
$("anaHmGo").onclick = runHeatmap;

// ---- Timeline ------------------------------------------------------
async function runTimeline() {
  $("anaStatus").textContent = "lade Timeline...";
  const body = {
    run_ids: selectedRunIds(),
    bucket_s: parseInt($("anaTlBucket").value, 10),
    metric: $("anaTlMetric").value,
  };
  try {
    const r = await fetch("/api/analysis/timeline", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    if (chartTimeline) chartTimeline.destroy();
    const colors = ["#2c5f8a","#3b7a1e","#a23636","#e8833a","#9b59b6","#16a085","#c0392b"];
    const datasets = d.runs.map((s, i) => ({
      label: `run ${s.run_id}`,
      data: s.ts.map((t, j) => ({ x: t, y: s.value[j] })),
      borderColor: colors[i % colors.length],
      backgroundColor: colors[i % colors.length] + "33",
      tension: 0.2, pointRadius: 1,
    }));
    chartTimeline = new Chart($("chartTimeline").getContext("2d"), {
      type: "line",
      data: { datasets },
      options: { animation:false, responsive:true, maintainAspectRatio:false,
                 parsing: false,
                 scales: { x: { type: "category" },
                           y: d.metric === "pdr" ? { min:0, max:100 } : {} } },
    });
    $("anaStatus").textContent = `Timeline: ${d.runs.length} Run(s).`;
  } catch (e) { $("anaStatus").textContent = "Fehler: " + e.message; }
}
$("anaTlGo").onclick = runTimeline;

// ---- Verteilung (Boxplot + CDF) ------------------------------------
async function runDistribution() {
  $("anaStatus").textContent = "lade Verteilung...";
  const body = {
    run_ids: selectedRunIds(),
    group_by: $("anaDistGroup").value,
    metric: $("anaDistMetric").value,
  };
  try {
    const r = await fetch("/api/analysis/distribution", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    drawBox(d); drawCdf(d);
    $("anaStatus").textContent = `${d.groups.length} Gruppen.`;
  } catch (e) { $("anaStatus").textContent = "Fehler: " + e.message; }
}
function drawBox(d) {
  // Ersatz-Boxplot in Standard-Chart.js: floating bar p25..p75,
  // Median als Punkt, Whisker p10/p90 als zusaetzliche Punkte.
  const labels = d.groups.map(g => String(g.group));
  if (chartBox) chartBox.destroy();
  const datasets = [
    { label:"p25-p75", type:"bar",
      data: d.groups.map(g => [g.p25, g.p75]),
      backgroundColor:"#2c5f8acc", borderColor:"#2c5f8a",
      borderWidth:1, borderSkipped:false },
    { label:"median", type:"line",
      data: d.groups.map(g => g.p50),
      borderColor:"#000", borderWidth:0, pointRadius:6,
      pointBackgroundColor:"#fff", pointBorderColor:"#000", pointBorderWidth:2,
      showLine:false },
    { label:"p10", type:"line", data: d.groups.map(g => g.p10),
      borderColor:"#666", pointRadius:3, showLine:false },
    { label:"p90", type:"line", data: d.groups.map(g => g.p90),
      borderColor:"#666", pointRadius:3, showLine:false },
  ];
  chartBox = new Chart($("chartBox").getContext("2d"), {
    type: "bar", data:{ labels, datasets },
    options:{ animation:false, responsive:true, maintainAspectRatio:false,
              plugins:{ tooltip:{ mode:"index", intersect:false } } },
  });
}
function drawCdf(d) {
  if (chartCdf) chartCdf.destroy();
  const colors = ["#2c5f8a","#3b7a1e","#a23636","#e8833a","#9b59b6","#16a085"];
  const datasets = d.groups.map((g, i) => ({
    label: String(g.group),
    data: g.cdf_samples.map((v, j) => ({ x: v, y: 100 * (j+1) / g.cdf_samples.length })),
    borderColor: colors[i % colors.length], pointRadius: 0, tension: 0,
  }));
  chartCdf = new Chart($("chartCdf").getContext("2d"), {
    type: "line", data: { datasets },
    options: { animation:false, responsive:true, maintainAspectRatio:false,
               parsing: false,
               scales: { x: { type: "linear", title:{ display:true, text: d.metric } },
                         y: { min:0, max:100, title:{ display:true, text: "CDF %" } } } },
  });
}
$("anaDistGo").onclick = runDistribution;

// ---- Distanz vs. RSSI ----------------------------------------------
async function runScatter() {
  $("anaStatus").textContent = "lade Scatter...";
  const body = { ...commonFilters(), group_by: "dst" };
  try {
    const r = await fetch("/api/analysis/scatter_distance", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body)
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    if (chartScatter) chartScatter.destroy();
    const byHops = new Map();
    for (const p of d.points) {
      const k = p.hops || 0;
      if (!byHops.has(k)) byHops.set(k, []);
      byHops.get(k).push({ x: p.distance_m, y: p.rssi_avg,
        label: `run${p.run_id} dst${p.dst} (${p.label||""}) n=${p.n}` });
    }
    const colors = ["#2c5f8a","#3b7a1e","#a23636","#e8833a","#9b59b6"];
    const datasets = [...byHops.entries()].sort((a,b)=>a[0]-b[0]).map(([h, pts], i) => ({
      label: `${h} hops`,
      data: pts, borderColor: colors[i % colors.length],
      backgroundColor: colors[i % colors.length] + "aa",
      pointRadius: 5,
    }));
    chartScatter = new Chart($("chartScatter").getContext("2d"), {
      type:"scatter", data:{ datasets },
      options:{ animation:false, responsive:true, maintainAspectRatio:false,
                parsing: false,
                plugins:{ tooltip:{ callbacks:{
                  label: c => c.raw.label + ` -> ${c.raw.x} m, RSSI ${c.raw.y}` } } },
                scales:{ x:{ title:{ display:true, text:"Distanz (m)" } },
                         y:{ title:{ display:true, text:"RSSI dBm" } } } },
    });
    $("anaStatus").textContent = `Scatter: ${d.points.length} Punkte.`;
  } catch (e) { $("anaStatus").textContent = "Fehler: " + e.message; }
}
$("anaScatGo").onclick = runScatter;
