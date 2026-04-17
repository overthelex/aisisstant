"""Project-level activity report: extraction, aggregation, snapshotter.

The snapshotter runs as a background task in the main tracker service and
writes a JSON file containing pre-computed reports for every supported
time range. The GTK `aisisstant-report` UI just reads that file, so the
window opens instantly instead of waiting on a live DB query.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import asyncpg

_HOME = os.path.expanduser("~")
_PROJECT_NOISE_DIRS = {
    "src", "repos", "repo", "work", "code", "projects",
    "documents", "dev", "development", "git",
}

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Heuristic project extraction
# ---------------------------------------------------------------------------

_TERMINAL_CLASSES = {
    "alacritty",
    "gnome-terminal-server", "gnome-terminal", "org.gnome.terminal",
    "kitty",
    "wezterm", "org.wezfurlong.wezterm",
    "tilix",
    "konsole", "org.kde.konsole",
    "xterm", "urxvt",
    "terminator",
    "ghostty", "com.mitchellh.ghostty",
    "foot", "foot-server",
    "st-256color", "st",
}

_BROWSER_CLASSES = {
    "firefox", "firefox-esr",
    "google-chrome", "google-chrome-unstable",
    "chromium", "chromium-browser",
    "brave-browser",
    "microsoft-edge", "microsoft-edge-stable",
    "vivaldi-stable",
    "zen-alpha", "zen-browser",
}

_EDITOR_CLASSES = {
    "code", "code-oss", "code-insiders", "vscodium",
    "cursor",
    "windsurf",
    "sublime_text",
    "emacs",
    "nvim", "nvim-qt",
}

_EDITOR_PREFIX_JETBRAINS = "jetbrains-"

# bash-ish "user@host: ~/some/path"
_RE_USER_AT_HOST = re.compile(r"^[^@\s]+@[^\s:]+:\s*(?P<path>\S.*)$")
# Bare "~/foo/bar" or "/home/x/foo/bar"
_RE_BARE_PATH = re.compile(r"^(?P<path>~(?:/[^\s]+)*|/[^\s]+)$")
# GitHub-style "owner/repo" slug anywhere in a title
_RE_REPO_SLUG = re.compile(r"\b([A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]+)\b")


def _norm_class(wm_class: str) -> str:
    return (wm_class or "").strip().lower().replace(" ", "-")


def _classify(wm_class: str) -> str:
    if not wm_class:
        return "other"
    norm = _norm_class(wm_class)
    if norm in _TERMINAL_CLASSES:
        return "terminal"
    if norm in _BROWSER_CLASSES or norm.endswith("-browser"):
        return "browser"
    if norm in _EDITOR_CLASSES or norm.startswith(_EDITOR_PREFIX_JETBRAINS):
        return "editor"
    return "other"


def _project_from_path(path: str) -> str | None:
    path = path.strip().rstrip("/")
    if not path:
        return None
    if path == "~":
        return "~"
    if path.startswith("~/"):
        tail = path[2:]
    elif path.startswith("/"):
        tail = path.lstrip("/")
    else:
        tail = path
    parts = [p for p in tail.split("/") if p]
    if not parts:
        return "~"
    noise = {"src", "repos", "work", "code", "projects", "documents"}
    for p in parts:
        if p.lower() in noise:
            continue
        return p
    return parts[-1]


def _project_from_cwd(cwd: str) -> str | None:
    """Map a filesystem cwd to a project name when it lives under $HOME."""
    if not cwd:
        return None
    cwd = cwd.rstrip("/")
    if cwd in ("", "/", _HOME):
        return None
    if not cwd.startswith(_HOME + os.sep):
        return None
    rel = cwd[len(_HOME) + 1:]
    parts = [p for p in rel.split(os.sep) if p]
    for p in parts:
        if p.lower() in _PROJECT_NOISE_DIRS:
            continue
        return p
    return parts[-1] if parts else None


def extract_project(
    wm_class: str, title: str, cwd: str = ""
) -> tuple[str, str]:
    """Return (bucket, project_name). Bucket ∈ terminal/browser/editor/other.

    When `cwd` points into the user's home tree, it is the strongest signal
    of which project a row belongs to and wins over heuristic title parsing.
    """
    wm_class = (wm_class or "").strip()
    title = (title or "").strip()
    bucket = _classify(wm_class)

    cwd_proj = _project_from_cwd(cwd)
    if cwd_proj and bucket in ("terminal", "editor", "other"):
        return bucket, cwd_proj

    if bucket == "terminal":
        m = _RE_USER_AT_HOST.match(title)
        if m:
            proj = _project_from_path(m.group("path"))
            if proj:
                return bucket, proj
        m = _RE_BARE_PATH.match(title)
        if m:
            proj = _project_from_path(m.group("path"))
            if proj:
                return bucket, proj
        if ":" in title:
            head = title.split(":", 1)[0].strip()
            if head:
                return bucket, head
        return bucket, title or "(terminal)"

    if bucket == "browser":
        for m in _RE_REPO_SLUG.finditer(title):
            slug = m.group(1)
            if "/" in slug and not slug.startswith(("http", "www")):
                return bucket, slug
        parts = re.split(r"\s+[—–-]\s+", title)
        head = parts[0].strip() if parts else title
        return bucket, head or "(browser)"

    if bucket == "editor":
        parts = re.split(r"\s+[—–-]\s+", title)
        if len(parts) >= 2:
            app_tail = parts[-1].lower()
            if any(
                k in app_tail
                for k in ("visual studio code", "pycharm", "intellij",
                          "cursor", "windsurf", "sublime", "emacs")
            ):
                parts = parts[:-1]
        if len(parts) >= 2:
            return bucket, parts[-1].strip() or parts[0].strip() or "(editor)"
        if parts:
            return bucket, parts[0].strip() or "(editor)"
        return bucket, "(editor)"

    return bucket, wm_class or "(unknown)"


# ---------------------------------------------------------------------------
# Aggregation -> JSON-serialisable dicts
# ---------------------------------------------------------------------------

MAX_TITLES_PER_PROJECT = 30


@dataclass
class _Title:
    title: str
    wm_class: str
    seconds: float = 0.0
    hits: int = 0


@dataclass
class _Project:
    bucket: str
    name: str
    seconds: float = 0.0
    hits: int = 0
    titles: dict[str, _Title] = field(default_factory=dict)


def aggregate(rows: Iterable) -> tuple[list[dict], float]:
    """Aggregate rows of (window_start, window_end, wm_class, window_title[, cwd])."""
    projects: dict[tuple[str, str], _Project] = {}
    total = 0.0
    for r in rows:
        start = r["window_start"]
        end = r["window_end"]
        if not end or not start or end <= start:
            continue
        wm = r["wm_class"] or ""
        title = r["window_title"] or ""
        cwd = ""
        try:
            cwd = r["cwd"] or ""
        except (KeyError, TypeError):
            cwd = ""
        secs = (end - start).total_seconds()
        total += secs
        bucket, name = extract_project(wm, title, cwd)
        key = (bucket, name)
        proj = projects.get(key)
        if proj is None:
            proj = _Project(bucket=bucket, name=name)
            projects[key] = proj
        proj.seconds += secs
        proj.hits += 1
        te = proj.titles.get(title)
        if te is None:
            te = _Title(title=title or "(no title)", wm_class=wm)
            proj.titles[title] = te
        te.seconds += secs
        te.hits += 1

    ordered = sorted(projects.values(), key=lambda p: p.seconds, reverse=True)
    out: list[dict] = []
    for p in ordered:
        titles = sorted(
            p.titles.values(), key=lambda t: t.seconds, reverse=True
        )
        title_rows = [
            {
                "title": t.title,
                "wm_class": t.wm_class,
                "seconds": round(t.seconds, 2),
                "hits": t.hits,
            }
            for t in titles[:MAX_TITLES_PER_PROJECT]
        ]
        extra = len(titles) - len(title_rows)
        out.append(
            {
                "bucket": p.bucket,
                "name": p.name,
                "seconds": round(p.seconds, 2),
                "hits": p.hits,
                "titles": title_rows,
                "titles_truncated": extra if extra > 0 else 0,
            }
        )
    return out, round(total, 2)


# ---------------------------------------------------------------------------
# Cache file layout
# ---------------------------------------------------------------------------

RANGES: list[tuple[str, timedelta]] = [
    ("1h", timedelta(hours=1)),
    ("3h", timedelta(hours=3)),
    ("8h", timedelta(hours=8)),
    ("24h", timedelta(hours=24)),
    ("2d", timedelta(days=2)),
    ("3d", timedelta(days=3)),
    ("7d", timedelta(days=7)),
]

_CACHE_ENV = "AISISSTANT_REPORT_CACHE"


def cache_path() -> Path:
    override = os.environ.get(_CACHE_ENV)
    if override:
        return Path(override)
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "aisisstant" / "report.json"
    return Path.home() / ".cache" / "aisisstant" / "report.json"


# ---------------------------------------------------------------------------
# Snapshotter task
# ---------------------------------------------------------------------------

class ReportSnapshotter:
    """Pre-compute the project report every `interval` seconds.

    Fetches the widest range once, slices in memory for the shorter ranges,
    writes JSON atomically to the cache file. Pre-computing means the
    GUI opens instantly — no DB roundtrip on user click.
    """

    def __init__(self, pool: asyncpg.Pool, interval_seconds: float = 1.0):
        self.pool = pool
        self.interval = max(0.25, interval_seconds)
        self.path = cache_path()

    async def run(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        log.info(
            "ReportSnapshotter started (every %.2fs → %s)",
            self.interval, self.path,
        )
        while True:
            try:
                snapshot = await self._build()
                self._write(snapshot)
            except Exception:
                log.exception("ReportSnapshotter iteration failed")
            await asyncio.sleep(self.interval)

    async def _build(self) -> dict:
        now = datetime.now(timezone.utc)
        widest = max(delta for _, delta in RANGES)
        since_widest = now - widest

        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT window_start, window_end, wm_class, window_title, cwd
                FROM activity_scores
                WHERE window_start >= $1
                ORDER BY window_start
                """,
                since_widest,
            )

        ranges_out: dict[str, dict] = {}
        for key, delta in RANGES:
            cutoff = now - delta
            subset = [r for r in rows if r["window_start"] >= cutoff]
            projects, total = aggregate(subset)
            ranges_out[key] = {
                "since": cutoff.isoformat(),
                "total_seconds": total,
                "sample_count": len(subset),
                "projects": projects,
            }
        return {
            "generated_at": now.isoformat(),
            "interval_seconds": self.interval,
            "ranges": ranges_out,
        }

    def _write(self, data: dict) -> None:
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, self.path)
