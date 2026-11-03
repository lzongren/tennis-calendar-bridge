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

## Search Backend And Annual Table Follow-up: 2026-11-02

An isolated Mojeek probe returned HTTP 200 but no parsed results. An isolated Yahoo
probe returned HTTP 200 and seven public source references, including the expected
operator. The opt-in worker now selects Yahoo explicitly, leaving legacy behavior
unchanged. A runtime guard rejects a disabled/missing backend before DDGS can choose
its automatic provider fallback. This is not a claim that HTML search is reliable:
the repeated runs below still fail. User-supplied public source URLs remain readable
when search is unavailable; their results are marked partial, not city-wide coverage.

The first repeated run with explicit search passed **1/2**. Its successful run took
102.45s, 16 requests and 11 tools, returning eight candidates from six checked sources,
with no denied calls and partial coverage. The second failed at PDF extraction after
57.31s and 15 requests. The model copied a year and member-only price from a table
header but cited only one course row, then repeated the same error after correction.
The validator correctly refused the unsupported field ownership.

A deterministic adapter now recognizes explicitly year-labelled adult clinic tables
with `DAY TYPE TIME LEVEL PRO` columns. It retains row ownership, separate per-page
pricing tiers, and the published year boundary. Term restrictions and closures keep
the schedule unresolved rather than inventing exceptions. No operator, city, URL or
expected course is hard-coded. A fresh read of the
[public annual PDF](https://lgsrc.com/wp-content/uploads/2026/03/2026-Tennis-Clinics-Schedule.pdf)
with the final installed wheel yielded eight candidates and 32 weekly timetable
occurrences with **zero model requests**. Membership labels remain attached to all
prices; these are not confirmed dates, seats or user eligibility. The generic price
projection now preserves qualified price text instead of dropping it when no single
numeric price can be established.

The following city-search run, before the final term-restriction guard, remained
**1/2**: 95.08s, 17 requests, 12 tools, four candidates and five checked sources in
the successful partial run, with no denied calls. The second failed at its initial
search after 3.77s and one model request, before any source was registered. No more
provider probes were used to chase a passing score. A supported search-API option
has been requested; no paid service was activated or new credential provisioned.

Final local checks: **384 tests**, **23 benchmarks**, Ruff lint/format, mypy and
wheel/Twine checks pass. Sanitized table fixtures test row and page ownership,
year boundaries, changed layouts, restricted terms, qualified prices through the
agent result, and no model calls for recognized tables. The real-city search gate
remains open. Production is unchanged and the PR remains draft.

On the final installed candidate, the original private-model oracle again passed
**8/8** with its network/process audit: baseline 7.05/2.99s, injection 4.01/3.04s,
date conflict 5.30/4.30s, and transient read 3.42/3.49s. The generic quoted-page
case passed **2/2** (9.95/4.79s, four requests each), including exact title, price,
date/time and evidence, with no denied calls and non-partial completion. These
replay results do not resolve the separate live-search failure.

## Browser Registration Evidence: 2026-11-02

The retrieval direction is now general browser access, without a dedicated search
API requirement. The optional API PR was closed, not merged. An authorized
read-only local-browser inspection reached the original course's dynamic detail
page using an existing session. It displayed November 2, 6:30p-7:50p, $54.00 and
`Full - 2 Registrant(s) Waitlisted`. No registration, account or payment action
was performed, and no browser profile or cookies were exported.

The next component accepts a trusted rendered observation through the existing
reader boundary and deterministically parses the selected occurrence. Its fixture
is a sanitized structural transcription of the inspected DOM, not an archived
authenticated page. The original screenshot oracle remains unchanged. Replays
exercise the real source backend and agent loop; they do not exercise autonomous
search, login or browser navigation.

The configured private Qwen/Rapid-MLX endpoint passed **2/2** new browser-evidence
replays before the final series-label guard (3.79/2.14s). After the URL and
unexpanded-series safeguards, a fresh installed wheel passed **2/2** again
(2.83/2.12s). Each run used three model requests, two tool calls, one checked
source and zero denied tool attempts. Exact start/end, $54 price, full status,
two-person waitlist, provenance and no personal queue-position inference passed.
The series remains explicitly partial because other occurrence rows are not yet
parsed. Both reports recorded one model connection and zero blocked connection,
DNS or subprocess attempts under the model-only audit guard.

Local checks on the final candidate: **416 pytest tests**, including **25 benchmark
tests**, strict typing across 40 modules, Ruff lint/format and wheel/Twine checks
pass. Fixtures use synthetic IDs and omit account data. Production is unchanged;
the autonomous browser transport and live city-search acceptance remain open gates.

## Anonymous-First Browser: 2026-11-02

The original clinic detail is readable without login. A fresh browser context on
the private Mac read the November 2 occurrence at 18:30-19:50 Pacific for $54.00,
with status `Full`. The anonymous view did not expose a waitlist count. The new
sanitized fixture keeps that count unknown instead of reusing the signed-in
screenshot's two-person count. No login profile was loaded or exported.

The earlier reader conflated the provider's hostname with authentication. It
also dropped anonymous `Set-Cookie` state and frontend content-negotiation headers.
The replacement starts anonymously, follows bounded validated redirect hops,
identifies its HTTP relay honestly, and keeps anonymous cookies inside one job.
Two strictly validated browser-generated catalog queries are permitted; action
requests and bearer forwarding remain blocked. Authentication cannot be requested
through the agent protocol. A 403 is not treated as a login requirement.

The final installed candidate passed the live known-source Pydantic AI + private
Qwen/Rapid-MLX benchmark **2/2** from the VPS (7.20s and 6.47s). Each run used
three model requests and two tool calls, read one source, produced one occurrence,
and had zero denied model tool attempts. Clinic identity, exact timestamps, $54,
full status, unknown waitlist count, provenance and explicit partial-series status
all passed. This is real retrieval, not a replay, but the detail URL was supplied:
it does not establish autonomous discovery recall.

The sanitized anonymous replay also passed **2/2** (2.84s and 2.16s), including
the clinic-identity assertion. Its model-only audit recorded one model connection
and zero blocked connection, DNS or subprocess attempts. Local checks passed
**509 pytest tests**, typing across 47 modules, Ruff lint/format, wheel build and
Twine metadata checks.

The anonymous directory path is a separate failed acceptance case. After fixing
the browser query transport, the provider explicitly required organization
membership to list its courses. The final VPS-to-Mac check returned HTTP 503 with
`membership_required`, not an empty successful catalog. Public detail access and
catalog access therefore need separate policies. Membership-aware discovery,
city-wide recall and production calendar/UI acceptance remain open. Only the
isolated Mac browser worker was updated; the production tennis service and agent
enable flag were not changed.

## Unseeded Discovery: 2026-11-02

The new `clinic-search` case starts from San Jose, NTRP 3.0 and the fixed date
window. Its expected registration URL is scorer-only, never a seed, tool result
or model input. The scorer matches the club/event/occurrence before checking the
original facts. Replay and supplied source URLs are rejected for this case.

The first three exploratory attempts failed: 3.19s before any source was registered,
then 86.72s and 88.85s after six and five source reads. A separate direct worker
search returned seven public sources. That does not explain or erase the initial
failure. Instrumented retrieval then isolated a repeatable application failure:
exhausted evidence validation on a social-media page aborted the entire run.

| Candidate | Time | Model requests | Read sources | Outcome |
| --- | --- | --- | --- | --- |
| Before source isolation, exact-occurrence scorer | 63.72s | 14 | 5 | Extraction retries exhausted; run aborted |
| After source isolation | 71.56s | 15 | 6 | Four partial candidates; original clinic absent; benchmark failed |

The post-change live run returned an empty extraction for the problematic page,
so its completion alone does not prove that exception isolation caused the
improvement. Deterministic agent tests exercise that branch explicitly: an
invalid optional source cannot discard independent valid evidence; a failed
required source or an all-failed run still retains previous results. Model HTTP
failures and total request-budget exhaustion are not swallowed.

Local verification: **520 tests**, including **29 benchmark cases**, with typing
and lint checks. A separate post-change known-source live check passed **2/2**
(8.55s / 25.39s), retaining the original exact facts. This increment improves
failure isolation and makes discovery recall measurable; it does **not** pass the original city-to-clinic acceptance
gate. The agent can still finish after one search focus once it has some
candidates, leaving relevant operators undiscovered. Search coverage, registration
navigation and production acceptance remain open. No production service was changed.

## Area Coverage And Search Layouts: 2026-11-02

Area discovery now requires overview, registration and schedule searches before
page reads, and an attempted read from each newly introduced source group before
completion. The application and recall harness use the same 48-reference ceiling;
model, read, tool and time budgets are unchanged. Supplied-source and evidence
replays explicitly use `source_check`, retaining their factual oracles without
claiming area coverage. The unseeded anchor case remains in `area` mode.

A live search failure was not actually an empty result set. Yahoo returned HTTP
200 and course links in `section.algo`, while DDGS recognized only `div.relsrch`.
The adapter now handles both observed shapes, uses heading-owned result links,
and retains the existing transport and redirect decoding. The new fixture is a
structural transcription with synthetic links, not a captured search session.
DDGS is pinned to the evaluated 9.16.1 version. An unrecognized result-less layout
was also observed and still fails closed; it has not been classified as a genuine
zero-result response. These observations do not establish search reliability.

| Live area attempt | Time | Sources read | Outcome |
| --- | --- | --- | --- |
| Coverage gate before layout fix | 8.42s | 0 | Second search unavailable |
| Layout compatibility candidate | 56.40s | 5 | All three searches completed; later run failed |
| Same candidate rerun | 4.93s | 0 | Second search unavailable |
| Same candidate with stage diagnostics | 38.03s | 1 | All three searches completed; a browser read failed |

None passed the original clinic-recall gate. The final worker's separate remote
search checks returned 8, 8 and 7 HTTPS links for the three focuses. Its live
known-source check passed **2/2** (8.95s / 6.99s), with the original time, price,
full status and unknown anonymous waitlist count. These component passes do not
replace the failed area runs.

Local validation: **527 tests**, **32 benchmark cases**, typing across **48 modules**,
lint/format and wheel metadata checks. Only the isolated Mac browser test worker
was updated. Production discovery and centrally managed ingress are unchanged.
The remaining acceptance work includes search-state classification, reliable
source navigation, geographic recall and the complete production calendar flow.

## Browser Failure Scope: 2026-11-02

A fresh unseeded baseline completed all three searches, then aborted at a browser
read: **35.95s**, 8 model requests, 30 registered sources and one completed source.
The old worker protocol conflated unreadable pages with process/runtime failures;
the client treated both as a run-level outage.

The worker now emits distinct, fixed codes. Only recognized read-page failures
are source-scoped. Optional failures produce partial coverage; required/all-source
failures, worker faults, authentication errors and unknown legacy replies remain
fatal. No additional login, permissions, retries or budget increases are involved.
Six new benchmark variants combine the real anonymous course transcription with
synthetic page/membership failures in optional, required and all-source positions.
The optional cases retain the exact original facts; the other cases reject completion.

The candidate's full city search still **failed at the 300.03s deadline**: 18 model
requests, 26 registered sources, two completed sources. Page extraction retries
and unsuitable source selection consumed time. That run did not reproduce the
browser failure, so it is not causal live proof of the isolation fix and does not
pass the recall gate. The final worker's known-source anonymous benchmark passed
**2/2**, at **15.37s / 10.54s** (7 / 5 model requests), preserving the original
time, price, full status, provenance and unknown waitlist count.

Local validation: **565 tests**, **38 benchmark cases**, typing across **48 modules**,
Ruff, format, wheel build and metadata checks. The isolated browser test worker was
updated; the production application and ingress were not. Geographic recall,
source selection and the full production calendar path remain acceptance gates.
Browser regressions also reject HTTP 404/410/429/500/503 documents rather than
treating their error-page text as a successful source.

A separate candidate-list probe found no target operator in any of the three
focuses. The PDF-only query returned multiple out-of-area operators. Three broader
generic queries produced more local results but still did not find the target.
These observations motivate geographic expansion and source-selection work, not
seeding the benchmark with the expected registration URL.

## Offline Nearby Coverage: 2026-11-03

Area search now derives up to two nearby populated places from an offline GeoNames
snapshot, resolving state/country qualifiers and remaining alias ambiguity with
the request time zone. GeographicLib measures same-country centers within 25km.
Unknown/ambiguous inputs do not expand. Geographic coverage is explicitly partial:
the population threshold, urban districts and two-place cap prevent an exhaustive
claim. Neither a club name nor the expected course URL is part of this expansion.

The agent must search those places before reading, even if the original city has
courses. The live application and harness share one immutable limit configuration:
six search queries instead of four, with the same 24 model requests, 48 references,
30 tool calls and 300-second deadline. Generic registration and schedule queries
no longer require the course's level label or PDF format to discover its operator.

Four additional benchmark cases cover offline geography across three regions and
an end-to-end scripted Pydantic AI loop that obtains the real anonymous-course
transcription only after a neighboring-city search. The real-case time, price,
full status and unknown waitlist oracle is unchanged; this is not live recall proof.

| Unseeded live run | Evidence | Outcome |
| --- | --- | --- |
| First candidate | 3 model requests; 14 registered sources; 31.77s | Automatically searched both neighboring places; third search unavailable before any page read |
| Second candidate | 17 model requests; 40 registered sources; 4 completed reads; 88.47s | All five queries completed; target operator appeared in neighboring-city results, but the agent selected other sources and a later extraction raised `ModelRequestRejectedError` |

Neither run passed the original course-recall gate. The target operator entering
the public candidate set is observed progress, not a found course or a confirmed
registration. The model rejection cause has not been established. Source selection,
search reliability, provider navigation and production calendar acceptance remain open.

Local gates: **588 tests**, **42 benchmark cases**, typing across **49 modules**,
Ruff/format and wheel build checks. Only isolated test environments were updated;
the production discovery switch and centrally managed ingress remain unchanged.

## Public Navigation Metadata: 2026-11-03

The planner previously received source IDs and titles without their public URLs.
The candidate adds validated public URL hints to search results, read observations
and discovered links. Reads still accept only registered opaque IDs. Private/feed
URLs, credential-bearing URLs and changed source identities remain rejected.
This is a navigation hypothesis, not an established recall improvement.

| Live run | Evidence | Outcome |
| --- | --- | --- |
| Baseline diagnostic, without hints | 24 model requests; 48 references; 7 completed reads; 161.63s | Failed with `UnexpectedModelBehavior` at the request ceiling; no reproduced HTTP rejection |
| Candidate, with hints | 20 model requests; 48 references; 7 completed reads; 242.49s | Failed with `ModelTimeoutError`; target occurrence not discovered |
| Candidate, known public detail URL | 3 model requests each; 31.02s / 10.95s | 2/2 passes for original clinic identity, time, price, full status, source provenance and unknown waitlist count |

The known-source runs use fresh anonymous browser contexts and the private local
model. They do not establish unseeded city-wide recall. The unseeded run supplies
the expected occurrence only to the scorer, not to the agent or initial sources.
Neither failed run establishes a cause for the earlier provider rejection, and
their different failure modes are not evidence that URL hints improve latency.

Local gates: **596 tests**, including **42 benchmark cases**, typing across
**49 modules**, Ruff/format, wheel build and metadata validation passed. The
standalone benchmark command initially lacked the local Chrome channel setting;
rerunning with the same Chrome configuration as the full suite passed all 42.
The candidate remains experimental. No production deployment, authentication
expansion or ingress change was made. Date-window-aware catalog enumeration and
successful autonomous navigation to the original occurrence remain open gates.

## Search Capacity And Query Phases: 2026-11-03

A public operator-page probe confirmed that its body contains a registration portal
and current-season clinic PDFs. They were not removed by HTML cleanup. The source
registry was instead shared with search results: six disjoint eight-result queries
could occupy all 48 references before the operator's linked pages were registered.

The regression now replays a sanitized operator link structure, a synthetic catalog
and the existing anonymous clinic transcription. With eight results admitted per
query, it fails when the portal cannot be registered. With four, the actual Pydantic
AI loop follows operator, catalog and detail, preserving the original time, price,
full status and unknown waitlist. Model choices and search results are scripted in
this regression; it is a capacity/ownership test, not autonomous recall proof.

The capacity-only live candidate exposed a second failure: optional neighboring-city
refinements used all six queries before the original city's schedule query. It
stopped after **61.91s**, **10 model requests**, **18 references**, and no reads.
Dynamic tool schemas now expose mandatory query phases first, with matching
server-side checks before spending a search attempt. Reads are hidden until that
coverage is complete; searches are hidden at the limit.

With both changes, the next live run completed the five mandatory queries with
**16 references** and began reading. It then failed after **66.44s / 10 requests**
on an extraction HTTP **422**. Safe diagnostics recorded a 7,060-byte request,
four messages, and tool/prompt error flags. The raw error was not retained or
published. Installed runtime code confirms that forced-tool text-only generations
can produce 422, but two isolated extraction probes returned 200; they did not
reproduce the exact rejection. This is not evidence of a context-length limit.

The private profile now declares no reliable forced-tool-choice support. Tests
verify `auto` on the wire while Pydantic AI still rejects plain-text final output,
permits one bounded recovery for extraction, and fails after repeated invalid
output. Other HTTP errors, including unrelated 422 responses, remain fatal and
are not retried by the transport.

The final candidate's known-source anonymous benchmark passed **2/2**, at
**13.43s / 14.46s**, using **3 / 6 requests**. The original facts and provenance
passed unchanged. Its unseeded city run failed at the second public search after
**11.94s / 3 requests**, before reading; this cannot evaluate the navigation fix.

Local gates: **604 tests**, **46 benchmark cases**, typing across **49 modules**,
Ruff/format, wheel and metadata checks passed. Public search failure classification,
date-aware catalog navigation and full original-occurrence recall remain open.
Only isolated test files were updated remotely; production and ingress are unchanged.

A fresh anonymous portal probe returned the fixed `membership_required` code.
The public seasonal PDF was readable and contained the original instructor/time,
but linked only to that portal and quoted a different price from the dated detail.
It cannot establish current price or availability. This confirms a separate catalog
access boundary, not a need to authenticate every detail read. No authenticated
fallback was enabled by these changes.

## Public Search Recovery: 2026-11-03

Expected search-provider failures were reaching the browser client as
`worker_unavailable`, bypassing the research loop's existing bounded retry.
The worker now emits `search_unavailable` only for the search adapter's provider
exception type. The client recognizes it only on HTTP 503 search responses.
Missing/unrecognized provider results use the same exception type rather than
becoming empty catalogs. Unknown worker faults, wrong-operation labels, failed
child processes and authentication/protocol errors remain fatal.

Ten assertions failed against the earlier implementation before the correction.
The added real-case replay tests cover one successful retry and two consecutive
failures through the actual client, backend and Pydantic AI loop. Recovery retains
the original clinic facts; exhaustion performs no detail read and does not invent
an empty successful result. Both attempts use the same query and only one backoff.

In the unseeded live run, the original city's registration query failed, was
classified as `TransientSourceError`, and succeeded on its single retry. The agent
then completed all mandatory searches and read the target operator page, including
its newly registered portal/PDF links. This directly verifies recovery from an
observed live failure, rather than inferring it from a run with no failure.

The complete run still **failed** later with `UnexpectedModelBehavior` after
**128.81s**, **21 model requests**, **36 references** and **6 completed reads**.
The target occurrence was not discovered. No claim is made about the precise
later validation failure or overall autonomous recall. The separate anonymous
known-source benchmark passed **2/2**, **14.77s / 27.59s**, with **3 requests each**
and unchanged identity, time, price, full status, provenance and unknown waitlist.

Local gates: **630 tests**, **48 benchmark cases**, typing across **49 modules**,
Ruff/format, wheel build and metadata checks passed. The private experimental
browser worker and isolated VPS test client were updated together; worker health
was verified from the VPS. Production application, ingress and browser login
permissions were not changed. This component can advance independently; the
parent discovery feature still requires full recall and deployment acceptance.

## Completion Guidance: 2026-11-03

A fresh unseeded run isolated a planner failure previously reported only as
`UnexpectedModelBehavior`: the model attempted completion before nearby search
coverage, then twice before inspecting new sources from all search groups. Each
validation response identified only the first omission. The run exhausted its
output retries after **117.74s**, **16 model requests** and **3 completed reads**.
The operator portal also returned `membership_required`; this was a separate
source-access limitation, not the completion-validation failure.

The experimental planner now receives bounded, state-owned progress instructions
on each turn. Required detail reads and all outstanding source groups are reported
together on premature completion. The progress instructions and completion gate
share the same source-group calculation. Page text and source titles never enter
these instructions; read permissions, failure handling and retry budgets are
unchanged. Three regression tests failed before the change and passed afterward.
An additional test preserves overlapping-group and optional-source failure semantics.

The next unseeded live run **completed with partial results**, rather than exhausting
completion retries: **267.96s**, **23 model requests**, **12 tool calls**,
**5 completed reads**, **1 course**, and **0 denied tool attempts**. However, it
still **failed the original-course recall oracle**. It selected other sources and
did not retrieve the target registration. One general directory also exhausted
its extraction validation. This is evidence of a completed bounded workflow, not
proof of a causal recall improvement or production readiness; live search results
and model choices varied between the two runs.

Local checks passed: **634 tests**, including **49 benchmark cases**, typing across
**49 modules**, Ruff/format, wheel build and Twine. Only the isolated experimental
VPS planner was updated. Production and authentication permissions are unchanged.

The updated planner's separate live anonymous known-source check passed **2/2**
with the unchanged factual oracle: **12.85s / 10.41s**, **3 / 5 model requests**,
one read source each and no denied tool attempts. Identity, date, time, price,
full status and unknown personal waitlist were preserved. This supplied-link
check is still not an autonomous-discovery success.

## Source Access Diagnostics: 2026-11-03

The operator's public homepage exposed a single program-registration link to the
member-style event list. Fresh anonymous reads reached the homepage but its
browser-generated catalog request returned the fixed `membership_required`
response. A separate fresh native Chrome run received a website protection page,
so it did not provide a comparable catalog response. No challenge bypass was tried.

The anonymous page supplied an opaque authorization header. A temporary isolated
same-origin, read-only forwarding probe still received the same membership denial;
the missing-header hypothesis was not supported. No credential values were logged,
and no authorization-forwarding or login-fallback change was retained. CourtReserve
documents a separate opt-in [public booking flow](https://help.courtreserve.com/en/articles/11787240-public-booking-setup-guide-and-overview);
this does not prove that the observed operator publishes such a link.

The application previously collapsed these recognized reasons into a generic
unavailable result, and displayed uninspected references as failed reads. It now
preserves fixed reason codes for the planner and source list. Unknown diagnostics
remain generic; internal exception messages are never used as UI text. A separate
regression reproduced exhausted tool deadlines leaving sources marked `not_read`;
source reporting now consults the retrieval audit for those failures.

Fourteen targeted diagnostic assertions failed before the implementation; the
deadline test failed separately. Desktop/mobile browser checks and the original
anonymous-course failure matrix now pass, including required-source aborts,
optional-source partial results, cached failures and unchanged time/price/status.

A live **known-source, no-model** check through the private worker and updated
client/backend passed all checks in **23.07s**: the catalog restriction reached
both planner-facing and UI-facing data, while the public detail retained the
original 18:30-19:50 Pacific occurrence, $54 price, full status and unknown waitlist.
This validates diagnostics and anonymous extraction, not autonomous recall.

Local gates passed: **639 tests**, **49 benchmark cases**, typing across **49
modules**, Ruff/format, wheel build and Twine. Production and login permissions
are unchanged. Original-course discovery remains an unmet release gate.

## Anonymous Series Evidence: 2026-11-03

Anonymous inspection of the original public detail showed later row-owned dates
that the worker discarded. Following its back control returned to the previously
observed portal, not an alternative public catalog. The user chose to keep discovery
anonymous and defer authorized-login development. No stored login profile was used.

A new structural transcription retains the visible November 9, 16, 23, 30 and
December 7 rows without real titles, IDs or action controls. For a November 3
28-day window, the first four must appear at 18:30-19:50 Pacific and $54 each.
Only November 9 establishes Full; the others remain unknown. December 7 is outside
the window. The baseline returned no occurrences because the selected November 2
date was past. Eight new targeted assertions failed before the change.

The worker now retains up to seven visible rows, and the adapter parses their
fields independently. Missing fields never inherit the selected occurrence's
price/status. Malformed rows preserve a partial result; conflicting duplicate
occurrences reject the source. Main and row evidence use the same date/weekday,
time-zone and request-window checks. No year transition or extra occurrence URL
is invented. Original screenshot assertions now locate their occurrence by date,
not result ordering; its expected price, time and waitlist facts are unchanged.

Initial live series runs failed **2/2** after successful retrieval (**16.60s /
9.78s**, three model requests each). A diagnostic preserving the tool signature
confirmed that the model passed an internal session key to `read_source`, which
correctly rejected it. One diagnostic that inadvertently changed the tool signature
is excluded from acceptance evidence. The planner now sees a candidate count, not
internal reconciliation IDs; unknown-source enforcement is unchanged.

The subsequent real Qwen/private-browser runs passed **2/2**, **13.95s / 7.66s**,
three model requests, two tool calls, four courses and zero denied attempts each.
The original anonymous detail regression also passed **2/2**, **10.49s / 7.48s**,
with the same request/tool counts and unchanged original facts. These are known-link
tests, not autonomous search successes. Series coverage remains explicitly partial.

Only the experimental browser worker and isolated VPS evaluation code were updated;
the worker's previous installed package was copied for rollback. Production app,
ingress and authentication permissions remain unchanged. Unseeded original-course
recall is still an unmet release gate and was not rerun for this extraction change.

Final local gates: **653 tests**, **51 benchmark cases**, typing across **49 modules**,
Ruff/format, wheel build and Twine passed. The added tests cover row ownership,
past-selected-date filtering, duplicate conflicts, normalized source URLs, hidden
fields, browser sanitization and internal-ID separation from planner tool inputs.

## Read Deadline Isolation: 2026-11-03

An unseeded run with the series changes still failed after **300.03s**, **20 model
requests**, **37 registered sources** and **4 successful reads**. Search returned
the target operator, but the planner had not read its detail. An unrelated source's
extraction consumed its tool deadline; the research loop then restarted the entire
read, including browser retrieval and model extraction. The global deadline expired
before completion. This failure occurred before the target catalog restriction was
reached, so membership requirements alone do not explain the recall failure.

The read loop now checks whether its own timeout context expired. An exhausted tool
deadline marks that source unavailable without an automatic second read. Early
adapter timeouts and explicit transient failures retain one retry. Required details
still abort; optional source failures preserve verified facts with partial coverage;
external cancellation does not become a cached source failure. No budget increased.

Two targeted baseline checks confirmed duplicate reads before the change. New
regressions cover direct deadline handling, the original course with optional and
required slow sources, early adapter timeout recovery, explicit transient recovery,
and external cancellation. The optional-source case retains the original factual
oracle while requiring incomplete coverage rather than claiming a complete result.

Local gates: **659 tests**, **53 benchmark cases**, typing across **49 modules**,
Ruff/format and wheel build passed. Only the isolated VPS evaluation module changed;
production, browser worker, authentication policy and ingress were not modified.

The next unseeded live run still hit the total deadline: **300.05s**, **18 model
requests**, **43 registered sources** and **5 successful reads**. It reached the
target operator's page and registered its portal link, then selected a general
directory rather than following that registration link. It did not reproduce the
same tool-level timeout, so this run is not evidence of a causal latency or recall
gain from the fix. Original-course recall remains failed. The independent deadline
regressions prove the bounded retry correction; source prioritization and end-to-end
runtime remain unresolved. Twine metadata validation also passed.

## Stage Measurements: 2026-11-03

Source discovery now retains a bounded, content-free timing timeline per repetition,
including failures and cancellation. Planner and extractor model calls are measured
separately from source reads and searches. This instrumentation does not alter the
agent's tools, instructions, oracle, limits or authentication policy.

A fresh unseeded run failed at **171.74s**, with **16 completed model requests**,
**22 registered sources** and **2 successful source reads**. The timeline recorded
14 planner calls (**79.44s**), 5 searches (**7.56s**), 4 source reads (**10.99s**)
and 3 extractor calls (**72.21s**, including one cancelled call). These measured
intervals were sequential in this run; they are not generally additive. The
cancelled extraction alone consumed **58.28s** after a general-information page
loaded. Model work, not just browser retrieval, dominated this observation.

The terminal failure was a source-read `ValueError`, not the total deadline. A
separate fresh anonymous probe reproduced a worker page response with eight links;
one ordinary HTTP link failed the client's HTTPS-reference policy. The original course was
not reached. This identifies a concrete worker/client link-policy mismatch to fix
separately; it does not justify weakening navigation validation or using a login.

The initial timing probe used zero defaults when no response usage was available.
The final implementation reports those counts as `null` instead of implying zero
consumption. A subsequent known-link anonymous benchmark with this final format
passed **2/2**, **11.89s / 7.36s**, three model requests and two tool calls each,
with the unchanged original course facts, partial series coverage and no denied
tool attempts. Supplied-link extraction is not autonomous recall.

Local validation: **665 tests**, **54 benchmark cases**, typing across **50 modules**,
Ruff/format, isolated wheel build and Twine passed. A sandboxed Chrome test launch
failed before execution and passed when rerun with the required browser permission;
a non-isolated build lacked setuptools and the normal isolated build passed.
Tests cover request forwarding, usage, error/cancellation propagation, bounded
storage, content exclusion and per-repetition isolation through the original oracle.
Only isolated evaluation files changed on the VPS. Production, private browser
worker, login state and ingress were untouched; the release gate remains unmet.
