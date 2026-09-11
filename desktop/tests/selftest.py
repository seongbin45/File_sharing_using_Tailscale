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
from tsbackup.receiver import Receiver  # noqa: E402
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
    cfg.sender.transport = "http"
    check("http receiver without token is refused",
          any("토큰" in p for p in cfg.problems()))
    check("http receiver uses http server", cfg.receiver_uses_http())


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
        check("archive still on disk for retry", len(list(work.glob("*.7z"))) >= 1)


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

        # first scan records the size; a settled file unpacks on the next scan
        receiver.scan_once()
        time.sleep(0.05)
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
    test_archiver_and_prune()
    test_progress_and_cancel()
    test_sender_pass_with_fallback()
    test_receiver_unpack()
    test_transport_registry()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for name in FAILURES:
            print("  -", name)
        sys.exit(1)
    print("all checks passed")
