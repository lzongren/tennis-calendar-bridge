from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tennis_overview import db
from tennis_overview.calendar import make_ics, parse_ics_events
from tennis_overview.models import TennisEvent


def main() -> None:
    with TemporaryDirectory() as tmp:
        conn = db.connect(str(Path(tmp) / "tennis.db"))
        start = datetime(2026, 6, 15, 18, 0, tzinfo=ZoneInfo("America/Los_Angeles"))
        event = TennisEvent(
            club_id="example-clubautomation",
            title="Private Lesson",
            starts_at=start,
            ends_at=start + timedelta(hours=1),
            timezone="America/Los_Angeles",
            location="Court 4",
            external_id="lesson-1",
            raw={"instructor": "Example Coach"},
        )
        seen = db.upsert_events(conn, [event])
        assert seen == ["lesson-1"]
        events = db.list_events(conn, start - timedelta(days=1), start + timedelta(days=1))
        assert len(events) == 1
        assert events[0].title == "Private Lesson"
        ics = make_ics(events, "Tennis Calendar")
        assert "BEGIN:VCALENDAR" in ics
        assert "Instructor: Example Coach" in ics
        parsed = parse_ics_events(ics, "example-clubautomation", "America/Los_Angeles", "local")
        assert len(parsed) == 1
        assert parsed[0].title == "Private Lesson"
        conn.close()
    print("smoke test passed")


if __name__ == "__main__":
    main()
