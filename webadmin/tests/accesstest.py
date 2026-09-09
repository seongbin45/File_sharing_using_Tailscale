"""Role enforcement test.

    cd webadmin && python -m tests.accesstest

Needs httpx2 and websockets (tests/requirements-dev.txt).

The question this answers is the only one that matters about a permission
system: does a viewer actually fail to open a shell? A table that says so and
an endpoint that allows it anyway is worse than no table, because it is
believed.

tailscaled is not running here, so identity.whois is replaced with a stub.
That is the right seam: everything below it - the policy lookup, the level
comparison, the middleware, the WebSocket gate - is the part with the bugs.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets                                    # noqa: E402
from fastapi.testclient import TestClient            # noqa: E402

from app import access, audit, identity              # noqa: E402
import app.main as main                              # noqa: E402

FAILURES: list[str] = []

VIEWER = "readonly@example.com"
OPERATOR = "teammate@example.com"
ADMIN = "boss@example.com"
STRANGER = "nobody@example.com"

# Which tailnet login the next request will appear to come from.
CURRENT = {"login": ADMIN}


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        FAILURES.append(name)


def section(title):
    print(f"\n{title}")


async def fake_whois(address, port):
    login = CURRENT["login"]
    if login is None:
        return None
    return {"login": login, "name": login, "node": "test", "via": "whois"}


def setup() -> None:
    policy_json = json.dumps({
        "default": "none",
        "users": {VIEWER: "viewer", OPERATOR: "operator", ADMIN: "admin"},
    })
    import os
    os.environ["TSCONSOLE_ACCESS_JSON"] = policy_json
    os.environ["TSCONSOLE_AUDIT"] = "/tmp/ts-access-audit.log"
    Path("/tmp/ts-access-audit.log").unlink(missing_ok=True)

    access.policy.load()
    audit.AUDIT_FILE = Path("/tmp/ts-access-audit.log")
    identity.whois = fake_whois          # type: ignore[assignment]
    main.AUTH_MODE = "tailscale"


def test_levels() -> None:
    section("policy")
    p = access.policy
    check("admin resolves", p.level_for(ADMIN) == access.ADMIN)
    check("operator resolves", p.level_for(OPERATOR) == access.OPERATOR)
    check("viewer resolves", p.level_for(VIEWER) == access.VIEWER)
    check("unlisted gets nothing", p.level_for(STRANGER) == access.NONE)
    check("anonymous gets nothing", p.level_for(None) == access.NONE)

    check("terminal requires admin",
          access.required_for("GET", "/api/hosts/sender/terminal") == access.ADMIN)
    check("action requires operator",
          access.required_for("POST", "/api/hosts/sender/action") == access.OPERATOR)
    check("saving a server requires admin",
          access.required_for("POST", "/api/servers") == access.ADMIN)
    check("reads require viewer",
          access.required_for("GET", "/api/overview") == access.VIEWER)
    check("an unknown POST defaults to admin",
          access.required_for("POST", "/api/something/new") == access.ADMIN)

    section("bad policy is refused loudly")
    for bad in ({"users": {"a@b.c": "adminn"}}, {"default": "root"}):
        import os
        saved = os.environ["TSCONSOLE_ACCESS_JSON"]
        os.environ["TSCONSOLE_ACCESS_JSON"] = json.dumps(bad)
        try:
            access.Policy()
            check(f"{bad} rejected", False, "it loaded")
        except ValueError:
            check(f"{bad} rejected", True)
        finally:
            os.environ["TSCONSOLE_ACCESS_JSON"] = saved
    access.policy.load()


def test_http_by_role() -> None:
    section("HTTP by role")
    c = TestClient(main.app, follow_redirects=False)

    matrix = [
        # login,     GET /api/overview, POST action, POST /api/servers
        (ADMIN,    200, 200, 200),
        (OPERATOR, 200, 200, 403),
        (VIEWER,   200, 403, 403),
        (STRANGER, 403, 403, 403),
        (None,     403, 403, 403),
    ]
    for login, read, act, save in matrix:
        CURRENT["login"] = login
        who = login or "anonymous"

        got = c.get("/api/overview").status_code
        check(f"{who:24} read   -> {read}", got == read, got)

        got = c.post("/api/hosts/sender/action", json={"action": "run"}).status_code
        check(f"{who:24} action -> {act}", got == act, got)

        got = c.post("/api/servers",
                     json={"address": "h", "username": "u"}).status_code
        check(f"{who:24} save   -> {save}", got == save, got)

    section("audit log is admin-only")
    for login, expect in ((ADMIN, 200), (OPERATOR, 403), (VIEWER, 403)):
        CURRENT["login"] = login
        got = c.get("/api/audit").status_code
        check(f"{login:24} audit  -> {expect}", got == expect, got)


def test_terminal_by_role() -> None:
    section("terminal by role  (the line that matters)")

    async def drive():
        import uvicorn
        cfg = uvicorn.Config(main.app, host="127.0.0.1", port=8793, log_level="error")
        server = uvicorn.Server(cfg)
        threading.Thread(target=server.run, daemon=True).start()
        for _ in range(80):
            if server.started:
                break
            await asyncio.sleep(0.1)

        url = "ws://127.0.0.1:8793/api/hosts/sender/terminal"
        results = {}
        for login in (ADMIN, OPERATOR, VIEWER, STRANGER, None):
            CURRENT["login"] = login
            try:
                async with websockets.connect(url) as ws:
                    first = await asyncio.wait_for(ws.recv(), 3)
                    results[login] = ("opened", first)
            except Exception as exc:
                results[login] = ("refused", type(exc).__name__)
        server.should_exit = True
        await asyncio.sleep(0.3)
        return results

    results = asyncio.run(drive())
    for login in (ADMIN, OPERATOR, VIEWER, STRANGER, None):
        outcome = results[login][0]
        want = "opened" if login == ADMIN else "refused"
        check(f"{login or 'anonymous':24} terminal -> {want}", outcome == want, results[login])


def test_header_trust() -> None:
    section("identity header trust")
    saved = identity.TRUST_HEADERS

    identity.TRUST_HEADERS = False
    forged = {identity.HEADER_LOGIN: ADMIN}
    check("a forged header is ignored on a public bind",
          identity.from_headers(forged) is None)

    identity.TRUST_HEADERS = True
    got = identity.from_headers(forged)
    check("the header is used on a loopback bind",
          got is not None and got["login"] == ADMIN, got)
    check("and is labelled as coming from serve",
          got is not None and got["via"] == "serve-header")

    identity.TRUST_HEADERS = saved


def test_audit_written() -> None:
    section("audit log")
    events = audit.tail(200)
    kinds = {e["event"] for e in events}
    check("denials are recorded", "denied" in kinds, str(sorted(kinds)))
    check("actions are recorded", "action" in kinds, str(sorted(kinds)))
    check("terminal sessions are recorded", "terminal.open" in kinds, str(sorted(kinds)))

    denied = [e for e in events if e["event"] == "denied"]
    check("a denial names the person", any(e["who"] == VIEWER for e in denied),
          str([e.get("who") for e in denied][:6]))
    check("a denial names what was needed",
          any(e.get("required") == access.ADMIN for e in denied))

    opened = [e for e in events if e["event"] == "terminal.open"]
    check("only the admin opened a terminal",
          all(e["who"] == ADMIN for e in opened), str([e["who"] for e in opened]))

    blob = json.dumps(events, ensure_ascii=False)
    check("no secret leaks into the audit log",
          "password" not in blob.lower(), blob[:200])


if __name__ == "__main__":
    setup()
    test_levels()
    test_http_by_role()
    test_terminal_by_role()
    test_header_trust()
    test_audit_written()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for name in FAILURES:
            print("  -", name)
        sys.exit(1)
    print("all access checks passed")
