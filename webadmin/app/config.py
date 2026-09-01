"""Host registry.

The console is deliberately host-agnostic: it holds no knowledge of any
particular machine, only what hosts.json tells it. That is what makes it
usable from a laptop, from the receiving PC, or from a small always-on box,
without editing code.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
HOSTS_FILE = Path(os.environ.get("TSCONSOLE_HOSTS", BASE_DIR / "hosts.json"))
EXAMPLE_FILE = BASE_DIR / "hosts.example.json"


@dataclass
class Host:
    id: str
    label: str
    role: str  # "sender" | "receiver"
    address: str
    username: str
    port: int = 22
    task: str = ""
    scripts_dir: str = r"C:\Scripts"
    work_dir: str = ""

    # Never serialised to the browser. Set either from hosts.json or, more
    # usually, from the credential prompt - in which case it lives here for
    # as long as the process does and nowhere else.
    password: str | None = field(default=None, repr=False)

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    def public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "address": self.address,
            "username": self.username,
            "port": self.port,
            "task": self.task,
            "has_password": self.has_password,
        }


class Registry:
    """Loaded once at startup, mutated only by the credential endpoint."""

    def __init__(self) -> None:
        self._hosts: dict[str, Host] = {}
        self._lock = threading.Lock()
        self.source: str = ""
        self.load()

    def load(self) -> None:
        path = HOSTS_FILE if HOSTS_FILE.exists() else EXAMPLE_FILE
        raw = json.loads(path.read_text(encoding="utf-8"))
        hosts: dict[str, Host] = {}
        for entry in raw.get("hosts", []):
            entry = {k: v for k, v in entry.items() if not k.startswith("_")}
            host = Host(**entry)
            if not host.work_dir:
                host.work_dir = (
                    r"C:\TempBackup" if host.role == "sender" else r"C:\TempReceive"
                )
            hosts[host.id] = host
        with self._lock:
            self._hosts = hosts
            self.source = str(path)

    @property
    def using_example(self) -> bool:
        return self.source == str(EXAMPLE_FILE)

    def all(self) -> list[Host]:
        with self._lock:
            return list(self._hosts.values())

    def get(self, host_id: str) -> Host | None:
        with self._lock:
            return self._hosts.get(host_id)

    def set_password(self, host_id: str, password: str | None) -> bool:
        with self._lock:
            host = self._hosts.get(host_id)
            if host is None:
                return False
            host.password = password or None
            return True


registry = Registry()
