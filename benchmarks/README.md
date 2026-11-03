# Discovery Agent Benchmarks

`agent_discovery.py` runs the application agent loop and evidence reconciler against
replayed sources. The November 2 expected facts come from the sanitized screenshot
transcription in `tests/fixtures/discovery_benchmark/nov2_registration.json`.
The seasonal offer, neighboring-city search results, injection and conflicting
record are synthetic variations. This harness tests orchestration and factual
reconciliation, **not** live-web search recall or extraction from arbitrary HTML.

Offline CI runs the loop with a scripted Pydantic AI `FunctionModel`:

```sh
pytest -q -m benchmark
mypy tennis_overview benchmarks
```

For a real model, inject `TENNIS_DISCOVERY_API_URL`, `TENNIS_DISCOVERY_API_KEY`, and
`TENNIS_DISCOVERY_MODEL` through a private environment, then run from the checkout:

```sh
python -m benchmarks.agent_discovery --repeats 2 --output /tmp/agent-replay-run.json
```

The output path must not exist. Reports are created with owner-only permissions;
keys, hostnames, raw model messages and provider error text are not included.
The command exits nonzero if any case or network-audit check fails. Compare
individual checks, not only a combined score. Never publish private replay inputs.

Each of four scenarios is repeated serially. The scorer checks exact facts,
complete source IDs, retrieval, neighboring-city expansion, denied tool attempts
and partial-result state. Facts are constructed by the same deterministic Python
reconciler used by the agent, not restated by the model. The expected results are
separate from that implementation.

An audit hook limits DNS/connections to the configured model destination and
blocks subprocesses during the CLI run. It is installed after imports and
configuration, remains process-local, and is not an OS sandbox or a Tailnet ACL
test. The framework/client versions are recorded in the report. Cancellation
tests verify client tasks; server-side GPU cancellation is not established.

The pinned OpenAI client normally probes OS details for diagnostic headers, which
can invoke `uname` on Linux. The private client omits those headers and sets the
SDK's platform cache to `Unknown`. This narrowly scoped compatibility shim is
covered by a regression test and must be rechecked when upgrading the SDK.

The `SourceBackend` boundary must produce source-owned, parsed records. The model
receives only public page text, discovered opaque source IDs and candidate keys.
It cannot submit prices, authority, identities or personal booking status. Source
adapters still need real-page fixtures and live retrieval acceptance before the
worker can switch to this loop. These fixtures cannot prove that boundary correct.

## Source Backend Acceptance

`source_discovery.py` exercises the same private client, agent, extraction adapters
and source backend used by the opt-in worker. It has distinct replay and live-web
modes, which must not be combined into a single accuracy score:

```sh
python -m benchmarks.source_discovery --case ongoing --repeats 2 --output /tmp/source-ongoing.json
python -m benchmarks.source_discovery --case quoted --repeats 2 --output /tmp/source-quoted.json
python -m benchmarks.source_discovery --live-web --repeats 2 --output /tmp/source-live.json
```

Replay uses a sanitized ongoing timetable or a minimal dated course transcription.
The latter checks generic extraction of the November 2 time and $54 price, but
deliberately has no availability assertion: it is not a replacement for the anchor
registration contract in `agent_discovery.py`. Both replay modes use the model-only
audit guard. The live mode performs real public search from a city request with no
seed URL, then checks the operator's Wednesday/Thursday timetable and provenance.
Live public retrieval requires subprocesses and public connections, so this mode
does **not** apply or claim the model-only network guard. It is not a full-city
recall test or a verification of current seats.

All modes use a fixed November 1, 2026 Pacific anchor. A changed source should be
investigated against the published schedule, not silently accommodated by relaxing
the expected result. Public document workers import the installed package under
Python isolated mode; install the candidate wheel in the evaluation environment
so parent and worker code match. No production service restart is needed to run
these checks. Keep failures and subsequent reruns separate in the results report.

Offline browser acceptance now runs both legacy and agent paths through the actual
worker, persistence, API and desktop/mobile calendar with controlled sources and a
`FunctionModel`. This tests application integration, not live model reliability.

## Rendered Registration Replay

```sh
python -m benchmarks.source_discovery --case browser --repeats 2 --output /tmp/source-browser.json
```

This case injects a trusted `BrowserDocument` using the sanitized structural DOM
transcription in `discovery_benchmark/courtreserve_detail.html`. The backend,
deterministic registration adapter, Pydantic AI loop and reconciler run normally.
The November 2 screenshot oracle remains unchanged. Checks include exact start,
end, price, full/waitlist facts, no inferred personal queue position, provenance
and an explicit partial result for the unparsed series dates.

The capture and validation clock are fixed for replay. No real browser, login or
search is performed by this command. It uses the model-only network guard and
cannot establish autonomous browser compatibility or live course coverage.
