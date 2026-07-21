# OpenAI Build Week Disclosure

## Pre-existing project

Tennis Calendar Bridge existed before the July 13, 2026 submission period. The
repository already contained the portal adapters, normalized SQLite event store,
private dashboard, tokenized iCalendar feed, Docker/Ansible deployment,
synthetic screenshots, and original unit tests.

## Meaningful work added during the submission period

1. `tennis-overview brief` uses the OpenAI Responses API with GPT-5.6 to
   synthesize the next week of normalized events into an actionable tennis plan.
2. A privacy filter excludes credentials, access codes, instructors, source
   URLs, raw portal data, club/account identifiers, and real venue names.
   Repeated venues become request-local labels such as `Venue 1`.
3. `brief --preview` lets users inspect the exact request without a key or call.
4. Tests verify the model, request shape, redaction, response parsing, local
   venue key, and non-GPT-5.6 rejection.
5. CI now checks Ruff formatting/lint, mypy typing, pytest, wheel/source builds,
   and Twine metadata on Python 3.11 and 3.12.
6. The repository adds a synthetic judge path, Build Week disclosure, demo plan,
   Devpost draft, and explicit Codex/GPT-5.6 documentation.

## Evidence

- Dated commits and pull request from `codex/build-week-submission`.
- The primary Codex project Session ID entered in Devpost.
- The narrated public demo showing the synthetic product and redacted request.
- CI logs and `v0.2.0` release artifacts.

No claim is made that the pre-existing adapters or dashboard were first built
during Build Week. No live GPT-5.6 call is claimed until captured successfully.
