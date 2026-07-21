from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .models import TennisEvent


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_API_URL = "https://api.openai.com/v1/responses"


class BriefError(RuntimeError):
    """Raised when an AI calendar brief cannot be generated safely."""


@dataclass(frozen=True)
class BriefResult:
    text: str
    model: str
    response_id: str | None
    venue_key: dict[str, str]


Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


def create_calendar_brief(
    events: list[TennisEvent],
    *,
    timezone_name: str,
    api_key: str | None = None,
    model: str = DEFAULT_MODEL,
    safety_identifier: str | None = None,
    transport: Transport | None = None,
) -> BriefResult:
    """Create a concise plan from normalized events using GPT-5.6.

    The model receives only titles, local start/end times, categories, and
    pseudonymous venue labels. Raw portal data, credentials, access codes,
    instructor names, URLs, club identifiers, and real venue names are omitted.
    """
    key = api_key or os.getenv("OPENAI_API_KEY")
    if not key:
        raise BriefError("OPENAI_API_KEY is required to generate a calendar brief")
    if not model.startswith("gpt-5.6"):
        raise BriefError("The calendar brief requires a GPT-5.6 family model")

    payload, venue_key = build_request_payload(
        events,
        timezone_name=timezone_name,
        model=model,
        safety_identifier=safety_identifier,
    )
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    response = (transport or _post_json)(
        os.getenv("OPENAI_RESPONSES_URL", DEFAULT_API_URL),
        headers,
        payload,
        60.0,
    )
    text = _response_text(response)
    returned_model = response.get("model")
    return BriefResult(
        text=text,
        model=returned_model if isinstance(returned_model, str) else model,
        response_id=response.get("id") if isinstance(response.get("id"), str) else None,
        venue_key=venue_key,
    )


def build_request_payload(
    events: list[TennisEvent],
    *,
    timezone_name: str,
    model: str = DEFAULT_MODEL,
    safety_identifier: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    sanitized_events, venue_key = _sanitized_events(events, timezone_name)
    request: dict[str, Any] = {
        "model": model,
        "instructions": (
            "Create a short, practical tennis schedule brief using only the supplied "
            "event facts. Do not invent travel times, conflicts, cancellations, health "
            "advice, or missing details. Use three compact headings: Week at a glance, "
            "Logistics, and Watch-outs. Mention the confirmed event count, tight gaps "
            "under 24 hours, late finishes, and venue-label changes when present. If a "
            "section has nothing material, say so briefly. Never request or reveal "
            "credentials, access codes, source URLs, or personal calendar identifiers."
        ),
        "input": json.dumps(
            {
                "timezone": timezone_name,
                "events": sanitized_events,
            },
            separators=(",", ":"),
        ),
        "reasoning": {"effort": "low"},
        "text": {"verbosity": "low"},
        "max_output_tokens": 500,
        "store": False,
    }
    if safety_identifier:
        request["safety_identifier"] = safety_identifier
    return request, venue_key


def _sanitized_events(
    events: list[TennisEvent], timezone_name: str
) -> tuple[list[dict[str, Any]], dict[str, str]]:
    location_to_alias: dict[str, str] = {}
    venue_key: dict[str, str] = {}
    sanitized: list[dict[str, Any]] = []
    for event in events:
        venue_alias: str | None = None
        if event.location:
            venue_alias = location_to_alias.get(event.location)
            if venue_alias is None:
                venue_alias = f"Venue {len(location_to_alias) + 1}"
                location_to_alias[event.location] = venue_alias
                venue_key[venue_alias] = event.location
        sanitized.append(
            {
                "title": event.title,
                "starts_at": _iso_minutes(event.starts_at, timezone_name),
                "ends_at": _iso_minutes(event.ends_at, timezone_name),
                "category": event.category,
                "venue": venue_alias,
                "status": event.status,
            }
        )
    return sanitized, venue_key


def _iso_minutes(value: datetime, timezone_name: str) -> str:
    return value.astimezone(ZoneInfo(timezone_name)).isoformat(timespec="minutes")


def _post_json(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout: float,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise BriefError(f"OpenAI Responses API returned HTTP {exc.code}: {detail}") from exc
    except URLError as exc:
        raise BriefError(f"OpenAI Responses API request failed: {exc.reason}") from exc
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise BriefError("OpenAI Responses API returned invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise BriefError("OpenAI Responses API returned an unexpected response")
    return parsed


def _response_text(response: dict[str, Any]) -> str:
    direct = response.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    for item in response.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if isinstance(content, dict) and content.get("type") == "output_text":
                text = content.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
    error = response.get("error")
    if error:
        raise BriefError(f"OpenAI Responses API error: {error}")
    raise BriefError("OpenAI Responses API response did not contain output text")
