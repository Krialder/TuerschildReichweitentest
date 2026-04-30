"""platformio.ini-Parser.

Liefert die Liste der ``[env:*]``-Sektionen mit den fuer das Dashboard
relevanten Build-Flags (Rolle, NODE_ID, Channel, LR, ALLOWED_PREV_MASK).
"""
from __future__ import annotations

import configparser
import re
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


class EnvInfo(BaseModel):
    """Eine [env:*]-Sektion in platformio.ini."""
    name: str
    role: str                # sender / receiver / relay / unknown
    node_id: Optional[int]
    channel: Optional[int]
    lr: Optional[int]
    chain: bool              # ALLOWED_PREV_MASK gesetzt?


_ROLE_FLAGS = {
    "ROLE_SENDER":   "sender",
    "ROLE_RECEIVER": "receiver",
    "ROLE_RELAY":    "relay",
}

_FLAG_RE = re.compile(r"-D\s+([A-Z_][A-Z0-9_]*)\s*=\s*(\S+)")


def _to_int(v: Optional[str]) -> Optional[int]:
    """Parst dezimal/hex/oktal. Liefert None bei Fehler oder Leerstring."""
    if v is None:
        return None
    try:
        return int(v, 0)
    except ValueError:
        return None


def parse_build_flags(raw: str) -> dict[str, str]:
    """Build-Flags aus -D NAME=VALUE-Tokens extrahieren."""
    return {m.group(1): m.group(2) for m in _FLAG_RE.finditer(raw or "")}


def parse_pio_envs(ini_path: Path) -> list[EnvInfo]:
    """Liefert alle Build-Envs aus platformio.ini."""
    if not ini_path.exists():
        raise FileNotFoundError(f"platformio.ini nicht gefunden: {ini_path}")
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.read(ini_path, encoding="utf-8")
    envs: list[EnvInfo] = []
    for section in cp.sections():
        if not section.startswith("env:"):
            continue
        name = section.split(":", 1)[1]
        flags = parse_build_flags(cp.get(section, "build_flags", fallback=""))
        # Rolle ueber das gesetzte ROLE_*-Flag bestimmen.
        role = "unknown"
        for flag_name, role_name in _ROLE_FLAGS.items():
            if flags.get(flag_name) in ("1", "true", "True"):
                role = role_name
                break
        # Chain = Topologie-Filter aktiv (Mask != 0).
        mask = flags.get("ALLOWED_PREV_MASK")
        chain = mask is not None and mask not in ("0", "0x0", "0x00", "0x00000000")
        envs.append(EnvInfo(
            name=name,
            role=role,
            node_id=_to_int(flags.get("NODE_ID")),
            channel=_to_int(flags.get("ESPNOW_CHANNEL")),
            lr=_to_int(flags.get("ESPNOW_LR")),
            chain=chain,
        ))
    return envs
