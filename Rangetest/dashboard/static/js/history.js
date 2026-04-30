// History-Tab: Liste vergangener Runs + Detailansicht.

async function deleteRun(id) {
  if (!confirm(`Run #${id} wirklich loeschen?\n\n` +
               `Alle Pakete, RX-Events, Noise-Samples und Buckets ` +
               `dieses Runs werden aus der DB entfernt. ` +
               `Das laesst sich nicht rueckgaengig machen.`)) return;
  try {
    const r = await fetch(`/api/run/${id}`, { method: "DELETE" });
    const j = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(j.detail || r.statusText);
    // Detailansicht leeren, falls dieser Run gerade angezeigt war.
    const det = $("runDetail");
    if (det && det.dataset.runId === String(id)) {
      det.innerHTML = "";
      det.dataset.runId = "";
    }
    await loadRuns();
  } catch (e) {
    alert("Loeschen fehlgeschlagen: " + e.message);
  }
}

async function loadRuns() {
  const box = $("runsBox"); box.textContent = "Lade...";
  try {
    const data = await (await fetch("/api/runs")).json();
    if (!data.runs.length) {
      box.innerHTML = `<i>Keine Runs in ${data.db}</i>`;
      return;
    }
    let html = `<small>DB: ${data.db}</small><table><thead><tr>
                <th>id</th><th>started</th><th>ended</th><th>fw</th>
                <th>notes</th><th></th><th></th></tr></thead><tbody>`;
    for (const run of data.runs) {
      html += `<tr><td>${run.id}</td><td>${run.started_at||""}</td>
               <td>${run.ended_at||"<i>läuft?</i>"}</td>
               <td>${run.firmware_version||""}</td>
               <td>${(run.notes||"").substring(0,60)}</td>
               <td><button type="button" data-detail="${run.id}">Detail</button></td>
               <td><button type="button" class="danger"
                           data-del="${run.id}"
                           title="Run inkl. aller Daten loeschen">Loeschen</button></td>
             </tr>`;
    }
    html += "</tbody></table>";
    box.innerHTML = html;
    box.querySelectorAll("button[data-detail]").forEach(b =>
      b.onclick = () => loadRunDetail(parseInt(b.dataset.detail, 10)));
    box.querySelectorAll("button[data-del]").forEach(b =>
      b.onclick = () => deleteRun(parseInt(b.dataset.del, 10)));
  } catch (e) { box.textContent = "Fehler: " + e; }
}

async function loadRunDetail(id) {
  const box = $("runDetail"); box.textContent = "Lade...";
  box.dataset.runId = String(id);
  try {
    const r = await fetch(`/api/run/${id}/summary`);
    if (!r.ok) throw new Error(await r.text());
    const d = await r.json();
    let html = `<h3>Run ${d.run.id}</h3>
      <p><b>${d.totals.sent}</b> gesendet, <b>${d.totals.ok}</b> ok,
         PDR <b>${d.totals.pdr_pct.toFixed(1)}%</b>,
         ⌀ Latenz ${d.totals.avg_latency_ms ?? "–"} ms<br>
         Notes: ${d.run.notes || ""}<br>
         FW: ${d.run.firmware_version || ""}</p>
      <div class="grid2">
        <div><h4>Pro Paketgröße</h4><table><thead><tr>
             <th>size</th><th>ok/total</th><th>PDR%</th></tr></thead><tbody>`;
    for (const r of d.per_size)
      html += `<tr><td>${r.size}</td><td>${r.ok}/${r.total}</td><td>${r.pdr_pct.toFixed(1)}</td></tr>`;
    html += `</tbody></table></div>
      <div><h4>Pro Empfänger</h4><table><thead><tr>
           <th>dst</th><th>label</th><th>ok/total</th><th>PDR%</th></tr></thead><tbody>`;
    for (const r of d.per_dst)
      html += `<tr><td>${r.dst}</td><td>${r.label||""}</td><td>${r.ok}/${r.total}</td><td>${r.pdr_pct.toFixed(1)}</td></tr>`;
    html += `</tbody></table></div></div>`;
    if (d.buckets.length) {
      html += `<h4 style="margin-top:1rem;">Verlauf (Buckets, PDR über Zeit)</h4>
               <canvas id="histCanvas" style="max-height:300px;"></canvas>`;
    }
    box.innerHTML = html;
    if (d.buckets.length) {
      const labels = [...new Set(d.buckets.map(b => b.ts))];
      const tot = labels.map(l => d.buckets.filter(b => b.ts===l).reduce((a,b)=>a+b.total,0));
      const ok  = labels.map(l => d.buckets.filter(b => b.ts===l).reduce((a,b)=>a+b.ok,0));
      const pdr = labels.map((_,i) => tot[i] ? 100*ok[i]/tot[i] : 0);
      new Chart($("histCanvas").getContext("2d"), {
        type: "line",
        data: { labels, datasets: [{ label:"PDR %", data: pdr,
                  borderColor:"#3b7a1e", backgroundColor:"#3b7a1e22", fill:true }] },
        options: { animation:false, responsive:true, maintainAspectRatio:false,
                   scales: { y:{ min:0, max:100 } }, elements: { point:{ radius:0 } } },
      });
    }
  } catch (e) { box.textContent = "Fehler: " + e; }
}

$("btnReloadRuns").onclick = loadRuns;
