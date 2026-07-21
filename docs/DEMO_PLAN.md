# Demo Story, Shot List, And Voiceover

Target runtime: 2:20 to 2:40. Export a 1920x1080 H.264 MP4 with clear narration
and no music.

## Story and shot list

1. **0:00-0:12 — Hook.** Title card, then generic portal tabs: one player,
   several portals, no reliable overview.
2. **0:12-0:35 — Product.** Synthetic dashboard, next session, and agenda.
3. **0:35-0:58 — Outcome.** Tokenized synthetic `.ics` feed and calendar use.
4. **0:58-1:25 — GPT-5.6.** Run `brief --preview`, highlight the model and venue
   aliases, then show a live brief. If no live key is available, show only the
   preview and say so; do not imply a live call.
5. **1:25-1:48 — Privacy.** README architecture and local-only fields.
6. **1:48-2:12 — Codex.** Dated Build Week commit/PR and test output.
7. **2:12-2:30 — Close.** Dashboard and “Every tennis booking, one dependable
   calendar.”

## Final voiceover script

“A tennis schedule should not require checking three club portals every night.
Lessons, clinics, and court reservations arrive through different systems, and
one missed change can mean a missed session.

Tennis Calendar Bridge turns those fragmented bookings into one private,
dependable schedule. Provider adapters normalize each event into a local SQLite
store. This synthetic dashboard shows what is next, while a tokenized iCalendar
feed works with Apple Calendar, Google Calendar, and other clients. The bridge
never asks for permission to write into your personal calendar.

For Build Week, I added an opt-in GPT-5.6 weekly brief. This preview shows the
exact Responses API request. GPT-5.6 receives event titles, local times,
categories, statuses, and pseudonymous venue labels. Credentials, access codes,
instructors, raw portal data, source URLs, club identifiers, and real venue
names stay local. GPT-5.6 turns the week into a concise plan, calling out
logistics, tight gaps, late finishes, and venue changes without inventing facts.

The core calendar works without AI or an API key. GPT-5.6 has a focused role:
synthesis across a week that spans multiple booking systems.

I used Codex to inspect the pre-existing architecture and official rules,
implement this privacy boundary and Responses API integration, add redaction
and response tests, strengthen CI through typing and package checks, and verify
the clean synthetic judge path. I kept the product decisions: opt-in AI,
aliases instead of real venues, no calendar write access, and no claims beyond
verified evidence.

Tennis Calendar Bridge: every tennis booking, one dependable calendar.”

## Recording QA

- Use only the synthetic database and example config.
- Hide history, notifications, avatars, bookmarks, and API keys.
- Cut waits and mistakes without hiding limitations.
- Confirm 1080p, under three minutes, intelligible narration, and no copyrighted
  music.
- Watch the entire export and inspect representative frames before approval.
