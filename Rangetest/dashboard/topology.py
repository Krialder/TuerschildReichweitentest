"""Topologie-Wizard.

Erzeugt fuer eine N-stufige Chain-Topologie (1 Sender + Y parallele Ketten
zu je X Relays + 1 Empfaenger) das passende Set aus PlatformIO-Build-Envs
und schreibt sie in einen markierten Block der ``platformio.ini``.

ID-Schema (sequentiell, leicht lesbar):
    sender                   = 1
    chain c, relay r (1..X)  = 1 + c*(X+1) + r
    chain c, receiver        = (c+1)*(X+1) + 1

prev_mask (Bit n gesetzt = Vorgaenger n erlaubt):
    sender         : OR ueber alle Ketten c von  bit(erster Knoten der Kette c)
    relay r in c   : bit(prev_data) | bit(prev_ack)
    receiver in c  : bit(letztes Relay)  bzw. bit(sender) wenn X==0

Da ``ALLOWED_PREV_MASK`` ein 32-Bit-Wert ist, gilt:
    1 + Y * (X + 1) <= 31
"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, Field


AUTO_BEGIN = "; ===== AUTO TOPOLOGY BLOCK BEGIN ====="
AUTO_END = "; ===== AUTO TOPOLOGY BLOCK END ====="
_AUTO_RE = re.compile(
    re.escape(AUTO_BEGIN) + r".*?" + re.escape(AUTO_END) + r"\r?\n?",
    re.DOTALL,
)


class TopologyPlanRequest(BaseModel):
    """Request-Body fuer ``POST /api/topology/plan``."""
    x_relays: int = Field(ge=0, le=29)
    y_chains: int = Field(ge=1, le=15)
    channel: int = Field(ge=1, le=14, default=1)
    lr: int = Field(ge=0, le=1, default=0)


def build_topology(req: TopologyPlanRequest) -> dict:
    """Berechnet das Knoten-Layout. Wirft HTTPException wenn IDs > 31."""
    x = req.x_relays
    y = req.y_chains
    block = x + 1
    total = 1 + y * block
    if total > 31:
        raise HTTPException(
            400,
            f"Max 31 IDs (32-Bit-Mask): 1 + {y}*({x}+1) = {total}. "
            f"Reduziere X oder Y.",
        )

    nodes: list[dict] = []

    # 1) Sender. Endpunkt-Filter: ACK darf nur ueber den ersten Knoten
    # einer beliebigen Kette zurueckkommen (Reverse-Path: path[0]).
    sender_mask = 0
    chain_first_ids: list[int] = []
    for c in range(y):
        first_id = 2 + c * block
        chain_first_ids.append(first_id)
        sender_mask |= (1 << first_id)
    nodes.append({
        "step": 1,
        "id": 1,
        "role": "sender",
        "label": "sender",
        "env": "auto_sender",
        "prev_mask": sender_mask,
        "channel": req.channel,
        "lr": req.lr,
        "neighbors": chain_first_ids,
        "instruction": (
            f"Sender flashen. Akzeptiert ACKs von den ersten Knoten jeder "
            f"der {y} Kette(n)."
        ),
    })

    step = 2
    for c in range(y):
        chain_ids = [2 + c * block + i for i in range(x)]    # Relay-IDs
        recv_id = 1 + (c + 1) * block                        # Empfaenger
        for r in range(x):
            rid = chain_ids[r]
            prev_data = 1 if r == 0 else chain_ids[r - 1]
            prev_ack = recv_id if r == x - 1 else chain_ids[r + 1]
            mask = (1 << prev_data) | (1 << prev_ack)
            nodes.append({
                "step": step,
                "id": rid,
                "role": "relay",
                "label": f"chain{c+1}/relay{r+1}",
                "env": f"auto_c{c+1}_relay{r+1}",
                "prev_mask": mask,
                "channel": req.channel,
                "lr": req.lr,
                "neighbors": [prev_data, prev_ack],
                "instruction": (
                    f"Relay {r+1} der Kette {c+1}. Forwarded zwischen "
                    f"NODE {prev_data} (Vorgaenger) und NODE {prev_ack} "
                    "(Nachfolger)."
                ),
            })
            step += 1
        # Empfaenger: bei X==0 direkt vom Sender, sonst vom letzten Relay.
        prev_recv = chain_ids[-1] if x > 0 else 1
        nodes.append({
            "step": step,
            "id": recv_id,
            "role": "receiver",
            "label": f"chain{c+1}/receiver",
            "env": f"auto_c{c+1}_recv",
            "prev_mask": (1 << prev_recv),
            "channel": req.channel,
            "lr": req.lr,
            "neighbors": [prev_recv],
            "instruction": (
                f"Empfaenger der Kette {c+1}. Antwortet ACKs an NODE "
                f"{prev_recv}. Im Run-Setup als dst={recv_id} eintragen."
            ),
        })
        step += 1

    return {
        "x_relays": x,
        "y_chains": y,
        "channel": req.channel,
        "lr": req.lr,
        "total_nodes": total,
        "max_id": total,
        "nodes": nodes,
        "receiver_ids": [1 + (c + 1) * block for c in range(y)],
    }


def render_pio_block(plan: dict) -> str:
    """Formatiert den Plan als Block aus ``[env:auto_*]``-Sektionen."""
    role_flag = {
        "sender": "ROLE_SENDER",
        "relay": "ROLE_RELAY",
        "receiver": "ROLE_RECEIVER",
    }
    lines = [
        AUTO_BEGIN,
        f"; auto-generiert vom Topologie-Wizard "
        f"({plan['y_chains']} Kette(n) a {plan['x_relays']} Relay(s), "
        f"Kanal {plan['channel']}, LR={plan['lr']})",
        "; NICHT manuell editieren. Der Block wird beim naechsten Plan "
        "vollstaendig ersetzt.",
    ]
    for n in plan["nodes"]:
        lines += [
            "",
            f"[env:{n['env']}]",
            "build_flags =",
            f"    -D {role_flag[n['role']]}=1",
            f"    -D NODE_ID={n['id']}",
            f"    -D ESPNOW_CHANNEL={n['channel']}",
            f"    -D ESPNOW_LR={n['lr']}",
            f"    -D ALLOWED_PREV_MASK=0x{n['prev_mask']:08X}",
        ]
    lines.append(AUTO_END)
    return "\n".join(lines) + "\n"


def write_topology_to_ini(plan: dict, ini_path: Path) -> None:
    """Ersetzt einen vorhandenen AUTO-Block oder haengt einen neuen an."""
    if not ini_path.exists():
        raise HTTPException(500, f"platformio.ini fehlt: {ini_path}")
    txt = ini_path.read_text(encoding="utf-8")
    txt = _AUTO_RE.sub("", txt).rstrip() + "\n"
    txt += "\n" + render_pio_block(plan)
    ini_path.write_text(txt, encoding="utf-8")


def clear_topology_from_ini(ini_path: Path) -> None:
    """Entfernt nur den AUTO-Block, manuelle Envs bleiben erhalten."""
    if not ini_path.exists():
        raise HTTPException(500, f"platformio.ini fehlt: {ini_path}")
    txt = ini_path.read_text(encoding="utf-8")
    new = _AUTO_RE.sub("", txt).rstrip() + "\n"
    ini_path.write_text(new, encoding="utf-8")


def receivers_from_ini(ini_path: Path) -> list[dict]:
    """Liest die Receiver-IDs aus dem AUTO-Block der platformio.ini.

    Wird vom Run-Setup-Schritt 1 genutzt ("Aus Flash uebernehmen"), damit
    die Empfaenger nicht von Hand eingetragen werden muessen.

    Sucht nach Sektionen ``[env:auto_*recv*]`` und extrahiert das
    ``NODE_ID``-Build-Flag. Liefert eine leere Liste, wenn die Datei
    fehlt oder kein AUTO-Block gefunden wird.
    """
    if not ini_path.exists():
        return []
    txt = ini_path.read_text(encoding="utf-8")
    m = _AUTO_RE.search(txt)
    block = m.group(0) if m else txt
    out: list[dict] = []
    # Sehr einfacher Sektions-Parser: Ueberschrift + folgende Zeilen bis
    # zur naechsten [section] oder Block-Ende.
    sec_re = re.compile(
        r"\[env:(auto_[A-Za-z0-9_\-]*recv[A-Za-z0-9_\-]*)\]([^\[]*)",
    )
    for sm in sec_re.finditer(block):
        env_name = sm.group(1)
        body = sm.group(2)
        node_match = re.search(r"NODE_ID\s*=\s*(\d+)", body)
        if not node_match:
            continue
        node_id = int(node_match.group(1))
        # Kettennummer aus env_name extrahieren (z. B. auto_c2_recv -> 2)
        chain_match = re.search(r"_c(\d+)_", env_name)
        chain = int(chain_match.group(1)) if chain_match else 1
        out.append({
            "id": node_id,
            "label": f"chain{chain}/recv",
            "env": env_name,
            "chain": chain,
        })
    out.sort(key=lambda r: r["id"])
    return out
