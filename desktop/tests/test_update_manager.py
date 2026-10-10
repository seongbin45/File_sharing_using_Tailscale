"""Headless tests for the update_manager/ package - no Qt, no live GitHub,
no real process kill. Mirrors selftest.py's/gui_smoke.py's own house style
(check()/section()/FAILURES, no pytest dependency) rather than CloneUp's
pytest-based test_update_manager.py, which this file's coverage is adapted
from for the parts that actually ported over (see the update-manager plan).

    cd desktop && python -m tests.test_update_manager
"""

from __future__ import annotations

import hashlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from update_manager import apply as apply_mod  # noqa: E402
from update_manager import github_release  # noqa: E402
from update_manager import paths  # noqa: E402
from update_manager import process_win as pw  # noqa: E402
from update_manager import status_io  # noqa: E402
from update_manager.__main__ import run_once  # noqa: E402
from update_manager.github_release import LatestRelease, host_allowed  # noqa: E402
from update_manager.logutil import setup_logging  # noqa: E402
from update_manager.versioning import is_newer, normalize_version  # noqa: E402

FAILURES: list[str] = []


def check(name, cond, detail=""):
    print(("  PASS  " if cond else "  FAIL  ") + name + (f"   {detail}" if not cond else ""))
    if not cond:
        FAILURES.append(name)


def section(t):
    print(f"\n{t}")


def _ok_url() -> str:
    return "https://objects.githubusercontent.com/github-production-release-asset-2e65be/x"


class _FakeHeaders(dict):
    def get(self, key, default=None):
        for k, v in self.items():
            if k.lower() == str(key).lower():
                return v
        return default


class _FakeResp:
    def __init__(self, *, status, body, headers=None, fail_after=None):
        self.status = status
        self._body = body
        self._pos = 0
        self.headers = _FakeHeaders(headers or {})
        self._fail_after = fail_after

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def geturl(self):
        return _ok_url()

    def getcode(self):
        return self.status

    def read(self, n=-1):
        if self._fail_after is not None and self._pos >= self._fail_after:
            raise TimeoutError("The read operation timed out")
        if self._pos >= len(self._body):
            return b""
        if n < 0:
            n = len(self._body) - self._pos
        if self._fail_after is not None:
            n = min(n, max(0, self._fail_after - self._pos))
            if n == 0:
                raise TimeoutError("The read operation timed out")
        chunk = self._body[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


def test_versioning():
    section("versioning: normalize_version / is_newer")
    check("v-prefixed tag parses", normalize_version("v0.2.1") == (0, 2, 1))
    check("bare version parses", normalize_version("0.2.1") == (0, 2, 1))
    check("embedded in text parses", normalize_version("Naru 0.2.1") == (0, 2, 1))
    check("unparseable text is None", normalize_version("nope") is None)
    check("newer release beats older install", is_newer((0, 2, 2), (0, 2, 1)) is True)
    check("same version is not newer", is_newer((0, 2, 1), (0, 2, 1)) is False)
    check("older release is not newer", is_newer((0, 2, 0), (0, 2, 1)) is False)


def test_host_allowed():
    section("github_release.host_allowed - download redirect allowlist")
    check("github.com is allowed",
          host_allowed("https://github.com/seongbin45/x/releases/download/v1/y.exe"))
    check("*.githubusercontent.com is allowed",
          host_allowed("https://objects.githubusercontent.com/x"))
    check("an unrelated host is refused", not host_allowed("https://evil.example/x.exe"))


def test_fetch_latest_release_parsing():
    section("github_release.fetch_latest_release - asset selection")
    payload_with_exe = {
        "tag_name": "v0.2.2",
        "assets": [
            {"name": "Naru-Setup.exe", "browser_download_url": "https://github.com/x/setup.exe"},
            {"name": "Naru.exe", "browser_download_url": "https://github.com/x/Naru.exe",
             "digest": "sha256:" + "a" * 64},
        ],
    }
    payload_setup_only = {
        "tag_name": "v0.2.3",
        "assets": [
            {"name": "Naru-Setup.exe", "browser_download_url": "https://github.com/x/setup.exe"},
        ],
    }

    class FakeResp:
        def __init__(self, data):
            import json
            self._body = json.dumps(data).encode("utf-8")
            self.status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    with patch("update_manager.github_release.urllib.request.urlopen",
               return_value=FakeResp(payload_with_exe)):
        got = github_release.fetch_latest_release()
    check("picks the raw Naru.exe asset, never Setup.exe",
          got is not None and got.asset_name == "Naru.exe", got)
    check("version comes from the tag", got.version == (0, 2, 2))

    with patch("update_manager.github_release.urllib.request.urlopen",
               return_value=FakeResp(payload_setup_only)):
        got_none = github_release.fetch_latest_release()
    check("a release with only Setup.exe is refused, not silently substituted",
          got_none is None, got_none)


def test_download_asset_retries_then_ok():
    section("apply.download_asset - single attempt budget, retries once, then completes")
    payload = b"MZ" + b"x" * 100
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    calls = {"n": 0}

    def fake_urlopen(req, context=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 2:
            raise TimeoutError("simulated timeout")
        return _FakeResp(status=200, body=payload,
                          headers={"Content-Length": str(len(payload)), "ETag": '"abc"'})

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "a.exe"
        with patch("update_manager.apply.urllib.request.urlopen", fake_urlopen), \
             patch("update_manager.apply.time.sleep", lambda _s: None):
            apply_mod.download_asset(_ok_url(), dest, digest=digest)
        check("retried exactly once before succeeding", calls["n"] == 2)
        check("final content matches the payload", dest.read_bytes() == payload)
        check("no .part left behind", not Path(str(dest) + ".part").exists())


def test_download_asset_resumes_with_206():
    section("apply.download_asset - intra-call Range resume after a mid-stream timeout")
    payload = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    mid = 10
    calls = {"n": 0}
    seen_range = []

    def _hdr(req, name):
        for k, v in req.headers.items():
            if k.lower() == name.lower():
                return v
        return None

    def fake_urlopen(req, context=None, timeout=None):
        calls["n"] += 1
        seen_range.append(_hdr(req, "Range"))
        if calls["n"] == 1:
            return _FakeResp(status=200, body=payload,
                              headers={"Content-Length": str(len(payload)), "ETag": '"v1"'},
                              fail_after=mid)
        return _FakeResp(
            status=206, body=payload[mid:],
            headers={"Content-Range": f"bytes {mid}-{len(payload)-1}/{len(payload)}",
                     "Content-Length": str(len(payload) - mid)},
        )

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "a.exe"
        with patch("update_manager.apply.urllib.request.urlopen", fake_urlopen), \
             patch("update_manager.apply.time.sleep", lambda _s: None):
            apply_mod.download_asset(_ok_url(), dest, digest=digest)
        check("resumed with a Range header at the byte it stopped on",
              seen_range[1] == f"bytes={mid}-", seen_range)
        check("resumed content matches the full payload (not truncated/doubled)",
              dest.read_bytes() == payload)


def test_download_asset_digest_mismatch_clears_partial():
    section("apply.download_asset - a digest mismatch wipes the partial download")
    payload = b"MZ" + b"y" * 20

    def fake_urlopen(req, context=None, timeout=None):
        return _FakeResp(status=200, body=payload,
                          headers={"Content-Length": str(len(payload)), "ETag": '"z"'})

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / "a.exe"
        with patch("update_manager.apply.urllib.request.urlopen", fake_urlopen):
            try:
                apply_mod.download_asset(_ok_url(), dest, digest="sha256:" + "0" * 64)
                check("a digest mismatch raises", False, "it did not raise")
            except RuntimeError as exc:
                check("a digest mismatch raises", "digest mismatch" in str(exc), str(exc))
        check("no half-good file left on disk", not dest.exists())
        check("no .part left on disk either", not Path(str(dest) + ".part").exists())


def test_download_asset_requires_digest():
    section("apply.download_asset - fail-closed without a digest at all")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            apply_mod.download_asset(_ok_url(), Path(tmp) / "a.exe", digest=None)
            check("refuses to download with no digest to verify against", False, "it did not raise")
        except RuntimeError as exc:
            check("refuses to download with no digest to verify against",
                  "digest missing" in str(exc), str(exc))


def test_install_staged_exe_replaces_live_one():
    section("apply.install_staged_exe - atomic replace of the live Naru.exe")
    with tempfile.TemporaryDirectory() as tmp:
        install_dir = Path(tmp)
        (install_dir / "Naru.exe").write_bytes(b"old content")
        staged = install_dir / "Naru.exe.new"
        staged.write_bytes(b"new content")
        apply_mod.install_staged_exe(staged, install_dir)
        check("live exe now holds the staged content",
              (install_dir / "Naru.exe").read_bytes() == b"new content")
        check("the staged file is consumed (moved, not copied)", not staged.exists())


def test_find_install_dir_env_override():
    section("paths.find_tsbackup_install_dir - NARU_INSTALL_DIR override")
    with tempfile.TemporaryDirectory() as tmp:
        app = Path(tmp) / "Naru"
        app.mkdir()
        (app / "Naru.exe").write_bytes(b"MZ")
        with patch.dict("os.environ", {"NARU_INSTALL_DIR": str(app)}):
            got = paths.find_tsbackup_install_dir()
        check("resolves to the overridden dir when it looks like a real install",
              got == app.resolve(), got)

        empty = Path(tmp) / "empty"
        empty.mkdir()
        with patch.dict("os.environ", {"NARU_INSTALL_DIR": str(empty)}):
            got_bad = paths.find_tsbackup_install_dir()
        check("an override with no Naru.exe in it does not fall through to "
              "a real install on this machine - returns None instead",
              got_bad is None, got_bad)


def test_tsbackup_exe_running_korean_locale_bytes():
    section("process_win._tsbackup_exe_running - Korean-locale tasklist output (cp949 bytes)")

    class R:
        def __init__(self, stdout):
            self.stdout = stdout

    def fake_run_no_match(*_a, **_k):
        # Korean "no matching tasks" message, real cp949 bytes - decoding
        # this as UTF-8 (text=True) used to raise and make the kill-wait
        # exit early on a false "not running" read.
        return R(
            b"\xc1\xa4\xba\xb8: \xbd\xc7\xc7\xe0 \xc1\xdf\xc0\xce "
            b"\xc0\xdb\xbe\xf7 \xc1\xdf \xc1\xf6\xc1\xa4\xb5\xc8 "
            b"\xc1\xb0\xb0\xc7\xbf\xa1 \xc0\xcf\xc4\xa1\xc7\xcf\xb4\xc2 "
            b"\xc0\xdb\xbe\xf7\xc0\xcc \xbe\xf8\xbd\xc0\xb4\xcf\xb4\xd9.\r\n"
        )

    with patch.object(pw.subprocess, "run", fake_run_no_match):
        check("no match in the (undecoded) byte output -> reports not running",
              pw._tsbackup_exe_running() is False)

    def fake_run_hit(*_a, **_k):
        return R(b"Naru.exe                 1234 Console    1    50,000 K\r\n")

    with patch.object(pw.subprocess, "run", fake_run_hit):
        check("Naru.exe present in output -> reports running",
              pw._tsbackup_exe_running() is True)


def test_run_once_state_machine():
    section("__main__.run_once - state machine, via a fake install dir + mocked release")
    log = setup_logging()

    with tempfile.TemporaryDirectory() as tmp:
        install_dir = Path(tmp)
        with patch.dict("os.environ", {"NARU_INSTALL_DIR": str(install_dir)}):
            check("no install dir at all -> no_install",
                  run_once(log) == "no_install")

            (install_dir / "Naru.exe").write_bytes(b"old")
            (install_dir / "VERSION").write_text("0.1.0")

            with patch("update_manager.__main__.fetch_latest_release", return_value=None):
                check("network/no usable release -> no_release",
                      run_once(log) == "no_release")

            same = LatestRelease(
                tag="v0.1.0", version=(0, 1, 0), asset_name="Naru.exe",
                download_url="https://github.com/x/Naru.exe",
                digest="sha256:" + "a" * 64,
            )
            with patch("update_manager.__main__.fetch_latest_release", return_value=same):
                check("release version equals installed version -> up_to_date",
                      run_once(log) == "up_to_date")

            new_content = b"brand new exe content"
            digest = "sha256:" + hashlib.sha256(new_content).hexdigest()
            newer = LatestRelease(
                tag="v0.2.0", version=(0, 2, 0), asset_name="Naru.exe",
                download_url="https://github.com/x/Naru.exe", digest=digest,
            )

            def fake_urlopen(req, context=None, timeout=None):
                return _FakeResp(status=200, body=new_content,
                                  headers={"Content-Length": str(len(new_content)),
                                           "ETag": '"n"'})

            # ensure_exe() downloads before main_window_visible()/kill are
            # ever checked (Tier 2: the download itself is never deferred,
            # only the apply step is) - so these two cases need the same
            # urlopen mock as the "updated" case below, or the real network
            # call to a fake URL fails before the behavior under test runs.
            with patch("update_manager.__main__.fetch_latest_release", return_value=newer), \
                 patch("update_manager.__main__.main_window_visible", return_value=True), \
                 patch("update_manager.apply.urllib.request.urlopen", fake_urlopen):
                check("main window visible -> deferred_ui, exe untouched",
                      run_once(log) == "deferred_ui")
                check("...and the old exe is still in place, not half-replaced",
                      (install_dir / "Naru.exe").read_bytes() == b"old")

            with patch("update_manager.__main__.fetch_latest_release", return_value=newer), \
                 patch("update_manager.__main__.main_window_visible", return_value=False), \
                 patch("update_manager.__main__.kill_tsbackup_processes", return_value=False), \
                 patch("update_manager.apply.urllib.request.urlopen", fake_urlopen):
                check("process won't stop -> killed_failed, exe untouched",
                      run_once(log) == "killed_failed")
                check("...and the old exe is still in place",
                      (install_dir / "Naru.exe").read_bytes() == b"old")

            with patch("update_manager.__main__.fetch_latest_release", return_value=newer), \
                 patch("update_manager.__main__.main_window_visible", return_value=False), \
                 patch("update_manager.__main__.kill_tsbackup_processes", return_value=True), \
                 patch("update_manager.__main__.is_tray_autostart_registered", return_value=False), \
                 patch("update_manager.apply.urllib.request.urlopen", fake_urlopen):
                result = run_once(log)
                check("everything lines up -> updated", result == "updated", result)
                check("the exe now holds the new release's content",
                      (install_dir / "Naru.exe").read_bytes() == new_content)

            run_id = status_io.read_current_run_id(install_dir)
            check("the last run's status was actually persisted for the tray to poll",
                  run_id is not None and status_io.read_run(install_dir, run_id) is not None)


def main() -> int:
    test_versioning()
    test_host_allowed()
    test_fetch_latest_release_parsing()
    test_download_asset_retries_then_ok()
    test_download_asset_resumes_with_206()
    test_download_asset_digest_mismatch_clears_partial()
    test_download_asset_requires_digest()
    test_install_staged_exe_replaces_live_one()
    test_find_install_dir_env_override()
    test_tsbackup_exe_running_korean_locale_bytes()
    test_run_once_state_machine()

    print()
    if FAILURES:
        print(f"FAILED: {len(FAILURES)}")
        for name in FAILURES:
            print("  -", name)
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
