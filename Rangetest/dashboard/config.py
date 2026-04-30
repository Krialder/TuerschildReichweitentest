"""Settings- und Pfad-Aufloesung.

Liest die Datei ``config.toml`` aus dem Projekt-Root. Falls einzelne
Felder fehlen oder die Datei nicht existiert, werden sinnvolle Defaults
benutzt. So bleibt der Setup-Aufwand minimal.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

if sys.version_info >= (3, 11):
    import tomllib
else:                                   # pragma: no cover
    import tomli as tomllib             # type: ignore


# Projekt-Wurzel = Eltern des dashboard-Pakets.
ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT_DIR / "config.toml"
FIRMWARE_DIR = ROOT_DIR / "firmware"
PIO_INI = FIRMWARE_DIR / "platformio.ini"
STATIC_DIR = Path(__file__).resolve().parent / "static"


@dataclass(frozen=True)
class Settings:
    """Aufgeloeste Laufzeit-Einstellungen."""
    db_path: Path
    host: str
    port: int
    pio_exe: Path


def _resolve_pio_exe(raw: str) -> Path:
    """Pio-CLI finden. Reihenfolge: explizit -> ~/.platformio -> PATH."""
    if raw:
        p = Path(raw).expanduser()
        if p.exists():
            return p
    home = Path(os.path.expanduser("~"))
    candidates = [
        home / ".platformio" / "penv" / "Scripts" / "pio.exe",
        home / ".platformio" / "penv" / "bin" / "pio",
    ]
    for c in candidates:
        if c.exists():
            return c
    return Path("pio")  # vom Shell-PATH aufloesen lassen


def load_settings(config_path: Optional[Path] = None) -> Settings:
    """Liest config.toml, faellt auf Defaults zurueck."""
    cfg_file = config_path or CONFIG_FILE
    raw: dict = {}
    if cfg_file.exists():
        with cfg_file.open("rb") as f:
            raw = tomllib.load(f)

    storage = raw.get("storage", {})
    server = raw.get("server", {})
    pio = raw.get("platformio", {})

    db_str = str(storage.get("db_path", "data/espnow.sqlite"))
    db_path = Path(db_str)
    if not db_path.is_absolute():
        db_path = ROOT_DIR / db_path

    return Settings(
        db_path=db_path,
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8000)),
        pio_exe=_resolve_pio_exe(str(pio.get("pio_exe", ""))),
    )
