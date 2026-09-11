"""Taildrop: the transport this project already runs.

`tailscale file cp <archive> <device>:` pushes the file into a device's
Taildrop inbox by tailnet name alone - no ports, no accounts, no cloud. The
targets are tried in order and the first that accepts the file wins, the same
fallback the batch version does with its TARGETS list.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable

from .base import Transport, TransferResult, register

SEND_TIMEOUT = 60 * 60  # a multi-GB push over Taildrop can legitimately be long


def tailscale_binary() -> str | None:
    found = shutil.which("tailscale")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\Tailscale\tailscale.exe",
        r"C:\Program Files (x86)\Tailscale\tailscale.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


@register
class TaildropTransport(Transport):
    name = "taildrop"

    def send(self, archive_path: Path,
             progress: Callable[[int], None] | None = None) -> TransferResult:
        binary = tailscale_binary()
        if binary is None:
            return TransferResult(False, "tailscale 명령을 찾지 못했습니다.")

        targets = list(self.cfg.taildrop_targets)
        if not targets:
            return TransferResult(False, "Taildrop 대상 기기가 없습니다.")

        started = time.time()
        for device in targets:
            if progress:
                progress(0)
            try:
                proc = subprocess.run(
                    [binary, "file", "cp", str(archive_path), f"{device}:"],
                    capture_output=True, text=True, timeout=SEND_TIMEOUT,
                )
            except subprocess.TimeoutExpired:
                self.log(f"전송 시간 초과: -> {device}")
                continue
            except OSError as exc:
                return TransferResult(False, f"tailscale 실행 실패: {exc}")

            if proc.returncode == 0:
                if progress:
                    progress(100)
                self.log(f"전송 성공: {archive_path.name} -> {device}")
                return TransferResult(True, device, time.time() - started)

            reason = (proc.stderr or proc.stdout or "").strip().splitlines()
            self.log(f"전송 실패: -> {device} ({reason[-1] if reason else proc.returncode})")

        return TransferResult(False, "모든 Taildrop 대상 실패", time.time() - started)
