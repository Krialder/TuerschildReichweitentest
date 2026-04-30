// Globaler Mini-Helfer + Tabs + Init-Reihenfolge.
// Der Rest der Logik liegt in den thematischen js/*.js-Modulen.

window.$ = (id) => document.getElementById(id);

// ---------- Tabs ----------
document.querySelectorAll(".tabs button").forEach(btn => {
  btn.onclick = () => {
    document.querySelectorAll(".tabs button").forEach(b => b.classList.remove("active"));
    document.querySelectorAll(".pane").forEach(p => p.classList.remove("active"));
    btn.classList.add("active");
    $("tab-" + btn.dataset.tab).classList.add("active");
    if (btn.dataset.tab === "history") loadRuns();
    if (btn.dataset.tab === "analyse") loadAnalyseRuns();
  };
});

// ---------- Init beim Seitenstart ----------
window.addEventListener("DOMContentLoaded", () => {
  // Targets-Tabelle bleibt leer; der User klickt "Aus Flash uebernehmen",
  // "Letzten Run klonen" oder "+ Manuell hinzufuegen".
  loadPorts();
  loadEnvs();
  resetCharts();
  connectLive();

  // "Programm beenden" - stoppt Run + faehrt den Server herunter.
  if ($("btnQuit")) {
    $("btnQuit").onclick = async () => {
      if (!confirm("Dashboard-Server wirklich beenden?\n"
                 + "Ein laufender Run wird zuerst gestoppt.")) return;
      $("btnQuit").disabled = true;
      $("btnQuit").textContent = "beende...";
      try {
        await fetch("/api/quit", { method: "POST" });
      } catch (e) { /* Server beendet sich -> Fehler erwartet */ }
      // Kurz warten, dann Hinweis im UI anzeigen.
      setTimeout(() => {
        document.body.innerHTML =
          '<div style="padding:2rem; font-family:sans-serif;">'
          + '<h2>Server beendet.</h2>'
          + '<p>Du kannst dieses Browser-Fenster schliessen.</p>'
          + '</div>';
      }, 800);
    };
  }
});
