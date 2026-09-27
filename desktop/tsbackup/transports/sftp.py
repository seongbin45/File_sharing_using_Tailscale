"""SFTP push over OpenSSH.

The archive is written to a temporary name on the far end and renamed into
place only after the upload completes, so a receiver watching the directory
never sees a half-written file and tries to unpack it.

Host key handling differs from the web console's on purpose: the console
refuses an unpinned key (an operator must paste the fingerprint in by hand),
because it runs on a server with someone at a terminal to do that. A desktop
install has no such operator standing by during an unattended run, so an
empty pin here trusts-and-pins automatically on the first connection instead
(trust on first use) and refuses only a later MISMATCH - a machine that
answers differently than the one already pinned. See tsbackup/hostkeys.py.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .. import hostkeys
from .base import Transport, TransferResult, register

DEFAULT_PORT = 22
CONNECT_TIMEOUT = 15


@register
class SftpTransport(Transport):
    name = "sftp"

    def __init__(self, sender_config, log: Callable[[str], None]) -> None:
        super().__init__(sender_config, log)
        self._client = None

    def send(self, archive_path: Path,
             progress: Callable[[int], None] | None = None) -> TransferResult:
        try:
            import paramiko
        except ImportError:
            return TransferResult(False, "paramiko 가 설치되지 않았습니다.")

        cfg = self.cfg
        if not cfg.host or not cfg.username:
            return TransferResult(False, "sftp host/username 이 없습니다.")
        password = self._password()
        if not password:
            return TransferResult(False, "sftp 비밀번호가 없습니다 (환경변수 TSBACKUP_SFTP_PASSWORD).")

        started = time.time()
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(_pinned_policy(paramiko, cfg, self.log))
        try:
            client.connect(
                hostname=cfg.host,
                port=cfg.port or DEFAULT_PORT,
                username=cfg.username,
                password=password,
                timeout=CONNECT_TIMEOUT,
                look_for_keys=False,
                allow_agent=False,
            )
        except Exception as exc:  # noqa: BLE001
            return TransferResult(False, f"연결 실패: {exc}")

        try:
            sftp = client.open_sftp()
            remote_dir = cfg.remote_dir or "."
            final = f"{remote_dir}/{archive_path.name}".replace("\\", "/")
            tmp = final + ".part"

            size = archive_path.stat().st_size

            def cb(sent: int, _total: int) -> None:
                if progress and size:
                    progress(min(100, int(sent * 100 / size)))

            sftp.put(str(archive_path), tmp, callback=cb)
            # Atomic swap into the watched name.
            try:
                sftp.remove(final)
            except OSError:
                pass
            sftp.rename(tmp, final)
            sftp.close()
            self.log(f"전송 성공(sftp): {archive_path.name} -> {cfg.host}:{final}")
            return TransferResult(True, cfg.host, time.time() - started)
        except Exception as exc:  # noqa: BLE001
            return TransferResult(False, f"업로드 실패: {exc}", time.time() - started)
        finally:
            client.close()

    def _password(self) -> str:
        import os
        return os.environ.get("TSBACKUP_SFTP_PASSWORD", "")


def _pinned_policy(paramiko, cfg, log):
    class Pinned(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            fp = hostkeys.fingerprint(key)
            expected = (getattr(cfg, "host_key", "") or "").strip()
            if not expected:
                # First connection: trust and pin. cfg here is the same
                # SenderConfig object the caller's AppConfig holds, so this
                # mutation is visible to (and persisted by) engine.py's
                # cfg.save() after the run finishes.
                cfg.host_key = fp
                log(f"호스트 키를 처음 보고 등록했습니다: {fp}")
                return
            if not hostkeys.same(fp, expected):
                raise paramiko.SSHException(
                    f"호스트 키 불일치: 받은 {fp}, 등록된 {expected}"
                )

    return Pinned()
