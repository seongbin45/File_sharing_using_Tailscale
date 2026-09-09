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
import os
import urllib.parse
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import auth
from .backends import Backend, MockBackend
from .config import PATHS, Host, registry

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Tailscale 백업 관리 콘솔", docs_url="/api/docs", redoc_url=None)

# Swapped for SshBackend by the launcher. Everything above the interface is
# unaware of which one is in place; the browser is told, and says so.
backend: Backend = MockBackend()


def use_backend(new: Backend) -> None:
    global backend
    backend = new


# ------------------------------------------------------------------- auth

# Paths reachable without a session. Everything else, including every /api
# route and the WebSocket, requires one.
OPEN_PATHS = {"/login", "/healthz"}


@app.middleware("http")
async def require_session(request: Request, call_next):
    if not auth.configured_password():
        # No password configured means loopback-only operation, which
        # check_deployment() has already enforced at startup.
        return await call_next(request)
    if request.url.path in OPEN_PATHS:
        return await call_next(request)
    if auth.cookie_valid(request.cookies.get(auth.COOKIE_NAME)):
        return await call_next(request)

    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _client(request: Request) -> str:
    # X-Forwarded-For is set by the platform's load balancer. It is spoofable
    # by anyone talking to us directly, which only widens the rate-limit
    # bucket - it can never let an attacker past the password itself.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "?"


LOGIN_PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>TS CONTROL — 로그인</title>
<style>
 body{margin:0;height:100vh;display:flex;align-items:center;justify-content:center;
      background:#0c0c0c;color:#ccc;
      font-family:"JetBrains Mono","D2Coding",Consolas,"Malgun Gothic",monospace}
 form{width:min(340px,90vw);border:1px solid #1f1f1f;padding:28px}
 h1{margin:0 0 4px;font-size:13px;letter-spacing:.16em;color:#fff}
 p{margin:0 0 20px;font-size:11px;color:#767676}
 input{width:100%;height:36px;padding:0 10px;margin-bottom:14px;background:#111214;
       border:1px solid #2b2b2b;color:#e6e6e6;font:inherit;font-size:13px;outline:none}
 input:focus{border-color:#61d6d6}
 button{width:100%;height:36px;background:#61d6d6;border:none;color:#06282a;
        font:inherit;font-weight:700;cursor:pointer}
 .err{color:#e74856;font-size:12px;margin:0 0 14px}
</style></head><body>
<form method="post" action="/login">
  <h1>TS CONTROL</h1>
  <p>__SUB__</p>
  __ERROR__
  <input type="password" name="password" placeholder="비밀번호" autofocus autocomplete="current-password">
  <button type="submit">로그인</button>
</form></body></html>"""


def _login_html(error: str = "", sub: str = "관리 콘솔") -> str:
    return LOGIN_PAGE.replace(
        "__ERROR__", f'<p class="err">{error}</p>' if error else ""
    ).replace("__SUB__", sub)


@app.get("/login")
async def login_page() -> HTMLResponse:
    if not auth.configured_password():
        return HTMLResponse(_login_html(sub="비밀번호가 설정되지 않았습니다"), status_code=200)
    return HTMLResponse(_login_html())


async def _submitted_password(request: Request) -> str:
    """Read the password out of the request body without python-multipart.

    Starlette's request.form() pulls in that dependency even for a plain
    url-encoded form, and one fewer package in a deployed image is one fewer
    thing to keep patched. JSON is accepted too so the endpoint can be driven
    from curl.
    """
    body = (await request.body()).decode("utf-8", "replace")
    if request.headers.get("content-type", "").startswith("application/json"):
        try:
            return str(json.loads(body or "{}").get("password", ""))
        except ValueError:
            return ""
    return urllib.parse.parse_qs(body).get("password", [""])[0]


@app.post("/login")
async def login(request: Request) -> Any:
    client = _client(request)
    wait = auth.throttled(client)
    if wait:
        return HTMLResponse(
            _login_html(f"시도가 너무 많습니다. {wait}초 후 다시 시도하십시오."), status_code=429
        )

    if auth.password_ok(await _submitted_password(request)):
        auth.clear_failures(client)
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            auth.COOKIE_NAME,
            auth.make_cookie(),
            max_age=auth.SESSION_HOURS * 3600,
            httponly=True,
            samesite="lax",
            # Render terminates TLS, so the browser always speaks https to it.
            secure=request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto") == "https",
        )
        return response

    auth.record_failure(client)
    return HTMLResponse(_login_html("비밀번호가 맞지 않습니다."), status_code=401)


@app.post("/logout")
async def logout() -> Any:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(auth.COOKIE_NAME)
    return response


@app.get("/healthz")
async def healthz() -> dict[str, Any]:
    """Unauthenticated on purpose - the platform polls it to decide whether
    the container is alive. It deliberately reveals nothing about hosts."""
    return {"ok": True, "backend": backend.name}


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
        "from_env": registry.from_env,
        "writable": registry.writable,
        "auth": auth.describe(),
    }


@app.get("/api/hosts")
async def list_hosts() -> list[dict[str, Any]]:
    return [h.public() for h in registry.all()]


@app.get("/api/devices")
async def devices() -> dict[str, Any]:
    """The tailnet, as the machine running this console sees it, merged with
    the hosts it is configured to drive."""
    return await backend.devices()


@app.get("/api/overview")
async def overview() -> dict[str, Any]:
    """Both sides at once. The 개요 tab is about the backup system, not about
    whichever machine happens to be selected in the sidebar."""
    out: dict[str, Any] = {}
    for role in ("sender", "receiver"):
        host = registry.by_role(role)
        if host is None:
            out[role] = None
            continue
        try:
            status = await backend.status(host)
        except Exception as exc:
            status = {"id": host.id, "reachable": False,
                      "error": f"{type(exc).__name__}: {exc}", "task": None, "log": []}
        status["host"] = host.public()
        out[role] = status
    return out


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


class JumpModel(BaseModel):
    address: str = ""
    port: int = 22
    username: str = ""
    password: str | None = None


class ServerRequest(BaseModel):
    id: str | None = None
    label: str | None = None
    role: str | None = None
    address: str
    port: int = 22
    username: str
    password: str | None = None
    task: str | None = None
    scripts_dir: str | None = None
    work_dir: str | None = None
    path: str = "direct"
    jump: JumpModel | None = None


@app.post("/api/servers")
async def save_server(req: ServerRequest) -> dict[str, Any]:
    if req.path not in PATHS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 연결 경로: {req.path}")
    data = req.model_dump(exclude_none=True)
    if req.jump is not None:
        data["jump"] = req.jump.model_dump()
    host = registry.upsert(data)
    try:
        registry.save()
        saved, note = True, f"hosts.json 에 저장했습니다"
    except OSError as exc:
        # A read-only checkout should not lose the settings for this session.
        saved, note = False, f"메모리에만 적용했습니다 - 파일 저장 실패: {exc}"
    return {"ok": True, "host": host.public(), "saved_to_disk": saved, "note": note}


@app.post("/api/servers/{host_id}/test")
async def test_server(host_id: str) -> dict[str, Any]:
    host = _host_or_404(host_id)
    tester = getattr(backend, "test", None)
    if tester is None:
        return {"ok": False,
                "detail": "MOCK 백엔드에서는 연결을 시험할 수 없습니다. --ssh 로 실행하십시오."}
    return await tester(host)


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
    # The HTTP middleware does not run for WebSocket connections - the scope
    # type is different - so the session is checked here explicitly. Without
    # this the dashboard would be behind a password and the shell would not.
    if auth.configured_password() and not auth.cookie_valid(ws.cookies.get(auth.COOKIE_NAME)):
        await ws.close(code=1008)   # policy violation
        return

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
        # CancelledError derives from BaseException, not Exception, so
        # suppress(Exception) lets it through and every disconnect logs a
        # traceback. Name it explicitly.
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await pump
        with contextlib.suppress(Exception):
            await shell.close()


# ------------------------------------------------------------------ static

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# ------------------------------------------------------------------- main


def check_deployment(bind: str) -> None:
    """Refuse to start open.

    Binding anywhere but loopback publishes an interactive shell on other
    people's computers. Requiring a password there is not a hardening option
    to be talked out of; the process exits instead, because a console that
    fails to boot is recoverable and one that boots wide open may not be.
    """
    if auth.auth_required(bind) and not auth.configured_password():
        raise SystemExit(
            f"\n거부: {bind} 에 바인딩하려면 TSCONSOLE_PASSWORD 가 필요합니다.\n"
            "\n이 콘솔은 다른 컴퓨터의 셸을 그대로 열어 줍니다. 로그인 없이 공개 주소에\n"
            "띄우면 그 자체가 무인증 원격 실행 창구가 됩니다.\n"
            "\n  export TSCONSOLE_PASSWORD='...'   (Render: 환경변수에 추가)\n"
            "\n비밀번호 없이 쓰려면 루프백(127.0.0.1)에 바인딩하십시오.\n"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Tailscale 백업 관리 콘솔")
    parser.add_argument(
        "--host",
        default=os.environ.get("TSCONSOLE_BIND", "127.0.0.1"),
        help="bind address (default: loopback, or $TSCONSOLE_BIND)",
    )
    parser.add_argument(
        "--port",
        type=int,
        # $PORT is what every PaaS injects, Render included; it is not ours
        # to choose there.
        default=int(os.environ.get("PORT") or os.environ.get("TSCONSOLE_PORT") or 8765),
    )
    parser.add_argument(
        "--ssh",
        action="store_true",
        default=os.environ.get("TSCONSOLE_BACKEND", "").lower() == "ssh",
        help="connect to the real machines instead of serving mock data "
             "(or TSCONSOLE_BACKEND=ssh)",
    )
    args = parser.parse_args()

    check_deployment(args.host)

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

    uvicorn.run(
        app,
        host=args.host,
        port=args.port,
        log_level="info",
        # Behind Render's proxy the client address and scheme arrive in
        # headers; without this every request looks like it came from the
        # load balancer over plain http.
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
