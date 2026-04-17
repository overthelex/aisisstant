from __future__ import annotations

import asyncio
import logging
from typing import Any

import asyncpg

from .config import Config
from .models import (
    ActivityScore,
    IdleEvent,
    InputBucket,
    MicState,
    WindowSession,
)

log = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS window_sessions (
    id          BIGSERIAL PRIMARY KEY,
    started_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at    TIMESTAMPTZ,
    wm_class    TEXT NOT NULL,
    window_title TEXT,
    pid         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_ws_started ON window_sessions (started_at);
CREATE INDEX IF NOT EXISTS idx_ws_wm_class ON window_sessions (wm_class);

CREATE TABLE IF NOT EXISTS input_activity (
    id               BIGSERIAL PRIMARY KEY,
    bucket_start     TIMESTAMPTZ NOT NULL,
    bucket_end       TIMESTAMPTZ NOT NULL,
    key_press_count  INTEGER NOT NULL DEFAULT 0,
    key_rate_per_sec REAL,
    mouse_distance_px REAL NOT NULL DEFAULT 0,
    mouse_click_left  INTEGER NOT NULL DEFAULT 0,
    mouse_click_right INTEGER NOT NULL DEFAULT 0,
    mouse_click_middle INTEGER NOT NULL DEFAULT 0,
    scroll_distance   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ia_bucket ON input_activity (bucket_start);

CREATE TABLE IF NOT EXISTS mic_activity (
    id          BIGSERIAL PRIMARY KEY,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active   BOOLEAN NOT NULL,
    source_node TEXT,
    client_app  TEXT,
    client_pid  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mic_detected ON mic_activity (detected_at);

CREATE TABLE IF NOT EXISTS activity_scores (
    id             BIGSERIAL PRIMARY KEY,
    window_start   TIMESTAMPTZ NOT NULL,
    window_end     TIMESTAMPTZ NOT NULL,
    wm_class       TEXT NOT NULL,
    window_title   TEXT NOT NULL DEFAULT '',
    score          REAL NOT NULL,
    score_label    TEXT NOT NULL,
    key_presses    INTEGER NOT NULL DEFAULT 0,
    mouse_distance REAL NOT NULL DEFAULT 0,
    clicks         INTEGER NOT NULL DEFAULT 0,
    scroll         INTEGER NOT NULL DEFAULT 0,
    mic_active     BOOLEAN NOT NULL DEFAULT FALSE
);
ALTER TABLE activity_scores ADD COLUMN IF NOT EXISTS window_title TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_as_window ON activity_scores (window_start);
CREATE INDEX IF NOT EXISTS idx_as_class ON activity_scores (wm_class, window_start);

CREATE TABLE IF NOT EXISTS idle_events (
    id        BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMPTZ NOT NULL DEFAULT now(),
    idle_ms   BIGINT NOT NULL,
    is_locked BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_idle_ts ON idle_events (timestamp);
"""


async def create_pool(config: Config) -> asyncpg.Pool:
    # Retry on transient connect failures: at boot Postgres (docker) may not
    # be ready yet when aisisstant.service starts.
    delay = 1.0
    max_delay = 15.0
    total_waited = 0.0
    timeout = 120.0
    while True:
        try:
            return await asyncpg.create_pool(
                host=config.db_host,
                port=config.db_port,
                database=config.db_name,
                user=config.db_user,
                password=config.db_password,
                min_size=2,
                max_size=5,
            )
        except (OSError, asyncpg.PostgresError) as e:
            if total_waited >= timeout:
                raise
            log.warning(
                "DB connect failed (%s), retrying in %.1fs", e, delay
            )
            await asyncio.sleep(delay)
            total_waited += delay
            delay = min(delay * 2, max_delay)


async def run_migrations(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        for statement in SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(stmt)
    log.info("Database migrations applied")


class BatchWriter:
    """Collects data records and flushes them to PostgreSQL in batches."""

    def __init__(self, pool: asyncpg.Pool, flush_interval: float = 5.0):
        self.pool = pool
        self.flush_interval = flush_interval
        self._queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue(maxsize=10000)

    async def put(self, table: str, record: Any) -> None:
        await self._queue.put((table, record))

    async def run(self) -> None:
        log.info("BatchWriter started")
        while True:
            await asyncio.sleep(self.flush_interval)
            items: list[tuple[str, Any]] = []
            while not self._queue.empty():
                try:
                    items.append(self._queue.get_nowait())
                except asyncio.QueueEmpty:
                    break
            if items:
                await self._flush(items)

    async def _flush(self, items: list[tuple[str, Any]]) -> None:
        grouped: dict[str, list[Any]] = {}
        for table, record in items:
            grouped.setdefault(table, []).append(record)

        async with self.pool.acquire() as conn:
            for table, records in grouped.items():
                try:
                    if table == "input_activity":
                        await self._insert_input_activity(conn, records)
                    elif table == "window_sessions":
                        await self._insert_window_sessions(conn, records)
                    elif table == "mic_activity":
                        await self._insert_mic_activity(conn, records)
                    elif table == "activity_scores":
                        await self._insert_activity_scores(conn, records)
                    elif table == "idle_events":
                        await self._insert_idle_events(conn, records)
                    elif table == "window_session_close":
                        await self._close_window_sessions(conn, records)
                except Exception:
                    log.exception("Failed to flush %s (%d records)", table, len(records))

    async def _insert_input_activity(
        self, conn: asyncpg.Connection, records: list[InputBucket]
    ) -> None:
        await conn.executemany(
            """INSERT INTO input_activity
               (bucket_start, bucket_end, key_press_count, key_rate_per_sec,
                mouse_distance_px, mouse_click_left, mouse_click_right,
                mouse_click_middle, scroll_distance)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)""",
            [
                (
                    r.bucket_start,
                    r.bucket_end,
                    r.key_press_count,
                    r.key_rate_per_sec,
                    r.mouse_distance_px,
                    r.mouse_click_left,
                    r.mouse_click_right,
                    r.mouse_click_middle,
                    r.scroll_distance,
                )
                for r in records
            ],
        )

    async def _insert_window_sessions(
        self, conn: asyncpg.Connection, records: list[WindowSession]
    ) -> None:
        await conn.executemany(
            """INSERT INTO window_sessions
               (started_at, wm_class, window_title, pid)
               VALUES ($1,$2,$3,$4)""",
            [(r.started_at, r.wm_class, r.window_title, r.pid) for r in records],
        )

    async def _close_window_sessions(
        self, conn: asyncpg.Connection, records: list[dict]
    ) -> None:
        for r in records:
            await conn.execute(
                """UPDATE window_sessions SET ended_at = $1
                   WHERE wm_class = $2 AND started_at = $3 AND ended_at IS NULL""",
                r["ended_at"],
                r["wm_class"],
                r["started_at"],
            )

    async def _insert_mic_activity(
        self, conn: asyncpg.Connection, records: list[MicState]
    ) -> None:
        await conn.executemany(
            """INSERT INTO mic_activity
               (is_active, source_node, client_app, client_pid)
               VALUES ($1,$2,$3,$4)""",
            [
                (r.is_active, r.source_node, r.client_app, r.client_pid)
                for r in records
            ],
        )

    async def _insert_activity_scores(
        self, conn: asyncpg.Connection, records: list[ActivityScore]
    ) -> None:
        await conn.executemany(
            """INSERT INTO activity_scores
               (window_start, window_end, wm_class, window_title, score, score_label,
                key_presses, mouse_distance, clicks, scroll, mic_active)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
            [
                (
                    r.window_start,
                    r.window_end,
                    r.wm_class,
                    r.window_title,
                    r.score,
                    r.score_label,
                    r.key_presses,
                    r.mouse_distance,
                    r.clicks,
                    r.scroll,
                    r.mic_active,
                )
                for r in records
            ],
        )

    async def _insert_idle_events(
        self, conn: asyncpg.Connection, records: list[IdleEvent]
    ) -> None:
        await conn.executemany(
            """INSERT INTO idle_events (timestamp, idle_ms, is_locked)
               VALUES ($1,$2,$3)""",
            [(r.timestamp, r.idle_ms, r.is_locked) for r in records],
        )
