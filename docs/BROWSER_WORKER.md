# Private Browser Worker

Prepared by AI (Codex) on 2026-11-02 from the implementation and live setup checks.

The discovery preview can read rendered pages through an isolated browser on a
Mac. The VPS runs the discovery agent; the Mac runs the browser and, separately,
the existing local inference service. No paid search API or club-specific private
API is required. Anonymous web search still sends query terms to search providers.

This is an experimental reader, not a general computer-use agent. The public-page
path has passed a live VPS-to-Mac read. A fresh anonymous browser on the Mac also
read the original clinic's date, time, price and full status. The earlier 403 did
not establish a login requirement. City-wide search recall and the full production
workflow remain release gates; installing the worker does not enable them.

## Boundaries

The request path is VPS agent -> Tailnet HTTPS ingress -> loopback worker ->
public website. Only sanitized course evidence returns to the agent and local
model. Worker requests start in a fresh anonymous context and never load the
dedicated login profile. Anonymous session cookies issued by the website exist
only for that job; they are not the user's login cookies. The internal reader's
optional `allow_authenticated` switch defaults off and is not exposed to the
agent protocol. It permits a profile snapshot only after an explicit login
requirement, never after a 403 or a parser failure. Those snapshots contain
`Local State` and the encrypted cookie database, not the interactive profile itself. Password databases, history,
extensions, and saved tabs are not copied. The parent worker removes the job
directory after completion or subprocess timeout. Public pages use a fresh
context without login state.

The worker listens on `127.0.0.1:8134`, authenticates a dedicated bearer key, and
restricts proxied callers to configured VPS Tailnet addresses. The infrastructure
operator owns the HTTPS route, conventionally port `8444`; the installer never
changes Tailscale Serve. Do not expose CDP, reuse a daily browser profile, or bind
the worker to a public interface.

This deployment trusts the local Tailscale Serve proxy to replace
`X-Forwarded-For` with the actual peer address. Do not substitute a proxy that
passes arbitrary client headers. Direct loopback access still requires the key.
This is an application-level peer check, not a change to Tailnet ACLs.

The protocol accepts bounded read/search requests, not scripts, arbitrary clicks,
shell commands, or credentials. Browser traffic passes through a DNS-pinned
read relay. GET is the default. Two browser-generated anonymous POST queries
are recognized: the public portal home and event-list filters. Exact paths,
bounded strict schemas and an optional language tag are validated; arbitrary
POSTs, unknown fields and these exceptions in logged-in contexts are denied.
The home query goes to the provider's observed public backend with bounded
organization context; cookies and bearer headers are never sent there. The event
list query remains same-origin. A member-only catalog returns
`membership_required`, not a successful empty catalog or an automatic login retry.
The worker still navigates the rendered UI, not a separate club API client.
Private destinations, writes, WebSockets, downloads, and known
action URLs are blocked. CourtReserve document navigation is restricted to
portal, event-list, and event-detail paths. Navigation uses observed read-only
controls; registration, cancellation, and payment controls are never clicked.

These are defense-in-depth application controls, not proof of complete OS-level
containment. A site can misuse GET for mutations. Unknown endpoints must not be
enabled merely to get a page to load. Cookies are forwarded only to CourtReserve.
Response cookies are accepted into the job's isolated context to support anonymous
organization/session initialization. They are discarded at the end of the job.
The relay identifies itself as TennisCalendarBridge rather than forwarding a
Chromium user-agent while using a different HTTP stack. Bounded, same-origin
content-negotiation headers are preserved; bearer credentials are not forwarded.

## Installation and Login

Install the built project wheel into a dedicated macOS virtual environment and
keep `scripts/install_macos_browser.py` alongside it. Google Chrome must already
be installed. Example commands assume the deployment directory below:

```sh
WORKER="$HOME/.local/share/tennis-browser"
"$WORKER/venv/bin/python" "$WORKER/install_macos_browser.py" install \
  --allowed-peer <VPS_TAILNET_IPV4> --allowed-peer <VPS_TAILNET_IPV6>
```

The installer creates LaunchAgent `org.tennis-calendar.browser`, an owner-only
`~/.config/tennis-browser` directory, a separate `profile`, and a dedicated
`api-key` file. It requires a logged-in macOS user. It does not prevent sleep;
sleep or logout makes the worker unavailable.

Login is not a prerequisite for discovery. The remote worker does not enable the
internal reader's optional login fallback. For a separately authorized manual
inspection of a genuinely restricted page, run this on the Mac with its desktop open:

```sh
WORKER="$HOME/.local/share/tennis-browser"
"$WORKER/venv/bin/python" "$WORKER/install_macos_browser.py" login \
  --url https://app.courtreserve.com/Online/Portal/Index/<CLUB_ID>
```

This stops the worker and opens its dedicated Chrome profile. Complete normal
login and any website verification yourself. Choose the site's stay-signed-in
option when available: session-only cookies may not survive closing Chrome.
Authenticated macOS reads use Chrome's native keychain rather than Playwright's
mock keychain so the worker can read cookies persisted by the interactive browser.
Quit that dedicated Chrome instance
before restarting; do not run the worker and interactive browser against the
same profile simultaneously.

```sh
"$WORKER/venv/bin/python" "$WORKER/install_macos_browser.py" start
```

Manual login may not resolve an automated-reader 403. If the protected page still
rejects the worker, leave it unsupported and retain previous discovery results.
Do not bypass the challenge or relax the write restrictions.

The post-login check on 2026-11-02 established that the interactive profile had
13 CourtReserve cookies after normal exit, including nine persistent records.
The VPS worker request failed; a subsequent direct SSH diagnostic sent no Cookie
header, received HTTP 403, and left the database empty. This sequence does not
isolate which launch removed the records. The snapshot boundary fixes the
original-profile mutation risk; it does not establish working authentication.

A synthetic-cookie probe failed when launched directly through SSH, but passed
when bootstrapped in the macOS GUI login domain, including a clean-environment,
new-session subprocess matching the worker's launch pattern. Both headed and
headless GUI-domain launches retained the synthetic cookie. Do not run direct
SSH browser diagnostics against login originals.

After a fresh human login, the protected GUI-domain reader sent a Cookie header
to CourtReserve but still received HTTP 403. The original cookie database hash
was identical before and after the reads. This establishes cookie handoff and
original-profile preservation, not acceptance of the login by the website or a
successful course read. The response did not match the limited challenge-page
markers checked; the specific reason for rejection remains unknown. Do not ask
for repeated login as a remedy for this result. Keep production agent discovery
disabled until the complete discovery acceptance gates pass. A subsequent fresh
anonymous read succeeded after preserving anonymous response cookies, bounded
content-negotiation headers and explicitly guarded redirect hops. Do not treat
the earlier 403 as proof that the site requires login.

## VPS Configuration

Transfer the dedicated key through an approved private channel, never chat or git.
The VPS application's owner-only `.browser.env` contains:

```dotenv
TENNIS_DISCOVERY_BROWSER_URL=https://browser-host.example.ts.net:8444
TENNIS_DISCOVERY_BROWSER_KEY=<DEDICATED_SECRET>
```

`scripts/configure_browser_client.py` accepts the key on standard input, validates
the endpoint, and creates a new owner-only file without overwriting an existing
one. Compose loads this file optionally. Git, Docker build context, and deployment
rsync exclude it. Ansible preserves an existing file unless Vault supplies
`vault_tennis_discovery_browser_key`; when it does, also configure
`tennis_discovery_browser_url`. Rendering is `no_log` with mode `0600`.

Browser configuration alone does not set `TENNIS_DISCOVERY_AGENT_ENABLED`. Keep
that preview flag off until the acceptance gates in
[Agent Development](AGENT_DEVELOPMENT.md) pass. Neither the existing inference
service nor the VPS ingress routes need to change for this worker.

## Verification and Recovery

Run the offline suite with `pytest`, `ruff check .`, and
`mypy tennis_overview benchmarks`. Install CI's Chromium using
`python -m playwright install chromium`; an existing local Chrome can be selected
with `PLAYWRIGHT_CHANNEL=chrome pytest`.

Tests exercise real browser rendering against synthetic responses, sanitized
evidence, isolated anonymous cookies, guarded redirects, validated catalog-query
exceptions, blocked writes/private destinations, explicit login/403 errors, immutable
login originals, private snapshot cleanup and WAL consistency, cancellation,
authenticated HTTP handling, peer restriction, response
limits, and preservation of prior results when the Mac is unavailable.

Live acceptance must separately establish authenticated health from the VPS,
rejection without a key, rejection of a non-VPS peer even with a valid key and
spoofed forwarding header, a public-page read, and a fresh anonymous course-detail read.
Health explicitly reports `authentication: not_verified`; it is not proof of a
usable club session. Model-driven discovery and calendar output must then be
checked against the actual source date, time, price, and availability.

To stop this worker, run the installer with `stop`. Remove only its dedicated
ingress route through the infrastructure owner. Never reset all Tailscale Serve
routes or stop the inference service to roll back this component.

## Appendix: Coverage Limits

Only one browser job runs at a time, with bounded connection count, request size,
network traffic, read deadline, and subprocess lifetime. Failure is explicit and
must not become a successful empty catalog. The client does not fall back to a
cloud model or a second credential source.

CourtReserve list navigation currently selects at most four matching adult
tennis/clinic cards and marks the index partial. Detail extraction reads the
selected occurrence, not every date in a series. This is not complete discovery
or a guarantee that the requested level has been found. Public-page navigation
is broader, but login-dependent providers other than CourtReserve are unsupported.

The live anonymous test reached the original public clinic detail with its known
URL. The same provider's list query explicitly required organization membership.
That is a discovery-coverage limitation, not a reason to require login for every
detail read. The production acceptance gate still includes finding relevant
courses from the user's city/level query, not just replaying a supplied URL.
