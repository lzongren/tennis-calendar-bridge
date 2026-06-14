from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from tennis_overview import db
from tennis_overview.models import TennisEvent


def _event(external_id: str, start: datetime) -> TennisEvent:
    return TennisEvent(
        club_id="example-accusportview",
        external_id=external_id,
        title="Rent/Private",
        starts_at=start,
        ends_at=start + timedelta(hours=2),
        timezone="America/Los_Angeles",
        location="Tennis Court 1",
    )


def test_upsert_list_and_mark_missing_cancelled(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 6, 14, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(db, "utc_now", lambda: now)

    conn = db.connect(str(tmp_path / "tennis.db"))
    zone = ZoneInfo("America/Los_Angeles")
    keep = _event("keep", datetime(2026, 6, 15, 19, 0, tzinfo=zone))
    missing = _event("missing", datetime(2026, 6, 16, 19, 0, tzinfo=zone))
    old = _event("old", datetime(2026, 6, 12, 19, 0, tzinfo=zone))

    assert db.upsert_events(conn, [keep, missing, old]) == ["keep", "missing", "old"]

    cancelled_count = db.mark_missing_cancelled(
        conn,
        club_id="example-accusportview",
        seen_external_ids=["keep"],
        lookahead_days=7,
    )

    assert cancelled_count == 1
    events = db.list_events(
        conn,
        datetime(2026, 6, 11, tzinfo=zone),
        datetime(2026, 6, 20, tzinfo=zone),
        include_cancelled=True,
    )
    statuses = {event.external_id: event.status for event in events}
    assert statuses == {
        "old": "confirmed",
        "keep": "confirmed",
        "missing": "cancelled",
    }
    visible = db.list_events(
        conn,
        datetime(2026, 6, 11, tzinfo=zone),
        datetime(2026, 6, 20, tzinfo=zone),
    )
    assert [event.external_id for event in visible] == ["old", "keep"]
    conn.close()
