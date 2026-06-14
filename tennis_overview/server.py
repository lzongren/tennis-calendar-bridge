from __future__ import annotations

import calendar as calendar_module
import json
import struct
import threading
import time
import zlib
from datetime import date, timedelta
from functools import cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from . import db
from .calendar import make_ics
from .config import load_config
from .models import Config, TennisEvent
from .sync import sync_all
from .timeutils import to_db, utc_now

PWA_THEME_COLOR = "#0e7c66"
PWA_BACKGROUND_COLOR = "#f7f8f5"


class TennisServer(ThreadingHTTPServer):
    def __init__(self, server_address: tuple[str, int], config: Config):
        self.config = config
        db.connect(config.app.database_path).close()
        super().__init__(server_address, TennisRequestHandler)


class TennisRequestHandler(BaseHTTPRequestHandler):
    server: TennisServer

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/healthz":
            self._send_json({"ok": True}, send_body=False)
        elif parsed.path == "/":
            self._send("", "text/html; charset=utf-8", send_body=False)
        elif self._handle_pwa_asset(parsed.path, send_body=False):
            pass
        elif parsed.path.startswith("/calendar/") and parsed.path.endswith("/tennis.ics"):
            self._handle_calendar(parsed.path, send_body=False)
        else:
            self._send_text("Not found", HTTPStatus.NOT_FOUND, send_body=False)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._handle_dashboard()
        elif parsed.path == "/healthz":
            self._send_json({"ok": True})
        elif parsed.path == "/api/events":
            self._handle_events(parsed.query)
        elif self._handle_pwa_asset(parsed.path):
            pass
        elif parsed.path.startswith("/calendar/") and parsed.path.endswith("/tennis.ics"):
            self._handle_calendar(parsed.path)
        else:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/sync":
            self._handle_sync(parsed.query)
        else:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _handle_dashboard(self) -> None:
        conn = db.connect(self.server.config.app.database_path)
        try:
            now = utc_now()
            events = db.list_events(conn, now - timedelta(hours=6), now + timedelta(days=45))
            runs = db.list_recent_sync_runs(conn, 12)
        finally:
            conn.close()
        body = _dashboard_html(self.server.config, events, runs)
        self._send(body, "text/html; charset=utf-8")

    def _handle_pwa_asset(self, path: str, send_body: bool = True) -> bool:
        icon_sizes = {
            "/apple-touch-icon.png": 180,
            "/icons/icon-192.png": 192,
            "/icons/icon-512.png": 512,
        }
        if path == "/manifest.webmanifest":
            self._send(
                _manifest_json(self.server.config),
                "application/manifest+json; charset=utf-8",
                send_body=send_body,
                cache_control="no-cache",
            )
            return True
        if path == "/sw.js":
            self._send(
                _service_worker_js(),
                "text/javascript; charset=utf-8",
                send_body=send_body,
                cache_control="no-cache",
            )
            return True
        if path in icon_sizes:
            self._send_bytes(
                _app_icon_png(icon_sizes[path]),
                "image/png",
                send_body=send_body,
                cache_control="public, max-age=604800",
            )
            return True
        return False

    def _handle_events(self, query: str) -> None:
        params = parse_qs(query)
        days = int(params.get("days", ["45"])[0])
        include_cancelled = params.get("include_cancelled", ["false"])[0].lower() == "true"
        conn = db.connect(self.server.config.app.database_path)
        try:
            now = utc_now()
            events = db.list_events(
                conn,
                now - timedelta(hours=6),
                now + timedelta(days=days),
                include_cancelled=include_cancelled,
            )
        finally:
            conn.close()
        self._send_json([_event_json(event) for event in events])

    def _handle_calendar(self, path: str, send_body: bool = True) -> None:
        token = path.removeprefix("/calendar/").removesuffix("/tennis.ics").strip("/")
        expected = self.server.config.app.calendar_token
        if not expected or token != expected:
            self._send_text("Calendar feed not found", HTTPStatus.NOT_FOUND, send_body=send_body)
            return
        conn = db.connect(self.server.config.app.database_path)
        try:
            now = utc_now()
            events = db.list_events(conn, now - timedelta(days=1), now + timedelta(days=180))
        finally:
            conn.close()
        self._send(
            make_ics(events, self.server.config.app.calendar_name, self.server.config.app.timezone),
            "text/calendar; charset=utf-8",
            send_body=send_body,
        )

    def _handle_sync(self, query: str) -> None:
        if not self._is_admin(query):
            self._send_text("Unauthorized", HTTPStatus.UNAUTHORIZED)
            return
        params = parse_qs(query)
        club_id = params.get("club", [None])[0]
        results = sync_all(self.server.config, only_club_id=club_id)
        self._send_json([result.__dict__ for result in results])

    def _is_admin(self, query: str) -> bool:
        expected = self.server.config.app.admin_token
        if not expected:
            return False
        auth = self.headers.get("Authorization", "")
        if auth == f"Bearer {expected}":
            return True
        token = parse_qs(query).get("token", [""])[0]
        return token == expected

    def _send_json(
        self,
        payload: Any,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool = True,
    ) -> None:
        body = json.dumps(payload, indent=2, default=str)
        self._send(body, "application/json; charset=utf-8", status, send_body=send_body)

    def _send_text(
        self,
        body: str,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool = True,
    ) -> None:
        self._send(body, "text/plain; charset=utf-8", status, send_body=send_body)

    def _send(
        self,
        body: str,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool = True,
        cache_control: str = "no-store",
    ) -> None:
        encoded = body.encode("utf-8")
        self._send_bytes(encoded, content_type, status, send_body, cache_control)

    def _send_bytes(
        self,
        body: bytes,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        send_body: bool = True,
        cache_control: str = "no-store",
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache_control)
        self.end_headers()
        if send_body:
            self.wfile.write(body)


class SchedulerThread(threading.Thread):
    def __init__(self, config: Config):
        super().__init__(daemon=True)
        self.config = config
        self.stop_event = threading.Event()

    def run(self) -> None:
        interval = max(5, self.config.app.sync_interval_minutes) * 60
        while not self.stop_event.wait(interval):
            try:
                sync_all(self.config)
            except Exception as exc:
                print(f"scheduled sync failed: {type(exc).__name__}: {exc}")

    def stop(self) -> None:
        self.stop_event.set()


def serve(host: str, port: int, config_path: str | None = None, run_initial_sync: bool = False) -> None:
    config = load_config(config_path)
    if run_initial_sync:
        sync_all(config)
    scheduler = SchedulerThread(config)
    scheduler.start()
    httpd = TennisServer((host, port), config)
    print(f"Serving Tennis Calendar Bridge at http://{host}:{port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop()
        httpd.server_close()


def _event_json(event: TennisEvent) -> dict[str, Any]:
    return {
        "club_id": event.club_id,
        "external_id": event.external_id,
        "title": event.title,
        "starts_at": to_db(event.starts_at, event.timezone),
        "ends_at": to_db(event.ends_at, event.timezone),
        "timezone": event.timezone,
        "location": event.location,
        "category": event.category,
        "source_url": event.source_url,
        "status": event.status,
        "instructor": _event_instructor(event),
    }


def _manifest_json(config: Config) -> str:
    return json.dumps(_manifest_payload(config), indent=2)


def _manifest_payload(config: Config) -> dict[str, Any]:
    app_name = config.app.calendar_name.strip() or "Tennis Calendar"
    short_name = app_name if len(app_name) <= 12 else "Tennis"
    return {
        "name": app_name,
        "short_name": short_name,
        "description": "Private tennis schedule dashboard and calendar feed.",
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "background_color": PWA_BACKGROUND_COLOR,
        "theme_color": PWA_THEME_COLOR,
        "icons": [
            {
                "src": "/icons/icon-192.png",
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": "/icons/icon-512.png",
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }


def _service_worker_js() -> str:
    return """const CACHE_NAME = "tennis-calendar-bridge-v1";
const STATIC_ASSETS = [
  "/manifest.webmanifest",
  "/apple-touch-icon.png",
  "/icons/icon-192.png",
  "/icons/icon-512.png"
];
const OFFLINE_HTML = `<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Tennis Calendar Offline</title>
  <style>
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #f7f8f5;
      color: #17201b;
      font: 16px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }
    main {
      max-width: 420px;
      padding: 24px;
    }
    h1 {
      margin: 0 0 8px;
      font-size: 24px;
    }
    p {
      margin: 0;
      color: #647067;
    }
  </style>
</head>
<body>
  <main>
    <h1>Tennis Calendar</h1>
    <p>You are offline. Reconnect to refresh your private schedule.</p>
  </main>
</body>
</html>`;

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") {
    return;
  }
  const url = new URL(event.request.url);
  if (url.origin !== location.origin) {
    return;
  }
  if (event.request.mode === "navigate") {
    event.respondWith(fetch(event.request).catch(() => new Response(OFFLINE_HTML, {
      headers: { "Content-Type": "text/html; charset=utf-8" }
    })));
    return;
  }
  if (STATIC_ASSETS.includes(url.pathname)) {
    event.respondWith(caches.match(event.request).then((cached) => cached || fetch(event.request)));
  }
});
"""


@cache
def _app_icon_png(size: int) -> bytes:
    if size <= 0:
        raise ValueError("Icon size must be positive")
    margin = max(10, size // 7)
    line = max(3, size // 48)
    ball_radius = max(10, size // 9)
    ball_x = size - margin - ball_radius
    ball_y = margin + ball_radius
    center = size // 2
    rows = bytearray()
    for y in range(size):
        row = bytearray()
        for x in range(size):
            red, green, blue, alpha = 14, 124, 102, 255
            inside_court = margin <= x < size - margin and margin <= y < size - margin
            on_line = inside_court and (
                abs(x - margin) < line
                or abs(x - (size - margin - 1)) < line
                or abs(y - margin) < line
                or abs(y - (size - margin - 1)) < line
                or abs(x - center) < line
                or abs(y - center) < line
            )
            ball_distance = (x - ball_x) ** 2 + (y - ball_y) ** 2
            on_ball = ball_distance <= ball_radius**2
            on_ball_curve = on_ball and abs((x - ball_x) + (y - ball_y)) < line * 2
            if on_line or on_ball_curve:
                red, green, blue = 247, 248, 245
            elif on_ball:
                red, green, blue = 222, 241, 65
            row.extend((red, green, blue, alpha))
        rows.extend(b"\x00" + row)
    return _png_from_rgba(size, size, bytes(rows))


def _png_from_rgba(width: int, height: int, scanlines: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind)
        checksum = zlib.crc32(data, checksum)
        return struct.pack("!I", len(data)) + kind + data + struct.pack("!I", checksum & 0xFFFFFFFF)

    header = struct.pack("!IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines, level=9))
        + chunk(b"IEND", b"")
    )


def _dashboard_html(config: Config, events: list[TennisEvent], runs: list[Any]) -> str:
    calendar_url = ""
    if config.app.public_base_url and config.app.calendar_token:
        base = config.app.public_base_url.rstrip("/")
        calendar_url = f"{base}/calendar/{config.app.calendar_token}/tennis.ics"
    rows = "\n".join(_event_row(event) for event in events) or (
        "<tr><td colspan='5'>No upcoming events are stored yet.</td></tr>"
    )
    calendar_view = _calendar_view(events, config.app.timezone)
    run_rows = "\n".join(_run_row(run) for run in runs) or (
        "<tr><td colspan='5'>No syncs have run yet.</td></tr>"
    )
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="theme-color" content="{PWA_THEME_COLOR}">
  <meta name="mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-capable" content="yes">
  <meta name="apple-mobile-web-app-title" content="{_html(config.app.calendar_name)}">
  <meta name="apple-mobile-web-app-status-bar-style" content="default">
  <link rel="manifest" href="/manifest.webmanifest">
  <link rel="apple-touch-icon" href="/apple-touch-icon.png">
  <link rel="icon" type="image/png" sizes="192x192" href="/icons/icon-192.png">
  <link rel="icon" type="image/png" sizes="512x512" href="/icons/icon-512.png">
  <title>{_html(config.app.calendar_name)}</title>
  <style>
    :root {{
      color-scheme: light dark;
      --bg: #f7f8f5;
      --text: #17201b;
      --muted: #647067;
      --line: #d7ddd5;
      --accent: #0e7c66;
      --panel: #ffffff;
      --warn: #9d5b05;
      --bad: #b42318;
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #101512;
        --text: #eef4ef;
        --muted: #a7b0aa;
        --line: #2d3731;
        --accent: #51c6a8;
        --panel: #151c18;
      }}
    }}
    body {{
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 15px/1.5 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      max-width: 1120px;
      margin: 0 auto;
      padding: 32px 20px 56px;
    }}
    header {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 28px;
    }}
    h1 {{
      margin: 0;
      font-size: 30px;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 30px 0 10px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    .muted {{
      color: var(--muted);
    }}
    .feed {{
      max-width: 520px;
      overflow-wrap: anywhere;
      color: var(--accent);
    }}
    .calendar {{
      display: grid;
      gap: 16px;
    }}
    .calendar-month {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    .calendar-month h3 {{
      margin: 0;
      padding: 12px;
      font-size: 16px;
      border-bottom: 1px solid var(--line);
    }}
    .calendar-weekdays,
    .calendar-days {{
      display: grid;
      grid-template-columns: repeat(7, minmax(0, 1fr));
    }}
    .calendar-weekday {{
      padding: 8px 10px;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: .08em;
      border-bottom: 1px solid var(--line);
    }}
    .calendar-day {{
      min-height: 118px;
      padding: 8px;
      border-right: 1px solid var(--line);
      border-bottom: 1px solid var(--line);
      overflow: hidden;
    }}
    .calendar-day:nth-child(7n) {{
      border-right: 0;
    }}
    .calendar-day.is-outside {{
      background: color-mix(in srgb, var(--muted) 7%, transparent);
      color: var(--muted);
    }}
    .calendar-day.is-today .day-number {{
      color: var(--accent);
      font-weight: 750;
    }}
    .day-number {{
      margin-bottom: 6px;
      font-size: 13px;
      font-weight: 650;
    }}
    .calendar-event {{
      margin-top: 6px;
      padding: 6px 7px;
      border-left: 3px solid var(--accent);
      background: color-mix(in srgb, var(--accent) 10%, var(--panel));
      border-radius: 6px;
      font-size: 12px;
    }}
    .calendar-event-time {{
      color: var(--muted);
      font-weight: 650;
    }}
    .calendar-event-title {{
      margin-top: 2px;
      font-weight: 650;
    }}
    .event-secondary {{
      margin-top: 2px;
      color: var(--muted);
      font-size: 12px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }}
    th, td {{
      padding: 11px 12px;
      border-bottom: 1px solid var(--line);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    tr:last-child td {{
      border-bottom: 0;
    }}
    .status-error {{
      color: var(--bad);
      font-weight: 650;
    }}
    .status-running {{
      color: var(--warn);
      font-weight: 650;
    }}
    .status-success {{
      color: var(--accent);
      font-weight: 650;
    }}
    @media (max-width: 760px) {{
      header {{
        display: block;
      }}
      table {{
        font-size: 13px;
        display: block;
      }}
      thead {{
        display: none;
      }}
      tbody,
      tr,
      td {{
        display: block;
      }}
      tr {{
        padding: 8px 0;
        border-bottom: 1px solid var(--line);
      }}
      tr:last-child {{
        border-bottom: 0;
      }}
      td {{
        display: grid;
        grid-template-columns: minmax(72px, 30%) 1fr;
        gap: 10px;
        padding: 7px 10px;
        border-bottom: 0;
        overflow-wrap: anywhere;
      }}
      td::before {{
        content: attr(data-label);
        color: var(--muted);
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: .08em;
      }}
      td[colspan] {{
        display: block;
      }}
      td[colspan]::before {{
        content: "";
        display: none;
      }}
      .calendar-weekdays {{
        display: none;
      }}
      .calendar-days {{
        display: block;
      }}
      .calendar-day {{
        min-height: 0;
        border-right: 0;
      }}
      .calendar-day.is-outside {{
        display: none;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>{_html(config.app.calendar_name)}</h1>
        <div class="muted">Unified tennis schedule from {len(config.clubs)} configured clubs</div>
      </div>
      <div class="feed">{_html(calendar_url) if calendar_url else "Set TENNIS_PUBLIC_BASE_URL and TENNIS_CALENDAR_TOKEN to show the feed URL."}</div>
    </header>

    <h2>Calendar</h2>
    <div class="calendar">{calendar_view}</div>

    <h2>Upcoming</h2>
    <table>
      <thead><tr><th>When</th><th>Club</th><th>Event</th><th>Location</th><th>Status</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>

    <h2>Recent Syncs</h2>
    <table>
      <thead><tr><th>Started</th><th>Club</th><th>Status</th><th>Events</th><th>Detail</th></tr></thead>
      <tbody>{run_rows}</tbody>
    </table>
  </main>
  <script>
    if ("serviceWorker" in navigator) {{
      window.addEventListener("load", () => {{
        navigator.serviceWorker.register("/sw.js").catch(() => {{}});
      }});
    }}
  </script>
</body>
</html>"""


def _calendar_view(events: list[TennisEvent], timezone_name: str) -> str:
    zone = ZoneInfo(timezone_name)
    today = utc_now().astimezone(zone).date()
    months: set[tuple[int, int]] = {(today.year, today.month)}
    events_by_day: dict[date, list[TennisEvent]] = {}
    for event in events:
        local_day = event.starts_at.astimezone(zone).date()
        months.add((local_day.year, local_day.month))
        events_by_day.setdefault(local_day, []).append(event)
    for day_events in events_by_day.values():
        day_events.sort(key=lambda event: event.starts_at.astimezone(zone))
    return "\n".join(
        _calendar_month(year, month, events_by_day, today, zone)
        for year, month in sorted(months)
    )


def _calendar_month(
    year: int,
    month: int,
    events_by_day: dict[date, list[TennisEvent]],
    today: date,
    zone: ZoneInfo,
) -> str:
    month_date = date(year, month, 1)
    title = month_date.strftime("%B %Y")
    weekdays = "".join(
        f"<div class='calendar-weekday'>{day}</div>"
        for day in ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    )
    days = []
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month):
        for day in week:
            classes = ["calendar-day"]
            if day.month != month:
                classes.append("is-outside")
            if day == today:
                classes.append("is-today")
            day_events = "".join(_calendar_event(event, zone) for event in events_by_day.get(day, []))
            days.append(
                f"<div class='{' '.join(classes)}'>"
                f"<div class='day-number'>{day.day}</div>"
                f"{day_events}"
                "</div>"
            )
    return (
        f"<section class='calendar-month' aria-labelledby='calendar-{year}-{month}'>"
        f"<h3 id='calendar-{year}-{month}'>{_html(title)}</h3>"
        f"<div class='calendar-weekdays'>{weekdays}</div>"
        f"<div class='calendar-days'>{''.join(days)}</div>"
        "</section>"
    )


def _calendar_event(event: TennisEvent, zone: ZoneInfo) -> str:
    start = event.starts_at.astimezone(zone).strftime("%-I:%M %p")
    end = event.ends_at.astimezone(zone).strftime("%-I:%M %p")
    detail = _event_detail(event, include_club=True, include_location=True)
    detail_html = f"<div class='event-secondary'>{_html(detail)}</div>" if detail else ""
    return (
        "<div class='calendar-event'>"
        f"<div class='calendar-event-time'>{_html(start)} - {_html(end)}</div>"
        f"<div class='calendar-event-title'>{_html(event.title)}</div>"
        f"{detail_html}"
        "</div>"
    )


def _event_row(event: TennisEvent) -> str:
    zone = ZoneInfo(event.timezone)
    start = event.starts_at.astimezone(zone).strftime("%a %b %-d, %-I:%M %p")
    end = event.ends_at.astimezone(zone).strftime("%-I:%M %p")
    detail = _event_detail(event)
    event_cell = _html(event.title)
    if detail:
        event_cell += f"<div class='event-secondary'>{_html(detail)}</div>"
    return (
        "<tr>"
        f"<td data-label='When'>{_html(start)} - {_html(end)}</td>"
        f"<td data-label='Club'>{_html(event.club_id)}</td>"
        f"<td data-label='Event'>{event_cell}</td>"
        f"<td data-label='Location'>{_html(event.location or '')}</td>"
        f"<td data-label='Status'>{_html(event.status)}</td>"
        "</tr>"
    )


def _run_row(run: Any) -> str:
    started = run.started_at.astimezone().strftime("%b %-d, %-I:%M %p")
    status_class = f"status-{run.status}"
    detail = run.error or run.artifact_path or ""
    return (
        "<tr>"
        f"<td data-label='Started'>{_html(started)}</td>"
        f"<td data-label='Club'>{_html(run.club_id)}</td>"
        f"<td data-label='Status' class='{status_class}'>{_html(run.status)}</td>"
        f"<td data-label='Events'>{run.events_seen}</td>"
        f"<td data-label='Detail'>{_html(detail)}</td>"
        "</tr>"
    )


def _html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _event_detail(
    event: TennisEvent,
    include_club: bool = False,
    include_location: bool = False,
) -> str:
    parts = [event.club_id] if include_club else []
    instructor = _event_instructor(event)
    if instructor:
        parts.append(f"Instructor: {instructor}")
    elif include_location and event.location:
        parts.append(event.location)
    return " · ".join(parts)


def _event_instructor(event: TennisEvent) -> str | None:
    value = event.raw.get("instructor")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
