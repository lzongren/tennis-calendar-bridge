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
        path = self._route_path(parsed.path, parsed.query, send_body=False)
        if path is None:
            return
        if path == "/healthz":
            self._send_json({"ok": True}, send_body=False)
        elif path == "/":
            self._send("", "text/html; charset=utf-8", send_body=False)
        elif self._handle_pwa_asset(path, send_body=False):
            pass
        elif path.startswith("/calendar/") and path.endswith("/tennis.ics"):
            self._handle_calendar(path, send_body=False)
        else:
            self._send_text("Not found", HTTPStatus.NOT_FOUND, send_body=False)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = self._route_path(parsed.path, parsed.query)
        if path is None:
            return
        if path == "/":
            self._handle_dashboard()
        elif path == "/healthz":
            self._send_json({"ok": True})
        elif path == "/api/events":
            self._handle_events(parsed.query)
        elif self._handle_pwa_asset(path):
            pass
        elif path.startswith("/calendar/") and path.endswith("/tennis.ics"):
            self._handle_calendar(path)
        else:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = self._route_path(parsed.path, parsed.query)
        if path is None:
            return
        if path == "/api/sync":
            self._handle_sync(parsed.query)
        else:
            self._send_text("Not found", HTTPStatus.NOT_FOUND)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def _route_path(self, path: str, query: str = "", send_body: bool = True) -> str | None:
        base_path = self.server.config.app.base_path
        if base_path and path == base_path:
            suffix = f"?{query}" if query else ""
            self._redirect(f"{base_path}/{suffix}", send_body=send_body)
            return None
        if base_path and path.startswith(f"{base_path}/"):
            return path[len(base_path) :] or "/"
        return path

    def _redirect(self, location: str, send_body: bool = True) -> None:
        self.send_response(HTTPStatus.PERMANENT_REDIRECT)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()

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
                _service_worker_js(self.server.config),
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


def _run_initial_sync(config: Config) -> None:
    try:
        sync_all(config)
    except Exception as exc:
        print(f"initial sync failed: {type(exc).__name__}: {exc}")


def _start_initial_sync(config: Config) -> threading.Thread:
    thread = threading.Thread(target=_run_initial_sync, args=(config,), daemon=True)
    thread.start()
    return thread


def serve(host: str, port: int, config_path: str | None = None, run_initial_sync: bool = False) -> None:
    config = load_config(config_path)
    httpd = TennisServer((host, port), config)
    scheduler = SchedulerThread(config)
    scheduler.start()
    if run_initial_sync:
        _start_initial_sync(config)
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
        "access_code": _event_access_code(event),
    }


def _app_path(config: Config, path: str) -> str:
    if not path.startswith("/"):
        path = f"/{path}"
    if path == "/":
        return f"{config.app.base_path}/" if config.app.base_path else "/"
    return f"{config.app.base_path}{path}" if config.app.base_path else path


def _public_app_base_url(config: Config) -> str:
    if not config.app.public_base_url:
        return ""
    base = config.app.public_base_url.rstrip("/")
    if config.app.base_path:
        public_path = urlparse(base).path.rstrip("/")
        if public_path != config.app.base_path and not public_path.endswith(config.app.base_path):
            base = f"{base}{config.app.base_path}"
    return base


def _manifest_json(config: Config) -> str:
    return json.dumps(_manifest_payload(config), indent=2)


def _manifest_payload(config: Config) -> dict[str, Any]:
    app_name = config.app.calendar_name.strip() or "Tennis Calendar"
    short_name = app_name if len(app_name) <= 12 else "Tennis"
    return {
        "name": app_name,
        "short_name": short_name,
        "description": "Private tennis schedule dashboard and calendar feed.",
        "start_url": _app_path(config, "/"),
        "scope": _app_path(config, "/"),
        "display": "standalone",
        "background_color": PWA_BACKGROUND_COLOR,
        "theme_color": PWA_THEME_COLOR,
        "icons": [
            {
                "src": _app_path(config, "/icons/icon-192.png"),
                "sizes": "192x192",
                "type": "image/png",
                "purpose": "any maskable",
            },
            {
                "src": _app_path(config, "/icons/icon-512.png"),
                "sizes": "512x512",
                "type": "image/png",
                "purpose": "any maskable",
            },
        ],
    }


def _service_worker_js(config: Config) -> str:
    static_assets = [
        _app_path(config, "/manifest.webmanifest"),
        _app_path(config, "/apple-touch-icon.png"),
        _app_path(config, "/icons/icon-192.png"),
        _app_path(config, "/icons/icon-512.png"),
    ]
    return """const CACHE_NAME = "tennis-calendar-bridge-v2";
const STATIC_ASSETS = __STATIC_ASSETS__;
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
""".replace("__STATIC_ASSETS__", json.dumps(static_assets, indent=2))


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
    if config.app.calendar_token:
        base = _public_app_base_url(config)
    else:
        base = ""
    if base and config.app.calendar_token:
        calendar_url = f"{base}/calendar/{config.app.calendar_token}/tennis.ics"
    manifest_path = _html(_app_path(config, "/manifest.webmanifest"))
    apple_icon_path = _html(_app_path(config, "/apple-touch-icon.png"))
    icon_192_path = _html(_app_path(config, "/icons/icon-192.png"))
    icon_512_path = _html(_app_path(config, "/icons/icon-512.png"))
    service_worker_path = json.dumps(_app_path(config, "/sw.js"))
    agenda_items = "\n".join(_agenda_item(event) for event in events) or (
        "<p class='empty-state'>No upcoming events are stored yet.</p>"
    )
    calendar_view = _calendar_view(events, config.app.timezone)
    run_rows = "\n".join(_run_row(run) for run in runs) or (
        "<tr><td colspan='5'>No syncs have run yet.</td></tr>"
    )
    feed_actions = _feed_actions(calendar_url)
    next_session = _next_session_card(events, config.app.timezone)
    sync_summary = _sync_summary(runs)
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
  <link rel="manifest" href="{manifest_path}">
  <link rel="apple-touch-icon" href="{apple_icon_path}">
  <link rel="icon" type="image/png" sizes="192x192" href="{icon_192_path}">
  <link rel="icon" type="image/png" sizes="512x512" href="{icon_512_path}">
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
      padding: calc(24px + env(safe-area-inset-top)) max(20px, env(safe-area-inset-right)) calc(56px + env(safe-area-inset-bottom)) max(20px, env(safe-area-inset-left));
    }}
    .app-header {{
      display: flex;
      align-items: flex-end;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 22px;
    }}
    h1 {{
      margin: 0;
      font-size: clamp(28px, 4vw, 40px);
      line-height: 1.04;
      letter-spacing: 0;
    }}
    h2 {{
      margin: 0 0 12px;
      font-size: 18px;
      letter-spacing: 0;
    }}
    button,
    .button {{
      font: inherit;
    }}
    .muted {{
      color: var(--muted);
    }}
    .app-subtitle {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }}
    .sync-pill {{
      display: inline-flex;
      align-items: center;
      min-height: 32px;
      padding: 0 11px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--muted);
      background: var(--panel);
      font-size: 13px;
      white-space: nowrap;
    }}
    .hero-grid {{
      display: grid;
      grid-template-columns: minmax(0, 1.5fr) minmax(260px, .8fr);
      gap: 16px;
      align-items: stretch;
      margin-bottom: 18px;
    }}
    .next-card,
    .feed-panel {{
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
    }}
    .next-card {{
      display: grid;
      grid-template-columns: minmax(96px, .32fr) minmax(0, 1fr);
      min-height: 150px;
      overflow: hidden;
    }}
    .next-date {{
      display: grid;
      align-content: center;
      gap: 4px;
      padding: 18px;
      color: white;
      background: var(--accent);
    }}
    .next-day {{
      font-size: 38px;
      line-height: 1;
      font-weight: 800;
    }}
    .next-month {{
      font-size: 13px;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .next-weekday {{
      font-size: 13px;
      opacity: .86;
    }}
    .next-body {{
      padding: 18px;
      display: grid;
      align-content: center;
      gap: 8px;
    }}
    .eyebrow {{
      color: var(--muted);
      font-size: 12px;
      font-weight: 750;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .next-title {{
      margin: 0;
      font-size: clamp(22px, 3vw, 30px);
      line-height: 1.16;
      letter-spacing: 0;
    }}
    .next-time {{
      color: var(--accent);
      font-size: 17px;
      font-weight: 750;
    }}
    .next-meta,
    .agenda-meta {{
      color: var(--muted);
    }}
    .feed-panel {{
      padding: 18px;
      display: grid;
      align-content: center;
      gap: 12px;
    }}
    .feed-actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 40px;
      padding: 0 13px;
      border: 1px solid var(--line);
      border-radius: 8px;
      color: var(--text);
      background: var(--panel);
      text-decoration: none;
      cursor: pointer;
    }}
    .button-primary {{
      border-color: var(--accent);
      color: white;
      background: var(--accent);
    }}
    .view-tabs {{
      position: sticky;
      top: 0;
      z-index: 1;
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 4px;
      margin: 0 0 16px;
      padding: 4px;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: color-mix(in srgb, var(--bg) 92%, transparent);
      backdrop-filter: blur(16px);
    }}
    .view-tab {{
      min-height: 40px;
      border: 0;
      border-radius: 7px;
      color: var(--muted);
      background: transparent;
      cursor: pointer;
    }}
    .view-tab.is-active {{
      color: var(--text);
      background: var(--panel);
      box-shadow: 0 1px 8px color-mix(in srgb, var(--text) 8%, transparent);
    }}
    .view[hidden] {{
      display: none !important;
    }}
    .agenda-list {{
      display: grid;
      gap: 10px;
    }}
    .agenda-item {{
      display: grid;
      grid-template-columns: minmax(92px, .22fr) minmax(0, 1fr) auto;
      gap: 14px;
      align-items: center;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
    }}
    .agenda-date {{
      display: grid;
      gap: 2px;
    }}
    .agenda-day {{
      font-size: 20px;
      font-weight: 800;
    }}
    .agenda-weekday,
    .agenda-time {{
      color: var(--muted);
      font-size: 13px;
    }}
    .agenda-title {{
      font-size: 17px;
      font-weight: 750;
    }}
    .status-chip {{
      justify-self: end;
      padding: 5px 9px;
      border-radius: 999px;
      color: var(--accent);
      background: color-mix(in srgb, var(--accent) 12%, transparent);
      font-size: 12px;
      font-weight: 750;
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
    .day-number-full {{
      display: none;
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
    .empty-state {{
      margin: 0;
      padding: 18px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: var(--panel);
      color: var(--muted);
    }}
    @media (max-width: 760px) {{
      main {{
        padding-top: calc(16px + env(safe-area-inset-top));
      }}
      .app-header {{
        display: block;
        margin-bottom: 14px;
      }}
      .app-header h1 {{
        font-size: 30px;
      }}
      .sync-pill {{
        margin-top: 10px;
      }}
      .hero-grid {{
        display: grid;
        grid-template-columns: 1fr;
        gap: 10px;
      }}
      .next-card {{
        grid-template-columns: 82px minmax(0, 1fr);
        min-height: 122px;
      }}
      .next-date {{
        padding: 14px 12px;
      }}
      .next-day {{
        font-size: 32px;
      }}
      .next-body {{
        padding: 14px;
      }}
      .next-title {{
        font-size: 21px;
      }}
      .next-time {{
        font-size: 16px;
      }}
      .feed-panel {{
        padding: 13px;
      }}
      .feed-actions {{
        display: grid;
        grid-template-columns: 1fr 1fr;
      }}
      .button {{
        min-height: 42px;
        padding: 0 10px;
      }}
      .view-tabs {{
        top: env(safe-area-inset-top);
        margin-inline: -2px;
      }}
      .agenda-item {{
        grid-template-columns: 1fr;
        gap: 8px;
        padding: 13px;
      }}
      .agenda-date {{
        grid-template-columns: auto 1fr;
        column-gap: 8px;
        align-items: baseline;
      }}
      .agenda-day {{
        font-size: 18px;
      }}
      .status-chip {{
        justify-self: start;
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
      .calendar-day:not(.has-events):not(.is-today),
      .calendar-day.is-outside:not(.has-events) {{
        display: none;
      }}
      .day-number-short {{
        display: none;
      }}
      .day-number-full {{
        display: inline;
      }}
    }}
  </style>
</head>
<body>
  <main>
    <header class="app-header">
      <div>
        <h1>{_html(config.app.calendar_name)}</h1>
        <div class="app-subtitle">Unified tennis schedule from {len(config.clubs)} configured clubs</div>
      </div>
      <div class="sync-pill">{_html(sync_summary)}</div>
    </header>

    <section class="hero-grid" aria-label="Schedule summary">
      {next_session}
      <aside class="feed-panel" aria-label="Calendar feed">
        <div>
          <div class="eyebrow">Calendar Feed</div>
          <div class="muted">Private subscription link</div>
        </div>
        {feed_actions}
      </aside>
    </section>

    <nav class="view-tabs" aria-label="Schedule views">
      <button class="view-tab is-active" type="button" data-view-tab="agenda" aria-controls="agenda-view" aria-selected="true">Agenda</button>
      <button class="view-tab" type="button" data-view-tab="calendar" aria-controls="calendar-view" aria-selected="false">Calendar</button>
      <button class="view-tab" type="button" data-view-tab="sync" aria-controls="sync-view" aria-selected="false">Sync</button>
    </nav>

    <section id="agenda-view" class="view is-active" data-view-panel="agenda" aria-labelledby="agenda-title">
      <h2 id="agenda-title">Agenda</h2>
      <div class="agenda-list">{agenda_items}</div>
    </section>

    <section id="calendar-view" class="view" data-view-panel="calendar" aria-labelledby="calendar-title" hidden>
      <h2 id="calendar-title">Calendar</h2>
      <div class="calendar">{calendar_view}</div>
    </section>

    <section id="sync-view" class="view" data-view-panel="sync" aria-labelledby="sync-title" hidden>
      <h2 id="sync-title">Recent Syncs</h2>
      <table>
        <thead><tr><th>Started</th><th>Club</th><th>Status</th><th>Events</th><th>Detail</th></tr></thead>
        <tbody>{run_rows}</tbody>
      </table>
    </section>
  </main>
  <script>
    (() => {{
      const tabs = Array.from(document.querySelectorAll("[data-view-tab]"));
      const panels = Array.from(document.querySelectorAll("[data-view-panel]"));
      function setView(name) {{
        tabs.forEach((tab) => {{
          const active = tab.dataset.viewTab === name;
          tab.classList.toggle("is-active", active);
          tab.setAttribute("aria-selected", active ? "true" : "false");
        }});
        panels.forEach((panel) => {{
          panel.hidden = panel.dataset.viewPanel !== name;
          panel.classList.toggle("is-active", panel.dataset.viewPanel === name);
        }});
      }}
      tabs.forEach((tab) => tab.addEventListener("click", () => setView(tab.dataset.viewTab)));
      const initial = window.location.hash.replace("#", "");
      if (tabs.some((tab) => tab.dataset.viewTab === initial)) {{
        setView(initial);
      }}
      document.querySelectorAll("[data-copy-text]").forEach((button) => {{
        button.addEventListener("click", async () => {{
          const original = button.textContent;
          try {{
            await navigator.clipboard.writeText(button.dataset.copyText);
            button.textContent = "Copied";
          }} catch (error) {{
            button.textContent = "Copy failed";
          }}
          window.setTimeout(() => {{
            button.textContent = original;
          }}, 1400);
        }});
      }});
    }})();
    if ("serviceWorker" in navigator) {{
      window.addEventListener("load", () => {{
        navigator.serviceWorker.register({service_worker_path}).catch(() => {{}});
      }});
    }}
  </script>
</body>
</html>"""


def _feed_actions(calendar_url: str) -> str:
    if not calendar_url:
        return "<p class='muted'>Set TENNIS_PUBLIC_BASE_URL and TENNIS_CALENDAR_TOKEN to enable subscription actions.</p>"
    escaped_url = _html(calendar_url)
    subscribe_url = _html(_webcal_url(calendar_url))
    return (
        "<div class='feed-actions'>"
        f"<a class='button button-primary' href='{subscribe_url}'>Subscribe</a>"
        f"<button class='button' type='button' data-copy-text='{escaped_url}'>Copy Link</button>"
        "</div>"
    )


def _webcal_url(calendar_url: str) -> str:
    if calendar_url.startswith("https://"):
        return "webcal://" + calendar_url.removeprefix("https://")
    if calendar_url.startswith("http://"):
        return "webcal://" + calendar_url.removeprefix("http://")
    return calendar_url


def _next_session_card(events: list[TennisEvent], timezone_name: str) -> str:
    if not events:
        return (
            "<article class='next-card'>"
            "<div class='next-date'>"
            "<div class='next-month'>Next</div>"
            "<div class='next-day'>--</div>"
            "<div class='next-weekday'>No events</div>"
            "</div>"
            "<div class='next-body'>"
            "<div class='eyebrow'>Next Session</div>"
            "<h2 class='next-title'>Nothing scheduled</h2>"
            "<div class='next-meta'>Upcoming events will appear here after sync.</div>"
            "</div>"
            "</article>"
        )
    zone = ZoneInfo(timezone_name)
    event = min(events, key=lambda item: item.starts_at)
    start = event.starts_at.astimezone(zone)
    end = event.ends_at.astimezone(zone)
    month = start.strftime("%b")
    day = start.strftime("%-d")
    weekday = start.strftime("%A")
    time_range = f"{start.strftime('%-I:%M %p')} - {end.strftime('%-I:%M %p')}"
    detail = _event_detail(event, include_club=True, include_location=True) or event.club_id
    return (
        "<article class='next-card'>"
        "<div class='next-date'>"
        f"<div class='next-month'>{_html(month)}</div>"
        f"<div class='next-day'>{_html(day)}</div>"
        f"<div class='next-weekday'>{_html(weekday)}</div>"
        "</div>"
        "<div class='next-body'>"
        "<div class='eyebrow'>Next Session</div>"
        f"<h2 class='next-title'>{_html(event.title)}</h2>"
        f"<div class='next-time'>{_html(time_range)}</div>"
        f"<div class='next-meta'>{_html(detail)}</div>"
        "</div>"
        "</article>"
    )


def _agenda_item(event: TennisEvent) -> str:
    zone = ZoneInfo(event.timezone)
    start = event.starts_at.astimezone(zone)
    end = event.ends_at.astimezone(zone)
    day = start.strftime("%b %-d")
    weekday = start.strftime("%A")
    time_range = f"{start.strftime('%-I:%M %p')} - {end.strftime('%-I:%M %p')}"
    detail = _event_detail(event, include_club=True, include_location=True) or event.club_id
    return (
        "<article class='agenda-item'>"
        "<div class='agenda-date'>"
        f"<div class='agenda-day'>{_html(day)}</div>"
        f"<div class='agenda-weekday'>{_html(weekday)}</div>"
        "</div>"
        "<div>"
        f"<div class='agenda-time'>{_html(time_range)}</div>"
        f"<div class='agenda-title'>{_html(event.title)}</div>"
        f"<div class='agenda-meta'>{_html(detail)}</div>"
        "</div>"
        f"<div class='status-chip'>{_html(event.status)}</div>"
        "</article>"
    )


def _sync_summary(runs: list[Any]) -> str:
    if not runs:
        return "No syncs yet"
    latest = runs[0]
    started = latest.started_at.astimezone().strftime("%b %-d, %-I:%M %p")
    return f"Last sync {started} · {latest.status}"


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
            day_event_items = events_by_day.get(day, [])
            if day.month != month:
                classes.append("is-outside")
            if day == today:
                classes.append("is-today")
            if day_event_items:
                classes.append("has-events")
            day_events = "".join(_calendar_event(event, zone) for event in day_event_items)
            full_day = day.strftime("%a %b %-d")
            days.append(
                f"<div class='{' '.join(classes)}'>"
                "<div class='day-number'>"
                f"<span class='day-number-short'>{day.day}</span>"
                f"<span class='day-number-full'>{_html(full_day)}</span>"
                "</div>"
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
    if include_location and event.location:
        parts.append(event.location)
    instructor = _event_instructor(event)
    if instructor:
        parts.append(f"Instructor: {instructor}")
    access_code = _event_access_code(event)
    if access_code:
        parts.append(f"Access code: {access_code}")
    return " · ".join(parts)


def _event_instructor(event: TennisEvent) -> str | None:
    value = event.raw.get("instructor")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _event_access_code(event: TennisEvent) -> str | None:
    value = event.raw.get("access_code")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
