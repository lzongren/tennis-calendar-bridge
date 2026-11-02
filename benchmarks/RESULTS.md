# Pydantic AI Discovery Loop: Replay Acceptance

Measured on 2026-11-01 Pacific, using the configured private Qwen/Rapid-MLX
endpoint from the VPS. Inference was real; search results and source records
were replayed fixtures. Model weights and server revision were not independently
fingerprinted. These are orchestration results, not proof of live course recall.

Versions: Pydantic AI 2.64.0, OpenAI client 3.42.1, HTTPX2 2.16.0, Pydantic 2.14.2.

| Scenario | Repetition 1 | Repetition 2 | All six checks |
| --- | --- | --- | --- |
| November 2 clinic / neighboring city | 3.68 s | 2.97 s | 2/2 |
| Hostile source instructions | 2.99 s | 2.99 s | 2/2 |
| Equally authoritative date conflict | 4.23 s | 4.25 s | 2/2 |
| Transient detail read failure | 2.99 s | 2.99 s | 2/2 |

All eight runs passed exact facts, evidence completeness, required retrieval,
neighbor-city expansion, no denied tool attempts and non-partial completion.
The scorer uses the previously committed real-case transcription. Conflict cases
must have no definite occurrence or availability, and no personal queue position.

The final audit observed one reused model TCP connection, zero unexpected DNS or
connections, and zero subprocess attempts in the guarded interval. An earlier
guarded run passed the factual checks but failed the process audit: the SDK's
platform diagnostic called `uname`. The private client now suppresses platform
probing and those headers. Tests and this fresh run verify the correction; the
audit threshold was not relaxed.

This is not an apples-to-apples score comparison with the earlier framework
experiment. The new model plans retrieval and finishes with a typed completion;
Python derives the report from adapter-owned records. It no longer asks the model
to reproduce facts or enumerate its own citations. The small sample and shared
model server do not support a latency or throughput guarantee.

Offline checks: 16 benchmark tests and 292 total tests, including existing
desktop/mobile browser tests. New cases cover early completion, no-result neighbor
search, arbitrary-source denial, response limits, redirects, retry limits,
deadlines, cancellation, credential handling and the audit guard.

Still required before production enablement: real public-source adapters and
extraction, dynamic/PDF source coverage, integration with the saved-location
worker/UI, live-web acceptance for the reported courses, container validation and
private deployment/rollback. Client cancellation does not establish GPU cancellation;
the Python audit hook is not an OS sandbox and starts after module imports.
# Public Document Retrieval: 2026-11-01

This is a retrieval-layer follow-up, not a new model accuracy result. The historical
clinic case led to an actual source-discovery defect: the operator's large global
menu exhausted the 150-link limit before its main-content PDF and booking links.
Removing navigation before collecting links fixes that defect without increasing
the link budget. Sanitized structural regression tests do not contain personal data.

Live read-only checks used the new reader on macOS and from an isolated evaluation
environment on the Linux deployment host, with the built wheel installed outside
the production service:

| Check | Observed result |
| --- | --- |
| [Operator location page](https://www.lifetimeactivities.com/santa-clara/) | 32 content links; fall clinic PDF and public registration portal retained |
| [Fall clinic PDF](https://www.lifetimeactivities.com/wp-content/uploads/SC-FALL-2026-CSCs.pdf) | 2,811 characters of layout text; Monday clinic row and public registration reference read |
| PDF visual cross-check | Rendered page agrees with the extracted Monday 18:30-19:50 row and $52 seasonal fee |
| Public booking portal | Plain HTML contains only an application shell; dynamic retrieval is still needed |
| Offline acceptance | 18 benchmark cases pass, including the previous 16; full suite 314 passes |
| Packaging/type/lint | Wheel/sdist build and Twine checks, mypy, Ruff and format checks pass locally |

The PDF's seasonal fee differs from the user's dated registration screenshot ($54).
The reader does not resolve this conflict, infer occupancy, or turn a yearless row
into a booked session. No live registration status was obtained in this check.
The full city-search-to-registration benchmark, agent backend integration, dynamic
page access and production UI acceptance remain open. Production was not restarted
or reconfigured; no routing, inference credentials or portal sessions were accessed.

# Source Backend Integration: Development Gate

The opt-in worker now uses actual public search/document workers and typed generic
extraction, rather than requiring a replay backend. The production flag remains off.
This is a development record, not a production-readiness declaration.

The live private-model checks found defects that the earlier scripted-model tests
did not expose. The first generic dated transcription run silently lost required
evidence/time fields and returned no candidates (0/2). Making fields mandatory
changed that into an explicit failure (0/2), but did not itself fix extraction.
Wire inspection showed nested `$defs/$ref` schemas under the private model alias.
Using Pydantic AI's built-in definition inliner then passed the same dated case
twice (7.63s and 4.91s, four requests each) without changing its time/price oracle.
Those measurements preceded the subsequent evidence-span and metadata refinements;
they do not replace a final-candidate replay run.

Real city-search runs exposed two additional issues. The model selected disconnected
evidence lines, and copied an operator name and the requested skill level from
outside the course's evidence. Inclusive line spans now retain intervening text;
core factual errors are returned together in one bounded correction. Unsupported
optional club/venue/level labels are omitted deterministically, not displayed as
facts or used to label a course as matching the requested level. A live read of
the affected public drop-in page subsequently retained its sourced course/price
without the unsupported labels. Dates and registration status remained unknown.

Some later runs stopped before public retrieval because the model changed the
requested city. This was initially suspected to be a search-provider failure;
planner-call inspection disproved that hypothesis. The location allowlist had
correctly blocked the call. One bounded correction is now permitted without
executing that search; a repeated invalid city still aborts, and the denial remains
in the audit. The original orchestration scorer still requires zero denied calls
for a clean pass. Recovery is not relabeled as a clean security run.

Offline verification for this candidate: 353 tests, including the actual opt-in
worker/API/calendar path at desktop and mobile sizes; 21 case-level benchmarks;
Ruff lint/format and mypy pass. New checks cover nested wire schemas, required
fields, bounded evidence spans, intervening cancellations, cross-course metadata,
shared planning/extraction budgets, safe error diagnostics, shutdown cancellation,
snapshot preservation and unchanged legacy search queries. Live-source coverage,
the original dynamic registration case and private deployment gates remain open.

The last repeated live city-search check before the dynamic city-schema correction
passed **1/2**. The successful run took 69.28s with 13 model requests, nine tool calls
and four checked sources. It returned the expected Wednesday/Thursday timetable
and four candidates, but remained partial and recorded one denied city-search
attempt before correction. The failed run stopped after 98.40s, 15 model requests,
24 registered sources and five checked sources; the sanitized report does not
establish its final failure cause. A preceding diagnostic run passed the same
timetable checks in 103.76s, also partial. These runs are not pooled to improve the
acceptance score.

A subsequent original-oracle replay failed 0/8: the model repeatedly expanded the
authorized city name with a state suffix, and exact-match enforcement rejected it.
The checks and original fixture were not changed. The tool now exposes the current
user/adapter-authorized names as a dynamic schema enum while retaining runtime
checks; a unit test verifies that a neighboring city appears only after the backend
supplies it. The fresh original-oracle replay then passed **8/8**, including exact
facts, complete evidence, neighbor-city retrieval, no denied tool calls and
non-partial completion. Case timings were 7.05/2.98s (baseline), 4.02/3.00s
(injection), 4.44/3.61s (date conflict), and 2.98/2.98s (transient read). The
model-only network/process audit also passed. This repairs the orchestration
regression; it does not establish live city-search or dynamic registration recall.
Do not merge or enable the integrated worker based on replay and offline tests alone.

The final generic quoted-page replay after the city-schema change failed **0/2**
(69.24s/five requests and 6.75s/four requests). Both runs registered the fixture
source but failed typed extraction before accepting it. The exact cause still
requires message-level inspection on the sanitized fixture; the earlier 2/2
extraction result must not be presented as the final candidate's result. This is
an additional open merge gate, separate from the now-passing original-oracle replay.

## Extraction And Search Follow-up: 2026-11-02

Message inspection on the sanitized quoted page exposed two independent failures.
First, the model invented a title rather than quoting the source. It now selects a
bounded title-line reference; Python copies the source line. Second, it put the
quoted ending clock time in `date_end_text`. That passed quote membership but made
date expansion silently return no sessions. Date fields now require a calendar-date
reference, and the schema describes the distinction explicitly. A bounded correction
can repair it; persistent failure aborts instead of publishing an empty calendar.
The benchmark also now asserts the exact source title. Date/price expectations and
the original registration oracle remain unchanged.

The final candidate's private-model quoted-page check passed **2/2** (9.97s and
4.80s, four requests each), with the correct title, date, start/end times, $54 price,
complete source evidence, no denied calls and non-partial completion. The original
four scenarios repeated twice passed **8/8** again, including the model-only
network/process audit. Timings were 7.07/3.01s (baseline), 4.01/3.01s (injection),
4.49/3.64s (date conflict), and 3.02/3.01s (transient read).

A live city-search run before the search retry change passed **1/2**: the first run
returned the expected timetable and four candidates in 109.66s, with 19 requests,
13 tools, six checked sources, no denied calls, and partial coverage. The second
failed on the initial search, before extraction. A separate isolated search probe
returned `DDGSException` with the provider's ambiguous no-results response. This
does not establish that there are no local courses or identify the provider's
underlying cause. The worker now sends a redacted temporary-failure exit status for
provider exceptions. Search timeouts use the same existing two-attempt limit, with
a one-second delay. Invalid output is not retried; repeated failures still retain
the old snapshot rather than publish an empty catalog.

Local verification on this candidate: **365 pytest tests**, including desktop/mobile
acceptance, **21 benchmark tests**, Ruff lint/format, mypy and wheel/Twine checks pass.
Tests cover successful and exhausted retries, redacted provider errors, invalid
output, timeout process cleanup, title bounds, and both corrected and persistent
date-field errors. These checks are not proof of live web coverage or production
readiness; the repeated city-search and remaining release gates still apply.

The repeated live city-search check after that change failed **0/2** (4.54s and
3.04s, one model request each, no registered/read sources). A follow-up isolated
HTTP-status probe observed DuckDuckGo returning 202; DDGS 9.16.1 in the evaluation
environment has Bing disabled, so the configured `bing,duckduckgo` list did not
provide two working backends. A separate one-shot Brave probe returned 429 and
was not retried. Neither probe establishes a usable replacement. No alternate
provider or permissive automatic fallback has been added. The PR remains draft:
source-search reliability is an open gate, not a successful empty-city result.
