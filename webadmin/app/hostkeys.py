"""Proving the machine on the other end is the right one.

The console used to connect with paramiko's AutoAddPolicy, which trusts
whatever answers the first time and pins it afterwards. That has a hole big
enough to lose the whole system through:

  the first connection is the one that hands over the password.

Anyone in position to answer instead of the real machine - a stolen MagicDNS
name, a compromised tailnet node, a hosts entry - collects the SSH password
of a machine holding every project this project exists to protect. And it is
not a one-time risk: known_hosts lived on the filesystem, so on a platform
that wipes the disk each deploy (Render does), EVERY connection was a first
connection.

So the fingerprint is configuration, not state. It sits in hosts.json next to
the address, survives redeploys because it is deployed, and can be reviewed in
a diff. An unknown or changed key is a hard failure with no prompt to click
through, because the operator cannot verify a fingerprint from inside the
session that is being attacked.

Bootstrapping is the one case that genuinely cannot be verified in-band, so
it is an explicit, loud, opt-in step (TSCONSOLE_TOFU=1) that prints the
fingerprint for you to check against the machine itself:

    Windows:  ssh-keygen -lf C:\\ProgramData\\ssh\\ssh_host_ed25519_key.pub
"""

from __future__ import annotations

import base64
import hashlib
import os

import paramiko


class HostKeyError(RuntimeError):
    pass


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH's SHA256 form, so it can be compared against ssh-keygen -lf
    output by eye without converting anything."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def tofu_allowed() -> bool:
    return os.environ.get("TSCONSOLE_TOFU", "").strip() in ("1", "true", "yes")


class Pinned(paramiko.MissingHostKeyPolicy):
    """Accept exactly one fingerprint, or refuse.

    paramiko calls this only when the key is not already in known_hosts, so
    it covers the first connection - which is the dangerous one. A key that
    is present and differs raises BadHostKeyException inside paramiko before
    this is reached, which is the behaviour we want there too.
    """

    def __init__(self, expected: str | None, label: str) -> None:
        self.expected = (expected or "").strip()
        self.label = label
        self.seen: str | None = None

    def missing_host_key(self, client, hostname, key) -> None:  # noqa: ANN001
        got = fingerprint(key)
        self.seen = got

        if self.expected:
            if _same(got, self.expected):
                return
            raise HostKeyError(
                f"{self.label} 의 호스트 키가 등록된 값과 다릅니다.\n"
                f"  등록됨: {self.expected}\n"
                f"  받은 값: {got}\n"
                "연결을 중단했습니다. 기기를 다시 설치했다면 hosts.json 의 "
                "host_key 를 새 값으로 고치십시오. 그런 적이 없다면 "
                "중간자 공격일 수 있습니다."
            )

        if tofu_allowed():
            # Loud on purpose: this is the one moment nothing verifies the
            # far end, and it must not pass unnoticed in a log nobody reads.
            print(
                f"\n*** TSCONSOLE_TOFU: {self.label} 의 호스트 키를 검증 없이 받았습니다.\n"
                f"***   {got}\n"
                "*** 기기에서 직접 확인한 뒤 hosts.json 의 host_key 에 넣고,\n"
                "*** TSCONSOLE_TOFU 를 끄십시오.\n",
                flush=True,
            )
            return

        raise HostKeyError(
            f"{self.label} 의 호스트 키가 등록돼 있지 않습니다.\n"
            f"  받은 값: {got}\n"
            "\n첫 연결은 비밀번호를 건네는 연결이라 무조건 신뢰할 수 없습니다.\n"
            "기기에서 직접 지문을 확인하십시오:\n"
            "  ssh-keygen -lf C:\\ProgramData\\ssh\\ssh_host_ed25519_key.pub\n"
            "일치하면 hosts.json 의 host_key 에 넣으십시오.\n"
            "\n(부트스트랩 중이라 검증 없이 받아야 한다면 TSCONSOLE_TOFU=1)"
        )


def _same(a: str, b: str) -> bool:
    """Compare fingerprints tolerantly: people paste them with or without the
    SHA256: prefix, and with the base64 padding ssh-keygen omits."""
    return _norm(a) == _norm(b)


def _norm(value: str) -> str:
    text = value.strip()
    if text.lower().startswith("sha256:"):
        text = text[7:]
    return text.rstrip("=")


def load_private_key(path: str, passphrase: str | None) -> paramiko.PKey:
    """Any key type paramiko supports, chosen by content rather than by us
    guessing from the filename."""
    try:
        # paramiko calls it "password"; it is the key passphrase, not a
        # login password, and the two must never be confused at the caller.
        return paramiko.PKey.from_path(path, password=passphrase or None)
    except paramiko.PasswordRequiredException as exc:
        raise HostKeyError(
            f"개인 키 {path} 에 암호가 걸려 있습니다. "
            "TSCONSOLE_KEYPASS_<ID> 에 넣으십시오."
        ) from exc
    except (OSError, paramiko.SSHException) as exc:
        raise HostKeyError(f"개인 키를 읽지 못했습니다 ({path}): {exc}") from exc
