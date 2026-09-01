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
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
HOSTS_FILE = Path(os.environ.get("TSCONSOLE_HOSTS", BASE_DIR / "hosts.json"))
EXAMPLE_FILE = BASE_DIR / "hosts.example.json"

# How the SSH connection is made. The names match the "연결 경로" choice in
# the console.
#
#   direct  100.x.y.z (or the MagicDNS name) : 22, password auth. The default,
#           and the only one verified against these machines.
#   tsssh   Tailscale SSH. tailscaled terminates the connection and authorises
#           by tailnet identity, so no password is offered at all.
#   jump    Reach the target through another SSH host. This is the concrete,
#           standard form of "relay": an ordinary ProxyJump, no third-party
#           service, so credentials stay between you and machines you own.
PATHS = ("direct", "tsssh", "jump")


@dataclass
class JumpConfig:
    address: str = ""
    port: int = 22
    username: str = ""
    password: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "port": self.port,
            "username": self.username,
            "has_password": bool(self.password),
        }


@dataclass
class Host:
    id: str
    label: str
    role: str          # "sender" | "receiver" | "" (terminal only)
    address: str
    username: str
    port: int = 22
    task: str = ""
    scripts_dir: str = r"C:\Scripts"
    work_dir: str = ""
    path: str = "direct"
    jump: JumpConfig | None = None

    # Never serialised to the browser. Set either from hosts.json or, more
    # usually, from the connection form - in which case it lives here for as
    # long as the process does and nowhere else.
    password: str | None = field(default=None, repr=False)

    @property
    def has_password(self) -> bool:
        return bool(self.password)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "address": self.address,
            "username": self.username,
            "port": self.port,
            "task": self.task,
            "scripts_dir": self.scripts_dir,
            "work_dir": self.work_dir,
            "path": self.path,
            "has_password": self.has_password,
            "jump": self.jump.public() if self.jump else None,
        }

    def stored(self) -> dict[str, Any]:
        """What goes back into hosts.json. The password is written only if one
        was already being kept on disk for this host - the console never
        promotes a typed-in password to a stored one behind the operator's
        back."""
        out = {
            "id": self.id,
            "label": self.label,
            "role": self.role,
            "address": self.address,
            "port": self.port,
            "username": self.username,
            "password": None,
            "task": self.task,
            "scripts_dir": self.scripts_dir,
            "work_dir": self.work_dir,
            "path": self.path,
        }
        if self.jump:
            out["jump"] = {
                "address": self.jump.address,
                "port": self.jump.port,
                "username": self.jump.username,
                "password": None,
            }
        return out


class Registry:
    def __init__(self) -> None:
        self._hosts: dict[str, Host] = {}
        self._lock = threading.RLock()
        self.source: str = ""
        self.load()

    # ------------------------------------------------------------- loading

    @staticmethod
    def _build(entry: dict[str, Any]) -> Host:
        entry = {k: v for k, v in entry.items() if not k.startswith("_")}
        jump_raw = entry.pop("jump", None)
        entry.setdefault("role", "")
        host = Host(**entry)
        if jump_raw:
            host.jump = JumpConfig(**{k: v for k, v in jump_raw.items() if not k.startswith("_")})
        if not host.work_dir and host.role:
            # Only a host with a role has a work directory to speak of. A
            # terminal-only host getting the receiver's default would be a
            # quiet lie in every panel that shows it.
            host.work_dir = r"C:\TempBackup" if host.role == "sender" else r"C:\TempReceive"
        if host.path not in PATHS:
            host.path = "direct"
        return host

    def load(self) -> None:
        path = HOSTS_FILE if HOSTS_FILE.exists() else EXAMPLE_FILE
        raw = json.loads(path.read_text(encoding="utf-8"))
        hosts = {h.id: h for h in (self._build(e) for e in raw.get("hosts", []))}
        with self._lock:
            self._hosts = hosts
            self.source = str(path)

    def save(self) -> None:
        """Write hosts.json. Never writes over hosts.example.json - the first
        save on a fresh checkout creates the real file instead."""
        with self._lock:
            payload = {"hosts": [h.stored() for h in self._hosts.values()]}
            HOSTS_FILE.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            self.source = str(HOSTS_FILE)

    @property
    def using_example(self) -> bool:
        return self.source == str(EXAMPLE_FILE)

    # ------------------------------------------------------------- access

    def all(self) -> list[Host]:
        with self._lock:
            return list(self._hosts.values())

    def get(self, host_id: str) -> Host | None:
        with self._lock:
            return self._hosts.get(host_id)

    def by_role(self, role: str) -> Host | None:
        with self._lock:
            for host in self._hosts.values():
                if host.role == role:
                    return host
        return None

    def set_password(self, host_id: str, password: str | None) -> bool:
        with self._lock:
            host = self._hosts.get(host_id)
            if host is None:
                return False
            host.password = password or None
            return True

    def upsert(self, data: dict[str, Any]) -> Host:
        """Create or update a host from the connection form. A blank password
        leaves whatever is already held in memory alone, so re-saving the form
        after a page reload does not wipe a working session."""
        with self._lock:
            host_id = data.get("id") or data["address"].replace(".", "-").lower()
            existing = self._hosts.get(host_id)
            password = data.pop("password", None)

            merged = existing.public() if existing else {}
            merged.update({k: v for k, v in data.items() if v is not None})
            merged["id"] = host_id
            merged.setdefault("label", data.get("address", host_id))
            merged.setdefault("role", "")
            merged.pop("has_password", None)

            jump = merged.get("jump")
            if isinstance(jump, dict):
                jump.pop("has_password", None)
                if not jump.get("address"):
                    merged["jump"] = None

            host = self._build({k: v for k, v in merged.items() if v is not None or k == "jump"})
            host.password = password or (existing.password if existing else None)
            if existing and existing.jump and host.jump and not host.jump.password:
                host.jump.password = existing.jump.password
            self._hosts[host_id] = host
            return host


registry = Registry()
