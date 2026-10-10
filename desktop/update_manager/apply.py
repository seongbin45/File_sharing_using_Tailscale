"""Download the release Naru.exe and atomically replace the installed
one.

Does **not** run Naru-Setup.exe (that would show the installer GUI).
Since Naru is a PyInstaller --onefile build, "apply" is just "replace
one file" - unlike CloneUp's onedir zip-extract-then-copy-folder-tree,
there is no separate stage/extract step and no risk of the apply step
wiping the update manager's own files (they live in the same {app}
folder as Naru.exe but under a different filename - see the plan's
"Install location" section).

Download resilience (ported near-verbatim from CloneUp's apply.py, which
is generic and well-exercised - kept as-is rather than re-derived):
Intra-call Range resume only: ``.part`` survives timeouts *inside one*
``download_asset`` invocation. Cross-tick / defer persistence is handled
by ``pending.py``. Attempt budget is a single loop
(``_DOWNLOAD_MAX_ATTEMPTS``) - no outer retry multiplication.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from update_manager.config import TSBACKUP_EXE_NAME, USER_AGENT
from update_manager.github_release import LatestRelease, host_allowed
from update_manager.versioning import version_tuple_to_str

log = logging.getLogger("tsbackup_update_manager")

# Naru.exe is tens of MB, not hundreds - shorter timeout than CloneUp's
# 900s is fine, but keep it generous for a slow tailnet/VPN link.
_DOWNLOAD_TIMEOUT_SEC = 300
_DOWNLOAD_MAX_ATTEMPTS = 8
_ZERO_PROGRESS_ABORT_AFTER = 3
_REPLACE_RETRIES = 5
_REQUIRE_DIGEST = True
_DISK_MARGIN_BYTES = 8 * 1024 * 1024


def _ssl_context():
    import ssl

    return ssl.create_default_context()


def _part_path(dest: Path) -> Path:
    return Path(str(dest) + ".part")


def _meta_path(dest: Path) -> Path:
    return Path(str(dest) + ".part.meta")


def _load_meta(dest: Path) -> dict:
    path = _meta_path(dest)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_meta(dest: Path, meta: dict) -> None:
    path = _meta_path(dest)
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=0), encoding="utf-8")


def _clear_partial(dest: Path) -> None:
    for p in (_part_path(dest), _meta_path(dest)):
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_content_range(header: str | None) -> tuple[int | None, int | None, int | None]:
    if not header:
        return None, None, None
    m = re.match(r"bytes\s+(\d+)\s*-\s*(\d+)\s*/\s*(\d+|\*)", header.strip(), re.I)
    if not m:
        return None, None, None
    start = int(m.group(1))
    end = int(m.group(2))
    total_s = m.group(3)
    total = None if total_s == "*" else int(total_s)
    return start, end, total


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            chunk = f.read(1024 * 256)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest().lower()


def _ensure_disk_space(dest: Path, expected_total: int | None) -> None:
    if not expected_total or expected_total <= 0:
        return
    try:
        free = shutil.disk_usage(dest.parent).free
    except OSError as e:
        log.warning("disk_usage failed: %s", e)
        return
    need = expected_total + _DISK_MARGIN_BYTES
    if free < need:
        raise RuntimeError(
            f"insufficient disk space: need ~{need} bytes, free {free}"
        )


def _atomic_replace(part: Path, dest: Path) -> None:
    """os.replace with short retries - Windows AV often locks a
    just-closed file."""
    last: BaseException | None = None
    for i in range(1, _REPLACE_RETRIES + 1):
        try:
            os.replace(str(part), str(dest))
            return
        except PermissionError as e:
            last = e
            log.warning("replace attempt %s/%s PermissionError: %s", i, _REPLACE_RETRIES, e)
            time.sleep(0.2 * i)
        except OSError as e:
            last = e
            if getattr(e, "winerror", None) == 17 or e.errno == 18:
                shutil.copy2(part, dest)
                part.unlink(missing_ok=True)
                return
            log.warning("replace attempt %s/%s OSError: %s", i, _REPLACE_RETRIES, e)
            time.sleep(0.2 * i)
    raise RuntimeError(f"could not finalize download (file locked?): {last}")


def download_asset(url: str, dest: Path, *, digest: str | None = None) -> None:
    """
    Download ``url`` to ``dest`` with intra-call Range resume.

    Tier 1 only: ``.part`` is reused across attempts *inside this call*.
    """
    if not url.startswith("https://") or not host_allowed(url):
        raise RuntimeError(f"refusing download host: {url!r}")
    if _REQUIRE_DIGEST and not (digest and str(digest).strip()):
        raise RuntimeError(
            "release asset digest missing - refuse download (fail-closed)"
        )
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(dest)
    meta = _load_meta(dest)
    expected_total: int | None = meta.get("expected_total")
    if isinstance(expected_total, int) and expected_total <= 0:
        expected_total = None
    etag = str(meta.get("etag") or "").strip() or None
    last_mod = str(meta.get("last_modified") or "").strip() or None

    last_err: BaseException | None = None
    zero_progress_streak = 0
    completed = False

    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        resume_from = part.stat().st_size if part.is_file() else 0
        size_before = resume_from
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/octet-stream",
        }
        if resume_from > 0:
            headers["Range"] = f"bytes={resume_from}-"
            if etag:
                headers["If-Range"] = etag
            elif last_mod:
                headers["If-Range"] = last_mod
                log.warning("resume with Last-Modified If-Range only (no ETag)")
            else:
                log.warning("resume without ETag/Last-Modified - size/digest only")

        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(
                req, context=_ssl_context(), timeout=_DOWNLOAD_TIMEOUT_SEC
            ) as resp:
                final = resp.geturl()
                if not host_allowed(final):
                    raise RuntimeError(f"redirect to disallowed host: {final!r}")
                status = int(getattr(resp, "status", None) or resp.getcode())
                hdrs = resp.headers

                mode = "wb"
                if resume_from > 0:
                    if status == 200:
                        log.info("server returned 200 on resume - restarting from byte 0")
                        part.unlink(missing_ok=True)
                        resume_from = 0
                        etag = None
                        last_mod = None
                        expected_total = None
                        mode = "wb"
                    elif status == 206:
                        cr = hdrs.get("Content-Range")
                        start, _end, total = _parse_content_range(cr)
                        if cr is None or start is None:
                            log.warning("206 without usable Content-Range - restart from 0")
                            part.unlink(missing_ok=True)
                            resume_from = 0
                            mode = "wb"
                        elif start != resume_from:
                            log.warning(
                                "Content-Range start %s != resume_from %s - restart",
                                start, resume_from,
                            )
                            part.unlink(missing_ok=True)
                            resume_from = 0
                            mode = "wb"
                        else:
                            mode = "ab"
                            if total is not None:
                                expected_total = total
                    elif status == 416:
                        if expected_total and resume_from >= expected_total:
                            completed = True
                            break
                        log.warning("HTTP 416 on resume - clearing partial")
                        part.unlink(missing_ok=True)
                        resume_from = 0
                        mode = "wb"
                    else:
                        raise RuntimeError(f"unexpected HTTP status on resume: {status}")
                else:
                    if status != 200:
                        raise RuntimeError(f"unexpected HTTP status: {status}")
                    mode = "wb"
                    cl = hdrs.get("Content-Length")
                    if cl and cl.isdigit():
                        expected_total = int(cl)
                    etag = (hdrs.get("ETag") or "").strip() or etag
                    last_mod = (hdrs.get("Last-Modified") or "").strip() or last_mod

                _ensure_disk_space(dest, expected_total)
                _save_meta(
                    dest,
                    {
                        "etag": etag,
                        "last_modified": last_mod,
                        "expected_total": expected_total,
                        "url": url,
                    },
                )

                with part.open(mode) as out:
                    while True:
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)

            size_after = part.stat().st_size if part.is_file() else 0
            if size_after <= size_before:
                zero_progress_streak += 1
            else:
                zero_progress_streak = 0

            if expected_total is not None and size_after < expected_total:
                raise RuntimeError(
                    f"incomplete download: got {size_after} of {expected_total} bytes"
                )

            completed = True
            break

        except (TimeoutError, OSError, urllib.error.URLError, RuntimeError) as e:
            last_err = e
            size_after = part.stat().st_size if part.is_file() else 0
            if size_after <= size_before:
                zero_progress_streak += 1
            else:
                zero_progress_streak = 0
            log.warning(
                "download attempt %s/%s failed (resume_from=%s, bytes=%s): %s",
                attempt, _DOWNLOAD_MAX_ATTEMPTS, resume_from, size_after, e,
            )
            if zero_progress_streak >= _ZERO_PROGRESS_ABORT_AFTER:
                _clear_partial(dest)
                raise RuntimeError(
                    f"download stalled with no progress for "
                    f"{zero_progress_streak} attempts: {e}"
                ) from e
            if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                break
            if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
                log.error("ENOSPC during download - burns attempt budget until hard fail")
            time.sleep(min(30, 5 * attempt))
            continue

    if not completed or not part.is_file():
        raise RuntimeError(
            f"download failed after {_DOWNLOAD_MAX_ATTEMPTS} attempts: {last_err}"
        )

    final_size = part.stat().st_size
    if expected_total is not None and final_size != expected_total:
        _clear_partial(dest)
        raise RuntimeError(
            f"size mismatch after download: got {final_size}, expected {expected_total}"
        )

    got_hash = _sha256_file(part)
    if digest:
        expect = str(digest).split(":", 1)[-1].strip().lower()
        if expect and got_hash != expect:
            _clear_partial(dest)
            raise RuntimeError(
                f"digest mismatch: expected {expect[:12]}... got {got_hash[:12]}..."
            )
        log.info("integrity=sha256 ok")
    else:
        log.info("integrity=size-only (no digest)")

    dest.unlink(missing_ok=True)
    _atomic_replace(part, dest)
    try:
        _meta_path(dest).unlink(missing_ok=True)
    except OSError:
        pass
    log.info("download complete %s bytes (attempts used <= %s)", final_size, _DOWNLOAD_MAX_ATTEMPTS)


def install_staged_exe(staged_exe: Path, install_dir: Path) -> None:
    """Atomically replace the live Naru.exe with an already-downloaded
    and digest-verified file (produced by pending.py's Tier-2 staging).
    Call this only after confirming the main window isn't visible and the
    running process has been stopped - the retry loop in _atomic_replace
    handles a lingering AV file lock, not a still-running process holding
    the exe open."""
    dest = install_dir / TSBACKUP_EXE_NAME
    _atomic_replace(staged_exe, dest)
    log.info("applied %s -> %s", staged_exe, dest)


def apply_exe_update(release: LatestRelease, install_dir: Path) -> None:
    """Convenience all-in-one path with no persistent Tier-2 staging:
    download straight into install_dir and replace. Used by
    ``python -m update_manager --once`` in a dev/test context and by the
    tray's manual "check now" action, where a single already-decided
    apply doesn't need cross-tick defer/resume - the production
    run_once() loop in __main__.py uses pending.py + install_staged_exe()
    instead, so a download survives a deferred apply across ticks."""
    staged = install_dir / f"{TSBACKUP_EXE_NAME}.new"
    log.info(
        "downloading %s -> %s",
        version_tuple_to_str(release.version),
        staged,
    )
    download_asset(release.download_url, staged, digest=release.digest)
    install_staged_exe(staged, install_dir)
