// Live-Charts und Start/Stop des Runs.
// Run-Setup (Targets, Profil, Wizard) liegt in runsetup.js.
// Diese Datei kuemmert sich nur um die laufenden Charts und den
// WebSocket /ws/live.

// ---------- Start / Stop ----------
async function startRun() {
  const body = window.buildRunPayload();
  if (!body.port) return alert("Sender-Port waehlen (Schritt 3).");
  if (!body.targets.length) return alert("Mindestens ein Target (Schritt 1).");
  $("startMsg").textContent = "starte...";
  try {
    const r = await fetch("/api/run/start", {
      method: "POST", headers: {"Content-Type": "application/json"},
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok) throw new Error(j.detail || r.statusText);
    $("startMsg").textContent = "laeuft";
    resetCharts();
    document.querySelector('.tabs button[data-tab=live]').click();
  } catch (e) { $("startMsg").textContent = "Fehler: " + e.message; }
}
async function stopRun() {
  $("startMsg").textContent = "stoppe...";
  await fetch("/api/run/stop", { method: "POST" });
  $("startMsg").textContent = "gestoppt";
}

$("btnStart").onclick = startRun;
$("btnStop").onclick  = stopRun;

// Zusaetzlicher Stop-Button im Live-Tab (spiegelt #btnStop).
if ($("btnStopLive")) {
  $("btnStopLive").onclick = async () => {
    $("liveStopMsg").textContent = "stoppe...";
    try {
      await fetch("/api/run/stop", { method: "POST" });
      $("liveStopMsg").textContent = "gestoppt";
    } catch (e) {
      $("liveStopMsg").textContent = "Fehler: " + e.message;
    }
  };
}

// ---------- Live-Charts ----------
const WIN = 60;
let chartPdr, chartRssi, chartLat, chartSize;
let resultRing = []; const sizeStats = new Map();

function makeLine(canvasId, datasets, yOpts={}) {
  return new Chart($(canvasId).getContext("2d"), {
    type: "line", data: { labels: [], datasets },
    options: { animation:false, responsive:true, maintainAspectRatio:false,
      scales: { y: yOpts }, plugins: { legend:{ display: datasets.length > 1 } },
      elements: { point:{ radius: 0 }, line:{ borderWidth: 1.5 } } },
  });
}
function makeBar(canvasId, color="#6ab04c") {
  return new Chart($(canvasId).getContext("2d"), {
    type: "bar", data: { labels: [], datasets:
      [{ label:"PDR %", data:[], backgroundColor:color }] },
    options: { animation:false, responsive:true, maintainAspectRatio:false,
      scales: { y:{ min:0, max:100 } }, plugins: { legend:{display:false} } },
  });
}
function resetCharts() {
  resultRing = []; sizeStats.clear();
  if (chartPdr)  chartPdr.destroy();
  if (chartRssi) chartRssi.destroy();
  if (chartLat)  chartLat.destroy();
  if (chartSize) chartSize.destroy();
  chartPdr = makeLine("chartPdr", [
    { label:"PDR %", data:[], borderColor:"#3b7a1e",
      backgroundColor:"#3b7a1e22", fill:true }
  ], { min:0, max:100 });
  chartRssi = makeLine("chartRssi", [
    { label:"RSSI dBm", data:[], borderColor:"#2c5f8a" },
    { label:"SNR dB",   data:[], borderColor:"#e8833a", yAxisID:"y1" },
  ]);
  chartRssi.options.scales.y1 = { position:"right", grid:{ drawOnChartArea:false } };
  chartLat  = makeLine("chartLat", [
    { label:"lat ms (median)", data:[], borderColor:"#9b59b6" }
  ], { beginAtZero:true });
  chartSize = makeBar("chartSize");
  $("consoleLog").innerHTML = "";
}
function median(a) {
  const s = [...a].sort((x,y) => x-y);
  const m = Math.floor(s.length/2);
  return s.length%2 ? s[m] : (s[m-1]+s[m])/2;
}
function pushPoint(chart, label, t, val) {
  if (val == null || isNaN(val)) return;
  const ds = chart.data.datasets.find(d => d.label === label);
  if (!ds) return;
  if (chart.data.labels.length === 0 || chart.data.labels.at(-1) !== t) {
    chart.data.labels.push(t);
    if (chart.data.labels.length > WIN) chart.data.labels.shift();
  }
  ds.data.push(val);
  if (ds.data.length > WIN) ds.data.shift();
  chart.update("none");
}
function pushResult(m) {
  const ts = new Date().toLocaleTimeString();
  resultRing.push({
    ok: m.success ? 1 : 0,
    rssi: m.success ? m.rssi_remote : null,
    snr:  m.success ? m.snr_remote  : null,
    lat:  m.success ? m.latency_ms  : null,
    size: m.size,
  });
  if (resultRing.length > WIN) resultRing.shift();
  const ok = resultRing.reduce((a,r)=>a+r.ok, 0);
  pushPoint(chartPdr, "PDR %", ts, 100*ok/resultRing.length);
  const rssis = resultRing.filter(r => r.rssi != null).map(r => r.rssi);
  const snrs  = resultRing.filter(r => r.snr  != null).map(r => r.snr);
  const lats  = resultRing.filter(r => r.lat  != null).map(r => r.lat);
  if (rssis.length) pushPoint(chartRssi, "RSSI dBm", ts, median(rssis));
  if (snrs.length)  pushPoint(chartRssi, "SNR dB",   ts, median(snrs));
  if (lats.length)  pushPoint(chartLat,  "lat ms (median)", ts, median(lats));
  const ent = sizeStats.get(m.size) || {ok:0,total:0};
  ent.total += 1; if (m.success) ent.ok += 1;
  sizeStats.set(m.size, ent);
  const sizes = Array.from(sizeStats.keys()).sort((a,b)=>a-b);
  chartSize.data.labels = sizes.map(String);
  chartSize.data.datasets[0].data = sizes.map(s => {
    const e = sizeStats.get(s); return e.total ? 100*e.ok/e.total : 0;
  });
  chartSize.update("none");
}
function consoleLog(txt, color) {
  const d = document.createElement("div");
  if (color) d.style.color = color;
  d.textContent = txt;
  const box = $("consoleLog"); box.appendChild(d);
  while (box.childNodes.length > 200) box.removeChild(box.firstChild);
  box.scrollTop = box.scrollHeight;
}
function setLiveBadge(s) {
  const b = $("liveBadge");
  if (s === "running") { b.className = "badge live"; b.textContent = "running"; }
  else if (s === "error") { b.className = "badge err"; b.textContent = "error"; }
  else { b.className = "badge idle"; b.textContent = s || "idle"; }
}
function updateKpis(c, elapsed, runId) {
  if (runId != null) $("kRun").textContent = runId;
  if (elapsed != null) {
    const h = Math.floor(elapsed/3600), m = Math.floor((elapsed%3600)/60), s = elapsed%60;
    $("kElapsed").textContent = (h?h+"h ":"") + (m?m+"m ":"") + s + "s";
  }
  if (c) {
    $("kSent").textContent = c.sent || 0;
    $("kOk").textContent   = c.ok   || 0;
    $("kFail").textContent = c.fail || 0;
    $("kPdr").textContent  = c.sent ? (100*c.ok/c.sent).toFixed(1)+"%" : "–";
  }
}
let liveWs;
function connectLive() {
  liveWs = new WebSocket((location.protocol==="https:"?"wss":"ws")
                          + "://" + location.host + "/ws/live");
  liveWs.onmessage = (ev) => {
    const m = JSON.parse(ev.data);
    if (m.type === "hello") {
      const st = m.status || {};
      setLiveBadge(st.status); updateKpis(st.counters, st.elapsed_s, st.run_id);
      $("btnStop").disabled  = !st.running; $("btnStart").disabled =  st.running;
      if ($("btnStopLive")) $("btnStopLive").disabled = !st.running;
    }
    if (m.type === "status") setLiveBadge(m.status);
    if (m.type === "run_started") {
      consoleLog(`▶ run ${m.run_id} started  fw=${m.fw}`, "#7af");
      $("kRun").textContent = m.run_id;
      $("btnStop").disabled = false; $("btnStart").disabled = true;
      if ($("btnStopLive")) $("btnStopLive").disabled = false;
      if ($("liveStopMsg")) $("liveStopMsg").textContent = "";
      setLiveBadge("running");
    }
    if (m.type === "run_ended") {
      consoleLog(`■ run ${m.run_id} ended`, "#fa7"); setLiveBadge("idle");
      $("btnStop").disabled = true; $("btnStart").disabled = false;
      if ($("btnStopLive")) $("btnStopLive").disabled = true;
    }
    if (m.type === "result") pushResult(m);
    if (m.type === "bucket") {
      consoleLog(`bucket ${m.bucket_ts}  dst=${m.dst} sz=${m.size}  ${m.ok}/${m.total}`
                  + (m.rssi_med != null ? `  rssi=${m.rssi_med}` : ""));
    }
    if (m.type === "error") { consoleLog("ERROR: " + m.msg, "#f77"); setLiveBadge("error"); }
  };
  liveWs.onclose = () => setTimeout(connectLive, 1500);
}
// Status-Poll: schnell waehrend eines Runs, langsam im Idle
// (Live-Daten kommen primaer ueber /ws/live; das hier liefert nur die KPIs).
let _statusRunning = false;
async function pollStatus() {
  try {
    const s = await (await fetch("/api/run/status")).json();
    setLiveBadge(s.status); updateKpis(s.counters, s.elapsed_s, s.run_id);
    $("btnStop").disabled  = !s.running; $("btnStart").disabled =  s.running;
    if ($("btnStopLive")) $("btnStopLive").disabled = !s.running;
    _statusRunning = !!s.running;
  } catch {}
  setTimeout(pollStatus, _statusRunning ? 2000 : 10000);
}
pollStatus();
