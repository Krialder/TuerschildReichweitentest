// Flash-Tab + Topologie-Wizard.
// Liefert: loadPorts, loadEnvs, startFlash, plus den kompletten Wizard-Flow.

const logFlash = (txt, color) => {
  const div = document.createElement("div");
  if (color) div.style.color = color;
  div.textContent = txt;
  $("flashLog").appendChild(div);
  $("flashLog").scrollTop = $("flashLog").scrollHeight;
};

// ---------------------------------------------------------------
// Port-Cache + Helper: behalten der Auswahl ueber Refreshs hinweg,
// inklusive Auto-Refresh wenn sich die Geraeteliste aendert.
// ---------------------------------------------------------------
let _portsCache = [];     // letzte vom Server gemeldete Ports
let _portsSig   = "";     // Signatur (zum Aenderungs-Erkennen)

// Persistenter Vorschlag fuer den Sender-Port (Run-Setup).
// Wird gesetzt sobald im Wizard ein Port fuer die Sender-Zeile gewaehlt
// oder ein manuelles Flashen einer "sender"-Env gestartet wird.
const SENDER_PORT_KEY = "rangetest.senderPort";
function _saveSenderPort(port) {
  if (port) try { localStorage.setItem(SENDER_PORT_KEY, port); } catch {}
}
function _loadSenderPort() {
  try { return localStorage.getItem(SENDER_PORT_KEY) || ""; } catch { return ""; }
}
function _envIsSender(env) {
  if (!env) return false;
  if (wizPlan) {
    const n = wizPlan.nodes.find(x => x.env === env);
    if (n) return n.role === "sender";
  }
  // Fallback: Namenskonvention der Wizard-Envs (auto_sender) bzw. "sender".
  return /sender/i.test(env);
}

function _selValues(sel) {
  if (sel.multiple) return Array.from(sel.selectedOptions).map(o => o.value);
  return sel.value;
}
function _selSetValues(sel, val) {
  if (sel.multiple) {
    const want = new Set(val || []);
    Array.from(sel.options).forEach(o => { o.selected = want.has(o.value); });
  } else if (val != null && Array.from(sel.options).some(o => o.value === val)) {
    sel.value = val;
  }
}
// Befuellt ein <select> mit Ports und stellt die vorherige Auswahl wieder her.
// Wenn der zuvor gewaehlte Port nicht mehr vorhanden ist, wird er als
// "(getrennt)" trotzdem aufgelistet, damit die Auswahl visuell bestehen bleibt.
function fillPortSelect(sel, list, opts) {
  opts = opts || {};
  const prev = _selValues(sel);
  const presetValue = opts.preset != null ? opts.preset : prev;  // optional erzwingen
  sel.innerHTML = "";
  if (opts.includeBlank) {
    const b = document.createElement("option");
    b.value = ""; b.textContent = opts.blankText || "(Port wählen)";
    sel.appendChild(b);
  }
  if (!list.length && !opts.includeBlank) {
    const o = document.createElement("option");
    o.value = ""; o.textContent = "(keine Ports)";
    sel.appendChild(o);
  }
  for (const p of list) {
    const o = document.createElement("option");
    o.value = p.device; o.textContent = `${p.device}  ${p.description}`;
    sel.appendChild(o);
  }
  // Vorher gewaehlte, aber jetzt fehlende Ports als "(getrennt)" anhaengen
  const want = sel.multiple
    ? (Array.isArray(presetValue) ? presetValue : [])
    : (presetValue ? [presetValue] : []);
  for (const v of want) {
    if (v && !Array.from(sel.options).some(o => o.value === v)) {
      const o = document.createElement("option");
      o.value = v; o.textContent = `${v}  (getrennt)`;
      o.style.color = "#a23636";
      sel.appendChild(o);
    }
  }
  _selSetValues(sel, presetValue);
}

function _portsSignature(list) {
  return list.map(p => p.device + "|" + (p.description || "")).join(";");
}

// Liest /api/ports, aktualisiert den Cache und alle bekannten Port-Selects,
// behaelt dabei die Auswahl (auch wenn Geraete weggefallen sind).
async function refreshPortsFromServer(force) {
  let list;
  try { list = await (await fetch("/api/ports")).json(); }
  catch (e) { return; }
  const sig = _portsSignature(list);
  if (!force && sig === _portsSig) return;
  _portsSig = sig;
  _portsCache = list;
  // Standard-Selects
  if ($("port"))       fillPortSelect($("port"),       list);
  if ($("runPort")) {
    const cur = $("runPort").value;
    // Wenn noch nichts ausgewaehlt ist, den zuletzt gemerkten Sender-Port
    // als Default vorschlagen (User kann jederzeit umstellen).
    const preset = cur || _loadSenderPort();
    fillPortSelect($("runPort"), list, preset ? { preset } : undefined);
  }
  if ($("runRxPorts")) fillPortSelect($("runRxPorts"), list);
  // Wizard-Reihen
  document.querySelectorAll(".wiz-port").forEach(sel => {
    const env = sel.dataset.env;
    const preset = wizPortSel.get(env);
    fillPortSelect(sel, list, { includeBlank: true, preset });
  });
  if ($("portHint")) {
    $("portHint").textContent = list.length
      ? list.length + " Port(s) gefunden."
      : "Stecke ein ESP32-Board an und drücke ↻.";
  }
}

async function loadPorts() {
  await refreshPortsFromServer(true);
}

async function loadEnvs() {
  const sel = $("env"); sel.innerHTML = "";
  try {
    const list = await (await fetch("/api/envs")).json();
    for (const e of list) {
      const o = document.createElement("option");
      const ch = e.channel != null ? ` ch${e.channel}` : "";
      const lr = e.lr ? " LR" : "";
      const ch2 = e.chain ? " [chain]" : "";
      o.value = e.name;
      o.textContent = `${e.name}    ${e.role}/id=${e.node_id ?? "?"}${ch}${lr}${ch2}`;
      sel.appendChild(o);
    }
    $("envHint").textContent = list.length + " Build-Env(s).";
  } catch (e) { $("envHint").textContent = "Fehler: " + e; }
}

function startFlash() {
  const env = $("env").value, port = $("port").value;
  if (!env || !port) return alert("Port und Env wählen.");
  if (_envIsSender(env)) _saveSenderPort(port);
  $("flashLog").innerHTML = "";
  $("btnFlash").disabled = true;
  $("flashStatusManual").textContent = "verbinde...";
  const ws = new WebSocket((location.protocol === "https:" ? "wss" : "ws")
                            + "://" + location.host + "/ws/flash");
  ws.onopen = () => {
    ws.send(JSON.stringify({ env, port }));
    $("flashStatusManual").textContent = "läuft...";
  };
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { logFlash(ev.data); return; }
    if (m.type === "start") logFlash("$ " + m.cmd, "#7af");
    else if (m.type === "log") logFlash(m.line);
    else if (m.type === "done") {
      logFlash(`-- exit ${m.returncode} --`, m.returncode === 0 ? "#7f7" : "#f77");
      $("flashStatusManual").textContent = m.returncode === 0 ? "fertig ✓" : "Fehler";
    } else if (m.type === "error") {
      logFlash("ERROR: " + m.msg, "#f77");
      $("flashStatusManual").textContent = "Fehler";
    }
  };
  ws.onclose = () => { $("btnFlash").disabled = false; };
}

$("btnFlash").onclick       = startFlash;
$("btnReloadPorts").onclick = loadPorts;
$("btnReloadEnvs").onclick  = loadEnvs;

// ===================================================================
// Topologie-Wizard
// ===================================================================
let wizPlan = null;          // letzter Plan vom Server
const wizState   = new Map();  // env -> "pending"|"flashing"|"done"|"error"
const wizPortSel = new Map();  // env -> zuletzt gewaehlter COM-Port (bleibt erhalten)

function roleEmoji(role) {
  return role === "sender" ? "📡" : role === "relay" ? "🔁" : "🎯";
}
function maskHex(m) { return "0x" + m.toString(16).toUpperCase().padStart(8, "0"); }

async function wizardGenerate() {
  const body = {
    x_relays: parseInt($("wizX").value, 10),
    y_chains: parseInt($("wizY").value, 10),
    channel:  parseInt($("wizCh").value, 10),
    lr:       parseInt($("wizLr").value, 10),
  };
  $("wizStatus").textContent = "generiere Plan...";
  try {
    const r = await fetch("/api/topology/plan", {
      method:"POST", headers:{"Content-Type":"application/json"},
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    wizPlan = d;
    wizState.clear();
    wizPortSel.clear();
    for (const n of d.nodes) wizState.set(n.env, "pending");
    renderWizPlan();
    await loadEnvs();
    $("wizStatus").textContent =
      `Plan: ${d.total_nodes} Knoten (max ID ${d.max_id}). `
      + `Empfänger-IDs: ${d.receiver_ids.join(", ")}.`;
  } catch (e) {
    $("wizStatus").textContent = "Fehler: " + e.message;
  }
}

async function wizardClear() {
  if (!confirm("AUTO-Block aus platformio.ini entfernen?")) return;
  $("wizStatus").textContent = "lösche...";
  const r = await fetch("/api/topology/plan", { method: "DELETE" });
  if (r.ok) {
    wizPlan = null; wizState.clear(); wizPortSel.clear();
    $("wizPlanBox").innerHTML = "";
    $("wizStatus").textContent = "AUTO-Block entfernt.";
    await loadEnvs();
  } else {
    $("wizStatus").textContent = "Fehler beim Löschen.";
  }
}

function wizStatusHtml(st) {
  if (st === "done")     return '<span style="color:#3b7a1e;font-weight:600;">✓ ok</span>';
  if (st === "flashing") return '<span style="color:#2c5f8a;">… läuft</span>';
  if (st === "error")    return '<span style="color:#a23636;font-weight:600;">✗ Fehler</span>';
  return '<span class="hint">pending</span>';
}

function renderWizPlan() {
  if (!wizPlan) { $("wizPlanBox").innerHTML = ""; return; }
  let activeIdx = wizPlan.nodes.findIndex(n => wizState.get(n.env) === "pending");
  if (activeIdx < 0) activeIdx = wizPlan.nodes.length;
  let html = `<table class="wiz-tbl"><thead><tr>
    <th style="width:3rem;">#</th><th>Knoten</th><th>NODE_ID</th>
    <th>prev_mask</th><th>env</th><th style="min-width:180px;">Port</th>
    <th>Status</th><th></th></tr></thead><tbody>`;
  wizPlan.nodes.forEach((n, i) => {
    const st = wizState.get(n.env) || "pending";
    const cls = i === activeIdx ? "wiz-active" : "";
    html += `<tr class="${cls}" data-env="${n.env}">
      <td>${n.step}</td>
      <td>${roleEmoji(n.role)} <b>${n.role}</b><br>
          <small>${n.label}</small><br>
          <small class="hint">${n.instruction}</small></td>
      <td><b>${n.id}</b></td>
      <td><code>${maskHex(n.prev_mask)}</code><br>
          <small>nb: ${n.neighbors.join(", ")}</small></td>
      <td><code>${n.env}</code></td>
      <td><select class="wiz-port" data-env="${n.env}"></select></td>
      <td class="wiz-st">${wizStatusHtml(st)}</td>
      <td><button type="button" class="wiz-flash"
                  data-env="${n.env}" data-id="${n.id}"
                  ${st === "flashing" ? "disabled" : ""}>Flash</button></td>
    </tr>`;
  });
  html += `</tbody></table>
    <p style="margin-top:.6rem; font-size:.85rem;">
      <b>Anleitung:</b> Stecke <i>ein</i> ESP32 an, wähle dessen COM-Port in
      der aktiven (markierten) Zeile, und klicke <b>Flash</b>. Wiederhole für
      jeden Knoten in der angegebenen Reihenfolge. Jedes Board bekommt
      eine andere NODE_ID; bitte die Boards entsprechend labeln.
      Die Reihenfolge ist beliebig; die Hervorhebung zeigt nur den nächsten
      noch ungeflashten Knoten zur Orientierung.
    </p>`;
  if (activeIdx >= wizPlan.nodes.length) {
    html += `<p style="color:#3b7a1e; font-weight:600;">
      ✓ Alle Knoten geflasht. Du kannst jetzt im Run-Setup-Tab die Empfänger
      (dst = ${wizPlan.receiver_ids.join(", ")}) eintragen und einen Run
      starten.</p>`;
  }
  $("wizPlanBox").innerHTML = html;
  // Port-Dropdowns aus dem Cache fuellen und vorher gewaehlten Port wiederherstellen.
  // Falls Cache leer ist (allererster Aufruf), einmal nachladen.
  const fillWizPorts = (list) => {
    document.querySelectorAll(".wiz-port").forEach(sel => {
      const env = sel.dataset.env;
      fillPortSelect(sel, list, {
        includeBlank: true,
        preset: wizPortSel.get(env),
      });
      sel.onchange = () => {
        if (sel.value) {
          wizPortSel.set(env, sel.value);
          if (_envIsSender(env)) {
            _saveSenderPort(sel.value);
            // Direkt auch das Run-Setup-Dropdown aktualisieren, falls leer.
            const rp = $("runPort");
            if (rp && !rp.value) {
              fillPortSelect(rp, _portsCache, { preset: sel.value });
            }
          }
        } else {
          wizPortSel.delete(env);
        }
      };
    });
  };
  if (_portsCache.length === 0 && _portsSig === "") {
    fetch("/api/ports").then(r => r.json()).then(list => {
      _portsCache = list; _portsSig = _portsSignature(list);
      fillWizPorts(list);
    });
  } else {
    fillWizPorts(_portsCache);
  }
  document.querySelectorAll(".wiz-flash").forEach(b =>
    b.onclick = () => wizardFlashOne(b.dataset.env));
}

function wizardFlashOne(env) {
  const node = wizPlan.nodes.find(n => n.env === env);
  const portSel = document.querySelector(`.wiz-port[data-env="${env}"]`);
  const port = portSel ? portSel.value : "";
  if (!port) { alert("Bitte einen COM-Port für diesen Knoten wählen."); return; }
  wizPortSel.set(env, port);   // Auswahl merken (ueberlebt Re-Renders)
  if (_envIsSender(env)) _saveSenderPort(port);
  if (wizState.get(env) === "flashing") return;
  wizState.set(env, "flashing");
  renderWizPlan();
  $("flashLog").innerHTML = "";
  $("flashStatus").textContent = `Wizard: flashe ${env} (NODE_ID=${node.id}) an ${port}…`;
  const ws = new WebSocket((location.protocol === "https:" ? "wss" : "ws")
                            + "://" + location.host + "/ws/flash");
  ws.onopen = () => ws.send(JSON.stringify({ env, port }));
  ws.onmessage = (ev) => {
    let m; try { m = JSON.parse(ev.data); } catch { logFlash(ev.data); return; }
    if (m.type === "start") logFlash("$ " + m.cmd, "#7af");
    else if (m.type === "log") logFlash(m.line);
    else if (m.type === "done") {
      const ok = m.returncode === 0;
      logFlash(`-- exit ${m.returncode} --`, ok ? "#7f7" : "#f77");
      wizState.set(env, ok ? "done" : "error");
      $("flashStatus").textContent = ok
        ? `${env} ✓ (NODE_ID=${node.id})`
        : `${env} ✗  siehe Log`;
      renderWizPlan();
    } else if (m.type === "error") {
      logFlash("ERROR: " + m.msg, "#f77");
      wizState.set(env, "error");
      renderWizPlan();
    }
  };
  ws.onclose = () => {
    if (wizState.get(env) === "flashing") {
      wizState.set(env, "error");
      renderWizPlan();
    }
  };
}

$("wizPlan").onclick  = wizardGenerate;
$("wizClear").onclick = wizardClear;
if ($("wizScanPorts")) $("wizScanPorts").onclick = () => refreshPortsFromServer(true);
