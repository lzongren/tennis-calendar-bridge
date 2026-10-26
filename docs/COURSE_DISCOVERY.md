# Private Course Discovery

Course discovery is an optional workflow alongside reservation sync. It finds public
club pages for a saved location, extracts course information using a private local
model, and displays source-backed sessions in a four-week calendar. Suggestions never
become reservations or enter the personal iCalendar feed automatically.

## Architecture

```text
Browser -> tennis app on VPS -> public web search and club pages
                            -> HTTPS over Tailscale -> authenticated Rapid-MLX
                            -> validated discovery snapshot in SQLite
```

The model receives location/level preferences and public course text only. No portal
credentials, personal bookings, access codes, calendar tokens, or admin tokens are
included. Web-search providers receive the location query; this is not fully offline
discovery. No cloud AI account or API key is required.

The backend uses `/v1/chat/completions` and an explicit model identifier. It is not
tied to a particular club, city, host, or vendor. Qwen3-Coder in Rapid-MLX is the
initial local deployment; other compatible servers need their own extraction tests.

## Model Host

Install Rapid-MLX and download a compatible MLX model separately. On macOS, this
optional installer creates a dedicated loopback-only service and a random API key:

```sh
python3 scripts/install_macos_inference.py install \
  --executable "$HOME/.rapid-mlx-venv/bin/rapid-mlx" \
  --model /absolute/path/to/downloaded/model --port 8123
```

The service uses the API model name `tennis-discovery`, one active generation,
bounded cache memory, and no cloud fallback. It reads the key from
`~/.config/tennis-inference/api-key` (mode 0600); the key is not in process arguments
or the LaunchAgent plist. The installer does not download models or change routing.
The LaunchAgent runs after user login and restarts on failure. It does not keep a
sleeping Mac awake or run before login. Stop it with:

```sh
launchctl bootout "gui/$(id -u)/org.tennis-calendar.inference"
```

Avoid starting a second large model through a coding-agent runner while this service
is loaded. Shared-memory pressure affects both workloads.

### Private Routing

Routing remains an infrastructure concern, not part of tennis deployments. Inspect
the model host's existing Serve configuration before adding a dedicated port:

```sh
tailscale serve status
tailscale serve --bg --yes --https=8443 http://127.0.0.1:8123
```

Use the resulting **tailnet-only** HTTPS origin as the model endpoint. Do not use
Funnel or bind Rapid-MLX to all interfaces. Keep the existing VPS `/tennis/` route
under `vps-management`; it does not change. Restrict model-host port 8443 to the VPS
and intended administrators through tailnet grants where practical. API-key
authentication is required even inside the tailnet.

Verify that `/v1/models` returns 401 without a key, an authenticated inference call
succeeds from the **tennis container**, and Serve status has no public Funnel entry.
`/health` is a non-secret runtime health endpoint and may be unauthenticated.

## Tennis Configuration

Put these values in `.inference.env` on the VPS (mode 0600):

```dotenv
TENNIS_DISCOVERY_ENABLED=true
TENNIS_DISCOVERY_API_URL=https://model-host.your-tailnet.ts.net:8443/v1
TENNIS_DISCOVERY_MODEL=tennis-discovery
TENNIS_DISCOVERY_API_KEY=your-dedicated-inference-key
```

Use secure secret transfer rather than putting a real key in a shell argument,
chat, screenshot, or Git. Compose 2.24+ loads this optional file after `.env`.
Both deploy paths preserve it. It is excluded from Git, Docker builds, and rsync.
An existing file takes precedence over discovery values in `.env`.

For reproducible Ansible deployment, put the non-secret values in the ignored
`ansible/group_vars/tennis_servers/zz-local.yml`:

```yaml
tennis_discovery_enabled: true
tennis_discovery_api_url: https://model-host.your-tailnet.ts.net:8443/v1
tennis_discovery_model: tennis-discovery
```

Store the matching `vault_tennis_discovery_api_key` in the encrypted Vault. Ansible
renders `.inference.env` with mode 0600 and `no_log` only when that Vault value is
provided. Without it, the existing private inference file is retained. To disable
an existing installation, set `TENNIS_DISCOVERY_ENABLED=false` in `.inference.env`
(or set `tennis_discovery_enabled: false` with the Vault key present) and recreate
the container. Simply omitting the Vault key does not disable an existing setup.

```sh
cd ansible
ansible-playbook -i inventory.yml playbook.yml --ask-vault-pass
```

## Using Discovery

Open **Find courses**, add a city or region, skill level, and IANA time zone. An
administrator token is required to add, refresh, or remove locations. The browser
keeps that token only in memory until the page closes. Reads follow the app's
existing private-network access model; do not publish this app publicly without
adding authentication for all views.

Optionally supply up to four public course URLs. Search often lands on homepages;
the worker follows a bounded number of same-site adult/clinic/schedule links.
Search is best-effort and may be throttled. Seed URLs remain usable if search fails.
The worker checks at most six pages per location, serializes inference, refreshes
saved locations daily, and preserves the prior snapshot when inference fails.
Refresh cooldown is two minutes; at most ten locations may be saved.

## Accuracy Boundaries

- Calendar entries distinguish published dates, ongoing weekly timetables, and
  inferred years. The last two are visibly marked for date confirmation, not bookings.
  An ongoing timetable needs an explicit ongoing-clinics statement plus a weekday
  and unambiguous time. A yearless term requires a unique nearby year whose start
  and end dates match the published weekday and overlap the discovery window.
  Recurrences and time-zone conversion run in Python, not the model.
- Structured course cards retain each term's own weekday, time, date range, and
  registration link. Exclusions or a class-count mismatch remain unresolved rather
  than silently expanding incorrect dates. No inference from copyright years is used.
- Public USTA/ClubSpark course-detail pages have a dedicated reader for their
  explicitly dated sessions; it does not extrapolate dates between sessions.
- Unresolved courses appear under **Unresolved schedules** with a reason. The calendar
  initially opens the first upcoming week containing sessions, not an empty past week.
- Seats and current registration eligibility are not checked. Follow the source
  link to confirm prices, membership requirements, cancellations, and availability.
- Public text extraction does not bypass logins, CAPTCHAs, or access restrictions.
  JavaScript-only schedules, PDFs, and schedules expressed only in images are not
  supported in this initial version.
- Search and model filtering do not guarantee geographic completeness or exact
  driving-distance matching. These are course suggestions, not exhaustive inventory.
- Quoted evidence prevents many invented fields but is not proof that the model
  associated every field correctly. Review source pages before signing up.
- HTML pages are size-limited. Private destinations are rejected, redirects are
  revalidated, and public DNS answers are pinned for fetches to prevent SSRF.
- Discovery lives in a separate `*.discovery.sqlite3` database. Existing club
  adapters, booking records, and private calendar export remain independent.

## Tests

```sh
python -m pytest -q
ruff check .
mypy tennis_overview
```

Install the browser once with `python -m playwright install chromium` before pytest.
For an existing local Chrome installation, `PLAYWRIGHT_CHANNEL=chrome pytest` is also
supported. CI installs Chromium and runs the browser tests, without live model access.

Offline tests use sanitized HTML fixtures and mock inference. They cover source validation,
authentication, subpath handling, recurrence/DST, unsupported evidence, secret
redaction, and outage preservation. Live acceptance also requires an authenticated
model request, a real public course page, desktop/mobile rendering, and a container
request over Tailscale. Browser acceptance tests assert actual calendar cards, exact
times, confidence labels, registration links, week navigation, and DST on desktop
and mobile. A ready API response or a rendered but empty grid is not acceptance.
