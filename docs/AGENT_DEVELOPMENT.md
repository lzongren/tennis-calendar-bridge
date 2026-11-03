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
| Agent orchestration | Expand neighboring cities, read linked registration sources, recover transient reads, enforce request/tool/time budgets | Pydantic AI loop and replay harness implemented; opt-in source backend shares the model budget with extraction; see `benchmarks/README.md` |
| Real-source retrieval | Find the clinic from a city request; read dynamic/PDF detail when needed; retain retrieval evidence | Public HTML/PDF reader and search backend implemented; dynamic registration and complete live acceptance remain pending |
| Existing calendar regressions | Ongoing clinics, term dates, DST, exclusions, source-to-card ownership, mobile rendering | Existing discovery tests remain mandatory |
| Runtime/security | Private model only, credential redaction, no arbitrary URL/file/payment tools, outage preservation, cancellation | Opt-in worker, shutdown cancellation and snapshot preservation covered offline; live container/network checks still pending |
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
Source references include a validated public URL as untrusted navigation metadata,
so the planner can distinguish local operator pages from directories and platform
marketing. This does not grant URL-based tool access: reads still require a
registered opaque ID, and changing a registered reference is rejected. Private,
credential-bearing and personal subscription URLs cannot become these hints.

Read observations retain source metadata, navigation links, required detail IDs and
the unique candidate count. They summarize at most eight distinct title/date tuples,
with titles limited to 240 characters and an explicit truncation flag. The full page
body stays with the extractor; full source-owned records stay with reconciliation.
This projection changes neither extracted facts nor the final evidence set. Dates
are included because a title-only live trial caused redundant follow-up searches.

Pydantic AI exposes the completion tool only when the existing completion validator
finds no outstanding work. Tool preparation calls that same side-effect-free
validator; it does not maintain a second coverage policy. Terminal source failures
still reach the validator and abort rather than becoming success. Hiding a tool is
planning guidance, not a security boundary: the model can still attempt an absent
tool or return prose, and existing validation and request limits still apply.

Area discovery is the default. Dynamic Pydantic AI tool schemas expose the original
city's outstanding overview, registration and schedule queries first, then nearby
overview queries. Reads become available only after that coverage. The same rule
is checked before executing a search, so a model cannot spend the search budget on
optional refinements while mandatory queries remain. The search tool disappears at
the query limit. Completion still requires an attempted read from the new sources
introduced by each nonempty search. Existing candidates cannot bypass these gates.

Each model turn also receives state-owned progress instructions: outstanding query
phases, required detail IDs, unattempted source groups and remaining read capacity.
Source text, titles and URLs are not promoted into these instructions. Completion
validation reports all outstanding detail/group reads together, rather than spending
one output retry per missing group. The progress display and validator share the
same grouping logic; failed optional sources count as attempted, while required
failures still abort. This preserves the existing coverage and retry limits and is
not evidence that a live model will follow the plan or find the target course.

Each area query admits at most four distinct valid results. Six live queries can
therefore use at most 24 of the 48 source references; up to four user-provided
sources are preserved separately. Remaining capacity is available for operator
schedules and registration links. Results beyond the cap are not registered and
truncation remains explicit. This exchanges search breadth for navigable depth;
it does not promise exhaustive coverage or increase model, read, tool or time limits.
`source_check` is the explicit focused mode for supplied-source and evidence replay
benchmarks, not the production city-discovery path. It retains the same required
detail, grounding and failure rules but makes no area-recall claim.

The public search adapter retains the existing Yahoo transport and redirect decoder
and accepts both observed desktop and alternate result layouts. It excludes account
navigation and related-query promotions. Unknown or blocked pages are errors, never
successful empty catalogs; no automatic switch to another search provider is enabled.
DDGS is pinned to the evaluated version because this compatibility layer subclasses
its provider adapter. A dependency update must rerun the layout fixtures and live search.

The private client uses an explicit model and key, Tailscale HTTPS or loopback HTTP,
no environment proxy, no redirects, bounded decoded responses and no SDK transport
retries. Agent instrumentation is explicitly disabled. A `.ts.net` hostname alone
does not prove that Serve/Funnel and Tailnet grants are correctly configured.

The private model profile declares `supports_forced_tool_choice=False`. The
evaluated runtime can reject a plain-text generation with HTTP 422 when the
request requires a tool call; it does not guarantee decoder-level tool forcing.
Using `auto` leaves typed output enforcement and bounded validation retries with
Pydantic AI. Plain text is not accepted as a course report or completion. Transport
errors and unrelated HTTP 422 responses still fail closed; no error body is treated
as course evidence and no cloud fallback is introduced.

Live model results must identify dependency/model configuration, repeat count,
latency, individual failures and retrieval audit. Keep credentials and unredacted
runtime transcripts outside Git; public CI uses no Tailnet credentials. A future
live harness must distinguish prerecorded source replay from actual web retrieval.

## Public Documents

`read_public_document` runs the public fetch and PDF parser in a fixed-purpose child
process. It does not inherit inference keys, proxy settings, Python path overrides,
portal sessions or cookies. Each HTTP redirect is validated and its resolved public
IP pinned. HTML navigation is removed before the link budget, preserving body
schedule and cross-site registration references that large menus previously hid.

PDF extraction uses pinned `pypdf`, retaining layout text and page boundaries, plus
public URI references. It does not execute embedded actions or infer class dates.
Downloads are limited to 4 MB, PDFs to 12 pages/24,000 characters, and child output
to 1 MB. The parent kills and reaps on cancellation or its 20-second deadline;
the child also limits CPU to 10 seconds and, on Linux, address space to 512 MB.
macOS does not receive the address-space limit. This is resource containment,
not an OS sandbox. Encrypted, scanned-only and over-limit documents fail closed.

The original synchronous worker remains HTML-only until the new source backend is
enabled. The integrated agent is available behind `TENNIS_DISCOVERY_AGENT_ENABLED`,
defaulting to false. Reading a seasonal PDF does not establish registration availability.
The real November case still requires dynamic detail retrieval and reconciliation
against the dated registration, not substituting a seasonal price.

## Extraction Gate

Generic page extraction uses a nested typed report, but an OpenAI-compatible endpoint
need not understand reference-based tool schemas under a private model alias. The
client explicitly uses Pydantic AI's built-in definition inliner. Wire-level tests
verify that required fields and `additionalProperties: false` survive that conversion.
Missing course lists or evidence fields cannot default to a successful empty result.

The model selects inclusive start/end evidence lines; Python retains the full bounded
span, including intervening exclusion text. Core quoted fields are checked against
that span before date expansion; unsupported optional club/venue/level labels are
omitted rather than borrowed from other courses or the requested level.
A valid JSON object is not a sufficient gate:
live replay and live-web extraction must pass separately. A fixed minimal transcription
cannot establish dynamic registration recall, occupancy or geographic completeness.

When extraction exhausts its evidence-validation retry, that source is unavailable,
not an empty successful page. The agent may continue with independent sources and
must mark the result partial. Failed required details and all-failed searches still
retain the previous snapshot. Model HTTP failures and the shared model-request
budget remain fatal; this isolation does not introduce extra retries.

A read tool's deadline covers both retrieval and extraction. Once that deadline
expires, the source is unavailable for this run and is not automatically read a
second time. Adapter-reported transient errors or early transport timeouts retain
the existing one-retry allowance; they are distinct from exhausting the enclosing
tool budget. Required-source failures still abort, optional failures remain partial,
and external cancellation propagates without caching a completed failure. The
total run deadline and model-request limits are unchanged.

## Geographic Coverage

Area search resolves exact city names and aliases against the pinned offline
GeoNames populated-place dataset. A state/country qualifier disambiguates names;
the requested time zone can resolve remaining alias ambiguity. Unknown or still
ambiguous locations are not guessed. No geocoding service or model-authored
coordinates are used. The input city remains the original search argument.

The backend offers the two nearest same-country populated places with centers
between 1km and 25km away, using GeographicLib WGS84 distance. The dataset's
15,000-person threshold excludes many small places and can include urban districts;
this is neither driving distance nor complete metropolitan coverage. Searches are
explicitly partial even when every returned source was read.

The agent searches these bounded neighboring places before reading sources, even
when the original city has candidates, and must inspect a newly introduced source
from each nonempty query group before completion. Exact-source checks do not expand
geography. Application and live benchmark share `LIVE_DISCOVERY_LIMITS`: six search
queries, 24 model requests, 30 tools, 48 source references and 300 seconds. Only the
search-query ceiling increased (from four); the other runtime limits are unchanged.
General registration/schedule queries no longer require an NTRP label or PDF result;
level/date validation remains in evidence extraction.

## Browser Evidence

Course discovery should not require a dedicated search API. The next retrieval
layer uses general search and read-only browser navigation, starting with a fresh
anonymous context. Login is not inferred from a provider's domain name. Any
explicitly authorized authentication stays in a local browser. This does not mean giving the model unrestricted control
over a personal browser or copying its profile to the VPS.

`BrowserDocument` is the transport-neutral input boundary for a trusted reader.
It contains the observed URL, a bounded rendered course region and its capture
timestamp. It is not a model output schema or an HTTP ingestion endpoint. The
source backend accepts it through the existing injected reader, requires the
observed URL to match the registered source, and passes it to a deterministic
adapter before exposing any text to the model.

The first adapter handles CourtReserve event-detail summaries. A live, read-only
browser inspection on November 2, 2026 found the original clinic and the same
time, price and full/waitlist facts as the screenshot benchmark. The checked-in
fixture is a **structural transcription** with generalized title and synthetic
IDs, not a full authenticated page capture. Account navigation, personal action
buttons, cookies and credentials are excluded. No account state was changed.

The adapter copies only course-owned title, category, date, time, price and slot
fields. It rejects missing/duplicate fields, unsupported routes, unexpected query
parameters, captures older than 15 minutes and inconsistent weekdays. Short
meridiems such as `6:30p` are supported. A yearless current-year label uses the
fresh browser capture's local year, never the search year or a copyright footer.
Explicit years remain authoritative; ambiguous DST times are rejected. A
`Register` button is not proof of availability, and personal `Waitlisted` state
is not a queue position or count.

The selected occurrence and up to seven visible series rows are parsed separately.
Each row must own its date and time; price and capacity never inherit from the
selected occurrence. A past selected date does not suppress later rows inside the
requested 28-day window. Row date labels use the same fresh-capture year and weekday
validation as the selected date; ambiguous year transitions are not inferred.
Unknown status remains unknown, including rows showing a Register button without
a capacity field. Malformed rows are omitted with partial coverage; conflicting
duplicate occurrences invalidate the source. Series enumeration remains partial,
even if every currently visible row was parsed. Links identify the source series
page, not an invented occurrence-specific registration URL.

The browser sends only visible date/time/price/status fields from those rows, never
their action controls or surrounding account text. The planner receives a candidate
count and bounded title/date summary, not internal session keys: those keys are
reconciliation identities, not readable source IDs. Unknown read IDs still abort;
this does not grant retries or additional browser permissions. The adapter does not fall back to model-generated
registration facts.

The production worker still uses the public document reader. There is **no
autonomous browser transport enabled yet**: interactive browser inspection and
an injected-reader replay do not establish production search recall. A future
transport must provide fresh anonymous browser contexts, separately authorized
login fallback only on an explicit login requirement, allowlisted
read operations, origin/navigation checks, cancellation and resource budgets.
It must never expose arbitrary CDP access, scripts, files, booking or payment
actions to the model. Local cookies stay local; the VPS receives sanitized course
facts only. Existing private listeners and centrally managed ingress stay unchanged.

The experimental private worker distinguishes source failures from worker failures.
Only a recognized HTTP 503 read response (`page_unavailable`, `login_required`,
`membership_required` or `browser_attention_required`) can mark one source
unavailable. Optional failures leave an explicit partial result; required-detail
or all-source failures still abort and preserve the previous snapshot. These
responses never trigger login or a challenge bypass.
Public HTTP error documents are source failures, not successful empty pages.

Rendered public-page links pass the same `read_url` policy used by the client before
the browser worker applies its 24-link response limit. Unreadable references, such
as HTTP navigation chrome or checkout links, are omitted rather than invalidating
the otherwise readable page. They are not fetched, rewritten to HTTPS or treated
as registration evidence. The page destination and every returned link still face
client-side validation; malformed worker responses remain errors. Portal detail
selection, authentication requirements and partial-coverage semantics are unchanged.

Recognized failures retain a fixed `SourceFailureReason` through the browser client,
research loop and source report. Only that allowlisted reason reaches the model or
UI; exception messages and response bodies do not. The UI distinguishes sources
not inspected in this refresh from unreadable pages, membership/login restrictions,
manual website verification and invalid course evidence. Exhausted tool deadlines
use the research audit so a cancelled adapter cannot leave an attempted source
marked `not_read`. A successful retry clears the previous source diagnostic.
Older snapshots without `failure_reason` retain a generic unavailable label.
None of these reasons authorize authentication, repeated reads or extra tools.

A public page whose model-based course extraction exhausts validation retries may
still provide navigation. The backend retains only independently observed links
that pass the existing URL, depth and registry limits. Its document is marked
`evidence_unavailable`, must contain no course records and must remain partial.
The planner sees `evidence_invalid` plus those links, not the exception message,
page body or a misleading zero-candidate success. Cached reads do not repeat the
failed extraction. Linked sources require separate reads and establish their own
facts; the original page stays unavailable in the source report.

This recovery applies only to exhausted evidence validation on a public page with
admissible navigation. Required-source failures still abort even if linked details
succeed; no valid facts across all attempted sources cannot publish an empty success.
Model transport failures, request budgets, cancellation, invalid browser observations
and URL-policy violations do not become navigation-only success. No new retry,
authentication permission or source/read budget is introduced.

Expected search-provider failures have a separate `search_unavailable` response.
Only HTTP 503 with that code on a search request becomes `TransientSourceError`.
The existing research loop may repeat the same query once within its time budget;
the client adds no retries. Missing or unrecognized search results are errors, not
empty catalogs. A second failure aborts discovery and preserves the previous
snapshot. This behavior does not switch search providers or attempt to bypass
verification. Read requests, authentication errors and unknown worker failures
cannot acquire this retry behavior by returning a search error label.

Process exit, malformed/oversized output, browser disconnection, unknown exceptions,
service authentication, transport failure and the legacy ambiguous
`source_unavailable` response remain run-level failures. This distinction adds no
retries or new permissions. Roll out the worker and client together: mixed versions
fail closed rather than silently accepting an old worker fault as a page failure.

The anonymous November 2 clinic case is a separate fixture from the original
signed-in screenshot. It retains the same 18:30-19:50 Pacific occurrence and
$54 price, but shows only `Full`; the waitlist count is unknown, not two and not
zero. `pytest -m benchmark` checks both observations without merging their facts.
The opt-in `benchmarks.source_discovery --case anonymous` replay exercises the
Pydantic AI loop. With `--live-web --source-url <PUBLIC_DETAIL_URL>`, it uses the
private browser worker and fresh evidence. This is a known-source extraction
benchmark, not proof that city-wide search discovers that URL.

`anonymous-series` adds a separate November 3 observation with four subsequent
occurrences in the requested window. Its source URL is supplied explicitly, so it
tests anonymous series extraction and the local-model loop, not autonomous recall.
Authentication remains disabled by user decision. A future authorized-login path
is separate work, not a prerequisite silently enabled by the current agent.

Catalog enumeration remains partial: the current browser reader follows at most
four matching adult cards from the provider's default list. It does not yet apply
the request's date window or enumerate all pages. A previously observed occurrence
may no longer appear in that default list even when its public detail URL works.
Date-aware catalog navigation therefore remains a separate recall acceptance gate;
missing a card is not evidence that the course does not exist.
