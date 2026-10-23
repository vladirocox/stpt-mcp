# ADR 0005: Offline GTFS timetable and verified live freshness

- Status: Accepted
- Date: 2026-10-23
- Supersedes: [ADR 0003](0003-schedule-scraping.md)

## Context

On 2026-10-23 STPT's live arrivals endpoint served frozen per-stop snapshots: the
same payload for hours, each stop with its own stale implied clock (12:48 to
23:33 in a 64-stop sample taken at 13:24). Version 0.3.6 rejected predictions
whose implied clock diverged from the real clock, but its schedule fallback
scraped `smtt.ro` route pages, and `smtt.ro` refused connections. With both
failing, 0.3.6 returned the frozen minutes counted from the current time, which
looked like real departures. A frozen stop whose stale clock happened to equal
the current time would also have passed the skew check and been labelled live.

The project already bundled a network snapshot derived from STPT's official GTFS
feed. The same feed (`smtt-timisoara-ro.zip`, mirrored by the Mobility Database
as `mdb-2868`) contains `stop_times.txt` and the service calendar.

## Decision

- The schedule fallback reads a bundled `timetable.json` generated from the
  official GTFS zip by `scripts/generate_timetable.py`. It performs no network
  I/O. The generator refuses a feed whose version or trip stop sequences do not
  match `network.json`. The scraper, its settings, and BeautifulSoup are removed.
- Live predictions are used only if their freshness is verified against the real
  clock: the implied clock must be within tolerance **and** must not stand still
  for longer than the tolerance across fetches of that stop. A first sight of a
  stop while other stops recently looked stale is `unverified`. Predictions with
  no clock reference are `unverified`.
- Stale or unverified predictions are never returned as arrivals. Without a
  usable fallback the arrivals list is empty and the error is `data_stale`.
- Every provenance carries `freshness` (`fresh`, `stale`, `unverified`), which
  defaults to `unverified` and is forced to `stale` whenever `is_stale` is true.
  Only `fresh` data may be labelled live.

## Consequences

- The fallback works during any STPT web outage, but only until the timetable's
  end date (2026-12-31 for feed 4.28). After that it reports
  `schedule_unavailable` until a release regenerates both bundled files.
- Timetable times are planned departures, not predictions: in the reference case
  the timetable said 12:59 where Google Maps showed 12:57.
- After a restart, a frozen stop fetched for the first time inside its
  coincidence window is accepted until its next fetch; the feed has no
  timestamps that would allow better.
- The package grows by about 390 KB of JSON.
