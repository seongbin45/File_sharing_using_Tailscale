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

    # The clock time the schedule is anchored to ("HH:MM", local). A daily
    # run happens at this time, a weekly one on Sunday at this time, a
    # 6-hourly one at this time and every 6 hours from it - see
    # tsbackup/schedule.py. 새벽 4시 is the design's default.
    at_time: str = "04:00"

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

    # sftp: the far end's host-key fingerprint (webadmin/app/hostkeys.py's
    # SHA256: form), pinned so a later connection to a different machine
    # answering the same address is refused rather than silently trusted.
    # Empty until the first successful connection - transports/sftp.py's
    # _pinned_policy() trusts-and-pins automatically then, since there is no
    # operator at a terminal to paste one in for an unattended install - or
    # filled in automatically by a pairing exchange.
    host_key: str = ""


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

    # Whether a remote sender pushes to this receiver over HTTP - decides
    # whether the receiver's own HTTP listener starts. This is about what
    # THIS machine expects to receive, not what this machine would send if
    # its role were flipped to sender; see receiver_uses_http() below for
    # the bug this field replaces.
    expects_http: bool = False


@dataclass
class AppConfig:
    role: str = ROLE_SENDER
    minimize_to_tray: bool = True
    autostart_engine: bool = False   # begin the loop as soon as the app opens
    # Whether the first-run wizard has been completed. Defaults False only
    # for a genuinely new install (no config file at all yet) - load()
    # forces this True when loading a file saved before this field existed,
    # so upgrading never sends an already-working install through the
    # wizard again.
    onboarded: bool = False
    # Identifies this installation to a receiver's known_senders registry
    # (see tsbackup/pairing.py) regardless of which role this machine plays -
    # a device name alone can collide or change, this doesn't. Empty until
    # first needed; ensure_device_id() fills it in and the caller saves.
    device_id: str = ""
    sender: SenderConfig = field(default_factory=SenderConfig)
    receiver: ReceiverConfig = field(default_factory=ReceiverConfig)

    def ensure_device_id(self) -> str:
        if not self.device_id:
            import secrets

            self.device_id = secrets.token_hex(8)
        return self.device_id

    # ---------------------------------------------------------------- io

    @classmethod
    def load(cls, path: Path | None = None) -> "AppConfig":
        path = path or CONFIG_PATH
        if not path.exists():
            return cls()
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = cls.from_dict(raw)
        if "onboarded" not in raw:
            # This file predates the wizard - it's already a working
            # install, not a fresh one.
            cfg.onboarded = True
        return cfg

    @classmethod
    def from_dict(cls, raw: dict) -> "AppConfig":
        cfg = cls()
        cfg.role = raw.get("role", cfg.role)
        if cfg.role not in ROLES:
            cfg.role = ROLE_SENDER
        cfg.minimize_to_tray = bool(raw.get("minimize_to_tray", cfg.minimize_to_tray))
        cfg.autostart_engine = bool(raw.get("autostart_engine", cfg.autostart_engine))
        cfg.onboarded = bool(raw.get("onboarded", cfg.onboarded))
        cfg.device_id = raw.get("device_id", cfg.device_id)
        raw_sender = raw.get("sender", {})
        raw_receiver = raw.get("receiver", {})
        cfg.sender = _fill(SenderConfig, raw_sender)
        cfg.receiver = _fill(ReceiverConfig, raw_receiver)
        if cfg.sender.transport not in TRANSPORTS:
            cfg.sender.transport = "taildrop"
        cfg.sender.fallback_transports = [
            t for t in cfg.sender.fallback_transports if t in TRANSPORTS
        ]
        if (
            "expects_http" not in raw_receiver
            and cfg.role == ROLE_RECEIVER
            and raw_sender.get("transport") == "http"
        ):
            # Migrate the old receiver_uses_http() misread: some real
            # installs are already receiving over HTTP only because that
            # buggy check happened to read "http" off the (unused, on a
            # pure-receiver box) sender section. Fixing the bug must not
            # silently stop HTTP receiving for them.
            cfg.receiver.expects_http = True
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
                out.append("보낼 폴더를 아직 정하지 않았어요.")
            elif not Path(s.source_dir).is_dir():
                out.append(f"보낼 폴더가 없어요: {s.source_dir}")
            if not s.work_dir:
                out.append("압축을 만들 작업 폴더를 아직 정하지 않았어요.")
            if s.transport == "taildrop" and not s.taildrop_targets:
                out.append("받는 컴퓨터를 아직 정하지 않았어요.")
            if s.transport in ("sftp", "http") and not s.host:
                out.append("받는 컴퓨터의 주소를 아직 정하지 않았어요.")
            if s.interval_minutes < 1:
                out.append("보내는 간격은 1분 이상이어야 해요.")
        else:
            r = self.receiver
            if not r.incoming_dir:
                out.append("받을 폴더를 아직 정하지 않았어요.")
            if not r.unpack_dir:
                out.append("풀어 둘 폴더를 아직 정하지 않았어요.")
            if self.receiver_uses_http() and not r.http_token:
                out.append("HTTP로 받으려면 토큰이 있어야 해요. 아무나 올릴 수 없게 고급 설정에서 정해 주세요.")
        return out

    def receiver_uses_http(self) -> bool:
        # The receiver runs an HTTP server only when a remote sender pushes
        # over HTTP. This used to (wrongly) read self.sender.transport - this
        # machine's own OUTBOUND setting, meaningless on a dedicated receiver
        # box - instead of anything describing the remote sender. Now reads
        # the receiver's own expects_http field; see from_dict()'s migration
        # for configs saved before that field existed.
        return self.role == ROLE_RECEIVER and self.receiver.expects_http


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
