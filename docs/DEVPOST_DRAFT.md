# Devpost Draft

## Project name and tagline

**Tennis Calendar Bridge** — Every tennis booking, one dependable calendar.

## Recommended category

**Apps for Your Life.** The primary user is an individual player managing
lessons, clinics, and reservations across consumer booking portals. Work and
Productivity is weaker because this is not primarily a team or back-office
workflow. Category remains subject to entrant approval.

## Inspiration

Playing across multiple clubs creates a fragile routine. Each provider has a
different portal and schedule view. I wanted one trustworthy answer to “when am
I playing next?” without granting another service write access to my calendar.

## What it does

The bridge signs in to configured, authorized club portals, normalizes lessons,
clinics, and reservations into a local event store, and publishes a private
dashboard plus tokenized iCalendar feed. An opt-in GPT-5.6 weekly brief
synthesizes seven days into an at-a-glance plan, logistics, and watch-outs.

## How it works

Provider-specific Playwright adapters produce one normalized event model stored
in SQLite. The Python service renders a private dashboard and `.ics` feed. For
the AI brief, a privacy filter creates a minimal request with titles, local
times, categories, statuses, and pseudonymous venue labels. The Responses API
runs `gpt-5.6-sol`; real venue names and sensitive portal fields remain local.

Architecture: club portals → provider adapters → normalized events → local
SQLite → dashboard / iCalendar / privacy filter → GPT-5.6 weekly brief.

## How GPT-5.6 is used

GPT-5.6 has one visible product responsibility: cross-event synthesis. It spots
weekly patterns such as tight gaps, late finishes, and venue changes, then
communicates them in a short plan. Deterministic collection, storage, and export
do not depend on the model. `brief --preview` audits the exact request.

## How Codex was used

In the primary project session, Codex inspected the existing code and official
rules; helped design and implement the privacy boundary and GPT-5.6 Responses
API integration; created redaction and response tests; strengthened CI with
formatting, linting, typing, tests, build, and metadata checks; ran a clean
Python 3.12 install; and prepared judge, release, demo, and submission material.
I made the final privacy, architecture, scope, and disclosure decisions.

## Challenges

Calendar data combines routine facts with sensitive details. The central
challenge was giving GPT-5.6 enough structure to spot weekly patterns without
sending raw pages, credentials, access codes, instructors, source URLs, club
identifiers, or real venue names. Portal variability is handled with explicit
adapters and sanitized parser fixtures.

## Accomplishments

- One private calendar view across three provider families.
- Calendar subscription without calendar write permission.
- Auditable GPT-5.6 request with a tested privacy boundary.
- Synthetic two-minute judge path requiring no real accounts.
- Python 3.11/3.12 CI covering style, types, tests, build, and packaging.

## Lessons learned

AI is most trustworthy here when its role is narrow and its data boundary is
visible. Venue aliases preserve transition patterns without exposing real
locations. The deterministic calendar remains the source of truth; the model
adds explanation, not hidden state.

## Potential impact

The same private-first pattern can reduce scheduling friction for any activity
split across incompatible consumer portals while minimizing calendar
permissions and model data exposure.

## What is next

More adapters from sanitized fixtures, an authenticated dashboard brief,
deterministic conflict checks, dashboard authentication for public deployments,
and debug-artifact retention limits.

## Installation and testing

Use `docs/JUDGE_GUIDE.md`. macOS and Linux with Python 3.11 or 3.12 are
supported. Live scraping additionally requires Playwright Chromium and
authorized portal credentials. The GPT-5.6 brief requires an OpenAI API key;
`brief --preview` does not.

## Links and individual contribution

- Repository: https://github.com/lzongren/tennis-calendar-bridge
- Release: pending `v0.2.0` publication
- Public YouTube demo: pending final approval and upload
- Individual contribution: Zongren Liu designed, implemented, tested,
  documented, and submitted the project, using Codex as described above.
- Country: United States
