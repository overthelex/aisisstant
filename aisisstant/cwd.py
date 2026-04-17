"""Working-directory attribution for the focused-window pid.

Terminals host many independent sessions (tabs) under one emulator
process whose own cwd is just `$HOME`. The actual project the user is
working on is the cwd of a descendant (shell, Claude Code, editor
language server, etc.). This module walks `/proc` to pick the most
plausible project cwd for a given pid.

Heuristic: among the focused pid and its descendants, keep those whose
cwd is under `$HOME` and is not `$HOME` itself. Pick the one with the
most recent starttime — when the user is typing in a tab, new short-
lived children (git, ls, npm run, etc.) are spawned there and their
starttimes "win" over long-idle descendants elsewhere.
"""

from __future__ import annotations

import os
import time

_HOME = os.path.expanduser("~")
_BORING_CWDS = {"/", "/root", "/tmp", _HOME, "/home", f"{_HOME}/"}
_CACHE_TTL_SECONDS = 3.0

# Keyed by focused pid. Values are (captured_at_monotonic, cwd_or_None).
_cwd_cache: dict[int, tuple[float, str | None]] = {}


def _read_stat(pid: int) -> tuple[int, int] | None:
    """Return (ppid, starttime_ticks) for pid, or None if unavailable."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            data = f.read().decode("latin-1", errors="replace")
    except OSError:
        return None
    # /proc/<pid>/stat: "pid (comm) state ppid pgrp ... starttime ..."
    # comm may contain spaces and parens, so parse after the last ')'.
    rp = data.rfind(")")
    if rp < 0:
        return None
    fields = data[rp + 2:].split()
    if len(fields) < 20:
        return None
    try:
        ppid = int(fields[1])       # field 4 overall
        starttime = int(fields[19])  # field 22 overall
    except ValueError:
        return None
    return ppid, starttime


def _build_children_map() -> dict[int, list[int]]:
    out: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return out
    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        stat = _read_stat(pid)
        if stat is None:
            continue
        out.setdefault(stat[0], []).append(pid)
    return out


def _descendants(pid: int, children: dict[int, list[int]]) -> list[int]:
    out: list[int] = []
    stack = list(children.get(pid, []))
    while stack:
        p = stack.pop()
        out.append(p)
        stack.extend(children.get(p, []))
    return out


def _cwd(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return None


def _is_interesting(cwd: str | None) -> bool:
    if not cwd:
        return False
    if cwd in _BORING_CWDS:
        return False
    # Strict: require under $HOME — anything outside is either a system
    # directory or a sandbox (flatpak, snap, browser) where cwd isn't a
    # user project.
    if not cwd.startswith(_HOME + os.sep):
        return False
    return True


def best_cwd_for_pid(pid: int) -> str | None:
    """Return the most-likely project cwd associated with `pid`.

    Result is cached briefly per pid to keep /proc churn low; this is
    safe because window polls are frequent and pids are stable across
    polls while focus stays on one window.
    """
    if pid <= 0:
        return None
    now = time.monotonic()
    cached = _cwd_cache.get(pid)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    children = _build_children_map()
    candidates = [pid, *_descendants(pid, children)]

    scored: list[tuple[int, str]] = []
    for p in candidates:
        cwd = _cwd(p)
        if not _is_interesting(cwd):
            continue
        stat = _read_stat(p)
        starttime = stat[1] if stat else 0
        scored.append((starttime, cwd))

    if scored:
        scored.sort(reverse=True)  # most recent starttime first
        result: str | None = scored[0][1]
    else:
        # Fallback to the pid's own cwd even if it looks boring.
        result = _cwd(pid)

    _cwd_cache[pid] = (now, result)
    return result
