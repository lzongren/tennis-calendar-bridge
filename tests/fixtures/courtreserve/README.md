# Calendar Feed Fixture

`calendar.ics` is synthetic iCalendar data, not a member feed download. It covers
UTC and named-zone times, weekly recurrence, exclusions, moved sessions, and
cancellations. All names, locations, UIDs, and URLs are invented. The deliberate
`secret-feed-token` marker tests that URL/description fields are not retained in
normalized events or republished by the bridge.
