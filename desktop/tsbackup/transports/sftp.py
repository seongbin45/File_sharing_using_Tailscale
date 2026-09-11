"""SFTP push over OpenSSH.

The archive is written to a temporary name on the far end and renamed into
place only after the upload completes, so a receiver watching the directory
never sees a half-written file and tries to unpack it.

Host key handling mirrors the web console's: a fingerprint pinned in config is
required, and an unknown key is refused rather than trusted on sight - the
first connection is the one that hands over the password.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

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
    import base64
    import hashlib

    expected = (getattr(cfg, "host_key", "") or "").strip()

    class Pinned(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            fp = "SHA256:" + base64.b64encode(
                hashlib.sha256(key.asbytes()).digest()
            ).decode().rstrip("=")
            if not expected:
                # No pin configured. Refuse rather than trust on first sight -
                # the same stance as the web console.
                raise paramiko.SSHException(
                    f"호스트 키가 등록되지 않았습니다: {fp} "
                    "(설정의 host_key 에 넣으십시오)"
                )
            if _norm(fp) != _norm(expected):
                raise paramiko.SSHException(
                    f"호스트 키 불일치: 받은 {fp}, 등록된 {expected}"
                )

    return Pinned()


def _norm(v: str) -> str:
    v = v.strip()
    if v.lower().startswith("sha256:"):
        v = v[7:]
    return v.rstrip("=")
