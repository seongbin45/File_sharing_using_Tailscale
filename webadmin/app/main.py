"""FastAPI console for the Tailscale project backup.

Where this runs matters more than anything else in the file:

  The server must itself be a node on the tailnet. Then a host's MagicDNS
  name resolves and SSH to it just works. FastAPI is not relaying anything
  through Tailscale and there is nothing to configure for it - Tailscale is
  a network, not a proxy the application talks to.

  Machines that are NOT on a tailnet cannot be reached this way, and no
  amount of code here changes that. The fix is to join the machine, not to
  route around it.

Bind address defaults to 127.0.0.1. Serve it to the tailnet instead with
--host 100.x.y.z (that machine's own tailnet address) and it is reachable
from your other devices and from nowhere else. Never bind 0.0.0.0 on a
machine with a public interface: this console runs commands on other people's
computers and has no authentication of its own.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .backends import Backend, MockBackend
from .config import Host, registry

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Tailscale 백업 관리 콘솔", docs_url="/api/docs", redoc_url=None)

# Swapped for SshBackend by the launcher. Everything above the interface is
# unaware of which one is in place; the browser is told, and says so.
backend: Backend = MockBackend()


def use_backend(new: Backend) -> None:
    global backend
    backend = new


def _host_or_404(host_id: str) -> Host:
    host = registry.get(host_id)
    if host is None:
        raise HTTPException(status_code=404, detail=f"알 수 없는 호스트: {host_id}")
    return host


# ------------------------------------------------------------------ API


@app.get("/api/meta")
async def meta() -> dict[str, Any]:
    return {
        "backend": backend.name,
        "hosts_file": registry.source,
        "using_example": registry.using_example,
    }


@app.get("/api/hosts")
async def list_hosts() -> list[dict[str, Any]]:
    return [h.public() for h in registry.all()]


@app.get("/api/hosts/{host_id}/status")
async def host_status(host_id: str) -> dict[str, Any]:
    host = _host_or_404(host_id)
    try:
        return await backend.status(host)
    except Exception as exc:  # surfaced in the panel, never a 500 page
        return {
            "id": host.id,
            "reachable": False,
            "error": f"{type(exc).__name__}: {exc}",
            "task": None,
            "log": [],
        }


@app.get("/api/hosts/{host_id}/log")
async def host_log(host_id: str, lines: int = 40) -> dict[str, Any]:
    host = _host_or_404(host_id)
    lines = max(1, min(lines, 500))
    try:
        return {"lines": await backend.log(host, lines)}
    except Exception as exc:
        return {"lines": [], "error": f"{type(exc).__name__}: {exc}"}


class ActionRequest(BaseModel):
    action: str  # run | enable | disable


@app.post("/api/hosts/{host_id}/action")
async def host_action(host_id: str, req: ActionRequest) -> JSONResponse:
    host = _host_or_404(host_id)
    if req.action not in ("run", "enable", "disable"):
        raise HTTPException(status_code=400, detail=f"알 수 없는 동작: {req.action}")
    try:
        return JSONResponse(await backend.action(host, req.action))
    except Exception as exc:
        return JSONResponse(
            {"ok": False, "action": req.action, "output": [f"{type(exc).__name__}: {exc}"]},
            status_code=200,
        )


class CredentialRequest(BaseModel):
    password: str | None = None


@app.post("/api/hosts/{host_id}/credentials")
async def set_credentials(host_id: str, req: CredentialRequest) -> dict[str, Any]:
    _host_or_404(host_id)
    registry.set_password(host_id, req.password)
    return {"ok": True, "has_password": bool(req.password)}


# --------------------------------------------------------------- terminal


@app.websocket("/api/hosts/{host_id}/terminal")
async def terminal(ws: WebSocket, host_id: str) -> None:
    await ws.accept()
    host = registry.get(host_id)
    if host is None:
        await ws.send_json({"type": "error", "message": f"알 수 없는 호스트: {host_id}"})
        await ws.close()
        return

    try:
        shell = await backend.shell(host, cols=120, rows=32)
    except Exception as exc:
        await ws.send_json({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        await ws.close()
        return

    await ws.send_json({"type": "ready", "backend": backend.name})

    # Frame type carries the meaning, in both directions:
    #     binary = raw terminal bytes      text = JSON control message
    # Sniffing the payload instead (a leading "{", a NUL prefix) breaks the
    # moment a command prints one, and a terminal prints arbitrary bytes by
    # definition. WebSocket already draws this line, so use it.

    async def pump_out() -> None:
        while True:
            chunk = await shell.read()
            if chunk is None:
                await ws.send_json({"type": "closed"})
                return
            await ws.send_bytes(chunk)

    pump = asyncio.create_task(pump_out())
    try:
        while True:
            message = await ws.receive()
            if message["type"] == "websocket.disconnect":
                break

            data = message.get("bytes")
            if data is not None:
                await shell.write(data)
                continue

            text = message.get("text")
            if not text:
                continue
            try:
                ctl = json.loads(text)
            except ValueError:
                continue
            if ctl.get("type") == "resize":
                await shell.resize(int(ctl.get("cols", 80)), int(ctl.get("rows", 24)))
    except WebSocketDisconnect:
        pass
    finally:
        pump.cancel()
        with contextlib.suppress(Exception):
            await pump
        with contextlib.suppress(Exception):
            await shell.close()


# ------------------------------------------------------------------ static

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ------------------------------------------------------------------- main


def main() -> None:
    parser = argparse.ArgumentParser(description="Tailscale 백업 관리 콘솔")
    parser.add_argument("--host", default="127.0.0.1", help="bind address (default: loopback)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--ssh",
        action="store_true",
        help="connect to the real machines instead of serving mock data",
    )
    args = parser.parse_args()

    if args.ssh:
        try:
            from .sshbackend import SshBackend
        except ImportError as exc:
            parser.error(
                f"SSH 백엔드를 불러올 수 없습니다 ({exc}). "
                "pip install -r requirements.txt 로 paramiko 를 설치하십시오."
            )
        use_backend(SshBackend())

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
