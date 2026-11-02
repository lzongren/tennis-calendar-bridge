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
