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
