"""Flash-Routes (REST + WebSocket).

Liefert die HTTP-Endpoints fuer Port-/Env-Liste und einen
WebSocket-Endpoint, der ``pio run -t upload`` startet und das Log
zeilenweise streamt.
"""
from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from serial.tools import list_ports

from .config import Settings
from .pio_helpers import parse_pio_envs


_VALID_ENV = re.compile(r"^[A-Za-z0-9_\-]+$")
_VALID_PORT = re.compile(r"^(COM\d+|/dev/[A-Za-z0-9._\-/]+)$")


class FlashRequest(BaseModel):
    env: str
    port: str


def list_serial_ports() -> list[dict]:
    """COM-/tty-Ports inklusive Description sortieren."""
    out = []
    for p in list_ports.comports():
        out.append({
            "device": p.device,
            "description": p.description or "",
            "hwid": p.hwid or "",
            "manufacturer": p.manufacturer or "",
        })
    return sorted(out, key=lambda x: x["device"])


def build_router(settings: Settings, pio_ini: Path,
                 firmware_dir: Path) -> APIRouter:
    """Liefert einen Router mit gebundenen Pfad-Settings."""
    router = APIRouter(tags=["flash"])

    @router.get("/api/ports")
    def api_ports() -> list[dict]:
        return list_serial_ports()

    @router.get("/api/envs")
    def api_envs() -> list[dict]:
        try:
            return [e.model_dump() for e in parse_pio_envs(pio_ini)]
        except FileNotFoundError as e:
            raise HTTPException(500, str(e))

    @router.websocket("/ws/flash")
    async def ws_flash(ws: WebSocket) -> None:
        """Erwartet als erste Message ``{env, port}``, streamt dann pio-Log."""
        await ws.accept()
        try:
            first = await ws.receive_text()
            req = json.loads(first)
            env = str(req.get("env", "")).strip()
            port = str(req.get("port", "")).strip()
            if not _VALID_ENV.match(env):
                await ws.send_text(json.dumps(
                    {"type": "error", "msg": "invalid env name"}))
                await ws.close()
                return
            if not _VALID_PORT.match(port):
                await ws.send_text(json.dumps(
                    {"type": "error", "msg": "invalid port"}))
                await ws.close()
                return
            valid = {e.name for e in parse_pio_envs(pio_ini)}
            if env not in valid:
                await ws.send_text(json.dumps(
                    {"type": "error", "msg": f"unknown env: {env}"}))
                await ws.close()
                return
            await _stream_pio_run(env, port, ws, settings.pio_exe, firmware_dir)
        except WebSocketDisconnect:
            return
        except Exception as e:
            try:
                await ws.send_text(json.dumps({"type": "error", "msg": str(e)}))
            except Exception:
                pass
        finally:
            try:
                await ws.close()
            except Exception:
                pass

    return router


async def _stream_pio_run(env: str, port: str, ws: WebSocket,
                          pio_exe: Path, firmware_dir: Path) -> int:
    """Startet ``pio run -e env -t upload --upload-port <port>``.

    Wickelt den Aufruf in ``cmd /c pushd <fw> && pio ... && popd``, damit
    auch UNC-gemappte Netzlaufwerke (z. B. ``I:`` zeigt auf
    ``\\\\Server\\Share``) sauber funktionieren - ``cmd.exe`` faellt sonst
    auf ``C:\\Windows`` zurueck.
    """
    fw_dir = str(firmware_dir)
    pio = str(pio_exe)
    cmd_str = (
        f'pushd "{fw_dir}" && '
        f'"{pio}" run -e {env} -t upload --upload-port {port} && '
        f'popd'
    )
    await ws.send_text(json.dumps({"type": "start", "cmd": cmd_str}))
    proc = await asyncio.create_subprocess_shell(
        cmd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    while True:
        line = await proc.stdout.readline()
        if not line:
            break
        try:
            text = line.decode("utf-8", errors="replace").rstrip()
        except Exception:
            text = repr(line)
        await ws.send_text(json.dumps({"type": "log", "line": text}))
    rc = await proc.wait()
    await ws.send_text(json.dumps({"type": "done", "returncode": rc}))
    return rc
