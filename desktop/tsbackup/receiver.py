"""Receiving side: notice an arriving archive, unpack it, log it.

Two ways an archive arrives:

  a folder     taildrop and sftp both land the .7z in a directory. This side
               polls that directory and unpacks anything new. Polling, not a
               filesystem watch, because Taildrop's inbox and an SFTP target
               can be on network paths where change notifications are
               unreliable - a 5-second poll is simpler and never misses.

  an HTTP POST the built-in server saves the upload straight to the incoming
               directory, then the same unpack path runs.

An archive is only unpacked once it has stopped growing, so a file still being
written (an SFTP .part that was renamed early, a slow Taildrop write) is not
opened mid-transfer.
"""

from __future__ import annotations

import json
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

import py7zr

from . import pairing
from .archiver import STAMP_RE
from .config import config_dir

SETTLE_SECONDS = 3       # size must be unchanged this long before unpacking
POLL_SECONDS = 5


def _stamp_of(name: str) -> str:
    m = STAMP_RE.search(name)
    return m.group(1) if m else time.strftime("%Y_%m_%d_%H_%M")


class Receiver:
    def __init__(self, config, log: Callable[[str], None]) -> None:
        self.cfg = config
        self.log = log
        self._seen: set[str] = set()
        self._sizes: dict[str, tuple[int, float]] = {}
        self._http = None

    # ---------------------------------------------------------------- scan

    def scan_once(self) -> int:
        """Unpack any settled archives in the incoming directory. Returns how
        many were unpacked this pass."""
        self._pull_taildrop()
        incoming = Path(self.cfg.receiver.incoming_dir)
        if not incoming.is_dir():
            return 0

        done = 0
        for path in sorted(incoming.glob("*.7z")):
            if path.name in self._seen:
                continue
            if path.name.endswith(".part"):
                continue
            if not self._settled(path):
                continue
            if self._unpack(path):
                done += 1
            self._seen.add(path.name)
        return done

    def _pull_taildrop(self) -> None:
        """Get pending Taildrop archives into incoming_dir, by whichever
        path actually applies.

        Two different delivery paths exist, and only one of them is under
        our control:

          - No Tailscale GUI running (tailscaled alone, a headless
            receiver box): nothing claims pending files on its own, so
            `tailscale file get` is genuinely what retrieves them - and
            it's a no-op when nothing is pending, safe to call every pass.
          - The Tailscale GUI running (the normal desktop case, v1.34+):
            it auto-claims every pending file into
            %USERPROFILE%\\Downloads *itself*, in the background, on its
            own timer - and normally wins the race against our own poll
            long before it fires, so `tailscale file get` finds nothing
            left pending even though the file really did arrive. The
            only place it's reliably still sitting afterward is Downloads
            itself, since that destination isn't configurable.

        So both are done every pass: try the CLI claim (covers the no-GUI
        case), then sweep Downloads for archives matching this app's own
        naming scheme (archiver.py's STAMP_RE) and move only those into
        incoming_dir - never anything else a person keeps in Downloads.
        """
        from .transports.taildrop import tailscale_binary

        incoming = Path(self.cfg.receiver.incoming_dir)
        incoming.mkdir(parents=True, exist_ok=True)

        binary = tailscale_binary()
        if binary:
            try:
                subprocess.run(
                    [binary, "file", "get", "--wait=false", "--conflict=skip", str(incoming)],
                    capture_output=True, text=True, timeout=30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                self.log(f"taildrop 수신 확인 실패: {exc}")

        self._sweep_taildrop_default_dir(incoming)

    def _sweep_taildrop_default_dir(self, incoming: Path) -> None:
        default_dir = Path.home() / "Downloads"
        try:
            same = default_dir.samefile(incoming)
        except OSError:
            same = default_dir == incoming
        if same or not default_dir.is_dir():
            return
        for path in default_dir.glob("*.7z"):
            if not STAMP_RE.search(path.name):
                continue
            dest = incoming / path.name
            if dest.exists():
                continue
            try:
                path.replace(dest)
                self.log(f"Downloads에서 수신 파일 이동: {path.name}")
            except OSError as exc:
                self.log(f"Downloads 파일 이동 실패: {path.name} ({exc})")

    def _settled(self, path: Path) -> bool:
        try:
            size = path.stat().st_size
        except OSError:
            return False
        now = time.time()
        prev = self._sizes.get(path.name)
        self._sizes[path.name] = (size, now)
        if prev is None or prev[0] != size:
            return False
        return (now - prev[1]) >= SETTLE_SECONDS

    def _unpack(self, path: Path) -> bool:
        stamp = _stamp_of(path.name)
        dest = Path(self.cfg.receiver.unpack_dir) / f"PycharmProjects_{stamp}"
        dest.parent.mkdir(parents=True, exist_ok=True)
        self.log(f"압축 해제 시작: {path.name} -> {dest}")
        try:
            with py7zr.SevenZipFile(path, "r") as archive:
                archive.extractall(dest)
        except Exception as exc:  # noqa: BLE001
            self.log(f"압축 해제 실패: {path.name} ({exc})")
            return False
        self.log(f"압축 해제 완료: {dest}")
        self._record_heartbeat(dest)
        if self.cfg.receiver.delete_after_unpack:
            try:
                path.unlink()
                self.log(f"수신 압축 삭제: {path.name}")
            except OSError as exc:
                self.log(f"수신 압축 삭제 실패: {path.name} ({exc})")
        return True

    def _record_heartbeat(self, dest: Path) -> None:
        """A paired sender embeds a .ts_sender.json marker (archiver.py's
        `identity` param) in every archive it sends - not just the wizard's
        test-transfer. Reading it back here is what keeps
        known_senders.json's last_seen current for silence detection
        (pairing.overdue_senders()) after the one-time pairing handshake."""
        marker = dest / ".ts_sender.json"
        if not marker.exists():
            return
        try:
            info = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            self.log(f"짝 정보 읽기 실패: {exc}")
            return
        device_id = str(info.get("device_id", "")).strip()
        if not device_id:
            return
        known_path = config_dir() / pairing.KNOWN_SENDERS_FILENAME
        pairing.record_heartbeat(known_path, device_id, info.get("interval_minutes"))

    # ---------------------------------------------------------------- http

    def start_http(self) -> None:
        """Run the upload server if the sender pushes over HTTP. Bound to
        whatever receiver.http_bind says - default loopback. A public bind is
        the operator's explicit choice and is logged as a warning."""
        if self._http is not None:
            return
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        cfg = self.cfg.receiver
        incoming = Path(cfg.incoming_dir)
        incoming.mkdir(parents=True, exist_ok=True)
        token = cfg.http_token
        log = self.log
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence the default stderr spam
                pass

            def do_POST(self):
                if self.path != "/upload":
                    self.send_error(404)
                    return
                if not token or self.headers.get("X-TsBackup-Token") != token:
                    self.send_error(403, "bad token")
                    log("업로드 거부: 토큰 불일치")
                    return
                name = Path(self.headers.get("X-TsBackup-Name", "upload.7z")).name
                if not name.endswith(".7z"):
                    self.send_error(400, "not a .7z")
                    return
                length = int(self.headers.get("Content-Length", 0))
                tmp = incoming / (name + ".part")
                written = 0
                try:
                    with tmp.open("wb") as fh:
                        remaining = length
                        while remaining > 0:
                            block = self.rfile.read(min(1024 * 1024, remaining))
                            if not block:
                                break
                            fh.write(block)
                            written += len(block)
                            remaining -= len(block)
                    tmp.rename(incoming / name)
                except OSError as exc:
                    tmp.unlink(missing_ok=True)
                    self.send_error(500, str(exc))
                    return
                log(f"업로드 수신: {name} ({written} bytes)")
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")

        server = ThreadingHTTPServer((cfg.http_bind, cfg.http_port), Handler)
        if cfg.http_bind == "0.0.0.0":
            self.log("경고: HTTP 수신이 0.0.0.0 에 열렸습니다. 공개 인터페이스에 노출됩니다.")
        self.log(f"HTTP 수신 서버 시작: {cfg.http_bind}:{cfg.http_port}")
        self._http = server
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def stop_http(self) -> None:
        if self._http is not None:
            self._http.shutdown()
            self._http = None
            self.log("HTTP 수신 서버 중지")
