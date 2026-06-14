from __future__ import annotations

import argparse
import json
from datetime import timedelta

from . import db
from .config import append_club, init_config, load_config
from .server import serve
from .sync import sync_all
from .timeutils import to_db, utc_now


def main() -> None:
    parser = argparse.ArgumentParser(prog="tennis-overview")
    parser.add_argument("--config", help="Path to clubs TOML config")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve_parser = subparsers.add_parser("serve", help="Run the web server")
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--initial-sync", action="store_true")

    sync_parser = subparsers.add_parser("sync", help="Sync all clubs or one club")
    sync_parser.add_argument("club_id", nargs="?")

    events_parser = subparsers.add_parser("events", help="Print upcoming events as JSON")
    events_parser.add_argument("--days", type=int, default=45)
    events_parser.add_argument("--include-cancelled", action="store_true")

    subparsers.add_parser("init-config", help="Create config/clubs.toml from the example")

    add_parser = subparsers.add_parser("add-club", help="Append a club to the TOML config")
    add_parser.add_argument("--id", required=True)
    add_parser.add_argument("--name", required=True)
    add_parser.add_argument("--provider", required=True)
    add_parser.add_argument("--base-url", required=True)
    add_parser.add_argument("--login-url")
    add_parser.add_argument("--username-env", required=True)
    add_parser.add_argument("--password-env", required=True)
    add_parser.add_argument("--timezone", default="America/Los_Angeles")

    args = parser.parse_args()

    if args.command == "serve":
        serve(args.host, args.port, args.config, run_initial_sync=args.initial_sync)
    elif args.command == "sync":
        config = load_config(args.config)
        results = sync_all(config, only_club_id=args.club_id)
        print(json.dumps([result.__dict__ for result in results], indent=2))
    elif args.command == "events":
        config = load_config(args.config)
        conn = db.connect(config.app.database_path)
        try:
            events = db.list_events(
                conn,
                utc_now() - timedelta(hours=6),
                utc_now() + timedelta(days=args.days),
                include_cancelled=args.include_cancelled,
            )
        finally:
            conn.close()
        print(
            json.dumps(
                [
                    {
                        "club_id": event.club_id,
                        "title": event.title,
                        "starts_at": to_db(event.starts_at, event.timezone),
                        "ends_at": to_db(event.ends_at, event.timezone),
                        "location": event.location,
                        "instructor": event.raw.get("instructor"),
                        "status": event.status,
                    }
                    for event in events
                ],
                indent=2,
            )
        )
    elif args.command == "init-config":
        print(init_config(args.config))
    elif args.command == "add-club":
        path = append_club(
            config_path=args.config,
            club_id=args.id,
            name=args.name,
            provider=args.provider,
            base_url=args.base_url,
            login_url=args.login_url,
            username_env=args.username_env,
            password_env=args.password_env,
            timezone=args.timezone,
        )
        print(path)


if __name__ == "__main__":
    main()
