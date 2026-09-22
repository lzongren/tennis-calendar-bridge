# AccuSportView Member Reservations

`member-reservations.json` reproduces the authenticated `/my` page's
`ApplicationsQuery` request/response structure inspected in September 2026.
All member IDs, booking IDs, coach names, access codes, dates, and courts are
synthetic. No captured account responses, credentials, or payment data are kept.

The fixture covers individual reservations (including duplicate accounting
rows), tactics, doubles strategy, open play, and a multi-session league crossing
the default month boundary. Group titles are data, not a parser allowlist.
For groups, `place` may be the course description; `court_id` establishes whether
the value identifies a court.

Class court assignments are fetched separately through
`schedule_slot_reserves.schedule_slots.court.name`. Slots have their own local
start/end times, often in 15-minute increments. Only uncanceled assignments
overlapping the booked session contribute to its location. The tactics fixture
includes several out-of-order slots on the same court to verify deduplication.

The provider sends naive local timestamps and durations in minutes. Membership,
archive, cancellation, service, and enrollment filters belong to the observed
member request. Only the requested date window is expanded by the adapter.
