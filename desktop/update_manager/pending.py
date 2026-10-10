"""Persistent pending-download staging (Tier 2: a download started while
the main window was open survives across ticks, so a long defer doesn't
mean re-downloading from scratch once the window finally closes).

Much smaller than CloneUp's pending.py: there is no zip to extract, no
onedir root to find, and no reparse-point/junction handling for a
multi-file tree - just one staged Naru.exe with an idle-cache check
so an already-verified download isn't re-hashed every 10-minute tick."""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any

from update_manager.apply import _sha256_file, download_asset
from update_manager.github_release import LatestRelease
from update_manager.paths import pending_root

log = logging.getLogger("tsbackup_update_manager")


def pending_version_dir(install_dir: Path, version: str) -> Path:
    safe = "".join(c if c.isalnum() or c in ".-_" else "_" for c in version.strip())
    d = pending_root(install_dir) / (safe or "unknown")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _meta_path(pend: Path) -> Path:
    return pend / "meta.json"


def load_meta(pend: Path) -> dict[str, Any]:
    p = _meta_path(pend)
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_meta(pend: Path, meta: dict[str, Any]) -> None:
    pend.mkdir(parents=True, exist_ok=True)
    path = _meta_path(pend)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=0), encoding="utf-8")
    os.replace(str(tmp), str(path))


def exe_path(pend: Path, asset_name: str) -> Path:
    return pend / asset_name


def exe_ok_idle(pend: Path, release: LatestRelease) -> bool:
    """True if the complete exe is present and the idle cache says it was
    already verified (no re-hash needed)."""
    ep = exe_path(pend, release.asset_name)
    if not ep.is_file():
        return False
    meta = load_meta(pend)
    digest = (release.digest or "").split(":", 1)[-1].strip().lower()
    try:
        st = ep.stat()
    except OSError:
        return False
    return bool(
        meta.get("exe_sha256")
        and meta.get("exe_size") == st.st_size
        and meta.get("exe_mtime_ns") == getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
        and str(meta.get("digest") or "").split(":", 1)[-1].strip().lower() == digest
    )


def record_exe_verified(pend: Path, release: LatestRelease, sha256_hex: str) -> None:
    ep = exe_path(pend, release.asset_name)
    st = ep.stat()
    meta = load_meta(pend)
    meta.update(
        {
            "tag": release.tag,
            "asset_name": release.asset_name,
            "digest": release.digest,
            "download_url": release.download_url,
            "exe_sha256": sha256_hex,
            "exe_size": st.st_size,
            "exe_mtime_ns": getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9)),
            "verified_at": time.time(),
        }
    )
    save_meta(pend, meta)


def verify_exe_full(pend: Path, release: LatestRelease) -> str:
    """Apply-gate: always re-hash. Returns hex digest. Raises on mismatch."""
    ep = exe_path(pend, release.asset_name)
    if not ep.is_file():
        raise RuntimeError("pending exe missing at apply gate")
    got = _sha256_file(ep)
    expect = (release.digest or "").split(":", 1)[-1].strip().lower()
    if not expect or got != expect:
        ep.unlink(missing_ok=True)
        Path(str(ep) + ".part").unlink(missing_ok=True)
        Path(str(ep) + ".part.meta").unlink(missing_ok=True)
        raise RuntimeError(
            f"apply-gate digest mismatch: expected {expect[:12]}... got {got[:12]}..."
        )
    record_exe_verified(pend, release, got)
    return got


def ensure_exe(pend: Path, release: LatestRelease) -> None:
    """Download/resume the exe into pending if the idle cache misses."""
    meta = load_meta(pend)
    meta.update(
        {
            "tag": release.tag,
            "asset_name": release.asset_name,
            "digest": release.digest,
            "download_url": release.download_url,
        }
    )
    save_meta(pend, meta)
    if exe_ok_idle(pend, release):
        log.info("pending exe cache hit %s", release.asset_name)
        return
    ep = exe_path(pend, release.asset_name)
    part = Path(str(ep) + ".part")
    if ep.is_file() and not part.is_file():
        # Complete-looking file with no matching idle cache and no .part
        # in progress - treat as suspect, let download_asset start clean.
        ep.unlink(missing_ok=True)
    download_asset(release.download_url, ep, digest=release.digest)
    expect = (release.digest or "").split(":", 1)[-1].strip().lower()
    record_exe_verified(pend, release, expect)


def prune_other_versions(install_dir: Path, keep_version: str) -> None:
    root = pending_root(install_dir)
    if not root.is_dir():
        return
    keep = keep_version.strip()
    for child in list(root.iterdir()):
        if not child.is_dir():
            continue
        if child.name == keep:
            continue
        try:
            shutil.rmtree(child)
            log.info("pruned pending version %s", child.name)
        except OSError as e:
            log.warning("prune failed %s: %s", child, e)


def delete_version_dir(pend: Path) -> None:
    try:
        shutil.rmtree(pend, ignore_errors=False)
    except OSError as e:
        log.warning("could not delete pending %s: %s", pend, e)
