from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennis_overview import db
from tennis_overview.models import TennisEvent


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed a sanitized demo database.")
    parser.add_argument("database_path", help="SQLite database path to create or replace")
    args = parser.parse_args()

    timezone_name = "America/Los_Angeles"
    zone = ZoneInfo(timezone_name)
    today = datetime.now(zone).replace(hour=0, minute=0, second=0, microsecond=0)
    events = _demo_events(today, timezone_name)

    database_path = Path(args.database_path)
    if database_path.exists():
        database_path.unlink()
    conn = db.connect(str(database_path))
    try:
        db.upsert_events(conn, events)
        for club_id, count in (("northside", 2), ("lakeshore", 1), ("city-parks", 1)):
            run_id = db.start_sync_run(conn, club_id)
            db.finish_sync_run(conn, run_id, "success", count)
    finally:
        conn.close()

    print(database_path)


def _demo_events(today: datetime, timezone_name: str) -> list[TennisEvent]:
    next_evening = today + timedelta(days=1, hours=18)
    clinic = today + timedelta(days=3, hours=19, minutes=30)
    reservation = today + timedelta(days=5, hours=8)
    weekend = today + timedelta(days=7, hours=10)
    return [
        TennisEvent(
            club_id="northside",
            external_id="demo-private-lesson",
            title="Private Lesson",
            starts_at=next_evening,
            ends_at=next_evening + timedelta(hours=1),
            timezone=timezone_name,
            location="Court 4",
            category="lesson",
            status="confirmed",
            raw={"instructor": "Avery Chen"},
        ),
        TennisEvent(
            club_id="lakeshore",
            external_id="demo-doubles-clinic",
            title="Doubles Strategy Clinic",
            starts_at=clinic,
            ends_at=clinic + timedelta(minutes=90),
            timezone=timezone_name,
            location="Indoor Court 2",
            category="clinic",
            status="confirmed",
            raw={"instructor": "Mina Patel"},
        ),
        TennisEvent(
            club_id="city-parks",
            external_id="demo-court-reservation",
            title="Court Reservation",
            starts_at=reservation,
            ends_at=reservation + timedelta(hours=2),
            timezone=timezone_name,
            location="Court 7",
            category="reservation",
            status="confirmed",
        ),
        TennisEvent(
            club_id="northside",
            external_id="demo-cardio-tennis",
            title="Cardio Tennis",
            starts_at=weekend,
            ends_at=weekend + timedelta(minutes=75),
            timezone=timezone_name,
            location="Stadium Court",
            category="class",
            status="confirmed",
            raw={"instructor": "Jordan Lee"},
        ),
    ]


if __name__ == "__main__":
    main()
