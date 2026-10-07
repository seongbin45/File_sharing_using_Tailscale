"""Headless self-test for the pure core.

    cd desktop && python -m tests.selftest

No Qt, no network, no PyInstaller - just the logic that decides what gets
compressed, how it is named, what gets deleted, which transport is tried, and
what the receiver unpacks. That is where the bugs live; the GUI is wiring over
this and is checked separately by importing it under an offscreen Qt.

A fake transport stands in for the network so the full sender pass - prune,
compress, send, keep-or-delete - runs end to end and is asserted on, including
the fallback from a failing transport to a working one.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Windows' console defaults to a legacy codepage (cp1252), not UTF-8, so a
# bare print() of the Korean section/check labels below would crash before
# any real assertion runs. Force UTF-8 before anything else in this file
# prints. See docs/VERIFICATION.md section 15 for the failure this fixes.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tsbackup import archiver, engine_core  # noqa: E402
from tsbackup.config import AppConfig, SenderConfig  # noqa: E402
from tsbackup.receiver import SETTLE_SECONDS, Receiver  # noqa: E402
from tsbackup.transports import base  # noqa: E402

FAILURES: list[str] = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        FAILURES.append(name)


def section(t):
    print(f"\n{t}")


def make_tree(root: Path) -> Path:
    src = root / "PycharmProjects"
    proj = src / "A1_Project"
    proj.mkdir(parents=True)
    (proj / "main.py").write_text("print('hi')\n")
    (proj / ".env").write_text("SECRET=1\n")            # must be included
    (proj / ".gitignore").write_text("venv\n")
    gitdir = proj / ".git"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n")
    (root / "PycharmProjects" / "read_me_한글.txt").write_text("한글 파일명\n", encoding="utf-8")
    return src


# ------------------------------------------------------------- fake transport


class _FakeTransport(base.Transport):
    name = "fake"
    calls: list[str] = []
    should_fail = False
    inbox: Path | None = None

    def send(self, archive_path, progress=None):
        _FakeTransport.calls.append(archive_path.name)
        if progress:
            progress(100)
        if _FakeTransport.should_fail:
            return base.TransferResult(False, "실패하도록 설정됨")
        if _FakeTransport.inbox:
            import shutil
            shutil.copy(archive_path, _FakeTransport.inbox / archive_path.name)
        return base.TransferResult(True, "fake-ok")


class _AlwaysFail(base.Transport):
    name = "fail"

    def send(self, archive_path, progress=None):
        return base.TransferResult(False, "언제나 실패")


# --------------------------------------------------------------------- tests


def test_config_roundtrip():
    section("config")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        cfg = AppConfig()
        cfg.role = "receiver"
        cfg.sender.source_dir = r"C:\Users\me\PycharmProjects"
        cfg.sender.taildrop_targets = ["dev-a", "dev-b"]
        cfg.sender.interval_minutes = 90
        cfg.save(path)
        back = AppConfig.load(path)
        check("role round-trips", back.role == "receiver")
        check("targets round-trip", back.sender.taildrop_targets == ["dev-a", "dev-b"])
        check("interval round-trips", back.sender.interval_minutes == 90)

        # an unknown key in an older/newer file must not crash the load
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["sender"]["some_future_field"] = 1
        raw["future_top"] = True
        path.write_text(json.dumps(raw), encoding="utf-8")
        check("unknown keys are ignored", AppConfig.load(path).role == "receiver")

    section("config validation")
    cfg = AppConfig()
    check("empty sender reports problems", len(cfg.problems()) > 0)
    cfg.role = "receiver"
    cfg.receiver.expects_http = True
    check("http receiver without token is refused",
          any("토큰" in p for p in cfg.problems()))
    check("http receiver uses http server", cfg.receiver_uses_http())


def test_config_migrations():
    section("config migrations")
    with tempfile.TemporaryDirectory() as tmp:
        # A config saved by a version before `onboarded` existed must load
        # as already onboarded - an existing working install must never be
        # sent through the first-run wizard on upgrade.
        pre_existing = {
            "role": "sender",
            "sender": {"source_dir": "x", "work_dir": "y"},
            "receiver": {},
        }
        path = Path(tmp) / "pre_existing.json"
        path.write_text(json.dumps(pre_existing), encoding="utf-8")
        loaded = AppConfig.load(path)
        check("pre-existing config loads as onboarded", loaded.onboarded is True)

        # A genuinely fresh install (no file at all) must still see the
        # wizard.
        fresh = AppConfig.load(Path(tmp) / "missing.json")
        check("fresh install is not onboarded", fresh.onboarded is False)

        # The old receiver_uses_http() misread (checking sender.transport on
        # a receiver box) must migrate to expects_http, so an install
        # already relying on that coincidence keeps receiving over HTTP
        # after the bug is fixed rather than going silently deaf.
        old_http_receiver = {
            "role": "receiver",
            "sender": {"transport": "http"},
            "receiver": {"incoming_dir": "x", "unpack_dir": "y"},
        }
        path2 = Path(tmp) / "old_http_receiver.json"
        path2.write_text(json.dumps(old_http_receiver), encoding="utf-8")
        migrated = AppConfig.load(path2)
        check("old http-receiver workaround migrates to expects_http",
              migrated.receiver.expects_http is True)

        # A config that already has expects_http explicit (even False) must
        # not be overridden by the migration.
        explicit_false = {
            "role": "receiver",
            "sender": {"transport": "http"},
            "receiver": {"incoming_dir": "x", "unpack_dir": "y", "expects_http": False},
        }
        path3 = Path(tmp) / "explicit.json"
        path3.write_text(json.dumps(explicit_false), encoding="utf-8")
        not_migrated = AppConfig.load(path3)
        check("explicit expects_http is not overridden by migration",
              not_migrated.receiver.expects_http is False)


def test_retry_state():
    section("retry/backoff decision")
    from tsbackup.engine_core import next_retry_state

    check("success resets the counter",
          next_retry_state(True, 2) == ("reset", 0))
    check("first failure schedules a retry",
          next_retry_state(False, 0) == ("retry", 1))
    check("second failure schedules a retry",
          next_retry_state(False, 1) == ("retry", 2))
    check("third failure (at the limit) still retries",
          next_retry_state(False, 2) == ("retry", 3))
    check("fourth failure exhausts retries and resets the counter",
          next_retry_state(False, 3) == ("exhausted", 0))
    check("a success right after exhaustion still resets cleanly",
          next_retry_state(True, 0) == ("reset", 0))


def test_pairing():
    section("pairing code encode/decode")
    from tsbackup import pairing

    for ip, secret in [
        ("100.64.0.0", 0),
        ("100.127.255.255", (1 << pairing.SECRET_BITS) - 1),
        ("100.84.12.31", 12345),
    ]:
        code = pairing.pack_code(ip, secret)
        back_ip, back_secret = pairing.unpack_code(code)
        check(f"round-trip {ip}/{secret}", (back_ip, back_secret) == (ip, secret))
        check(f"code is {pairing.CODE_CHARS} characters (plus one dash)",
              len(code) == pairing.CODE_CHARS + 1, code)

    try:
        pairing.pack_code("192.168.1.1", 1)
        check("a non-CGNAT ip is refused", False, "it packed anyway")
    except pairing.PairingError:
        check("a non-CGNAT ip is refused", True)

    try:
        pairing.unpack_code("TOO-SHORT")
        check("a wrong-length code is refused", False, "it decoded anyway")
    except pairing.PairingError:
        check("a wrong-length code is refused", True)

    section("pairing exchange (listener + sender-side handlers)")
    import hashlib
    import tempfile
    import time
    from pathlib import Path

    from tsbackup.config import AppConfig

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        incoming = root / "incoming"
        incoming.mkdir()
        unpack = root / "unpack"
        unpack.mkdir()
        known_path = root / "known_senders.json"

        cfg = AppConfig()
        cfg.role = "receiver"
        cfg.receiver.incoming_dir = str(incoming)
        cfg.receiver.unpack_dir = str(unpack)

        listener = pairing.PairingListener(cfg, known_path, log=lambda _m: None)
        # No real Tailscale interface in this sandbox to bind to - exercise
        # the handler logic directly (what do_POST calls into) rather than
        # a real socket; a real bind is proven manually against a live
        # Tailscale-connected machine, per the plan's Phase 3 manual check.
        listener._tailscale_ip = "100.90.1.2"
        code = listener.regenerate()
        check("generated code decodes to the listener's own ip",
              pairing.unpack_code(code)[0] == "100.90.1.2")

        payload = listener._handle_pair({
            "secret": listener._session.secret,
            "device_name": "sender-1",
            "device_id": "dev-abc",
            "interval_minutes": 60,
            "transport_preference": "taildrop",
        })
        check("pair response carries the receiver's incoming_dir",
              payload.get("incoming_dir") == str(incoming))
        check("pair response carries a confirm_token", bool(payload.get("confirm_token")))
        check("is_paired() is true right after /pair, but is_confirmed() is not yet - "
              "the mandatory test-transfer still needs the listener alive to confirm",
              listener.is_paired() and not listener.is_confirmed())

        registry = pairing.load_known_senders(known_path)
        check("an unconfirmed pair does NOT yet write known_senders.json - "
              "only a real matching /confirm should, so a pairing that "
              "never completes can't later trigger a false silence-"
              "detection warning for a sender that never really worked",
              "dev-abc" not in registry, registry)

        # Exercise the real incoming -> settle -> unpack pipeline (not a file
        # dropped straight into unpack_dir) - _confirm_test_file() has to
        # scan twice on the same Receiver instance for scan_once()'s settle
        # check to ever unpack anything (a single call on a fresh instance
        # has no prior size to compare against and always skips).
        content = b"pairing self-test payload"
        digest = hashlib.sha256(content).hexdigest()
        archive_path = incoming / "PycharmProjects_2026_01_01_00_00.7z"
        import py7zr
        probe_src = root / "probe_src"
        probe_src.mkdir()
        (probe_src / "pair_test.bin").write_bytes(content)
        with py7zr.SevenZipFile(archive_path, "w") as archive:
            archive.writeall(probe_src, "PycharmProjects")

        ok_resp = listener._handle_confirm({
            "confirm_token": payload["confirm_token"],
            "test_name": "pair_test.bin",
            "expected_hash": digest,
        })
        check("confirm unpacks the real archive and matches the test file",
              ok_resp.get("match") is True)
        check("archive was actually unpacked, not just hash-compared in place",
              any(unpack.rglob("pair_test.bin")))
        check("is_confirmed() becomes true only after a real matching /confirm - "
              "this is what the wizard's receiver page waits for before letting "
              "the person close it and tear down the listener",
              listener.is_confirmed())

        registry = pairing.load_known_senders(known_path)
        check("paired sender IS recorded in known_senders.json once /confirm "
              "actually matches - the registry write moved from pair-time to "
              "confirm-time",
              registry.get("dev-abc", {}).get("device_name") == "sender-1", registry)

        bad_resp = listener._handle_confirm({
            "confirm_token": payload["confirm_token"],
            "test_name": "pair_test.bin",
            "expected_hash": "0" * 64,
        })
        check("confirm reports no match for a wrong hash", bad_resp.get("match") is False)

        section("pairing: single-use, wrong-attempt lockout, expiry")
        try:
            listener._handle_pair({"secret": listener._session.secret,
                                    "device_name": "x", "device_id": "y"})
            check("a used code is refused on a second /pair", False, "it paired again")
        except pairing.PairingError:
            check("a used code is refused on a second /pair", True)

        listener.regenerate()
        for _ in range(pairing.MAX_WRONG_ATTEMPTS):
            time.sleep(pairing.MIN_REQUEST_INTERVAL + 0.05)
            try:
                listener._handle_pair({"secret": -1, "device_name": "x", "device_id": "y"})
            except pairing.PairingError:
                pass
        check(f"{pairing.MAX_WRONG_ATTEMPTS} wrong attempts increments the counter",
              listener._session.wrong_attempts == pairing.MAX_WRONG_ATTEMPTS)
        time.sleep(pairing.MIN_REQUEST_INTERVAL + 0.05)
        try:
            listener._handle_pair({"secret": listener._session.secret,
                                    "device_name": "x", "device_id": "y"})
            check("the code is locked out even with the correct secret", False, "it paired")
        except pairing.PairingError as exc:
            check("the code is locked out even with the correct secret", "폐기" in str(exc), str(exc))

        listener.regenerate()
        listener._session.created_at = time.time() - pairing.CODE_TTL_SECONDS - 1
        try:
            listener._handle_pair({"secret": listener._session.secret,
                                    "device_name": "x", "device_id": "y"})
            check("an expired code is refused", False, "it paired")
        except pairing.PairingError as exc:
            check("an expired code is refused", "만료" in str(exc), str(exc))

        section("pairing: validation-before-consume, idempotent retry, confirm expiry")
        listener.regenerate()
        try:
            listener._handle_pair({"secret": listener._session.secret})
            check("a /pair missing device_name/device_id is refused", False, "it paired")
        except pairing.PairingError as exc:
            check("a /pair missing device_name/device_id is refused",
                  "정보가 없습니다" in str(exc), str(exc))
        check("...and crucially does NOT consume the code - a real /pair "
              "with the same secret right after still succeeds, instead of "
              "the whole code being burned by one malformed request",
              not listener._session.paired)

        time.sleep(pairing.MIN_REQUEST_INTERVAL + 0.05)
        first = listener._handle_pair({"secret": listener._session.secret,
                                        "device_name": "z", "device_id": "dev-z"})
        check("the real /pair right after succeeds, with a confirm_token",
              bool(first.get("confirm_token")))

        time.sleep(pairing.MIN_REQUEST_INTERVAL + 0.05)
        repeat = listener._handle_pair({"secret": listener._session.secret,
                                         "device_name": "z", "device_id": "dev-z"})
        check("a repeat /pair from the SAME device_id on an already-used "
              "code returns the identical cached response instead of "
              "refusing - this is what lets a sender retry after a failed "
              "test-transfer without the receiver having to issue a new "
              "code",
              repeat == first, (repeat, first))

        time.sleep(pairing.MIN_REQUEST_INTERVAL + 0.05)
        try:
            listener._handle_pair({"secret": listener._session.secret,
                                    "device_name": "other", "device_id": "dev-other"})
            check("a DIFFERENT device_id against an already-used code is "
                  "still refused - the idempotent retry doesn't weaken "
                  "single-use for anyone but the device that paired",
                  False, "it paired")
        except pairing.PairingError:
            check("a DIFFERENT device_id against an already-used code is "
                  "still refused - the idempotent retry doesn't weaken "
                  "single-use for anyone but the device that paired", True)

        listener.regenerate()
        time.sleep(pairing.MIN_REQUEST_INTERVAL + 0.05)
        stale = listener._handle_pair({"secret": listener._session.secret,
                                        "device_name": "z2", "device_id": "dev-z2"})
        listener._session.created_at = time.time() - pairing.CODE_TTL_SECONDS - 1
        check("is_expired() is true once a paired-but-unconfirmed session "
              "passes its TTL - the receiver page uses this to offer the "
              "새 코드 / 시험 없이 마침 escape hatches instead of waiting "
              "forever on a sender that may never come back",
              listener.is_expired())
        try:
            listener._handle_confirm({"confirm_token": stale["confirm_token"],
                                       "test_name": "x", "expected_hash": "y"})
            check("/confirm refuses once the code has expired, matching "
                  "/pair's own existing expiry check", False, "it confirmed")
        except pairing.PairingError as exc:
            check("/confirm refuses once the code has expired, matching "
                  "/pair's own existing expiry check", "만료" in str(exc), str(exc))

        listener.stop()


def test_heartbeat_on_real_arrival():
    section("silence-detection registry updates on a real (non-pairing) arrival")
    from tsbackup import pairing
    from tsbackup.transports import base

    base._REGISTRY["fake"] = _FakeTransport
    _FakeTransport.calls = []
    _FakeTransport.should_fail = False

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root)
        work = root / "work"
        incoming = root / "incoming"
        unpack = root / "unpack"
        incoming.mkdir()

        known_path = root / pairing.KNOWN_SENDERS_FILENAME
        pairing._save_registry(known_path, {
            "dev-heartbeat": {
                "device_name": "sender-heartbeat", "interval_minutes": 60,
                "first_seen": time.time() - 999999, "last_seen": time.time() - 999999,
            },
        })
        stale_last_seen = pairing.load_known_senders(known_path)["dev-heartbeat"]["last_seen"]

        # A paired sender's ordinary run - not the wizard's test-transfer -
        # embeds its device_id via AppConfig.device_id, exactly as
        # engine_core.run_sender_once() does for any config that has one.
        _FakeTransport.inbox = incoming
        cfg = AppConfig()
        cfg.device_id = "dev-heartbeat"
        cfg.sender = SenderConfig(source_dir=str(src), work_dir=str(work), level=1,
                                  keep_local=1, transport="fake", interval_minutes=60)
        result = engine_core.run_sender_once(cfg, lambda _l: None)
        check("real run succeeds", result.ok, result.detail)
        _FakeTransport.inbox = None

        rcfg = AppConfig()
        rcfg.role = "receiver"
        rcfg.receiver.incoming_dir = str(incoming)
        rcfg.receiver.unpack_dir = str(unpack)

        # Point receiver.py's own config_dir() lookup at this test's temp
        # registry rather than the real %LOCALAPPDATA%/~/.config path, for
        # the whole scan (the actual unpack - and _record_heartbeat call -
        # happens on the SECOND scan_once(), once the settle check passes).
        import tsbackup.receiver as receiver_module
        real_config_dir = receiver_module.config_dir
        receiver_module.config_dir = lambda: root
        try:
            receiver = Receiver(rcfg, lambda _l: None)
            receiver.scan_once()
            time.sleep(SETTLE_SECONDS + 0.5)
            receiver.scan_once()
        finally:
            receiver_module.config_dir = real_config_dir

        updated = pairing.load_known_senders(known_path)
        check("known sender's last_seen advances on a real arrival",
              updated["dev-heartbeat"]["last_seen"] > stale_last_seen, updated)
        check("interval_minutes carries through from the archive's marker",
              updated["dev-heartbeat"]["interval_minutes"] == 60, updated)

        no_longer_overdue = pairing.overdue_senders(updated)
        check("the sender is no longer overdue after a real arrival",
              not any(d["device_id"] == "dev-heartbeat" for d in no_longer_overdue),
              no_longer_overdue)

        section("an un-paired config (no device_id) sends no marker, updates nothing")
        # Fresh work/incoming/unpack (src is reused, harmlessly - only read,
        # never written), not a reuse of the ones above: _unpack()'s
        # destination folder name is derived only from the archive's
        # timestamp stamp, not the source folder's name, so a second run
        # within the same test-minute sharing incoming/unpack would extract
        # on top of the first run's already-unpacked .ts_sender.json and
        # defeat the very thing this checks.
        work2 = root / "work2"
        incoming2 = root / "incoming2"
        unpack2 = root / "unpack2"
        incoming2.mkdir()

        _FakeTransport.inbox = incoming2
        cfg_unpaired = AppConfig()
        cfg_unpaired.sender = SenderConfig(source_dir=str(src), work_dir=str(work2), level=1,
                                           keep_local=1, transport="fake")
        engine_core.run_sender_once(cfg_unpaired, lambda _l: None)
        _FakeTransport.inbox = None

        rcfg2 = AppConfig()
        rcfg2.role = "receiver"
        rcfg2.receiver.incoming_dir = str(incoming2)
        rcfg2.receiver.unpack_dir = str(unpack2)

        before = pairing.load_known_senders(known_path)
        receiver_module.config_dir = lambda: root
        try:
            receiver_unpaired = Receiver(rcfg2, lambda _l: None)
            receiver_unpaired.scan_once()
            time.sleep(SETTLE_SECONDS + 0.5)
            receiver_unpaired.scan_once()
        finally:
            receiver_module.config_dir = real_config_dir
        after = pairing.load_known_senders(known_path)
        check("registry is unchanged by an un-paired sender's arrival",
              after == before, (before, after))


def test_overdue_senders():
    section("silence detection (tsbackup.pairing.overdue_senders)")
    from tsbackup import pairing

    now = 1_000_000.0
    registry = {
        "on-time": {"device_name": "a", "interval_minutes": 60, "last_seen": now - 60},
        "late": {"device_name": "b", "interval_minutes": 60, "last_seen": now - 60 * 200},
        "borderline-ok": {"device_name": "c", "interval_minutes": 60, "last_seen": now - 60 * 89},
        "borderline-late": {"device_name": "d", "interval_minutes": 60, "last_seen": now - 60 * 91},
        "no-interval-yet": {"device_name": "e", "last_seen": now - 60 * 1000},
    }
    overdue = pairing.overdue_senders(registry, now=now)
    names = {d["device_name"] for d in overdue}
    check("an on-time sender is not overdue", "a" not in names)
    check("a very late sender is overdue", "b" in names)
    check("just under 1.5x the interval is not yet overdue", "c" not in names)
    check("just over 1.5x the interval is overdue", "d" in names)
    check("a sender missing interval_minutes is skipped, not guessed at",
          "e" not in names)
    check("most overdue sorts first",
          overdue[0]["device_name"] == "b" if overdue else False, overdue)


def test_run_test_transfer():
    section("wizard's test-transfer step (tsbackup.pairing.run_test_transfer)")
    import tempfile
    import threading

    from tsbackup import pairing
    from tsbackup.config import AppConfig
    from tsbackup.transports import base

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        incoming = root / "incoming"
        incoming.mkdir()
        unpack = root / "unpack"
        unpack.mkdir()
        known = root / "known.json"

        cfg_r = AppConfig()
        cfg_r.role = "receiver"
        cfg_r.receiver.incoming_dir = str(incoming)
        cfg_r.receiver.unpack_dir = str(unpack)

        listener = pairing.PairingListener(cfg_r, known)
        server = pairing._PairingHTTPServer(
            ("127.0.0.1", 0), pairing._PairingHandler, listener=listener
        )
        listener._server = server
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        port = server.server_address[1]

        listener._tailscale_ip = "100.90.1.2"  # cosmetic only below
        listener.regenerate()
        secret = listener._session.secret

        # This sandbox has no real Tailscale interface, so route the sender
        # side's decoded ip at the real bound loopback port instead of the
        # fixed PAIRING_PORT a real install would use - everything else
        # (secret, handler logic, HTTP wire format) is exercised for real.
        real_unpack_code = pairing.unpack_code
        real_port = pairing.PAIRING_PORT
        pairing.unpack_code = lambda code: ("127.0.0.1", secret)
        pairing.PAIRING_PORT = port
        try:
            class _CopyToIncoming(base.Transport):
                name = "_selftest_copy_to_incoming"

                def send(self, archive_path, progress=None):
                    import shutil
                    shutil.copy(archive_path, incoming / archive_path.name)
                    return base.TransferResult(True, "copied")

            base._REGISTRY["_selftest_copy_to_incoming"] = _CopyToIncoming

            pair_payload = listener._handle_pair({
                "secret": secret, "device_name": "sender-x", "device_id": "dev-x",
                "interval_minutes": 60, "transport_preference": "taildrop",
            })

            sender_cfg = AppConfig().sender
            sender_cfg.transport = "_selftest_copy_to_incoming"

            steps = []
            ok, detail = pairing.run_test_transfer(
                sender_cfg, "IGNORED-CODE", pair_payload["confirm_token"],
                step=lambda label, status: steps.append((label, status)),
            )
            check("test-transfer succeeds end to end (compress+send+confirm)",
                  ok, detail)
            check("every step reports ok, in order",
                  [s for s in steps if s[1] != "running"] ==
                  [("압축", "ok"), ("전송", "ok"), ("수신·해제 확인", "ok")], steps)

            # A wrong confirm_token must fail cleanly at the confirm step,
            # not raise or silently report success.
            bad_steps = []
            bad_ok, bad_detail = pairing.run_test_transfer(
                sender_cfg, "IGNORED-CODE", "wrong-token",
                step=lambda label, status: bad_steps.append((label, status)),
            )
            check("a wrong confirm_token fails at the confirm step",
                  not bad_ok and ("수신·해제 확인", "fail") in bad_steps, (bad_ok, bad_steps))
        finally:
            pairing.unpack_code = real_unpack_code
            pairing.PAIRING_PORT = real_port
            listener.stop()


def test_hostkeys():
    section("host key fingerprint (tsbackup/hostkeys.py)")
    import paramiko

    from tsbackup import hostkeys as hk

    key = paramiko.RSAKey.generate(2048)
    other = paramiko.RSAKey.generate(2048)
    fp = hk.fingerprint(key)
    check("fingerprint looks like ssh-keygen output",
          fp.startswith("SHA256:") and len(fp) > 20, fp)

    check("a fingerprint matches itself", hk.same(fp, fp))
    check("a fingerprint mismatches a different key",
          not hk.same(fp, hk.fingerprint(other)))

    # People (and a pairing exchange payload) hand these around with or
    # without the SHA256: prefix, and with or without base64 padding -
    # webadmin/app/hostkeys.py tolerates the same variants, and this must
    # keep matching it.
    for variant in (fp[7:], fp + "=", fp.replace("SHA256:", "sha256:")):
        check(f"tolerant of written form: {variant[:18]}...",
              hk.same(fp, variant))


def test_archiver_and_prune():
    section("archiver")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root)
        work = root / "work"

        result = archiver.create_archive(str(src), str(work), level=1)
        check("archive created", result.ok, result.error)
        check("named with a timestamp",
              archiver.STAMP_RE.search(result.path.name) is not None, result.path.name)
        check("top folder in the name", result.path.name.startswith("PycharmProjects_"))

        import py7zr
        with py7zr.SevenZipFile(result.path, "r") as z:
            names = z.getnames()
        check(".env is included", any(n.endswith(".env") for n in names))
        check(".git is included", any("/.git/" in n or n.endswith("/.git") for n in names))
        check("korean filename survives", any("한글" in n for n in names), str(names))

        section("retention (지우고 새로 압축)")
        # forge several older archives for the same source
        for stamp in ("2020_01_01_00_00", "2020_01_02_00_00", "2020_01_03_00_00"):
            (work / f"PycharmProjects_{stamp}.7z").write_bytes(b"x")
        (work / "SomethingElse_2020_01_01_00_00.7z").write_bytes(b"y")  # unrelated

        archiver.prune_local(str(work), str(src), keep=1)
        remaining = sorted(p.name for p in work.glob("PycharmProjects_*.7z"))
        check("keeps exactly one newest", len(remaining) == 1, str(remaining))
        check("keeps the newest by name", remaining[0].endswith(result.path.name.split("_", 1)[1]),
              remaining[0])
        check("unrelated archive untouched",
              (work / "SomethingElse_2020_01_01_00_00.7z").exists())


def test_progress_and_cancel():
    section("progress and cancel")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root)
        # add enough files that progress ticks and cancel has something to stop
        for i in range(200):
            (src / "A1_Project" / f"f{i}.txt").write_text("x" * 100)
        work = root / "work"

        seen = []
        archiver.create_archive(str(src), str(work), 1, progress=lambda p: seen.append(p))
        check("progress reaches 100", seen and seen[-1] == 100, str(seen[-3:]))

        # cancel immediately -> no archive left behind
        for p in work.glob("*.7z"):
            p.unlink()
        res = archiver.create_archive(str(src), str(work), 1, cancelled=lambda: True)
        check("cancel returns not-ok", not res.ok)
        check("cancel leaves no archive", not any(work.glob("*.7z")),
              str(list(work.glob("*.7z"))))


def test_sender_pass_with_fallback():
    section("sender pass end to end")
    base._REGISTRY["fake"] = _FakeTransport
    base._REGISTRY["fail"] = _AlwaysFail
    _FakeTransport.calls = []
    _FakeTransport.should_fail = False

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root)
        work = root / "work"
        cfg = AppConfig()
        cfg.sender = SenderConfig(
            source_dir=str(src), work_dir=str(work), level=1, keep_local=1,
            transport="fail", fallback_transports=["fake"],
        )
        lines = []
        result = engine_core.run_sender_once(cfg, lines.append)
        check("run succeeds via fallback", result.ok, result.detail)
        check("primary was tried first",
              any("fail" in ln for ln in lines), str(lines[-4:]))
        check("fallback actually sent", _FakeTransport.calls, str(_FakeTransport.calls))
        check("transport reported is the fallback", result.transport == "fake", result.transport)
        # keep_local=1 -> the sent archive remains as the newest kept copy
        check("newest archive kept locally", len(list(work.glob("*.7z"))) == 1)

        section("all transports fail -> archive retained")
        _FakeTransport.should_fail = True
        cfg.sender.transport = "fail"
        cfg.sender.fallback_transports = ["fake"]
        r2 = engine_core.run_sender_once(cfg, lambda _l: None)
        check("run reports failure", not r2.ok)
        check("archive kept in pending/ for retry",
              len(list((work / "pending").glob("*.7z"))) == 1)


def test_pending_resend():
    section("pending: an unsent archive is resent before compressing anew")
    from tsbackup import filelock, pending

    base._REGISTRY["fake"] = _FakeTransport
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root)
        work = root / "work"
        cfg = AppConfig()
        cfg.sender = SenderConfig(source_dir=str(src), work_dir=str(work), level=1,
                                  keep_local=1, transport="fake")

        _FakeTransport.calls = []
        _FakeTransport.should_fail = True
        engine_core.run_sender_once(cfg, lambda _l: None)
        parked = list((work / "pending").glob("*.7z"))
        check("a failed archive is parked in pending/ with its record",
              len(parked) == 1 and parked[0].with_suffix(".json").exists(), str(parked))

        _FakeTransport.should_fail = False
        lines: list[str] = []
        result = engine_core.run_sender_once(cfg, lines.append)
        check("the next run resends that same archive", result.ok and
              _FakeTransport.calls == [parked[0].name] * 2, str(_FakeTransport.calls))
        check("...without compressing a new snapshot",
              not any(ln.startswith("압축 완료") for ln in lines), str(lines))
        check("...and logs it as a pending resend with its snapshot time",
              any("pending 재전송" in ln and "스냅샷" in ln for ln in lines), str(lines))
        check("pending/ is empty once the send is confirmed",
              not any((work / "pending").iterdir()))

        section("pending: a damaged archive is discarded, never resent")
        _FakeTransport.should_fail = True
        engine_core.run_sender_once(cfg, lambda _l: None)
        bad = next((work / "pending").glob("*.7z"))
        data = bytearray(bad.read_bytes())
        data[len(data) // 2] ^= 0xFF
        bad.write_bytes(bytes(data))
        _FakeTransport.should_fail = False
        lines = []
        engine_core.run_sender_once(cfg, lines.append)
        check("hash mismatch is detected and the archive deleted",
              any("해시 불일치" in ln for ln in lines), str(lines))
        check("a fresh snapshot is compressed and sent instead",
              any(ln.startswith("압축 완료") for ln in lines) and
              not any((work / "pending").iterdir()), str(lines))

        section("pending: retention")
        _FakeTransport.should_fail = True
        engine_core.run_sender_once(cfg, lambda _l: None)
        later = time.time() + (pending.KEEP_DAYS + 1) * 86400
        expired = pending.take(str(work), str(src), lambda _l: None, now=later)
        check(f"a pending archive older than {pending.KEEP_DAYS} days is deleted",
              expired is None and not any((work / "pending").iterdir()))

        section("pending: one sender at a time")
        held = filelock.try_lock(work / ".sender.lock")
        try:
            r = engine_core.run_sender_once(cfg, lambda _l: None)
            check("a run that finds the work_dir locked does nothing",
                  not r.ok and "진행 중" in r.detail, r.detail)
        finally:
            held.close()


def test_receiver_unpack():
    section("receiver unpack")
    base._REGISTRY["fake"] = _FakeTransport
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        src = make_tree(root)
        work = root / "work"
        incoming = root / "incoming"
        unpack = root / "unpack"
        incoming.mkdir()

        # produce a real archive and drop it in the incoming dir
        _FakeTransport.should_fail = False
        _FakeTransport.inbox = incoming
        cfg = AppConfig()
        cfg.sender = SenderConfig(source_dir=str(src), work_dir=str(work), level=1,
                                  keep_local=1, transport="fake")
        engine_core.run_sender_once(cfg, lambda _l: None)
        _FakeTransport.inbox = None

        rcfg = AppConfig()
        rcfg.role = "receiver"
        rcfg.receiver.incoming_dir = str(incoming)
        rcfg.receiver.unpack_dir = str(unpack)
        rcfg.receiver.delete_after_unpack = True
        receiver = Receiver(rcfg, lambda _l: None)

        # first scan records the size; a settled file unpacks on the next
        # scan, once real time (not just a repeated poll) has passed the
        # settle window - see receiver.py's _settled().
        receiver.scan_once()
        time.sleep(SETTLE_SECONDS + 0.5)
        done = receiver.scan_once()
        check("one archive unpacked", done == 1, done)
        folders = list(unpack.glob("PycharmProjects_*"))
        check("unpacked into a timestamped folder", len(folders) == 1, str(folders))
        check("content restored",
              any(p.name == "main.py" for p in folders[0].rglob("*")) if folders else False)
        check(".env restored",
              any(p.name == ".env" for p in folders[0].rglob("*")) if folders else False)
        check("archive deleted after unpack", not any(incoming.glob("*.7z")))

        section("half-written .part is ignored")
        (incoming / "PycharmProjects_2099_01_01_00_00.7z.part").write_bytes(b"partial")
        check("no crash on .part", receiver.scan_once() == 0)

        section("taildrop pull (tailscale file get) - receiver.py's _pull_taildrop")
        from unittest.mock import patch

        with patch("tsbackup.transports.taildrop.tailscale_binary", return_value=None):
            ok = True
            try:
                receiver.scan_once()
            except Exception:  # noqa: BLE001
                ok = False
            check("no tailscale binary found -> scan_once() still runs cleanly "
                  "(this is the normal case for sftp-only receivers, and this "
                  "sandbox, which has no tailscale install at all)", ok)

        with patch("tsbackup.transports.taildrop.tailscale_binary", return_value="tailscale"), \
             patch("tsbackup.receiver.subprocess.run") as run:
            receiver.scan_once()
            check("tailscale binary found -> `tailscale file get` is invoked "
                  "on every scan pass, actively pulling from Taildrop's own "
                  "fixed save location instead of only watching incoming_dir "
                  "(which Taildrop itself never writes into)",
                  run.called)
            args = run.call_args[0][0]
            check("invoked with --wait=false so a poll never blocks on it",
                  "--wait=false" in args, args)
            check("targets receiver.incoming_dir, not Taildrop's own default",
                  args[-1] == str(incoming), args)

        section("taildrop Downloads sweep - the Windows GUI client auto-"
                "claims pending files into Downloads itself, on its own "
                "timer, almost always winning the race against `tailscale "
                "file get` above - so this is the fallback that actually "
                "finds them afterward")
        fake_home = root / "FakeHome"
        downloads = fake_home / "Downloads"
        downloads.mkdir(parents=True)
        matching = downloads / "PycharmProjects_2026_02_02_02_02.7z"
        matching.write_bytes(b"looks like one of ours")
        unrelated = downloads / "vacation_photo.7z"
        unrelated.write_bytes(b"not ours - don't touch it")

        with patch("tsbackup.transports.taildrop.tailscale_binary", return_value=None), \
             patch("tsbackup.receiver.Path.home", return_value=fake_home):
            receiver.scan_once()
        check("an archive matching our own naming scheme (archiver.py's "
              "STAMP_RE) is moved out of Downloads into incoming_dir",
              (incoming / matching.name).exists() and not matching.exists())
        check("a .7z that doesn't match our naming scheme is left alone - "
              "Downloads is the user's own folder, not ours to sweep",
              unrelated.exists())


def test_http_upload_dropped_midway():
    section("http receiver - an upload cut off mid-body is not an arrival")
    import socket

    with tempfile.TemporaryDirectory() as tmp:
        incoming = Path(tmp) / "incoming"
        rcfg = AppConfig()
        rcfg.role = "receiver"
        rcfg.receiver.incoming_dir = str(incoming)
        rcfg.receiver.http_port = 0
        rcfg.receiver.http_token = "tok"
        receiver = Receiver(rcfg, lambda _l: None)
        receiver.start_http()
        port = receiver._http.server_address[1]
        try:
            def upload(name: str, declared: int, body: bytes) -> None:
                with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                    s.sendall(
                        f"POST /upload HTTP/1.1\r\nHost: x\r\nX-TsBackup-Token: tok\r\n"
                        f"X-TsBackup-Name: {name}\r\nContent-Length: {declared}\r\n\r\n"
                        .encode() + body
                    )
                    s.shutdown(socket.SHUT_WR)
                    s.recv(1024)

            upload("PycharmProjects_2099_01_01_00_00.7z", 1000, b"x" * 100)
            check("a truncated upload leaves no .7z for the scan loop to unpack",
                  not any(incoming.glob("*.7z")), str(list(incoming.iterdir())))
            check("...and no .part is left behind either",
                  not any(incoming.glob("*.part")))

            upload("PycharmProjects_2099_01_01_00_01.7z", 100, b"x" * 100)
            arrived = incoming / "PycharmProjects_2099_01_01_00_01.7z"
            check("a complete upload still lands under its final name",
                  arrived.exists() and arrived.stat().st_size == 100)
        finally:
            receiver.stop_http()


def test_http_resume():
    section("http resume - a cut upload continues from the last byte, verified")
    import hashlib
    import json as _json
    import socket

    from tsbackup.transports import http_push

    http_push.RETRY_SLEEP = 0
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        incoming = root / "incoming"
        rcfg = AppConfig()
        rcfg.role = "receiver"
        rcfg.receiver.incoming_dir = str(incoming)
        rcfg.receiver.http_port = 0
        rcfg.receiver.http_token = "tok"
        receiver = Receiver(rcfg, lambda _l: None)
        receiver.start_http()
        port = receiver._http.server_address[1]

        archive = root / "PycharmProjects_2099_02_02_02_02.7z"
        data = os.urandom(300_000)
        archive.write_bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        scfg = SenderConfig(host="127.0.0.1", port=port, http_token="tok")
        part = incoming / (archive.name + ".part")
        final = incoming / archive.name

        def cut_upload(nbytes: int) -> None:
            with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
                s.sendall(
                    f"POST /upload HTTP/1.1\r\nHost: x\r\nX-TsBackup-Token: tok\r\n"
                    f"X-TsBackup-Name: {archive.name}\r\nX-TsBackup-Size: {len(data)}\r\n"
                    f"X-TsBackup-SHA256: {sha}\r\nX-TsBackup-Offset: 0\r\n"
                    f"Content-Length: {len(data)}\r\n\r\n".encode() + data[:nbytes])
                s.shutdown(socket.SHUT_WR)
                s.recv(1024)

        def send() -> tuple[bool, list[str]]:
            lines: list[str] = []
            ok = http_push.HttpTransport(scfg, lines.append).send(archive).ok
            return ok, lines

        try:
            cut_upload(100_000)
            check("a cut upload is kept as .part for resume, never promoted",
                  part.exists() and part.stat().st_size == 100_000 and not final.exists())
            ok, lines = send()
            check("the next send resumes from byte 100000",
                  any("100000/300000" in ln for ln in lines), str(lines))
            check("...and the result is byte-identical", ok and final.read_bytes() == data)
            final.unlink()

            # Same name, different archive (a fresh snapshot in the same
            # minute): its .part must not be appended onto.
            cut_upload(100_000)
            json_path = Path(str(part) + ".json")
            record = _json.loads(json_path.read_text())
            record["sha256"] = "0" * 64
            json_path.write_text(_json.dumps(record))
            ok, lines = send()
            check("a .part from a different archive with the same name restarts at 0",
                  ok and final.read_bytes() == data and not any("이어받기" in ln for ln in lines),
                  str(lines))
            final.unlink()

            cut_upload(100_000)
            raw = bytearray(part.read_bytes())
            raw[10] ^= 0xFF
            part.write_bytes(bytes(raw))
            ok, _lines = send()
            check("corrupt bytes already held are caught by sha256 and re-sent whole",
                  ok and final.read_bytes() == data)
            final.unlink()

            # Fully written, then the receiver died before verify + rename.
            part.write_bytes(data)
            Path(str(part) + ".json").write_text(_json.dumps({"size": len(data), "sha256": sha}))
            ok, lines = send()
            check("a complete-but-unrenamed .part is finished without resending",
                  ok and final.read_bytes() == data and any("이미 완전한" in ln for ln in lines),
                  str(lines))
        finally:
            receiver.stop_http()


def test_sftp_resume():
    section("sftp resume - continue a cut upload only if it is the same archive")
    import hashlib
    import json as _json

    from tsbackup.transports.sftp import _upload_resumable

    class LocalSftp:
        """The few SFTPClient calls _upload_resumable makes, on local files."""
        def open(self, path, mode):
            return open(path, mode)

        def stat(self, path):
            return os.stat(path)

        def remove(self, path):
            os.remove(path)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        local = root / "PycharmProjects_2099_04_04_04_04.7z"
        data = os.urandom(200_000)
        local.write_bytes(data)
        remote = str(root / "remote.7z.part")
        record = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}

        Path(remote).write_bytes(data[:80_000])
        Path(remote + ".json").write_text(_json.dumps(record))
        lines: list[str] = []
        ok = _upload_resumable(LocalSftp(), local, remote, None, lines.append)
        check("a matching remote .part is continued from its last byte",
              ok and any("80000/200000" in ln for ln in lines), str(lines))
        check("...giving the identical file", Path(remote).read_bytes() == data)

        Path(remote).write_bytes(b"x" * 80_000)
        Path(remote + ".json").write_text(_json.dumps({**record, "sha256": "0" * 64}))
        lines = []
        ok = _upload_resumable(LocalSftp(), local, remote, None, lines.append)
        check("a remote .part recorded for a different archive is rewritten from 0",
              ok and not lines and Path(remote).read_bytes() == data, str(lines))


def test_transport_registry():
    section("transport registry")
    from tsbackup import transports
    names = transports.available()
    check("taildrop registered", "taildrop" in names, str(names))
    check("sftp registered", "sftp" in names)
    check("http registered", "http" in names)
    try:
        transports.build("nonesuch", SenderConfig(), lambda _l: None)
        check("unknown transport refused", False, "it built one")
    except ValueError:
        check("unknown transport refused", True)


if __name__ == "__main__":
    test_config_roundtrip()
    test_config_migrations()
    test_retry_state()
    test_pairing()
    test_heartbeat_on_real_arrival()
    test_overdue_senders()
    test_run_test_transfer()
    test_hostkeys()
    test_archiver_and_prune()
    test_progress_and_cancel()
    test_sender_pass_with_fallback()
    test_pending_resend()
    test_receiver_unpack()
    test_http_upload_dropped_midway()
    test_http_resume()
    test_sftp_resume()
    test_transport_registry()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for name in FAILURES:
            print("  -", name)
        sys.exit(1)
    print("all checks passed")
