# Tennis Calendar Bridge

Tennis Calendar Bridge is a private-first schedule aggregator for tennis clubs,
training centers, and court-booking portals. It logs in to configured provider
sites, normalizes reservations and lessons into one SQLite-backed event store,
and publishes a private iCalendar feed that can be subscribed to from Apple
Calendar, Google Calendar, or any calendar client that supports `.ics` feeds.

The project started from a real multi-club scheduling workflow, but the design is
not tied to one city or one club. New providers can be added as scraper adapters,
and deployment is intentionally boring: a small Python service, Docker Compose,
and optional Ansible support for a VPS.

For OpenAI Build Week, the bridge adds an opt-in GPT-5.6 weekly brief. It turns
the next seven days of normalized tennis events into a concise plan while
keeping credentials, access codes, raw portal data, real venue names, source
URLs, instructors, and account identifiers out of the model request.

## Two-Minute Judge Path

This path uses only synthetic data and does not need club credentials or a
browser download:

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

Open <http://127.0.0.1:8097>, then inspect the synthetic feed at
<http://127.0.0.1:8097/calendar/judge-demo-token/tennis.ics>.

In a second terminal, inspect the exact privacy-redacted GPT-5.6 request without
making an API call:

```bash
TENNIS_DATABASE_PATH=/tmp/tennis-calendar-bridge-demo.db \
tennis-overview --config docs/demo/clubs.toml brief --preview
```

To run the live AI feature, set `OPENAI_API_KEY` and remove `--preview`. See
[the judge guide](docs/JUDGE_GUIDE.md) for expected results.

## Why This Exists

Club portals are optimized for booking inside one facility. Players who train or
reserve courts across multiple clubs often end up checking several websites to
answer a simple question: "When am I playing next?"

This service gives those bookings a single calendar-shaped interface without
asking for write access to a personal calendar account.

## Features

- Playwright-based login and scraping for provider portals.
- Provider adapters for Club Automation, AccuSportView, and CourtReserve-style
  portals.
- Normalized event storage in SQLite.
- Private iCalendar feed at `/calendar/<token>/tennis.ics`.
- Small web dashboard with next-session, agenda, calendar, and sync views.
- Home Screen/PWA metadata for a lightweight mobile-app experience.
- Periodic background sync while the server is running.
- Opt-in GPT-5.6 weekly brief with a previewable, privacy-redacted request.
- Docker Compose deployment with optional Ansible Vault support.
- Pytest coverage for parser regressions, sync orchestration, storage, ICS
  output, and dashboard rendering.

## Screenshots

These screenshots use synthetic demo data from `docs/demo/clubs.toml`; no real
clubs, credentials, tokens, hosts, or schedules are shown.

![Mobile dashboard with next session and agenda](docs/assets/dashboard-mobile.jpg)

![Desktop dashboard with next session, calendar actions, and agenda](docs/assets/dashboard-desktop.jpg)

## Architecture

```mermaid
flowchart LR
    A[Club portals] --> B[Provider adapters]
    B --> C[Normalized events]
    C --> D[(Local SQLite)]
    D --> E[Private dashboard]
    D --> F[Tokenized iCalendar feed]
    D --> G[Privacy filter]
    G -->|Titles, times, categories, venue aliases only| H[GPT-5.6 Responses API]
    H --> I[Weekly tennis brief]
```

The service is deliberately small. It favors explicit provider adapters and
sample-driven parser tests over a broad, fragile "scrape anything" approach.

## Design Choices

- **Calendar subscription over calendar write access.** The app publishes an
  `.ics` feed instead of asking for permission to mutate a personal calendar.
- **Provider adapters over opaque scraping heuristics.** Each portal family gets
  testable parsing logic that can be tightened from sanitized fixtures.
- **SQLite first.** A single-file database is enough for a personal service and
  keeps deployment simple.
- **Private by default.** The dashboard is intended to sit behind a private
  network or authenticated proxy; the public surface should be the calendar feed
  only when needed.
- **AI is explicit and inspectable.** `brief --preview` prints the exact model
  payload, and real venue names remain only in a local alias key.

## GPT-5.6 Product Role

`tennis-overview brief` uses the OpenAI Responses API with `gpt-5.6-sol` and
low reasoning effort. It synthesizes a week of cross-provider events into an
at-a-glance plan, logistics, and watch-outs such as tight gaps, late finishes,
and venue changes. Deterministic scraping, storage, and calendar export continue
to work without an API key.

The model receives only event title, local start/end time, category, status, and
pseudonymous labels such as `Venue 1`. The response is not written back to a
personal calendar. Run `brief --preview` to audit the complete request first.

## How Codex Was Used

Codex was the implementation and verification partner for the Build Week
extension. In the primary project session it inspected the architecture and
official rules, implemented the privacy boundary and GPT-5.6 Responses API
integration, added redaction and response tests, expanded CI, exercised a clean
Python 3.12 install, and prepared judge/release/submission documentation.

Human decisions remained explicit: preserve the no-calendar-write architecture,
keep model use opt-in, send venue aliases rather than real names, use synthetic
demo data, and avoid claims not supported by a live test. The primary Codex
Session ID is supplied directly in the Devpost form.

## Build Week Scope

This repository predates the July 13, 2026 submission period. Judges should
evaluate the meaningful extension documented in
[BUILD_WEEK.md](docs/BUILD_WEEK.md): the GPT-5.6 brief and privacy filter, tests,
judge workflow, stronger CI/package validation, and submission documentation.

## Privacy And Security

This app handles club credentials and personal schedule data. Treat it as a
private service unless you add authentication in front of the dashboard.

- Do not commit `.env`, real `config/clubs.toml`, Ansible Vault files, debug
  artifacts, or SQLite data.
- Keep the dashboard behind a private network, VPN, Tailscale Serve, or an
  authenticated reverse proxy.
- The calendar URL contains a bearer-style token in the path. Anyone with that
  URL can read the calendar feed.
- If you expose the feed publicly for Google Calendar subscriptions, use a
  long random token and avoid exposing the unauthenticated dashboard.
- Failed scraper runs may save screenshots and HTML under `data/debug/`; those
  artifacts can contain personal information from club portals.

This repository includes placeholder config only. Real deployment-specific
values should live in ignored files or Ansible Vault.

## Supported Adapter Examples

The example config includes starting adapters for:

- Club Automation portals
- AccuSportView portals
- CourtReserve portals

These are examples, not endorsements or official integrations. The project is
not affiliated with any club-management software provider or tennis club.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
cp config/clubs.example.toml config/clubs.toml
cp .env.example .env
```

Edit `.env` and provide:

- `TENNIS_ADMIN_TOKEN`
- `TENNIS_CALENDAR_TOKEN`
- club usernames and passwords

Then run:

```bash
set -a
. .env
set +a
tennis-overview serve --host 127.0.0.1 --port 8080
```

Open <http://127.0.0.1:8080>.

## Tests

```bash
pytest
```

The unit tests use local sample data only. They do not log in to tennis-club
websites or require real credentials.

GitHub Actions runs formatting, lint, type checking, tests, package builds, and
distribution metadata checks on Python 3.11 and 3.12.

```bash
ruff format --check .
ruff check .
mypy tennis_overview
pytest
python -m build
twine check dist/*
```

## Calendar Setup

Subscribe to:

```text
https://YOUR_DOMAIN/calendar/YOUR_TENNIS_CALENDAR_TOKEN/tennis.ics
```

For local testing:

```text
http://127.0.0.1:8080/calendar/YOUR_TENNIS_CALENDAR_TOKEN/tennis.ics
```

If the app is mounted under a subpath, set `TENNIS_BASE_PATH` and include the
same path in the public base URL:

```bash
TENNIS_BASE_PATH=/tennis
TENNIS_PUBLIC_BASE_URL=https://YOUR_DOMAIN/tennis
```

The external feed URL then becomes:

```text
https://YOUR_DOMAIN/tennis/calendar/YOUR_TENNIS_CALENDAR_TOKEN/tennis.ics
```

Apple Calendar can subscribe to private network URLs if the device refreshing
the feed can reach the service. Google Calendar generally fetches subscribed
calendar URLs from Google's servers, so it needs a public HTTPS URL such as
Tailscale Funnel or your own reverse proxy.

## Mobile Home Screen

The dashboard includes a web app manifest, Apple Home Screen metadata, generated
icons, and a small service worker. On iPhone, open the dashboard in Safari and
use Share -> Add to Home Screen.

The service worker intentionally does not cache the dashboard schedule, API
responses, or calendar feed. It caches only static app metadata and icons, then
shows a generic offline page if the private service is unreachable.

## Manual Sync

```bash
tennis-overview sync
tennis-overview sync CLUB_ID
```

Trigger a sync through HTTP:

```bash
curl -X POST \
  -H "Authorization: Bearer $TENNIS_ADMIN_TOKEN" \
  http://127.0.0.1:8080/api/sync
```

## Add A New Club

If the site is one of the known provider families:

```bash
tennis-overview add-club \
  --id my-club \
  --name "My Club" \
  --provider courtreserve \
  --base-url "https://example.com" \
  --username-env MY_CLUB_USERNAME \
  --password-env MY_CLUB_PASSWORD
```

For a new provider, add a scraper in `tennis_overview/scrapers/` and register it
in `tennis_overview/scrapers/__init__.py`. Add parser tests with sanitized HTML
or text fixtures before relying on it in production.

## VPS Deployment

The Docker setup works well on a small VPS.

### Ansible With Vault

Use this path if you want tokens and club passwords stored in Ansible Vault:

```bash
cp ansible/inventory.example.yml ansible/inventory.yml
cp ansible/group_vars/tennis_servers/vault.yml.example \
  ansible/group_vars/tennis_servers/vault.yml
$EDITOR ansible/group_vars/tennis_servers/vault.yml
ansible-vault encrypt ansible/group_vars/tennis_servers/vault.yml
bash scripts/deploy_ansible.sh
```

Optional deployment-specific non-secret overrides can go in:

```bash
cp ansible/group_vars/tennis_servers/zz-local.yml.example \
  ansible/group_vars/tennis_servers/zz-local.yml
```

`zz-local.yml` is ignored by Git and loads after `vars.yml`.

The Ansible defaults bind Docker privately on `127.0.0.1:8081` and configure the
app for `/tennis`. This repository does not manage Tailscale Serve routes; route
ownership should live in `vps-management`. This repo includes a non-secret
handoff example at [deploy/vps-management-handoff.example.json](deploy/vps-management-handoff.example.json).
The corresponding
`vps-management/config/routes.json` entry is:

```json
{
  "name": "tennis",
  "kind": "node-subpath",
  "enabled": true,
  "exposure": "tailnet-only",
  "host": "YOUR_NODE.YOUR_TAILNET.ts.net",
  "path": "/tennis/",
  "target": "http://127.0.0.1:8081",
  "app_base_path_env": "TENNIS_BASE_PATH=/tennis",
  "description": "Tennis app mounted under /tennis/. The tennis repo owns app deployment only and should not manage Tailscale Serve by default."
}
```

See [ansible/README.md](ansible/README.md) for the full flow.

### Shell Script

Prepare local config first:

```bash
cp config/clubs.example.toml config/clubs.toml
cp .env.example .env
```

Edit `.env` with your tokens and club credentials, then deploy:

```bash
bash scripts/deploy_vps.sh
```

Override the SSH target or remote directory when needed:

```bash
SSH_TARGET=my-vps REMOTE_DIR=/home/ubuntu/projects/tennis-calendar-bridge bash scripts/deploy_vps.sh
```

The script runs this on the server:

```bash
docker compose up -d --build
```

## Roadmap

- Harden dashboard authentication for public deployments.
- Add more provider adapters from sanitized fixtures.
- Add debug-artifact retention limits.

## Supported Platforms And Limitations

- Python 3.11 or 3.12 on macOS and Linux; Docker deployment is also provided.
  Playwright Chromium is required only for live scraping.
- Portal markup and login flows can change; adapters may need updates.
- The GPT-5.6 brief requires network access, an OpenAI API key, and applicable
  API charges. It is not medical advice and does not modify calendar events.
- Google Calendar generally needs a publicly reachable HTTPS feed URL, while
  the dashboard should remain private or sit behind authentication.
- The project is not affiliated with the example portal vendors or clubs.

## Release And Submission

- Release artifacts: wheel and source distribution on the `v0.2.0` GitHub
  [release](https://github.com/lzongren/tennis-calendar-bridge/releases/tag/v0.2.0).
- Demo video: [two-minute unlisted YouTube walkthrough](https://youtu.be/VzU0iqLYuHk).
- Devpost project: [OpenAI Build Week submission](https://devpost.com/software/tennis-calendar-bridge).

## License

MIT. See [LICENSE](LICENSE).
