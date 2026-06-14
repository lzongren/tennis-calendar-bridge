from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .models import SyncRun, TennisEvent
from .timeutils import from_db, to_db, utc_now


SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id TEXT NOT NULL,
  external_id TEXT NOT NULL,
  title TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  ends_at TEXT NOT NULL,
  timezone TEXT NOT NULL,
  location TEXT,
  category TEXT,
  source_url TEXT,
  status TEXT NOT NULL DEFAULT 'confirmed',
  raw_json TEXT NOT NULL DEFAULT '{}',
  content_hash TEXT NOT NULL,
  first_seen_at TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (club_id, external_id)
);

CREATE INDEX IF NOT EXISTS idx_events_starts_at ON events (starts_at);
CREATE INDEX IF NOT EXISTS idx_events_club_start ON events (club_id, starts_at);

CREATE TABLE IF NOT EXISTS sync_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  club_id TEXT NOT NULL,
  status TEXT NOT NULL,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  events_seen INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  artifact_path TEXT
);

CREATE INDEX IF NOT EXISTS idx_sync_runs_club_started ON sync_runs (club_id, started_at);
"""


def connect(database_path: str) -> sqlite3.Connection:
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def event_external_id(event: TennisEvent) -> str:
    if event.external_id:
        return event.external_id
    raw = "|".join(
        [
            event.club_id,
            event.title,
            to_db(event.starts_at, event.timezone),
            to_db(event.ends_at, event.timezone),
            event.location or "",
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def event_content_hash(event: TennisEvent) -> str:
    payload = {
        "title": event.title,
        "starts_at": to_db(event.starts_at, event.timezone),
        "ends_at": to_db(event.ends_at, event.timezone),
        "timezone": event.timezone,
        "location": event.location,
        "category": event.category,
        "source_url": event.source_url,
        "status": event.status,
        "raw": event.raw,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(encoded.encode("utf-8")).hexdigest()


def upsert_events(conn: sqlite3.Connection, events: Iterable[TennisEvent]) -> list[str]:
    now = to_db(utc_now())
    seen: list[str] = []
    for event in events:
        external_id = event_external_id(event)
        seen.append(external_id)
        content_hash = event_content_hash(event)
        raw_json = json.dumps(event.raw, sort_keys=True, default=str)
        conn.execute(
            """
            INSERT INTO events (
              club_id, external_id, title, starts_at, ends_at, timezone, location,
              category, source_url, status, raw_json, content_hash, first_seen_at,
              last_seen_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(club_id, external_id) DO UPDATE SET
              title = excluded.title,
              starts_at = excluded.starts_at,
              ends_at = excluded.ends_at,
              timezone = excluded.timezone,
              location = excluded.location,
              category = excluded.category,
              source_url = excluded.source_url,
              status = excluded.status,
              raw_json = excluded.raw_json,
              content_hash = excluded.content_hash,
              last_seen_at = excluded.last_seen_at,
              updated_at = CASE
                WHEN events.content_hash != excluded.content_hash THEN excluded.updated_at
                ELSE events.updated_at
              END
            """,
            (
                event.club_id,
                external_id,
                event.title,
                to_db(event.starts_at, event.timezone),
                to_db(event.ends_at, event.timezone),
                event.timezone,
                event.location,
                event.category,
                event.source_url,
                event.status,
                raw_json,
                content_hash,
                now,
                now,
                now,
            ),
        )
    conn.commit()
    return seen


def mark_missing_cancelled(
    conn: sqlite3.Connection, club_id: str, seen_external_ids: list[str], lookahead_days: int
) -> int:
    now = utc_now()
    lower = now - timedelta(days=1)
    upper = now + timedelta(days=lookahead_days)
    params: list[object] = [
        "cancelled",
        to_db(now),
        club_id,
        to_db(lower),
        to_db(upper),
        "confirmed",
    ]
    seen_clause = ""
    if seen_external_ids:
        placeholders = ",".join("?" for _ in seen_external_ids)
        seen_clause = f"AND external_id NOT IN ({placeholders})"
        params.extend(seen_external_ids)
    cursor = conn.execute(
        f"""
        UPDATE events
        SET status = ?, updated_at = ?
        WHERE club_id = ?
          AND starts_at >= ?
          AND starts_at <= ?
          AND status = ?
          {seen_clause}
        """,
        params,
    )
    conn.commit()
    return cursor.rowcount


def list_events(
    conn: sqlite3.Connection,
    start: datetime,
    end: datetime,
    include_cancelled: bool = False,
) -> list[TennisEvent]:
    clauses = ["starts_at >= ?", "starts_at <= ?"]
    params: list[object] = [to_db(start), to_db(end)]
    if not include_cancelled:
        clauses.append("status != 'cancelled'")
    rows = conn.execute(
        f"""
        SELECT * FROM events
        WHERE {" AND ".join(clauses)}
        ORDER BY starts_at ASC, title ASC
        """,
        params,
    ).fetchall()
    return [_event_from_row(row) for row in rows]


def list_recent_sync_runs(conn: sqlite3.Connection, limit: int = 20) -> list[SyncRun]:
    rows = conn.execute(
        """
        SELECT * FROM sync_runs
        ORDER BY started_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [_sync_run_from_row(row) for row in rows]


def start_sync_run(conn: sqlite3.Connection, club_id: str) -> int:
    cursor = conn.execute(
        """
        INSERT INTO sync_runs (club_id, status, started_at)
        VALUES (?, 'running', ?)
        """,
        (club_id, to_db(utc_now())),
    )
    conn.commit()
    return int(cursor.lastrowid)


def finish_sync_run(
    conn: sqlite3.Connection,
    run_id: int,
    status: str,
    events_seen: int,
    error: str | None = None,
    artifact_path: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE sync_runs
        SET status = ?, finished_at = ?, events_seen = ?, error = ?, artifact_path = ?
        WHERE id = ?
        """,
        (status, to_db(utc_now()), events_seen, error, artifact_path, run_id),
    )
    conn.commit()


def _event_from_row(row: sqlite3.Row) -> TennisEvent:
    raw = json.loads(row["raw_json"] or "{}")
    return TennisEvent(
        club_id=row["club_id"],
        external_id=row["external_id"],
        title=row["title"],
        starts_at=from_db(row["starts_at"]),
        ends_at=from_db(row["ends_at"]),
        timezone=row["timezone"],
        location=row["location"],
        category=row["category"],
        source_url=row["source_url"],
        status=row["status"],
        raw=raw,
    )


def _sync_run_from_row(row: sqlite3.Row) -> SyncRun:
    return SyncRun(
        id=row["id"],
        club_id=row["club_id"],
        status=row["status"],
        started_at=from_db(row["started_at"]),
        finished_at=from_db(row["finished_at"]) if row["finished_at"] else None,
        events_seen=row["events_seen"],
        error=row["error"],
        artifact_path=row["artifact_path"],
    )
