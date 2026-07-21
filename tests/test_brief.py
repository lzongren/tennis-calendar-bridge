from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from tennis_overview.brief import BriefError, build_request_payload, create_calendar_brief
from tennis_overview.models import TennisEvent


def _event() -> TennisEvent:
    zone = ZoneInfo("America/Los_Angeles")
    start = datetime(2026, 7, 22, 18, 30, tzinfo=zone)
    return TennisEvent(
        club_id="private-club-id",
        external_id="account-booking-123",
        title="Doubles Strategy Clinic",
        starts_at=start,
        ends_at=start + timedelta(minutes=90),
        timezone="America/Los_Angeles",
        location="Indoor Court 2",
        category="clinic",
        source_url="https://portal.example/private/booking/123",
        raw={"instructor": "Private Coach", "access_code": "1234#"},
    )


def test_payload_uses_gpt_5_6_and_redacts_private_fields() -> None:
    payload, venue_key = build_request_payload([_event()], timezone_name="America/Los_Angeles")

    assert payload["model"] == "gpt-5.6-sol"
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["store"] is False
    sent = json.dumps(payload)
    assert "Doubles Strategy Clinic" in sent
    assert "2026-07-22T18:30-07:00" in sent
    assert "Venue 1" in sent
    assert "Indoor Court 2" not in sent
    assert "private-club-id" not in sent
    assert "account-booking-123" not in sent
    assert "Private Coach" not in sent
    assert "1234#" not in sent
    assert "portal.example" not in sent
    assert venue_key == {"Venue 1": "Indoor Court 2"}


def test_create_calendar_brief_parses_response_and_keeps_venue_key_local() -> None:
    captured: dict[str, object] = {}

    def fake_transport(url, headers, payload, timeout):
        captured.update(url=url, headers=headers, payload=payload, timeout=timeout)
        return {
            "id": "resp_demo",
            "model": "gpt-5.6-sol-2026-07-15",
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "## Week at a glance\nOne clinic."}
                    ],
                }
            ],
        }

    result = create_calendar_brief(
        [_event()],
        timezone_name="America/Los_Angeles",
        api_key="test-key",
        transport=fake_transport,
    )

    assert result.response_id == "resp_demo"
    assert result.model == "gpt-5.6-sol-2026-07-15"
    assert result.text.endswith("One clinic.")
    assert result.venue_key == {"Venue 1": "Indoor Court 2"}
    assert "test-key" not in json.dumps(captured["payload"])


def test_brief_rejects_missing_key_and_non_5_6_model(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(BriefError, match="OPENAI_API_KEY"):
        create_calendar_brief([], timezone_name="UTC")
    with pytest.raises(BriefError, match="GPT-5.6"):
        create_calendar_brief([], timezone_name="UTC", api_key="test", model="gpt-4.1")
