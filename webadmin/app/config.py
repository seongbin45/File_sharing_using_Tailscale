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

# Where the host list comes from, in order:
#
#   1. TSCONSOLE_HOSTS_JSON   the whole config as one JSON value
#   2. hosts.json             the file, written by the 연결 설정 tab
#   3. hosts.example.json     the shipped sample, so a fresh checkout runs
#
# (1) exists for deployments with no persistent disk. On a PaaS the filesystem
# is wiped on every redeploy, so a hosts.json saved through the UI is gone the
# next time the container starts - the environment is the only durable place
# to put configuration there.
#
# Passwords are read separately, one variable per host:
#
#   TSCONSOLE_PASSWORD_<ID>   with <ID> upper-cased, non-alphanumerics as "_"
#
# so that secrets stay in the secret store rather than inside a JSON blob that
# tends to get pasted into chat windows and issue trackers.
HOSTS_JSON_ENV = "TSCONSOLE_HOSTS_JSON"
PASSWORD_ENV_PREFIX = "TSCONSOLE_PASSWORD_"


def _env_suffix(host_id: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in host_id).upper()


def password_env_name(host_id: str) -> str:
    return f"{PASSWORD_ENV_PREFIX}{_env_suffix(host_id)}"


def hostkey_env_name(host_id: str) -> str:
    """Expected SSH host fingerprint, e.g. TSCONSOLE_HOSTKEY_SENDER."""
    return f"TSCONSOLE_HOSTKEY_{_env_suffix(host_id)}"


def keyfile_env_name(host_id: str) -> str:
    """Private key path, e.g. TSCONSOLE_KEYFILE_SENDER."""
    return f"TSCONSOLE_KEYFILE_{_env_suffix(host_id)}"


def keypass_env_name(host_id: str) -> str:
    return f"TSCONSOLE_KEYPASS_{_env_suffix(host_id)}"

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

# Keys public() computes for the browser that are not fields on Host. upsert()
# round-trips through public(), so these have to be dropped on the way back or
# the constructor rejects them. Listed once, here, because adding a derived
# field and forgetting this is a bug that only shows up when someone saves.
DERIVED = ("has_password", "has_key_file", "auth")


@dataclass
class JumpConfig:
    address: str = ""
    port: int = 22
    username: str = ""
    host_key: str = ""
    password: str | None = field(default=None, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "address": self.address,
            "port": self.port,
            "username": self.username,
            "host_key": self.host_key,
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

    # SHA256 fingerprint of the machine's SSH host key. Configuration, not
    # cached state: it has to survive a wiped filesystem, because the first
    # connection after a wipe is the one that hands over the credential.
    host_key: str = ""

    # Path to a private key. Preferred over a password: nothing replayable
    # is sent, and revoking it is one line out of authorized_keys on the
    # machine itself - which we control, rather than asking anyone.
    key_file: str = ""

    # True when this host's key is pinned with command="...ts_guard.ps1" in
    # authorized_keys. The console then sends only verbs, and the terminal is
    # refused - which is the entire point: a stolen console cannot get a shell
    # on this machine. Set it to match reality on the far end; claiming a
    # restriction that is not there buys nothing, and claiming none when there
    # is one just makes every request fail.
    restricted: bool = False

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
            "host_key": self.host_key,
            "has_key_file": bool(self.key_file),
            "restricted": self.restricted,
            "auth": "key" if self.key_file else ("none" if self.path == "tsssh" else "password"),
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
            "host_key": self.host_key,
            "key_file": self.key_file,
            "restricted": self.restricted,
        }
        if self.jump:
            out["jump"] = {
                "address": self.jump.address,
                "port": self.jump.port,
                "username": self.jump.username,
                "host_key": self.jump.host_key,
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
        inline = os.environ.get(HOSTS_JSON_ENV, "").strip()
        if inline:
            raw = json.loads(inline)
            source = f"${HOSTS_JSON_ENV}"
        else:
            path = HOSTS_FILE if HOSTS_FILE.exists() else EXAMPLE_FILE
            raw = json.loads(path.read_text(encoding="utf-8"))
            source = str(path)

        # A bare list is accepted too - it is what people write first.
        entries = raw.get("hosts", []) if isinstance(raw, dict) else raw
        hosts = {h.id: h for h in (self._build(e) for e in entries)}

        for host in hosts.values():
            if not host.password:
                host.password = os.environ.get(password_env_name(host.id)) or None
            if not host.host_key:
                host.host_key = os.environ.get(hostkey_env_name(host.id), "")
            if not host.key_file:
                host.key_file = os.environ.get(keyfile_env_name(host.id), "")
            if host.jump and not host.jump.password:
                host.jump.password = (
                    os.environ.get(password_env_name(host.id) + "_JUMP") or None
                )

        with self._lock:
            self._hosts = hosts
            self.source = source

    def save(self) -> None:
        """Write hosts.json. Never writes over hosts.example.json - the first
        save on a fresh checkout creates the real file instead."""
        if not self.writable:
            raise OSError(
                f"설정이 환경변수({HOSTS_JSON_ENV})에서 왔습니다. "
                "파일로 저장해도 다음 재시작에 덮어써지므로 저장하지 않습니다."
            )
        with self._lock:
            payload = {"hosts": [h.stored() for h in self._hosts.values()]}
            HOSTS_FILE.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            self.source = str(HOSTS_FILE)

    @property
    def using_example(self) -> bool:
        return self.source == str(EXAMPLE_FILE)

    @property
    def from_env(self) -> bool:
        return self.source == f"${HOSTS_JSON_ENV}"

    @property
    def writable(self) -> bool:
        """Whether 저장 can persist. Configuration handed in by the
        environment is owned by the platform, not by this process - writing a
        file next to it would be shadowed on the next restart and is worse
        than admitting the button cannot help here."""
        return not self.from_env

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
            for key in DERIVED:
                merged.pop(key, None)

            jump = merged.get("jump")
            if isinstance(jump, dict):
                for key in DERIVED:
                    jump.pop(key, None)
                if not jump.get("address"):
                    merged["jump"] = None

            host = self._build({k: v for k, v in merged.items() if v is not None or k == "jump"})
            host.password = password or (existing.password if existing else None)
            if existing and existing.jump and host.jump and not host.jump.password:
                host.jump.password = existing.jump.password
            self._hosts[host_id] = host
            return host


registry = Registry()
