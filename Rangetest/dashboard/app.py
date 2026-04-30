"""FastAPI-Factory.

Setzt alle Module zusammen: Settings, statisches Frontend, Flash-Routes,
Run-Routes, Topologie-Wizard und Analyse-Endpoints.
"""
from __future__ import annotations

import os
import signal
import threading

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import analysis, flash, runs_api, topology_api
from .config import FIRMWARE_DIR, PIO_INI, STATIC_DIR, Settings
from .engine import ENGINE


def create_app(settings: Settings) -> FastAPI:
    """Baut die FastAPI-App, gebunden an die uebergebenen Settings."""
    app = FastAPI(title="Rangetest ESP-NOW Dashboard")

    # Statische Frontend-Dateien.
    if STATIC_DIR.exists():
        app.mount(
            "/static",
            StaticFiles(directory=str(STATIC_DIR)),
            name="static",
        )

    @app.get("/")
    def index():                                    # type: ignore[no-untyped-def]
        idx = STATIC_DIR / "index.html"
        if not idx.exists():
            return {"ok": True, "msg": "static/index.html fehlt"}
        return FileResponse(str(idx))

    @app.get("/api/health")
    def health() -> dict:
        return {
            "ok": True,
            "pio_exe": str(settings.pio_exe),
            "pio_exists": settings.pio_exe.exists(),
            "firmware_dir": str(FIRMWARE_DIR),
            "db": str(settings.db_path),
        }

    # Analyse-Router nutzt einen modul-globalen DB-Pfad. Vor dem Mount
    # einmal setzen, damit set_db_path() die Migration triggert.
    analysis.set_db_path(settings.db_path)
    app.include_router(analysis.router)

    # Funktionale Module liefern jeweils ihren eigenen Router.
    app.include_router(flash.build_router(settings, PIO_INI, FIRMWARE_DIR))
    app.include_router(runs_api.build_router(settings))
    app.include_router(topology_api.build_router(PIO_INI))

    @app.post("/api/quit")
    async def quit_server() -> dict:                # type: ignore[no-untyped-def]
        """Stoppt einen laufenden Run und beendet den Server-Prozess.

        Wird vom Dashboard-Button "Programm beenden" aufgerufen. Sendet
        SIGTERM (bzw. SIGINT auf Windows) nach kurzer Verzoegerung, damit
        die HTTP-Antwort noch beim Browser ankommt.
        """
        try:
            ENGINE.stop(wait_s=2.0)
        except Exception:
            pass

        def _shutdown() -> None:
            # Ein bisschen Zeit fuer die HTTP-Antwort, dann Prozess hart beenden.
            import time as _t
            _t.sleep(0.4)
            try:
                os.kill(os.getpid(),
                        signal.SIGINT if os.name == "nt" else signal.SIGTERM)
            except Exception:
                os._exit(0)

        threading.Thread(target=_shutdown, daemon=True).start()
        return {"ok": True, "msg": "Server wird beendet."}

    return app
