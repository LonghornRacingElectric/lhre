"""Listing and time-range selection of Pi log files over SSH, and the optional
removal of logs the server holds a verified copy of.

Loggerd writes one CSV per session named ``orion_<startMs>.csv`` and keeps
appending to it for the duration of that session. So a file covers the window
``[startMs, mtime]``: the filename gives the start, the mtime gives the end.
A file belongs to a requested ``[from, to]`` window iff those intervals overlap.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import shlex

from .config import config
from .models import RemoteFile


def _ssh_base_cmd() -> list[str]:
    # Reuse the same key/opts the rsync transport uses, so a successful listing
    # guarantees rsync can authenticate too.
    return ["ssh", "-i", config.ssh_key, *shlex.split(config.ssh_opts), config.ssh_target]


async def _ssh(remote_cmd: str, timeout: float, what: str,
               stdin: str | None = None) -> tuple[int, str, str]:
    """Run one command on the Pi. Returns (exit code, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *_ssh_base_cmd(), remote_cmd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
    )
    # Bound the call so a hung cellular link can't wedge the request indefinitely
    # (SSH keepalives would eventually bail too, but this is the hard ceiling).
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        await proc.communicate()
        raise RuntimeError(f"timed out {what} over SSH")
    return proc.returncode, out.decode(errors="replace"), err.decode(errors="replace").strip()


def _parse_start_ms(name: str) -> int | None:
    if not (name.startswith(config.log_prefix) and name.endswith(config.log_suffix)):
        return None
    core = name[len(config.log_prefix): -len(config.log_suffix)]
    if not core.isdigit():
        return None
    return int(core)


async def list_remote_logs() -> list[RemoteFile]:
    """SSH to the Pi and enumerate log files with size + mtime.

    Uses ``find -printf`` (epoch mtime, raw bytes, bare filename) which is
    locale-independent and trivially parseable, unlike ``ls``.
    """
    remote = shlex.quote(config.remote_log_dir)
    pattern = shlex.quote(f"{config.log_prefix}*{config.log_suffix}")
    # %f = filename, %s = size bytes, %T@ = mtime epoch seconds (float)
    find_cmd = (
        f"find {remote} -maxdepth 1 -type f -name {pattern} "
        f"-printf '%f\\t%s\\t%T@\\n'"
    )
    rc, out, err = await _ssh(find_cmd, 45.0, "listing remote logs")
    if rc != 0:
        raise RuntimeError(f"Failed to list remote logs (exit {rc}): {err}")

    files: list[RemoteFile] = []
    for line in out.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        name, size_s, mtime_s = parts
        start_ms = _parse_start_ms(name)
        if start_ms is None:
            continue
        try:
            size = int(size_s)
            end_ms = int(float(mtime_s) * 1000)
        except ValueError:
            continue
        # A session can't end before it starts; guard against odd mtimes.
        files.append(RemoteFile(name=name, start_ms=start_ms, end_ms=max(end_ms, start_ms), size=size))

    files.sort(key=lambda f: f.start_ms)
    return files


def select_in_range(files: list[RemoteFile], from_ms: int, to_ms: int) -> list[RemoteFile]:
    """Return files whose [start, end] overlaps the requested [from, to]."""
    if to_ms < from_ms:
        from_ms, to_ms = to_ms, from_ms
    return [f for f in files if f.start_ms <= to_ms and f.end_ms >= from_ms]


# Slowest rate at which the Pi is assumed to hash a file off its SD card. Only
# sizes the hard timeout of the verify pass; a dead link ends sooner (keepalives).
_VERIFY_MIN_BPS = 2_000_000


def _sha256(path: str) -> str:
    with open(path, "rb") as fh:
        # The car's copy is about to go: ours must be on disk, not just cached.
        os.fsync(fh.fileno())
        return hashlib.file_digest(fh, "sha256").hexdigest()


async def delete_verified(local: dict[str, str]) -> list[dict]:
    """Free the Pi's SD card of the logs in ``local`` (name -> server copy path).

    A log is removed only if the car's file has the size and sha256 of the server
    copy. The hash is checked on the Pi by the same shell function that removes
    the file, right before the ``rm``. With ``delete_dry_run`` the same checks
    run and nothing is removed.

    Never touched: a name the Pi's own listing doesn't return (so only real
    ``orion_<startMs>.csv`` files in the log dir, each by its exact quoted path,
    no glob), the newest log, and any log modified in the last
    ``delete_min_age_s`` seconds: the logger may still be writing those.

    Returns one ``{"name", "action", "reason"}`` per name, in order. Action is
    ``deleted``, ``would_delete`` (dry run), ``skipped`` or ``failed``.
    """
    # Clock first: a write between the two calls then reads as "too recent".
    rc, out, err = await _ssh("date +%s", 45.0, "reading the Pi clock")
    if rc != 0 or not out.strip().isdigit():
        raise RuntimeError(f"Failed to read the Pi clock (exit {rc}): {err}")
    pi_now = int(out)
    remote = {f.name: f for f in await list_remote_logs()}
    # Newest by filename and by mtime: the Pi's clock can be stale after a boot,
    # so the two need not be the same file.
    newest = set()
    if remote:
        newest = {max(remote.values(), key=lambda f: f.start_ms).name,
                  max(remote.values(), key=lambda f: f.end_ms).name}
    # ponytail: "may still be written" is judged by newest + mtime. A log the
    # logger holds open but has not written for delete_min_age_s, and that is not
    # the newest, would pass. Upgrade: ask the Pi which file loggerd has open.

    result: dict[str, tuple[str, str]] = {}
    act = 'echo "would_delete $n"' if config.delete_dry_run else 'rm -- "$1" && echo "deleted $n"'
    script = [
        'v() { n=${1##*/}; h=$(nice -n 19 sha256sum < "$1") || return; '
        'if [ "${h%% *}" = "$2" ]; then ' + act + '; else echo "differs $n"; fi; }'
    ]
    asked: list[str] = []
    nbytes = 0
    for name, path in local.items():
        rf = remote.get(name)
        age = pi_now - rf.end_ms / 1000 if rf else 0
        if rf is None:
            why = "not on the car"
        elif name in newest:
            why = "newest log on the car, may still be written"
        elif not os.path.isfile(path):
            why = "no server copy"
        elif os.path.getsize(path) != rf.size:
            why = f"size differs (car {rf.size}, server {os.path.getsize(path)})"
        elif age < config.delete_min_age_s:
            why = f"modified {age:.0f}s ago by the Pi's clock (limit {config.delete_min_age_s}s)"
        else:
            sha = await asyncio.to_thread(_sha256, path)
            script.append(f"v {shlex.quote(f'{config.remote_log_dir}/{name}')} {sha}")
            asked.append(name)
            nbytes += rf.size
            continue
        result[name] = ("skipped", why)

    if asked:
        try:
            rc, out, err = await _ssh(
                "sh", 120 + nbytes / _VERIFY_MIN_BPS, "verifying logs on the car",
                stdin="\n".join(script) + "\n",
            )
        except RuntimeError as e:
            rc, out, err = -1, "", str(e)
        said = {}
        for line in out.splitlines():
            word, _, name = line.partition(" ")
            said[name] = word
        for name in asked:
            word = said.get(name)
            if word in ("deleted", "would_delete"):
                result[name] = (word, "size and sha256 match the server copy")
            elif word == "differs":
                result[name] = ("skipped", "sha256 differs from the server copy")
            else:
                # What the Pi said about this file, else its last word (ssh's own error).
                said_why = [l for l in err.splitlines() if name in l] or err.splitlines()[-1:]
                result[name] = (
                    "failed", f"not confirmed by the car (ssh exit {rc}): {' '.join(said_why)}"
                )
    return [{"name": n, "action": result[n][0], "reason": result[n][1]} for n in local]
