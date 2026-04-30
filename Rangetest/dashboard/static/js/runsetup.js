// Run-Setup: 3-Schritte-Wizard (Targets -> Profil -> Start) plus
// Profil-Speicherung im localStorage.
//
// Verwendet die globale $()-Funktion aus app.js und ist nach app.js,
// vor live.js geladen, damit live.js bei DOMContentLoaded nur noch die
// Charts initialisiert.

(() => {
  const PROFILE_KEY = "rangetest.profiles.v1";

  // ---------- Targets-Tabelle ----------
  function addTargetRow(t) {
    t = t || { id: "", label: "", distance_m: "", walls: "", notes: "" };
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><input type="number" data-k="id"         value="${t.id ?? ""}"></td>
      <td><input type="text"   data-k="label"      value="${t.label || ""}"></td>
      <td><input type="number" data-k="distance_m" value="${t.distance_m ?? ""}" step="0.1" placeholder="opt."></td>
      <td><input type="number" data-k="walls"      value="${t.walls ?? ""}" placeholder="opt."></td>
      <td><input type="text"   data-k="notes"      value="${t.notes || ""}"></td>
      <td><button type="button" title="Zeile entfernen">x</button></td>`;
    tr.querySelector("button").onclick = () => tr.remove();
    $("targetsTbl").appendChild(tr);
  }
  // Wird auch von app.js / live.js erwartet.
  window.addTargetRow = addTargetRow;

  function clearTargets() {
    $("targetsTbl").innerHTML = "";
  }

  function readTargets() {
    const out = [];
    for (const tr of $("targetsTbl").querySelectorAll("tr")) {
      const t = {};
      for (const inp of tr.querySelectorAll("input")) {
        const k = inp.dataset.k, v = inp.value.trim();
        if (v === "") continue;
        if (k === "id" || k === "walls") t[k] = parseInt(v, 10);
        else if (k === "distance_m")     t[k] = parseFloat(v);
        else                              t[k] = v;
      }
      if (t.id != null && !isNaN(t.id)) out.push(t);
    }
    return out;
  }
  window.readTargets = readTargets;

  // ---------- Step-Indikator ----------
  function showStep(n) {
    document.querySelectorAll(".setup-steps .step").forEach(s => {
      const sn = parseInt(s.dataset.step, 10);
      s.classList.toggle("active", sn === n);
      s.classList.toggle("done",   sn  <  n);
    });
    document.querySelectorAll(".setup-pane").forEach(p => {
      p.style.display = (parseInt(p.dataset.step, 10) === n) ? "" : "none";
    });
    if (n === 3) refreshSummary();
  }

  document.querySelectorAll(".setup-steps .step").forEach(s => {
    s.onclick = () => showStep(parseInt(s.dataset.step, 10));
  });
  document.querySelectorAll("[data-go-step]").forEach(b => {
    b.onclick = () => showStep(parseInt(b.dataset.goStep, 10));
  });

  // ---------- Targets aus Flash / letztem Run ----------
  async function targetsFromFlash() {
    $("targetsHint").textContent = "Lade Receiver aus platformio.ini...";
    try {
      const r = await fetch("/api/topology/receivers");
      const j = await r.json();
      const rxs = j.receivers || [];
      if (!rxs.length) {
        $("targetsHint").textContent =
          "Kein AUTO-Block in platformio.ini gefunden. Erst im Tab Flash "
          + "den Topologie-Wizard generieren lassen.";
        return;
      }
      clearTargets();
      for (const r of rxs) addTargetRow({ id: r.id, label: r.label });
      $("targetsHint").textContent =
        `${rxs.length} Receiver aus dem Flash-Plan uebernommen.`;
    } catch (e) {
      $("targetsHint").textContent = "Fehler: " + e.message;
    }
  }

  async function targetsFromLastRun() {
    $("targetsHint").textContent = "Lade letzten Run aus DB...";
    try {
      const r = await fetch("/api/run/last/profile");
      const j = await r.json();
      if (!j.ok) {
        $("targetsHint").textContent =
          j.reason === "empty" ? "Es gibt noch keinen Run in der DB."
                               : "Keine DB gefunden.";
        return;
      }
      clearTargets();
      for (const t of j.targets || []) addTargetRow(t);
      if (j.run_notes) $("cfgNotes").value = j.run_notes;
      $("targetsHint").textContent =
        `Targets aus Run #${j.run_id} uebernommen (`
        + `${(j.targets || []).length} Empfaenger).`;
    } catch (e) {
      $("targetsHint").textContent = "Fehler: " + e.message;
    }
  }

  // ---------- Paketgroessen-Modus-Umschalter ----------
  function applySizeMode() {
    const mode = $("cfgSizeMode").value;
    document.querySelectorAll("[data-size-mode]").forEach(el => {
      const modes = el.dataset.sizeMode.split(",");
      el.style.display = modes.includes(mode) ? "" : "none";
    });
  }
  $("cfgSizeMode").onchange = applySizeMode;

  // ---------- Dauer Custom-Eingabe ----------
  $("runDuration").onchange = () => {
    $("runDurationCustom").style.display =
      $("runDuration").value === "-1" ? "" : "none";
  };

  // ---------- Konfiguration zusammenbauen ----------
  function buildPayload() {
    const mode = $("cfgSizeMode").value;
    const body = {
      port: $("runPort").value,
      rx_ports: Array.from($("runRxPorts").selectedOptions).map(o => o.value),
      retry_limit:        parseInt($("cfgRetry").value,   10),
      timeout_ms:         parseInt($("cfgTimeout").value, 10),
      inter_send_ms:      parseInt($("cfgInter").value,   10),
      jitter_pct:         parseInt($("cfgJitter").value,  10),
      noisefloor_samples: parseInt($("cfgNoise").value,   10),
      run_notes:          $("cfgNotes").value,
      targets:            readTargets(),
    };
    let dur = $("runDuration").value;
    if (dur === "-1") dur = $("runDurationCustom").value || null;
    body.duration_s = dur ? parseInt(dur, 10) : null;

    if (mode === "mix") {
      body.size_mode = "list";
      body.packet_sizes = [32, 64, 128, 192, 240];
    } else if (mode === "list") {
      body.size_mode = "list";
      body.packet_sizes = $("cfgSizes").value.split(",")
        .map(s => parseInt(s.trim(), 10)).filter(n => !isNaN(n));
    } else if (mode === "range") {
      body.size_mode = "range";
      body.size_min  = parseInt($("cfgSizeMin").value,  10);
      body.size_max  = parseInt($("cfgSizeMax").value,  10);
      body.size_step = parseInt($("cfgSizeStep").value, 10);
    } else if (mode === "random") {
      body.size_mode = "random";
      body.size_min  = parseInt($("cfgSizeMin").value, 10);
      body.size_max  = parseInt($("cfgSizeMax").value, 10);
    }
    return body;
  }
  window.buildRunPayload = buildPayload;

  function refreshSummary() {
    const b = buildPayload();
    const sizeTxt =
      b.size_mode === "random" ? `zufaellig ${b.size_min}..${b.size_max} Byte`
    : b.size_mode === "range"  ? `${b.size_min}..${b.size_max} in ${b.size_step}-Schritten`
    : (b.packet_sizes || []).join(", ") + " Byte";
    const durTxt = b.duration_s ? `${b.duration_s} s` : "endlos (bis Stop)";
    const tgts = (b.targets || []).map(t => t.id).join(", ") || "(keine!)";
    $("setupSummary").innerHTML =
      `<dl>
         <dt>Empfaenger</dt><dd>${tgts}</dd>
         <dt>Paketgroessen</dt><dd>${sizeTxt}</dd>
         <dt>Dauer</dt><dd>${durTxt}</dd>
         <dt>Sender-Port</dt><dd>${b.port || "(nicht gewaehlt)"}</dd>
         <dt>RX-Ports</dt><dd>${(b.rx_ports || []).join(", ") || "-"}</dd>
       </dl>`;
  }

  // ---------- Profile (localStorage) ----------
  function loadProfileMap() {
    try { return JSON.parse(localStorage.getItem(PROFILE_KEY)) || {}; }
    catch { return {}; }
  }
  function saveProfileMap(m) {
    localStorage.setItem(PROFILE_KEY, JSON.stringify(m));
  }
  function refreshProfileList() {
    const sel = $("profSelect");
    const m = loadProfileMap();
    sel.innerHTML = "";
    const names = Object.keys(m).sort();
    if (!names.length) {
      const opt = document.createElement("option");
      opt.textContent = "(noch keine Profile gespeichert)";
      opt.disabled = true;
      sel.appendChild(opt);
      return;
    }
    for (const n of names) {
      const opt = document.createElement("option");
      opt.value = opt.textContent = n;
      sel.appendChild(opt);
    }
  }
  function applyProfile(p) {
    if (!p) return;
    if (p.targets) {
      clearTargets();
      for (const t of p.targets) addTargetRow(t);
    }
    if (p.size_mode) {
      // Backward: alte Profile speichern u. U. nur packet_sizes.
      const uiMode =
        p.size_mode === "list" && Array.isArray(p.packet_sizes)
        && p.packet_sizes.join(",") === "32,64,128,192,240"
          ? "mix" : p.size_mode;
      $("cfgSizeMode").value = uiMode;
    }
    if (p.packet_sizes) $("cfgSizes").value = p.packet_sizes.join(",");
    if (p.size_min  != null) $("cfgSizeMin").value  = p.size_min;
    if (p.size_max  != null) $("cfgSizeMax").value  = p.size_max;
    if (p.size_step != null) $("cfgSizeStep").value = p.size_step;
    if (p.duration_s != null) {
      const sel = $("runDuration");
      const known = Array.from(sel.options).some(o => o.value === String(p.duration_s));
      if (known) sel.value = String(p.duration_s);
      else { sel.value = "-1"; $("runDurationCustom").value = p.duration_s; }
    }
    if (p.retry_limit  != null) $("cfgRetry").value   = p.retry_limit;
    if (p.timeout_ms   != null) $("cfgTimeout").value = p.timeout_ms;
    if (p.inter_send_ms!= null) $("cfgInter").value   = p.inter_send_ms;
    if (p.jitter_pct   != null) $("cfgJitter").value  = p.jitter_pct;
    if (p.noisefloor_samples != null) $("cfgNoise").value = p.noisefloor_samples;
    if (p.run_notes != null) $("cfgNotes").value = p.run_notes;
    applySizeMode();
    $("runDuration").dispatchEvent(new Event("change"));
    $("profHint").textContent = "Profil geladen.";
  }

  $("btnProfLoad").onclick = () => {
    const name = $("profSelect").value;
    const m = loadProfileMap();
    if (!m[name]) { $("profHint").textContent = "Kein Profil ausgewaehlt."; return; }
    applyProfile(m[name]);
  };
  $("btnProfSave").onclick = () => {
    const name = prompt("Profil-Name:");
    if (!name) return;
    const m = loadProfileMap();
    if (m[name] && !confirm(`Profil "${name}" ueberschreiben?`)) return;
    m[name] = buildPayload();
    delete m[name].port;
    delete m[name].rx_ports;
    saveProfileMap(m);
    refreshProfileList();
    $("profSelect").value = name;
    $("profHint").textContent = `Profil "${name}" gespeichert.`;
  };
  $("btnProfDelete").onclick = () => {
    const name = $("profSelect").value;
    const m = loadProfileMap();
    if (!m[name]) return;
    if (!confirm(`Profil "${name}" loeschen?`)) return;
    delete m[name];
    saveProfileMap(m);
    refreshProfileList();
    $("profHint").textContent = `Profil "${name}" geloescht.`;
  };

  // ---------- Targets-Buttons ----------
  $("btnTargetsFromFlash").onclick   = targetsFromFlash;
  $("btnTargetsFromLastRun").onclick = targetsFromLastRun;
  $("btnAddTarget").onclick          = () => addTargetRow();
  $("btnTargetsClear").onclick       = clearTargets;

  // ---------- Initialer Zustand ----------
  applySizeMode();
  refreshProfileList();
  showStep(1);
})();
