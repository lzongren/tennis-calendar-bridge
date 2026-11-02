# Benchmark-Driven Course Discovery

Pydantic AI is the selected agent SDK. The current production discovery worker
still uses its original bounded extractor. Implementation proceeds in reviewed
PRs; a passing component benchmark is not permission to label the whole system
production-ready. This document was prepared by AI (Codex) from the implementation
and the user's acceptance requirements on 2026-11-01.

## Acceptance Cases

The November 2 adult clinic is the anchor case: 18:30-19:50 Pacific, NTRP 2.5-3.0,
$54, full, two registrants waitlisted. The screenshot does not disclose a personal
queue position. Its year comes from the dated conversation, not the visible date
label alone. The checked-in fixture is a minimal transcription with a generic
title and synthetic IDs, not authenticated HTML or a claim of current availability.

Additional adversarial variations retain the intent of the framework experiment:
an older seasonal price, equally authoritative date/price conflicts, missing fields,
and cancellation. They are labeled synthetic, not additional observed incidents.
Neither private feed URLs nor session cookies, access codes, model credentials or
Tailnet hostnames belong in fixtures or PR artifacts.

| Layer | Gate | Current scope |
| --- | --- | --- |
| Evidence reconciliation | Exact time, price, status, no invented queue position, complete provenance; conflicts fail closed | `pytest -m benchmark`; real-case transcription plus synthetic counterexamples |
| Agent orchestration | Expand neighboring cities, read linked registration sources, recover transient reads, enforce request/tool/time budgets | Pydantic AI loop and replay harness implemented; see `benchmarks/README.md`; production source integration pending |
| Real-source retrieval | Find the clinic from a city request; read dynamic/PDF detail when needed; retain retrieval evidence | Pending; fixture search is not this gate |
| Existing calendar regressions | Ongoing clinics, term dates, DST, exclusions, source-to-card ownership, mobile rendering | Existing discovery tests remain mandatory |
| Runtime/security | Private model only, credential redaction, no arbitrary URL/file/payment tools, outage preservation, cancellation | Pending integrated worker and private endpoint checks |
| Release | Green CI and code review, live-model benchmark, container/API/UI acceptance, rollback | Pending; a component pass does not satisfy the deployment gate |

## PR Policy

Each PR states which layer it changes, the previous result, the new result, and
what remains untested. Do not change an oracle to make an implementation pass.
Changes to expected facts need source evidence and separate review. Do not merge
regressions in another layer to improve an aggregate score. Compare per-case
outcomes rather than only a combined pass percentage.

The first component PR introduces typed source-owned facts and deterministic
reconciliation. Its acceptance gate requires every component case plus the existing
suite to pass. This does **not** establish source recall or model compatibility.
Subsequent agent PRs must reuse these expected facts against the actual agent output,
in addition to protocol and source-retrieval checks. Production enablement requires
all layers above; there is no fallback from a failed live gate to an offline-only gate.

## Development Checks

```sh
python -m pip install -e '.[dev]'
ruff format --check tennis_overview tests
ruff check tennis_overview tests
mypy tennis_overview benchmarks
pytest -q -m benchmark
pytest -q
```

The evidence module uses strict mypy settings and Pydantic validation. Source facts
are accepted only after an adapter establishes ownership and identity. A model
must not be allowed to label its own output as authoritative registration evidence.
Evidence IDs are derived from the records actually supplied, not model citations.

The agent exposes only bounded city searches and reads of discovered opaque source
IDs. Required detail links must be read before completion. The application derives
results from source records and retains all relevant evidence, even if the model
tries to finish early. It has no shell, filesystem, booking or payment tools.

The private client uses an explicit model and key, Tailscale HTTPS or loopback HTTP,
no environment proxy, no redirects, bounded decoded responses and no SDK transport
retries. Agent instrumentation is explicitly disabled. A `.ts.net` hostname alone
does not prove that Serve/Funnel and Tailnet grants are correctly configured.

Live model results must identify dependency/model configuration, repeat count,
latency, individual failures and retrieval audit. Keep credentials and unredacted
runtime transcripts outside Git; public CI uses no Tailnet credentials. A future
live harness must distinguish prerecorded source replay from actual web retrieval.
