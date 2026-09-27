"""Comparing host-key fingerprints, ported from webadmin/app/hostkeys.py.

desktop/ ships as a single PyInstaller binary and webadmin/ ships as a
separate FastAPI service - different deploy stories, so this is a copy of
the pure comparison logic rather than a shared import across the two.
Keep this in sync with webadmin/app/hostkeys.py's
fingerprint()/_norm()/_same(); desktop/tests/selftest.py exercises the same
kind of fixtures and tolerance rules webadmin/tests/selftest.py does, to
catch the two drifting apart.

Where desktop's policy differs from webadmin's: webadmin refuses to trust
an unpinned key at all - an operator must paste the fingerprint in by hand,
or opt into TSCONSOLE_TOFU=1 for one deliberate bootstrap connection.
desktop has no operator at a terminal for an unattended installer, so its
own SFTP transport (transports/sftp.py) trusts-and-pins automatically on
the first connection instead - see that file's _pinned_policy().
"""

from __future__ import annotations

import base64
import hashlib

import paramiko


def fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH's SHA256 form, so it can be compared against ssh-keygen -lf
    output by eye without converting anything."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def same(a: str, b: str) -> bool:
    """Compare fingerprints tolerantly: people (and the pairing exchange)
    hand these around with or without the SHA256: prefix, and with or
    without the base64 padding ssh-keygen omits."""
    return _norm(a) == _norm(b)


def _norm(value: str) -> str:
    text = (value or "").strip()
    if text.lower().startswith("sha256:"):
        text = text[7:]
    return text.rstrip("=")
