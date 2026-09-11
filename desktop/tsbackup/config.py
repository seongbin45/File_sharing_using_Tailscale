"""Application configuration.

One JSON file holds everything both roles need, because the app is a single
binary with a role toggle - shipping two configs for one program is how the
sending and receiving halves drift apart.

The file lives next to the user's data, not next to the .exe: a PyInstaller
build is often dropped in Program Files, which a normal account cannot write
to. %LOCALAPPDATA%\\TsBackup on Windows, ~/.config/tsbackup elsewhere.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path


def config_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "TsBackup"
    return Path.home() / ".config" / "tsbackup"


CONFIG_PATH = Path(os.environ.get("TSBACKUP_CONFIG", config_dir() / "config.json"))

ROLE_SENDER = "sender"
ROLE_RECEIVER = "receiver"
ROLES = (ROLE_SENDER, ROLE_RECEIVER)

# Transport identifiers. taildrop first because it is the transport this
# project already runs and trusts; the other two are the options the user
# asked to have available alongside it.
TRANSPORTS = ("taildrop", "sftp", "http")


@dataclass
class SenderConfig:
    # The directory compressed whole on every trigger. Everything under it goes
    # in - .env and .git included - matching the rest of this project.
    source_dir: str = ""

    # Where archives are built and where the newest one is kept. Needs room for
    # one full archive.
    work_dir: str = ""

    # LZMA2 level. 1 is fast and still smaller than zip; 5 balances; 9 costs
    # several times the time and memory for a few percent. 5 is the default for
    # the same reason as the batch version: the run is unattended.
    level: int = 5

    # Keep this many archives locally. The trigger deletes older ones before
    # compressing a fresh one - "지우고 새로 압축". 1 = only ever the newest.
    keep_local: int = 1

    # Re-compress and send every this many minutes. Exposed in minutes, not
    # hours, so testing does not mean waiting an hour; the UI presents hours.
    interval_minutes: int = 720

    # Primary transport, then fallbacks tried in order - the same idea as the
    # batch version's TARGETS list, generalised across transports.
    transport: str = "taildrop"
    fallback_transports: list[str] = field(default_factory=list)

    # taildrop: tailnet device names, tried in order.
    taildrop_targets: list[str] = field(default_factory=list)

    # sftp / http: one destination host.
    host: str = ""
    port: int = 0            # 0 = transport default (22 for sftp, 8770 for http)
    username: str = ""
    remote_dir: str = ""     # sftp: directory to drop the archive in

    # http push target token (sent as a header; the receiver checks it).
    http_token: str = ""


@dataclass
class ReceiverConfig:
    # Where arriving archives land. For taildrop this is the Taildrop inbox;
    # for sftp it is the directory the sender writes into; for http it is where
    # the built-in server saves uploads.
    incoming_dir: str = ""

    # Unpacked snapshots go here, each in its own timestamped folder.
    unpack_dir: str = ""

    # Delete the .7z after a successful unpack. The unpacked tree is the thing
    # worth keeping; the archive is a delivery envelope.
    delete_after_unpack: bool = True

    # http receiver only.
    http_bind: str = "127.0.0.1"   # never 0.0.0.0 without meaning to
    http_port: int = 8770
    http_token: str = ""           # required; uploads without it are refused


@dataclass
class AppConfig:
    role: str = ROLE_SENDER
    minimize_to_tray: bool = True
    autostart_engine: bool = False   # begin the loop as soon as the app opens
    sender: SenderConfig = field(default_factory=SenderConfig)
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)

    # ---------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = path or CONFIG_PATH
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls.from_dict(raw)

    @classmethod
    def from_dict(cls, raw: dict) -> "AppConfig":
        cfg = cls()
        cfg.role = raw.get("role", cfg.role)
        if cfg.role not in ROLES:
            cfg.role = ROLE_SENDER
        cfg.minimize_to_tray = bool(raw.get("minimize_to_tray", cfg.minimize_to_tray))
        cfg.autostart_engine = bool(raw.get("autostart_engine", cfg.autostart_engine))
        cfg.sender = _fill(SenderConfig, raw.get("sender", {}))
        cfg.receiver = _fill(ReceiverConfig, raw.get("receiver", {}))
        if cfg.sender.transport not in TRANSPORTS:
            cfg.sender.transport = "taildrop"
        cfg.sender.fallback_transports = [
            t for t in cfg.sender.fallback_transports if t in TRANSPORTS
        ]
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(asdict(self), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def problems(self) -> list[str]:
        """Human-readable reasons the current role cannot run yet. Empty list
        means ready. The UI shows these instead of letting a run fail obscurely."""
        out: list[str] = []
        if self.role == ROLE_SENDER:
            s = self.sender
            if not s.source_dir:
                out.append("압축할 대상 폴더가 설정되지 않았습니다.")
            elif not Path(s.source_dir).is_dir():
                out.append(f"대상 폴더가 없습니다: {s.source_dir}")
            if not s.work_dir:
                out.append("작업 폴더가 설정되지 않았습니다.")
            if s.transport == "taildrop" and not s.taildrop_targets:
                out.append("Taildrop 대상 기기 이름이 없습니다.")
            if s.transport in ("sftp", "http") and not s.host:
                out.append(f"{s.transport} 대상 host 가 없습니다.")
            if s.interval_minutes < 1:
                out.append("트리거 간격은 1분 이상이어야 합니다.")
        else:
            r = self.receiver
            if not r.incoming_dir:
                out.append("수신 폴더가 설정되지 않았습니다.")
            if not r.unpack_dir:
                out.append("압축 해제 폴더가 설정되지 않았습니다.")
            if self.receiver_uses_http() and not r.http_token:
                out.append("HTTP 수신에는 토큰이 필요합니다. 누구나 업로드할 수 있게 두지 마십시오.")
        return out

    def receiver_uses_http(self) -> bool:
        # The receiver runs an HTTP server only when the sender pushes over
        # HTTP. There is no separate receiver-side transport switch: the
        # receiver watches a folder for taildrop/sftp and serves for http.
        return self.role == ROLE_RECEIVER and self.sender.transport == "http"


def _fill(cls, raw: dict):
    """Build a dataclass from a dict, ignoring unknown keys so an older config
    on disk never crashes a newer build."""
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(cls)}
    kwargs = {}
    for name, f in fields.items():
        if name in raw:
            kwargs[name] = raw[name]
    return cls(**kwargs)
