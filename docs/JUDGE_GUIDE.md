# Judge Guide

Prerequisites: Git, Python 3.11 or 3.12, and a browser on macOS or Linux. No club
credentials are needed for the synthetic path.

```bash
git clone https://github.com/lzongren/tennis-calendar-bridge.git
cd tennis-calendar-bridge
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
python scripts/seed_demo_data.py /tmp/tennis-calendar-bridge-demo.db

TENNIS_DATABASE_PATH=/tmp/tennis-calendar-bridge-demo.db \
TENNIS_CALENDAR_TOKEN=judge-demo-token \
tennis-overview --config docs/demo/clubs.toml serve --host 127.0.0.1 --port 8097
```

Expected results:

1. <http://127.0.0.1:8097> shows four synthetic upcoming events.
2. <http://127.0.0.1:8097/calendar/judge-demo-token/tennis.ics> is a valid
   calendar containing those events.
3. <http://127.0.0.1:8097/healthz> returns `{"ok": true}`.

## Inspect or run GPT-5.6

Preview the complete outbound request without a key:

```bash
TENNIS_DATABASE_PATH=/tmp/tennis-calendar-bridge-demo.db \
tennis-overview --config docs/demo/clubs.toml brief --preview
```

The request names `gpt-5.6-sol`, contains four synthetic events, and uses only
`Venue 1` through `Venue 4`. The local venue key is not in the API payload.

For a live brief:

```bash
export OPENAI_API_KEY=YOUR_KEY
TENNIS_DATABASE_PATH=/tmp/tennis-calendar-bridge-demo.db \
tennis-overview --config docs/demo/clubs.toml brief
```

The response should contain Week at a glance, Logistics, and Watch-outs. OpenAI
API use may incur charges.

Live provider scraping additionally requires `python -m playwright install
chromium`, ignored config files, and credentials for accounts the user is
authorized to access.
