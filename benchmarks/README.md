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

The evidence replay request defaults to `source_check` mode. It verifies supplied records,
neighboring-city tool use and required detail closure, not area-search coverage.
The unseeded live `clinic-search` case uses the default `area` mode and must pass
the overview, registration and schedule search gates before reading pages.

Use `--search-mode area` with `benchmarks.agent_discovery` to exercise application
search preflight against the same five replay scenarios. Compare before/after runs
in that same mode; it is not live-web recall. The report records `search_mode`.
Mandatory queries still count toward tool/search limits and audits, but the model
starts from their structured results instead of choosing each fixed query in turn.

Use `--suite navigation --search-mode area` for the registration-navigation
regressions. Other-club candidates and an observed operator signup link reproduce
the early-finish failure shape. Three scenarios cover readable catalog details,
anonymous membership denial and an injected catalog title. A denial must retain
other verified candidates and partial coverage without inventing target facts.
Successful navigation must recover the original course facts and evidence. The
scorer rejects skipped navigation even when another course is already present.
These are controlled transcripts, not proof of live-web discovery; the original
unseeded clinic case remains a separate acceptance gate.

Area planning uses a request-only state projection: the model receives the current
read/refinement choices, bounded candidate summaries and a generic validation-retry
indicator instead of replaying obsolete source IDs and tool exchanges. Eligible
read choices retain discovery order, not opaque-ID sort order. Pydantic AI's
framework transcript, the application's evidence and audit, coverage checks and
budgets remain intact. `source_check` mode keeps its existing conversation path.
The planner is asked for one advertised tool call per turn; this is a prompt, not
a decoder guarantee or a replacement for tool validation.

`tests/test_planner_frontier.py` checks outgoing context against the advertised
read set, source ordering, preserved framework history, bounded summaries, retry
feedback, unchanged request limits and original course facts. The navigation replay
above still verifies source retrieval and membership/injection behavior. A compact
request alone is not proof of improved live-model choices or successful discovery.

At the read limit, area discovery stops offering unread source choices and applies
the existing completion validator without another model request. Validated
evidence can return with partial coverage; missing required details, registration
navigation, search-group coverage or no-results coverage still fail closed. The
limit is not expanded and a successful completion is not an original-course recall
pass. `tests/test_read_budget_completion.py` exercises this boundary with the
original facts, conflict and injection cases, automatic and planner-chosen reads,
and incomplete mandatory/no-results coverage.

Public HTML retrieval preserves bounded inline Schema.org `sameAs` references
for a `SportsActivityLocation` or `LocalBusiness` whose `url` matches the page.
Only the supported inline context/graph shapes are read; remote contexts are never
fetched and scripts are never executed. These are untrusted navigation candidates,
not official-source certification or course evidence. They share the existing
public URL checks, link/depth/read budgets and rank after explicit schedules and
registration links. The original-case replay in `tests/test_facility_references.py`
uses synthetic directory/operator links plus the existing anonymous detail fixture;
it is not proof of finding that detail on the live web.

Area discovery attempts these identity references after mandatory coverage and
before unrelated optional reads or completion, while read capacity remains. When
the whole known reference set fits, the application advances it through the normal
read boundary in discovery order, rechecking higher-priority obligations each time.
Larger sets remain model choices. A failed reference is not retried; references do
not become required course evidence. Exhausted capacity can still return validated
partial results. `tests/test_reference_priority.py` covers these boundaries,
source ownership, shared tool budgets and unchanged focused-source behavior.

`tests/test_source_depth.py` replays the original anonymous course facts through a
directory, operator, registration entry and portal after direct search/refinement
rediscovery of the operator. It verifies shortest-observed-path tracking without
changing the two-hop ordinary-link limit, source identity or cached observations.
Depth truncation remains explicit partial coverage. This controlled graph is not
evidence that a live search returns the same roots or that a catalog admits guests.

Use `--suite priority --search-mode area` to replay the original course evidence with
three extra general-information links competing with its observed registration
route. The suite includes direct registration navigation, a single facility
reference and multiple facility references. The latter two retain both the terse
original link label and a clearer identity label to expose planner sensitivity.
The scorer keeps the original facts and coverage assertions and additionally
rejects optional directory reads before required detail verification. It permits
later optional exploration. This is a synthetic navigation variation around the
real-course transcription, not a new live-web recall result. Run it against both
baseline and candidate code with the same fixtures; do not present a scripted
request-count reduction as a measured real-model speedup.

Replay failures retain a bounded `error_types` chain alongside the outer
`error_type`. It records exception classes, not provider bodies, validation inputs
or exception messages. Keep failed batches when rerunning with diagnostics; a later
successful run does not replace them or establish live-web recall.

Each of five scenarios is repeated serially. The scorer checks exact facts,
complete source IDs, retrieval, neighboring-city expansion, denied tool attempts
and partial-result state. Facts are constructed by the same deterministic Python
reconciler used by the agent, not restated by the model. The expected results are
separate from that implementation.

`navigation_failure` adds an optional operator page whose course extraction failed
but whose observed schedule link remains usable. The original registration facts
and evidence oracle are unchanged. This case must retain an unavailable-source
audit and partial coverage; it cannot pass by relabeling the failed page as checked.
It is a synthetic failure around the real-course transcription, not a live search.
The injection case also puts hostile text in the source title so it reaches the
planner's actual metadata boundary, including area preflight input. The original
course facts are unchanged; page-body text alone no longer reaches the planner.

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
receives discovered opaque source IDs, public navigation metadata and bounded
candidate title/date summaries, not full page bodies or internal session keys.
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

The `extraction-deadline` replay injects a non-completing extractor into an optional
overview page that links to the anonymous November clinic transcription:

```sh
python -m benchmarks.source_discovery --case extraction-deadline --repeats 3 --output /tmp/extraction-deadline.json
```

Only the planner uses the private model in this case; the extractor stalls under a
one-second test tool budget. The source backend must cancel it once, retain only
observed navigation, and keep the overview unavailable. The agent must follow the
linked detail and pass the unchanged anonymous date/time/price/full-status oracle.
This is a focused fault-injection replay with the model-only network guard, not
live web discovery, model-outage recovery, or a production latency measurement.

Modes use a fixed November 1, 2026 Pacific anchor, except `anonymous-series`, which
starts November 3 to exercise a past selected occurrence. A changed source should be
investigated against the published schedule, not silently accommodated by relaxing
the expected result. Public document workers import the installed package under
Python isolated mode; install the candidate wheel in the evaluation environment
so parent and worker code match. No production service restart is needed to run
these checks. Keep failures and subsequent reruns separate in the results report.

Each repetition includes a bounded `stage_metrics` timeline, including failed runs.
It separates planner-model calls, extractor-model calls, source reads and searches.
Times use a monotonic clock; token counts are the SDK usage values on completed
model responses, not an independent tokenizer measurement. Calls without a response
(including cancelled requests) use `null`, not zero, for token counts. Stage
intervals can overlap and must not be added to claim wall time. Only fixed labels,
durations, outcomes and token counts are retained; no URLs, prompts, responses,
credentials or exception messages enter these measurements. At most 128 intervals
are retained and `dropped` reports overflow. This is local benchmark instrumentation,
not cloud tracing, and it changes neither the factual oracle nor runtime budgets.

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
and an explicit partial result for incomplete series enumeration. The original
occurrence is selected by its date before scoring; additional row-owned dates do
not replace or relax its exact factual assertions.

The capture and validation clock are fixed for replay. No real browser, login or
search is performed by this command. It uses the model-only network guard and
cannot establish autonomous browser compatibility or live course coverage.

## Anonymous Read And Discovery Recall

These live cases use the configured private browser worker and local model:

```sh
python -m benchmarks.source_discovery --live-web --case anonymous \
  --source-url "$PUBLIC_DETAIL_URL" --repeats 2 --output /tmp/anonymous-detail.json
python -m benchmarks.source_discovery --live-web --case clinic-search \
  --expected-source-url "$PUBLIC_DETAIL_URL" --repeats 2 --output /tmp/clinic-search.json
```

The first supplies a known detail URL to the agent. The second starts with only
the city, level and date window: no initial source or club URL is supplied to the
agent or search backend. Its expected URL is used only by the scorer to identify
the original club, event and occurrence, ignoring the selected UI tab. It is not
included in the report. Use the publicly accessible event detail, never a personal
calendar feed or credential-bearing URL.

Both check the original date, time, price and full status. The anonymous detail
does not establish a waitlist count; that field must remain unknown. A matching
course name alone cannot pass the search case. A supplied-link pass does not
compensate for a search failure, and successful search results alone do not prove
that the registration detail was found. `clinic-search` rejects replay mode and
preseeded source URLs. Keep baseline failures and reruns separate.

## Anonymous Series

```sh
python -m benchmarks.source_discovery --case anonymous-series \
  --repeats 2 --output /tmp/anonymous-series-replay.json
python -m benchmarks.source_discovery --live-web --case anonymous-series \
  --source-url "$PUBLIC_DETAIL_URL" --repeats 2 --output /tmp/anonymous-series-live.json
```

The November 3 observation supplies a selected November 2 course with visible
later dates. Its 28-day window must contain November 9, 16, 23 and 30 at
18:30-19:50 Pacific, each $54. Only the November 9 row establishes Full; the other
rows leave availability unknown. December 7 is outside this window. Prices, times
and status belong to each row; no queue count or personal position is inferred.
The result remains partial because neither pagination nor year transitions are
fully established. This is a known-source check and cannot satisfy `clinic-search`.

## Source-Derived Refinement

```sh
python -m benchmarks.source_discovery --case source-refinement \
  --repeats 3 --output /tmp/source-refinement-replay.json
```

This controlled case uses the real private planner with synthetic search/read
responses and a fixed empty directory extractor. The directory names an operator
but has no outbound course link. Only `refine_source` reveals its official schedule,
which links to the anonymized original November 2 detail fixture. The check requires
one successful refinement and the unchanged date, time, $54 price, full status,
unknown anonymous queue and evidence checks. No target URL is supplied in the
request; the fixture owns it. Outbound traffic is restricted to the private model.
This measures tool planning and reconciliation, not live web retrieval or city-wide
recall. `--live-web` is deliberately rejected for this replay case.
