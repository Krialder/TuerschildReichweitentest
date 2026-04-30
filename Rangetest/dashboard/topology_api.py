"""Topologie-Routes (Wizard).

Duenner Wrapper, der die reine Logik aus ``topology.py`` ueber HTTP
erreichbar macht.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from .topology import (
    TopologyPlanRequest, build_topology, clear_topology_from_ini,
    receivers_from_ini, write_topology_to_ini,
)


def build_router(pio_ini: Path) -> APIRouter:
    """Liefert einen Router, der den uebergebenen ini-Pfad bedient."""
    router = APIRouter(tags=["topology"])

    @router.post("/api/topology/plan")
    def api_topology_plan(req: TopologyPlanRequest) -> dict:
        plan = build_topology(req)
        write_topology_to_ini(plan, pio_ini)
        return plan

    @router.delete("/api/topology/plan")
    def api_topology_clear() -> dict:
        if not pio_ini.exists():
            raise HTTPException(500, f"platformio.ini fehlt: {pio_ini}")
        clear_topology_from_ini(pio_ini)
        return {"ok": True}

    @router.get("/api/topology/receivers")
    def api_topology_receivers() -> dict:
        """Receiver-IDs aus dem aktuellen AUTO-Block der platformio.ini.
        Quelle fuer die "Aus Flash uebernehmen"-Schaltflaeche im Run-Setup.
        """
        return {"receivers": receivers_from_ini(pio_ini)}

    return router
