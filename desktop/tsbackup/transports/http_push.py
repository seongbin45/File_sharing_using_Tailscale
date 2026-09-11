"""HTTP push to the receiver's built-in server.

A streamed POST, so neither side loads the whole archive into memory, with a
shared token in a header. The token is not real authentication - it is the
minimum that stops a stray request from filling the receiver's disk. The
receiver refuses uploads without it and should not bind a public interface;
that decision lives on the receiver, in receiver.py.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from .base import Transport, TransferResult, register

DEFAULT_PORT = 8770
SEND_TIMEOUT = 60 * 60


class _FileReader:
    """Yields the file in chunks and reports progress. requests accepts an
    iterable body, which is what keeps this off the heap."""

    def __init__(self, path: Path, progress, chunk: int = 1024 * 1024) -> None:
        self.path = path
        self.progress = progress
        self.chunk = chunk
        self.size = path.stat().st_size

    def __iter__(self):
        sent = 0
        with self.path.open("rb") as fh:
            while True:
                block = fh.read(self.chunk)
                if not block:
                    break
                sent += len(block)
                if self.progress and self.size:
                    self.progress(min(100, int(sent * 100 / self.size)))
                yield block

    def __len__(self):
        return self.size


@register
class HttpTransport(Transport):
    name = "http"

    def send(self, archive_path: Path,
             progress: Callable[[int], None] | None = None) -> TransferResult:
        try:
            import requests
        except ImportError:
            return TransferResult(False, "requests 가 설치되지 않았습니다.")

        cfg = self.cfg
        if not cfg.host:
            return TransferResult(False, "http host 가 없습니다.")

        port = cfg.port or DEFAULT_PORT
        url = f"http://{cfg.host}:{port}/upload"
        headers = {
            "X-TsBackup-Token": cfg.http_token or "",
            "X-TsBackup-Name": archive_path.name,
            "Content-Type": "application/octet-stream",
        }
        started = time.time()
        try:
            resp = requests.post(
                url, data=_FileReader(archive_path, progress),
                headers=headers, timeout=SEND_TIMEOUT,
            )
        except Exception as exc:  # noqa: BLE001
            return TransferResult(False, f"전송 실패: {exc}", time.time() - started)

        if resp.status_code == 200:
            self.log(f"전송 성공(http): {archive_path.name} -> {cfg.host}:{port}")
            return TransferResult(True, cfg.host, time.time() - started)
        if resp.status_code in (401, 403):
            return TransferResult(False, "수신 측이 토큰을 거부했습니다.", time.time() - started)
        return TransferResult(False, f"HTTP {resp.status_code}", time.time() - started)
