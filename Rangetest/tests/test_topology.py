"""Tests fuer den Topologie-Wizard.

Pruefen die Layout-Berechnung: ID-Schema, prev_mask-Bits, ini-Block.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from dashboard.topology import (
    TopologyPlanRequest, build_topology,
    clear_topology_from_ini, render_pio_block, write_topology_to_ini,
)


def test_simple_chain_ids_and_masks():
    """1 Sender + 1 Kette mit 2 Relays + 1 Empfaenger -> 4 Knoten gesamt."""
    plan = build_topology(TopologyPlanRequest(x_relays=2, y_chains=1))
    assert plan["total_nodes"] == 4
    by_id = {n["id"]: n for n in plan["nodes"]}
    assert set(by_id) == {1, 2, 3, 4}

    sender = by_id[1]
    relay1, relay2, recv = by_id[2], by_id[3], by_id[4]

    # Sender akzeptiert ACK nur vom ersten Relay (Bit 2 gesetzt).
    assert sender["prev_mask"] == (1 << 2)
    # Relay1: DATA von Sender (Bit 1), ACK von Relay2 (Bit 3).
    assert relay1["prev_mask"] == (1 << 1) | (1 << 3)
    # Relay2: DATA von Relay1 (Bit 2), ACK vom Empfaenger (Bit 4).
    assert relay2["prev_mask"] == (1 << 2) | (1 << 4)
    # Empfaenger: DATA nur von Relay2 (Bit 3).
    assert recv["prev_mask"] == (1 << 3)


def test_zero_relays_direct_link():
    """X=0 -> Sender und Empfaenger direkt verdrahtet."""
    plan = build_topology(TopologyPlanRequest(x_relays=0, y_chains=1))
    assert plan["total_nodes"] == 2
    by_id = {n["id"]: n for n in plan["nodes"]}
    assert by_id[1]["prev_mask"] == (1 << 2)
    assert by_id[2]["prev_mask"] == (1 << 1)


def test_max_id_constraint_rejects_too_large():
    """1 + Y*(X+1) <= 31. Y=10, X=3 -> 41 Knoten -> Fehler."""
    with pytest.raises(HTTPException) as exc:
        build_topology(TopologyPlanRequest(x_relays=3, y_chains=10))
    assert exc.value.status_code == 400


def test_pio_block_roundtrip(tmp_path: Path):
    """write_topology + clear_topology arbeiten auf demselben Marker."""
    ini = tmp_path / "platformio.ini"
    ini.write_text("[env:keep]\nbuild_flags = -D KEEP=1\n", encoding="utf-8")
    plan = build_topology(TopologyPlanRequest(x_relays=1, y_chains=1))
    write_topology_to_ini(plan, ini)
    txt = ini.read_text(encoding="utf-8")
    assert "[env:keep]" in txt        # manuelles env unangetastet
    assert "auto_sender" in txt
    clear_topology_from_ini(ini)
    txt2 = ini.read_text(encoding="utf-8")
    assert "[env:keep]" in txt2
    assert "auto_sender" not in txt2


def test_render_pio_block_contains_all_envs():
    plan = build_topology(TopologyPlanRequest(x_relays=2, y_chains=2))
    block = render_pio_block(plan)
    for n in plan["nodes"]:
        assert f"[env:{n['env']}]" in block
        assert f"NODE_ID={n['id']}" in block
