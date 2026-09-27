"""The pairing-code exchange: a receiver shows a short code, a sender types
it, and the fields that would otherwise be typed twice (device name, folder,
transport, host key) get filled in automatically instead.

A code can't literally carry a full config in 9 characters, so the real
mechanism is: the code encodes the receiver's Tailscale IP (fixed CGNAT
range, 100.64.0.0/10 - 22 bits) plus a random one-time secret (23 bits),
base32'd into 9 characters. The receiver runs a small stdlib HTTP listener
bound to that same Tailscale IP; the sender, having decoded the IP directly
from the code (no manual entry, no DNS needed), POSTs the secret to prove
it holds the code and to hand over its own device info in the same request
- this is a two-way exchange, not just the receiver handing out its own
details:

    receiver: generate_code() -> PairingListener.start() shows a code
    sender:   resolve_and_pair(code, ...) -> POST /pair
    receiver: records the sender into known_senders.json, issues a fresh
              confirm_token (and, for HTTP push, a fresh long-term upload
              token - never the pairing secret itself) and its own
              connection details in the response
    sender:   sends a small synthetic test archive, then
              confirm_test_transfer(...) -> POST /confirm, closing the loop
              the wizard's test-transfer step needs to prove receive+unpack
              actually happened, not just that sending didn't error

Brute force is stopped by a 5-wrong-attempt lockout and a minimum
inter-request delay, not by the secret's raw entropy (23 bits, ~8.4M values,
is not enough on its own against an unthrottled listener).
"""

from __future__ import annotations

import hashlib
import http.server
import json
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import hostkeys

# ------------------------------------------------------------- code shape

# Crockford's base32: excludes I, L, O, U so a typed/read-aloud code can't be
# confused between similar-looking letters and digits.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"

IP_BITS = 22          # 100.64.0.0/10: 6 bits of octet 2 + 8 + 8
SECRET_BITS = 23
CODE_CHARS = (IP_BITS + SECRET_BITS) // 5   # 45 bits / 5 = 9, exact fit
assert (IP_BITS + SECRET_BITS) % 5 == 0

PAIRING_PORT = 8781
CODE_TTL_SECONDS = 15 * 60
MAX_WRONG_ATTEMPTS = 5
MIN_REQUEST_INTERVAL = 0.2  # seconds - blocks flooding independent of lockout

KNOWN_SENDERS_FILENAME = "known_senders.json"


class PairingError(RuntimeError):
    """Raised for anything the wizard needs to show as a distinct error:
    expired/used/locked-out codes are a different message from an
    unreachable listener (a blocked port, a firewall prompt not yet
    answered) - see resolve_and_pair()/confirm_test_transfer()."""


# --------------------------------------------------------------- ip <-> int


def _ip_to_int(ip: str) -> int:
    parts = ip.split(".")
    if len(parts) != 4:
        raise PairingError(f"Tailscale 주소 형식이 아닙니다: {ip}")
    try:
        octets = [int(p) for p in parts]
    except ValueError as exc:
        raise PairingError(f"Tailscale 주소 형식이 아닙니다: {ip}") from exc
    if not all(0 <= o <= 255 for o in octets):
        raise PairingError(f"Tailscale 주소 형식이 아닙니다: {ip}")
    o1, o2, o3, o4 = octets
    if o1 != 100 or not (64 <= o2 <= 127):
        raise PairingError(
            f"Tailscale CGNAT 범위(100.64.0.0/10)의 주소가 아닙니다: {ip}"
        )
    return ((o2 - 64) << 16) | (o3 << 8) | o4


def _int_to_ip(value: int) -> str:
    o2 = ((value >> 16) & 0x3F) + 64
    o3 = (value >> 8) & 0xFF
    o4 = value & 0xFF
    return f"100.{o2}.{o3}.{o4}"


def _b32_encode(value: int, length: int) -> str:
    return "".join(
        _ALPHABET[(value >> (5 * i)) & 0x1F] for i in range(length - 1, -1, -1)
    )


def _b32_decode(text: str) -> int:
    value = 0
    for ch in text:
        try:
            idx = _ALPHABET.index(ch)
        except ValueError as exc:
            raise PairingError(f"코드에 올바르지 않은 글자가 있습니다: {ch}") from exc
        value = (value << 5) | idx
    return value


def pack_code(ip: str, secret: int) -> str:
    """Receiver side: encode (its own Tailscale IP, a fresh secret) into the
    9-character code shown on screen, formatted as two groups for
    readability (e.g. "K7M24-QPX9")."""
    if not (0 <= secret < (1 << SECRET_BITS)):
        raise ValueError(f"secret out of range for {SECRET_BITS} bits")
    combined = (_ip_to_int(ip) << SECRET_BITS) | secret
    raw = _b32_encode(combined, CODE_CHARS)
    return f"{raw[:5]}-{raw[5:]}"


def unpack_code(code: str) -> tuple[str, int]:
    """Sender side: decode a typed code back into (receiver's Tailscale IP,
    secret), with no server round trip needed to know where to connect."""
    raw = code.strip().upper().replace("-", "").replace(" ", "")
    if len(raw) != CODE_CHARS:
        raise PairingError(f"코드는 {CODE_CHARS}자여야 합니다: 받은 값 {len(raw)}자")
    combined = _b32_decode(raw)
    secret = combined & ((1 << SECRET_BITS) - 1)
    ip_bits = combined >> SECRET_BITS
    return _int_to_ip(ip_bits), secret


# ------------------------------------------------------- known senders file


def _load_registry(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_registry(path: Path, registry: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")


def load_known_senders(path: Path) -> dict:
    """Public read for Phase 4's silence-detection UI: device_id ->
    {device_name, interval_minutes, first_seen, last_seen}."""
    return _load_registry(path)


def record_heartbeat(path: Path, device_id: str, interval_minutes=None) -> None:
    """Update a known sender's last_seen when an ordinary scheduled transfer
    actually arrives - distinct from _handle_pair()'s registry write, which
    only ever fires once, at pairing time. Without this, overdue_senders()
    would eventually flag every paired sender as overdue regardless of
    whether it's still sending fine - the registry would only ever prove
    "we once paired," not "still alive." No auth needed here: this reads
    the receiver's own already-unpacked files (receiver.py's
    Receiver._record_heartbeat()), not a network request.

    A device_id the registry has never seen (e.g. a config that embeds one
    without having gone through /pair) is recorded fresh rather than
    dropped, so it starts being tracked from its first real arrival.
    """
    if not device_id:
        return
    registry = _load_registry(path)
    now = time.time()
    entry = dict(registry.get(device_id, {}))
    entry["last_seen"] = now
    entry.setdefault("first_seen", now)
    entry.setdefault("device_name", device_id)
    if interval_minutes:
        entry["interval_minutes"] = interval_minutes
    registry[device_id] = entry
    _save_registry(path, registry)


def overdue_senders(registry: dict, now: float | None = None, grace: float = 1.5) -> list[dict]:
    """Which known senders are overdue for their expected interval - the
    receiver home screen's silence-detection banner reads this, independent
    of Qt so it's testable headlessly. `grace` multiplies the sender's own
    reported interval before calling it late, so one run running a few
    minutes behind schedule isn't a false alarm.

    Returns a list of {device_id, device_name, interval_minutes, last_seen,
    elapsed_seconds}, most overdue first. A sender missing interval_minutes
    or last_seen (a registry written by a version predating those fields)
    is skipped rather than guessed at.
    """
    now = now if now is not None else time.time()
    overdue = []
    for device_id, info in registry.items():
        interval_minutes = info.get("interval_minutes")
        last_seen = info.get("last_seen")
        if not interval_minutes or not last_seen:
            continue
        elapsed = now - last_seen
        if elapsed > interval_minutes * 60 * grace:
            overdue.append({**info, "device_id": device_id, "elapsed_seconds": elapsed})
    return sorted(overdue, key=lambda d: -d["elapsed_seconds"])


# ------------------------------------------------------- local ssh host key

_HOST_KEY_CANDIDATES = [
    r"C:\ProgramData\ssh\ssh_host_ed25519_key.pub",
    r"C:\ProgramData\ssh\ssh_host_ecdsa_key.pub",
    r"C:\ProgramData\ssh\ssh_host_rsa_key.pub",
    "/etc/ssh/ssh_host_ed25519_key.pub",
    "/etc/ssh/ssh_host_ecdsa_key.pub",
    "/etc/ssh/ssh_host_rsa_key.pub",
]


def local_ssh_host_key_fingerprint() -> str | None:
    """Best-effort: if this machine runs an OpenSSH server (Windows'
    optional feature, or POSIX sshd) and its public host key is readable,
    return its fingerprint so pairing can hand it to the sender pre-
    verified. Returns None when there's nothing to read - not every
    receiver runs an SSH server, and sftp senders still work via
    transports/sftp.py's own trust-on-first-connect when this is absent;
    this is a best-effort enhancement over that, not a requirement."""
    import base64

    import paramiko

    for candidate in _HOST_KEY_CANDIDATES:
        path = Path(candidate)
        if not path.exists():
            continue
        try:
            line = path.read_text(encoding="ascii").split()
            if len(line) < 2:
                continue
            key_type, blob_b64 = line[0], line[1]
            blob = base64.b64decode(blob_b64)
            key = paramiko.PKey.from_type_string(key_type, blob)
            return hostkeys.fingerprint(key)
        except (OSError, ValueError, paramiko.SSHException):
            continue
    return None


# --------------------------------------------------------------- HTTP side


class _Session:
    def __init__(self, secret: int) -> None:
        self.secret = secret
        self.created_at = time.time()
        self.wrong_attempts = 0
        self.paired = False
        self.confirm_token: str | None = None
        self.issued_token: str | None = None
        self.last_request_at = 0.0

    def expired(self) -> bool:
        return time.time() - self.created_at > CODE_TTL_SECONDS

    def locked_out(self) -> bool:
        return self.wrong_attempts >= MAX_WRONG_ATTEMPTS


class _PairingHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler_cls, *, listener: "PairingListener") -> None:
        super().__init__(addr, handler_cls)
        self.listener = listener


class _PairingHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        pass  # keep stdlib's access log out of the app's own log

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw or b"{}")
        except ValueError:
            return {}

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802 (stdlib's naming convention)
        listener: PairingListener = self.server.listener
        try:
            if self.path == "/pair":
                self._respond(200, listener._handle_pair(self._json_body()))
            elif self.path == "/confirm":
                self._respond(200, listener._handle_confirm(self._json_body()))
            else:
                self._respond(404, {"error": "not found"})
        except PairingError as exc:
            self._respond(403, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._respond(500, {"error": str(exc)})


class PairingListener:
    """Owns one pairing session on the receiver side: the code, the HTTP
    listener, and writing a newly-paired sender into known_senders.json.
    Bound only to the machine's own Tailscale interface IP, never 0.0.0.0.
    Lives only for the duration of the wizard's pairing screen - from code
    generation through wizard finish or the code's 15-minute expiry -
    because it must still answer /confirm after /pair has already
    succeeded, so it does not close itself after the first request."""

    def __init__(self, cfg, known_senders_path: Path, log=None) -> None:
        self.cfg = cfg
        self._known_senders_path = known_senders_path
        self._log = log or (lambda _m: None)
        self._server: _PairingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._session: _Session | None = None
        self._tailscale_ip: str | None = None

    def start(self, tailscale_ip: str) -> str:
        """Bind the listener (once) and return a fresh code."""
        self._tailscale_ip = tailscale_ip
        if self._server is None:
            self._server = _PairingHTTPServer(
                (tailscale_ip, PAIRING_PORT), _PairingHandler, listener=self
            )
            self._thread = threading.Thread(
                target=self._server.serve_forever, daemon=True
            )
            self._thread.start()
        return self.regenerate()

    def regenerate(self) -> str:
        """A new code without restarting the listener - the mockup's
        "새 코드" button. Invalidates whatever code was showing before."""
        secret = secrets.randbelow(1 << SECRET_BITS)
        self._session = _Session(secret)
        return pack_code(self._tailscale_ip, secret)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        self._server = None
        self._thread = None
        self._session = None

    def is_paired(self) -> bool:
        """Whether the current code has been successfully used - the
        wizard's receiver-side screen polls this to know when to stop
        saying "대기 중" and let the person move on."""
        return bool(self._session and self._session.paired)

    # ---------------------------------------------------------- http hooks

    def _handle_pair(self, body: dict) -> dict:
        session = self._session
        if session is None or session.expired():
            raise PairingError("코드가 만료되었습니다")
        now = time.time()
        if now - session.last_request_at < MIN_REQUEST_INTERVAL:
            raise PairingError("너무 빠른 요청입니다 - 잠시 후 다시 시도하십시오")
        session.last_request_at = now
        if session.locked_out():
            raise PairingError("틀린 시도가 너무 많아 이 코드는 폐기되었습니다 - 새 코드를 만드십시오")

        if body.get("secret") != session.secret:
            session.wrong_attempts += 1
            raise PairingError("코드가 일치하지 않습니다")
        if session.paired:
            raise PairingError("이미 사용된 코드입니다")

        session.paired = True
        session.confirm_token = secrets.token_hex(16)

        device_name = str(body.get("device_name", "")).strip()
        device_id = str(body.get("device_id", "")).strip()
        if not device_name or not device_id:
            raise PairingError("보내는 쪽 정보가 없습니다 (device_name/device_id)")

        registry = _load_registry(self._known_senders_path)
        existing = registry.get(device_id, {})
        registry[device_id] = {
            "device_name": device_name,
            "interval_minutes": body.get("interval_minutes"),
            "first_seen": existing.get("first_seen", now),
            "last_seen": now,
        }
        _save_registry(self._known_senders_path, registry)
        self._log(f"짝 등록: {device_name} ({device_id})")

        r = self.cfg.receiver
        response: dict = {
            "device_name": socket.gethostname(),
            "incoming_dir": r.incoming_dir,
            "transport_preference": ["taildrop", "sftp"],
            "confirm_token": session.confirm_token,
        }
        fp = local_ssh_host_key_fingerprint()
        if fp:
            response["host_key_fingerprint"] = fp
        if str(body.get("transport_preference")) == "http":
            session.issued_token = secrets.token_hex(16)
            response["issued_token"] = session.issued_token
        return response

    def _handle_confirm(self, body: dict) -> dict:
        session = self._session
        if session is None or not session.confirm_token:
            raise PairingError("짝 절차가 아직 끝나지 않았습니다")
        if body.get("confirm_token") != session.confirm_token:
            raise PairingError("확인 토큰이 올바르지 않습니다")
        return {"match": self._confirm_test_file(
            str(body.get("test_name", "")), str(body.get("expected_hash", ""))
        )}

    def _confirm_test_file(self, test_name: str, expected_hash: str) -> bool:
        from .receiver import SETTLE_SECONDS, Receiver

        if not test_name or not expected_hash:
            return False
        # Receiver.scan_once() only unpacks a file once it has seen its size
        # unchanged for at least SETTLE_SECONDS across two polls - a single
        # call on a freshly-constructed Receiver never unpacks anything,
        # since it has no prior size recorded yet to compare against, and
        # a too-short gap between the two calls doesn't satisfy the settle
        # window either. By the time /confirm is called the sender's
        # transport.send() has already returned (the archive is fully
        # written), so its size is already stable - only real elapsed time
        # is needed here, not a retry loop.
        receiver = Receiver(self.cfg, self._log)
        receiver.scan_once()
        time.sleep(SETTLE_SECONDS + 0.5)
        receiver.scan_once()
        unpack_dir = Path(self.cfg.receiver.unpack_dir)
        if not unpack_dir.exists():
            return False
        for candidate in unpack_dir.rglob(test_name):
            digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
            if digest == expected_hash:
                return True
        return False


# ------------------------------------------------------------- sender side


def resolve_and_pair(
    code: str,
    device_name: str,
    device_id: str,
    interval_minutes: int,
    transport_preference: str,
    timeout: float = 5.0,
) -> dict:
    """Sender side: decode the code (no server round trip needed for this
    part - the IP is embedded), then POST /pair to hand over this device's
    info and get the receiver's connection details back."""
    ip, secret = unpack_code(code)
    body = {
        "secret": secret,
        "device_name": device_name,
        "device_id": device_id,
        "interval_minutes": interval_minutes,
        "transport_preference": transport_preference,
    }
    return _post(ip, "/pair", body, timeout)


def confirm_test_transfer(
    code: str, confirm_token: str, test_name: str, expected_hash: str,
    timeout: float = 10.0,
) -> bool:
    """Sender side, after the test archive has actually been sent: ask the
    receiver whether it arrived and unpacked correctly. Without this round
    trip the wizard's test-transfer checklist can't tell "수신 → 해제"
    apart from wishful thinking - the sender alone cannot observe that."""
    ip, _secret = unpack_code(code)
    response = _post(ip, "/confirm", {
        "confirm_token": confirm_token,
        "test_name": test_name,
        "expected_hash": expected_hash,
    }, timeout)
    return bool(response.get("match"))


def local_tailscale_ip() -> str | None:
    """This machine's own Tailscale IPv4 address - needed to start a
    receiver's pairing listener (bound to it, never 0.0.0.0) and to encode
    it into the code shown on screen. None if the tailscale CLI isn't found
    or the machine isn't connected to a tailnet; the wizard shows a clear
    error rather than silently binding somewhere wrong."""
    import subprocess

    from .transports.taildrop import tailscale_binary

    binary = tailscale_binary()
    if not binary:
        return None
    try:
        proc = subprocess.run(
            [binary, "ip", "-4"], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return lines[0] if lines else None


def run_test_transfer(
    sender_cfg, code: str, confirm_token: str,
    step=lambda label, status: None,
) -> tuple[bool, str]:
    """The wizard's test-transfer step, independent of Qt so it can be
    tested headlessly - app/wizard.py's _TestTransferWorker only wraps this
    in a QThread and translates `step` calls into Qt signals.

    Compresses a small synthetic probe file, sends it over the real
    negotiated transport (sender_cfg.transport, already resolved by
    resolve_and_pair), then asks the receiver to confirm it arrived and
    unpacked correctly - a real file and a real round trip, not a
    simulation, is what makes the checklist mean something.

    `step(label, status)` is called with status one of "running"/"ok"/
    "fail" for each of "압축", "전송", "수신·해제 확인". Returns (ok, detail).
    """
    import tempfile as _tempfile

    from . import archiver
    from .transports import build as build_transport

    with _tempfile.TemporaryDirectory(prefix="ts_pair_test_src_") as src_dir, \
         _tempfile.TemporaryDirectory(prefix="ts_pair_test_work_") as work_dir:
        probe_name = f"probe_{secrets.token_hex(4)}.bin"
        content = secrets.token_bytes(4096)
        Path(src_dir, probe_name).write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()

        step("압축", "running")
        result = archiver.create_archive(src_dir, work_dir, level=1)
        if not result.ok:
            step("압축", "fail")
            return False, f"압축 실패: {result.error}"
        step("압축", "ok")

        step("전송", "running")
        try:
            transport = build_transport(sender_cfg.transport, sender_cfg, lambda _m: None)
        except ValueError as exc:
            step("전송", "fail")
            return False, str(exc)
        try:
            tr = transport.send(result.path)
        finally:
            transport.close()
        if not tr.ok:
            step("전송", "fail")
            return False, f"전송 실패: {tr.detail}"
        step("전송", "ok")

        step("수신·해제 확인", "running")
        try:
            matched = confirm_test_transfer(code, confirm_token, probe_name, digest)
        except PairingError as exc:
            step("수신·해제 확인", "fail")
            return False, str(exc)
        if not matched:
            step("수신·해제 확인", "fail")
            return False, "받는 쪽에서 파일을 확인하지 못했습니다"
        step("수신·해제 확인", "ok")

    return True, ""


def _post(ip: str, path: str, body: dict, timeout: float) -> dict:
    url = f"http://{ip}:{PAIRING_PORT}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", str(exc))
        except ValueError:
            detail = str(exc)
        raise PairingError(detail) from exc
    except urllib.error.URLError as exc:
        # Distinct from the PairingError branch above: this is "couldn't
        # even reach it" (blocked port, firewall prompt, wrong network),
        # not "reached it and it said no" - the wizard shows these
        # differently so a blocked port doesn't look like a typo'd code.
        raise PairingError(f"연결할 수 없습니다: {exc.reason}") from exc
