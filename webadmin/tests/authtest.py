"""Auth surface test for a deployed console.

    cd webadmin && python -m tests.authtest

Separate from selftest.py because it needs httpx2 and websockets, which the
console itself does not - selftest.py must stay runnable with nothing but the
runtime dependencies.

Run this before every deployment. It is the difference between a console
behind a password and a public shell on someone's home PC, and the WebSocket
in particular is easy to leave open: @app.middleware("http") does not see it.
"""

import asyncio, os, sys, json
os.environ["TSCONSOLE_PASSWORD"] = "correct-horse"
os.environ["TSCONSOLE_SECRET"] = "test-secret"
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))

import websockets
from fastapi.testclient import TestClient
from app.main import app

c = TestClient(app, follow_redirects=False)
fails = []
def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + ("   " + str(detail) if not cond else ""))
    if not cond: fails.append(name)

print("\nunauthenticated")
for path in ("/", "/api/meta", "/api/devices", "/api/overview", "/api/hosts"):
    r = c.get(path)
    ok = r.status_code in (303, 401)
    check(f"{path} is closed", ok, r.status_code)
check("POST /api/servers is closed", c.post("/api/servers", json={"address":"h","username":"u"}).status_code == 401)
check("POST action is closed", c.post("/api/hosts/sender/action", json={"action":"run"}).status_code == 401)
check("/healthz is open", c.get("/healthz").status_code == 200)
check("/login is open", c.get("/login").status_code == 200)
check("/api/docs is closed", c.get("/api/docs").status_code in (303, 401))
check("static is closed", c.get("/static/app.js").status_code in (303, 401))

print("\nlogin")
r = c.post("/login", data={"password": "wrong"})
check("wrong password rejected", r.status_code == 401, r.status_code)
check("no cookie on failure", "ts_console" not in r.cookies)
r = c.post("/login", data={"password": "correct-horse"})
check("right password accepted", r.status_code == 303, r.status_code)
cookie = r.cookies.get("ts_console")
check("cookie issued", bool(cookie))

print("\nauthenticated")
c2 = TestClient(app, follow_redirects=False, cookies={"ts_console": cookie})
check("/api/meta opens", c2.get("/api/meta").status_code == 200)
check("/ opens", c2.get("/").status_code == 200)
meta = c2.get("/api/meta").json()
check("meta reports auth", meta["auth"]["password_set"] is True, meta["auth"])

print("\ncookie forgery")
from app import auth as A
check("empty cookie rejected", not A.cookie_valid(""))
check("garbage rejected", not A.cookie_valid("abc.def"))
body = cookie.split(".")[0]
check("unsigned payload rejected", not A.cookie_valid(body + ".AAAA"))
check("tampered payload rejected", not A.cookie_valid("x" + cookie[1:]))
check("valid cookie accepted", A.cookie_valid(cookie))

import base64, time, hmac, hashlib
expired = json.dumps({"exp": int(time.time()) - 10}).encode()
mac = hmac.new(b"test-secret", expired, hashlib.sha256).digest()
forged = (base64.urlsafe_b64encode(expired).decode().rstrip("=") + "." +
          base64.urlsafe_b64encode(mac).decode().rstrip("="))
check("expired but correctly signed cookie rejected", not A.cookie_valid(forged))

print("\nrate limit")
A._attempts.clear()
c3 = TestClient(app, follow_redirects=False)
codes = [c3.post("/login", data={"password": "no"}).status_code for _ in range(7)]
check("locks out after repeated failures", 429 in codes, codes)

print("\nwebsocket")
async def ws_checks():
    import uvicorn, threading
    cfg = uvicorn.Config(app, host="127.0.0.1", port=8791, log_level="error")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True); t.start()
    for _ in range(60):
        if server.started: break
        await asyncio.sleep(0.1)

    url = "ws://127.0.0.1:8791/api/hosts/sender/terminal"
    try:
        async with websockets.connect(url) as ws:
            await ws.recv()
        return False, None
    except Exception as exc:
        refused = exc

    ok = None
    try:
        async with websockets.connect(url, additional_headers={"Cookie": f"ts_console={cookie}"}) as ws:
            ok = json.loads(await ws.recv())
    except Exception as exc:
        ok = f"ERROR {exc}"
    server.should_exit = True
    return refused, ok

refused, opened = asyncio.run(ws_checks())
check("terminal refused without a cookie", refused is not False, "it connected!")
check("terminal opens with a cookie", isinstance(opened, dict) and opened.get("type") == "ready", opened)

print()
if fails:
    print(f"FAILED: {len(fails)}"); [print("  -", f) for f in fails]; sys.exit(1)
print("all auth checks passed")
