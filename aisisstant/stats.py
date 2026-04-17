"""Print DB record counts as JSON for the GNOME panel indicator."""
from __future__ import annotations

import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone

import asyncpg

from .config import Config

# (table, timestamp_column) pairs the indicator reports on.
_TABLES: list[tuple[str, str]] = [
    ("window_sessions", "started_at"),
    ("input_activity", "bucket_start"),
    ("mic_activity", "detected_at"),
    ("activity_scores", "window_start"),
    ("idle_events", "timestamp"),
]

# Interval label -> timedelta. Order is preserved in output.
_INTERVALS: list[tuple[str, timedelta]] = [
    ("10m", timedelta(minutes=10)),
    ("20m", timedelta(minutes=20)),
    ("30m", timedelta(minutes=30)),
    ("60m", timedelta(minutes=60)),
    ("1d", timedelta(days=1)),
    ("2d", timedelta(days=2)),
    ("3d", timedelta(days=3)),
]


async def _collect() -> dict:
    config = Config()
    try:
        conn = await asyncpg.connect(
            host=config.db_host,
            port=config.db_port,
            database=config.db_name,
            user=config.db_user,
            password=config.db_password,
            timeout=5,
        )
    except (OSError, asyncpg.PostgresError) as e:
        return {"ok": False, "error": str(e)}

    try:
        now = datetime.now(timezone.utc)
        intervals: list[dict] = []
        for label, delta in _INTERVALS:
            cutoff = now - delta
            per_table: dict[str, int] = {}
            total = 0
            for table, ts_col in _TABLES:
                try:
                    n = await conn.fetchval(
                        f"SELECT COUNT(*) FROM {table} WHERE {ts_col} >= $1",
                        cutoff,
                    )
                except asyncpg.PostgresError:
                    n = 0
                n = int(n or 0)
                per_table[table] = n
                total += n
            intervals.append(
                {"label": label, "total": total, "tables": per_table}
            )
        return {"ok": True, "intervals": intervals}
    finally:
        await conn.close()


def main() -> None:
    try:
        result = asyncio.run(_collect())
    except Exception as e:  # pragma: no cover - defensive, keep JSON contract
        result = {"ok": False, "error": str(e)}
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")
    sys.exit(0 if result.get("ok") else 1)


if __name__ == "__main__":
    main()
