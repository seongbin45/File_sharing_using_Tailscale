"""HTTP push to the receiver's built-in server, resumable.

A streamed POST, so neither side loads the whole archive into memory, with a
shared token in a header. The token is not real authentication - it is the
minimum that stops a stray request from filling the receiver's disk. The
receiver refuses uploads without it and should not bind a public interface;
that decision lives on the receiver, in receiver.py.

Resume: the archive is identified by its size and sha256, never by its name
alone. Before sending, a GET asks how many bytes of that exact archive the
receiver already holds, and the POST carries only the rest. The receiver
verifies the sha256 of the whole file before it answers 200, so a 200 means
the bytes on the far side are the bytes here. An older receiver without the
GET answers 501, and the archive then goes whole, as before.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from ..pending import sha256_file
from .base import Transport, TransferResult, register

DEFAULT_PORT = 8770
SEND_TIMEOUT = 60 * 60
QUERY_TIMEOUT = 5 * 60      # the receiver may hash a complete file to answer
ATTEMPTS = 3                # resumes within one send(); the engine retries later too
RETRY_SLEEP = 5.0


class _FileReader:
    """Yields the file from `start` in chunks and reports progress. requests
    accepts an iterable body, which is what keeps this off the heap."""

    def __init__(self, path: Path, progress, start: int = 0, chunk: int = 1024 * 1024) -> None:
        self.path = path
        self.progress = progress
        self.chunk = chunk
        self.start = start
        self.size = path.stat().st_size

    def __iter__(self):
        sent = self.start
        with self.path.open("rb") as fh:
            fh.seek(self.start)
            while True:
                block = fh.read(self.chunk)
                if not block:
                    break
                sent += len(block)
                if self.progress and self.size:
                    self.progress(min(100, int(sent * 100 / self.size)))
                yield block

    def __len__(self):
        return self.size - self.start


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
        size = archive_path.stat().st_size
        started = time.time()
        sha = sha256_file(archive_path)
        headers = {
            "X-TsBackup-Token": cfg.http_token or "",
            "X-TsBackup-Name": archive_path.name,
            "X-TsBackup-Size": str(size),
            "X-TsBackup-SHA256": sha,
        }

        last = ""
        for attempt in range(1, ATTEMPTS + 1):
            if attempt > 1:
                time.sleep(RETRY_SLEEP * (attempt - 1))
            try:
                q = requests.get(url, params={"name": archive_path.name},
                                 headers=headers, timeout=QUERY_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                last = f"전송 실패: {exc}"
                continue
            if q.status_code in (401, 403):
                return TransferResult(False, "수신 측이 토큰을 거부했습니다.", time.time() - started)
            if q.status_code == 200:
                state = q.json()
                if state.get("complete"):
                    self.log(f"전송 성공(http): {archive_path.name} - 수신 측에 이미 완전한 파일이 있음")
                    return TransferResult(True, cfg.host, time.time() - started)
                offset = int(state.get("offset", 0))
            elif q.status_code in (404, 405, 501):
                offset = 0          # older receiver: no resume, send it whole
            else:
                last = f"HTTP {q.status_code} (이어받기 위치 조회)"
                continue
            if offset:
                self.log(f"이어받기(http): {archive_path.name} {offset}/{size} bytes 부터")

            try:
                resp = requests.post(
                    url,
                    data=_FileReader(archive_path, progress, start=offset),
                    headers={**headers, "X-TsBackup-Offset": str(offset),
                             "Content-Type": "application/octet-stream"},
                    timeout=SEND_TIMEOUT,
                )
            except Exception as exc:  # noqa: BLE001
                last = f"전송 실패: {exc}"
                continue
            if resp.status_code == 200:
                self.log(f"전송 성공(http): {archive_path.name} -> {cfg.host}:{port} (sha256 확인)")
                return TransferResult(True, cfg.host, time.time() - started)
            if resp.status_code in (401, 403):
                return TransferResult(False, "수신 측이 토큰을 거부했습니다.", time.time() - started)
            last = f"HTTP {resp.status_code}"

        return TransferResult(False, last, time.time() - started)
