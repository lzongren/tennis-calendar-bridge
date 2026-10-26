# Practice Walls

Open **Practice walls** from the schedule or course-discovery page. The route is
`/walls`, or `/tennis/walls` with `TENNIS_BASE_PATH=/tennis`. No new ingress route,
model, credentials, booking-provider login, or database migration is required.

The initial directory covers three South Bay venues. This is a curated starter
catalog, not an exhaustive search or a live facility-status service. The
[community discussion](https://www.reddit.com/r/SanJose/comments/1udpmvu/any_tennis_walls_in_san_jose/)
provided leads; each entry retains its own evidence and source-review date.

## Evidence And Location Precision

- **Official practice wall:** a facility's published material identifies a
  practice wall. This does not establish current condition, hours or availability.
- **Community report:** someone reports a wall; an official tennis-court listing
  alone does not corroborate it.
- **Handball; tennis unverified:** documented handball facilities, without a
  claim that tennis use is permitted.
- **Unmapped leads:** unresolved locations or school access, deliberately excluded
  from map pins and navigation links.

Initial pins are venue reference positions from OpenStreetMap, not wall or
entrance coordinates. Directions use the named venue and address. Source checks
on 2026-10-26 found a practice-wall label in Campbell's official campus map;
Marijane Hamann remains community-reported; Backesto is labeled as handball.

## Extending The Catalog

Public entries live in `tennis_overview/catalogs/walls.json`. The schema supports any
region. To use an operator-managed directory instead, set `TENNIS_WALLS_FILE` to
a JSON file in the running service, or `app.walls_catalog_path` in its TOML config.
The custom file replaces the bundled catalog and is reloaded on requests.
For Docker, mount the file read-only and pass the container path via the environment;
the default deployment needs neither. This file is deliberately independent of
club credentials and private booking storage.

Use version `1` with `locations` and `leads` arrays. Follow the bundled schema:
unique lowercase slug IDs; `name`, `city`, `notes`, `reviewed_on` (ISO date), and
at least one labeled HTTPS `sources` entry. Mapped entries additionally require
`address`, numeric `lat` and `lon`, `coordinate_source`, `precision` (`venue` or
`wall`), and `status` (`official_practice_wall`, `community_report`, `handball_wall`).
Only publish an exact wall pin with supporting evidence. Unknown fields are
discarded by the public API; malformed catalogs return a generic 503, without file
paths or content. All **recognized fields are visible to app visitors**: do not
put secrets, private feed links or access codes in them.

Source-backed public venue data is distinct from a user's private club membership.
No location search, geocoding, LLM, or device-location request happens at runtime.
The initial coordinate lookup was a one-time OpenStreetMap Nominatim lookup;
the OSM object links are retained for review. Coordinates are attributed to
[OpenStreetMap contributors under ODbL](https://www.openstreetmap.org/copyright).
Treat the coordinate dataset as ODbL data, separately from the project's MIT code.

## Map Delivery And Privacy

Leaflet 1.9.4 is vendored locally with its BSD-2-Clause license. The JS and CSS
match the SHA-256 hashes published on the [official download page](https://leafletjs.com/download.html).
No CDN script, API key or third-party analytics is used. Files are included in
the Python wheel; asset serving uses an explicit allowlist, not arbitrary paths.
The overview control uses Lucide's `maximize` icon (0.468.0, ISC license included).

The browser fetches visible tiles directly from `tile.openstreetmap.org`. OSM
receives the viewer's IP, app origin and requested map area, not booking data or
authentication tokens. The page states this in Sources and privacy. Attribution
remains visible, normal browser caching is retained, and no tile prefetching,
offline download or server proxy is implemented. See the
[OSM tile policy](https://operations.osmfoundation.org/policies/tiles/).
This free tile service is best effort. For larger traffic, replace the tile-layer
URL and attribution in `static/walls.js` with an appropriately licensed provider.

If tiles or the map library fail, the searchable venue list and navigation links
remain available. An empty filter is distinguished from a failed catalog load.

## Verification

`tests/test_walls.py` covers catalog evidence/validation, config, base-path routing,
HEAD, asset allowlisting and private-error handling. `tests/test_walls_browser.py`
exercises real Leaflet markers, list selection, filtering, navigation URLs,
mobile layout, and network failures with deterministic local tile responses.
Tests do not bulk-fetch or pan across public tile servers.
