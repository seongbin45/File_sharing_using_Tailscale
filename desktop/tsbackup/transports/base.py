"""What a transport is, and how one is chosen.

A transport takes a finished archive on disk and gets it to the other machine.
Nothing above this interface knows which one is in use, so adding a transport
is writing one class and registering it - the engine, the UI and the config
are untouched.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class TransferResult:
    ok: bool
    detail: str = ""
    seconds: float = 0.0


class Transport(abc.ABC):
    name: str = "base"

    def __init__(self, sender_config, log: Callable[[str], None]) -> None:
        self.cfg = sender_config
        self.log = log

    @abc.abstractmethod
    def send(self, archive_path: Path,
             progress: Callable[[int], None] | None = None) -> TransferResult:
        ...

    def close(self) -> None:
        """Release anything held between sends (an SSH connection). Safe to
        call when nothing is open."""


_REGISTRY: dict[str, type[Transport]] = {}


def register(cls: type[Transport]) -> type[Transport]:
    _REGISTRY[cls.name] = cls
    return cls


def build(name: str, sender_config, log: Callable[[str], None]) -> Transport:
    if name not in _REGISTRY:
        raise ValueError(f"알 수 없는 전송 방식: {name}")
    return _REGISTRY[name](sender_config, log)


def available() -> list[str]:
    return sorted(_REGISTRY)
