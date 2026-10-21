# ADR 0003: Date- and shape-aware schedule scraping

- Status: Accepted
- Date: 2026-10-21

## Context

The legacy integration parsed the first `data-times` value containing a stop
code and ignored direction and date. It used `data-code` matching without shape
selection, mislabeled every schedule result as a bus, and used a stop name as a
destination. SMTT's schedule pages contain several lines and shape variants, and
the displayed timetable changes with the selected service date. A stop can be
served in both directions or by multiple route patterns.

## Decision

Derive schedule targets from the bundled GTFS snapshot:

- use the stop's `shape_ids` to know exactly which route patterns serve it;
- group shapes into unique line/direction pages (`_T` → base page, `_R` → `-r`
  page);
- request the page for the requested local service date using
  `custom_value=DD.MM.YYYY`;
- parse `[data-code]` elements for the exact stop ID and accept only time tokens
  whose shape suffix matches one of the stop's target shapes;
- label direction (`tur`/`retur`) from the shape suffix and destination from the
  terminal stop in that shape sequence;
- derive vehicle mode from GTFS route metadata;
- compute departure datetimes in `Europe/Bucharest` and filter to a configurable
  lookahead at response time, rather than caching countdown minutes.

Pages are fetched with bounded concurrency and cached per URL/date. A failure on
one page yields a partial warning, not fabricated departures. Zero successful
pages is an error, allowing last-known-good data to be used.

## Consequences

- A busy stop may require several page fetches on a cache miss. They are
  deduplicated, concurrency-bounded, and reused across stops where possible.
- A stop that is absent from the current network snapshot has no schedule
  fallback, even if live arrivals exist. Live data is still served and the tool
  explains the limitation.
- Schedule times are only a fallback. They must never be labeled as real-time.
- HTML changes can break the fallback; parser tests and explicit warnings make
  that visible without taking down the live path.
