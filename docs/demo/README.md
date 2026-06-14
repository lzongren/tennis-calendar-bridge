# Demo Data

This directory contains sanitized configuration used for public screenshots and
docs. It is intentionally disconnected from real clubs and credentials.

To regenerate a demo database:

```bash
python3 scripts/seed_demo_data.py /tmp/tennis-calendar-readme-demo.db
```

Then serve the app with the demo config:

```bash
TENNIS_CONFIG=docs/demo/clubs.toml \
TENNIS_DATABASE_PATH=/tmp/tennis-calendar-readme-demo.db \
TENNIS_PUBLIC_BASE_URL=http://127.0.0.1:8097 \
TENNIS_CALENDAR_TOKEN=demo-calendar-token \
python3 -m tennis_overview serve --host 127.0.0.1 --port 8097
```

Public screenshots should always be captured from this demo setup, not from a
real deployment.
