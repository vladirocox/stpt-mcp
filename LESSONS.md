# Lessons

Written by /aar-loop after each session's After Action Review. Read this file before starting a new task in this project. Every entry should be concrete and checkable, never vague.

## 2026-10-23 -- An upstream payload without an observation timestamp must not be treated as fresh; derive the implied clock from display time minus remaining minutes and compare it with the real clock before labelling data live.
- Expected: Live arrival payload carries HH:MM plus minutes; the server needs the implied observation instant.
- Actual: The live proxy returned a frozen snapshot with no timestamp and the server copied the fetch time into observed_at and labelled arrivals live, freezing ETAs.
- Why: Missing freshness metadata was treated as satisfied by an invented timestamp instead of a derived or unverified clock.
- tags: live-freshness,stale-data

## 2026-10-23 -- SUPERSEDED (see the 0.3.7 entries below: stale predictions are withheld, not shown for reference). When a live feed is stale and the schedule fallback also fails, serve the stale predictions as reference only via source live, is_stale true, and live false on each arrival, instead of relabelling the source schedule.
- Expected: Stale live predictions with no usable schedule fallback should be returned for reference with a data_stale error.
- Actual: The server returned status partial with source live, live false, and data_stale after the fallback failed.
- Why: The stale feed still held usable-looking predictions, but only the schedule fallback could present them as authoritative.
- tags: live-freshness,fallback

## 2026-10-23 -- Live arrival ETAs are anchored at fetch time and recomputed against the current instant on every materialization, never copied from upstream minutes as absolute arrivals.
- Expected: arrival_time and eta_minutes stay coherent and the countdown decreases with the wall clock.
- Actual: The frozen feed produced static ETAs labelled live; after the fix the invariant arrival_time minus now equals eta_minutes holds.
- Why: Upstream minutes are offsets from an upstream clock that may be wrong, so absolute instants must be derived from the fetch time.
- tags: live-eta

## 2026-10-23 -- Stale predictions must be withheld, not flagged: frozen ETA minutes anchored to the current time look exactly like real arrivals, whatever the live flag says.
- Expected: With a stale feed and no fallback, a client should get no departure times it could mistake for real ones.
- Actual: 0.3.6 returned line 14 at stop 135 as 13:39/13:55/14:01/14:02 (frozen 10/26/32/33 min counted from 13:29) with live false, which a client reads as a timetable.
- Why: A flag on each record does not stop a caller from showing the numbers; only omitting them does.
- tags: live-freshness,fallback

## 2026-10-23 -- A single clock comparison cannot prove a feed is live: STPT's frozen snapshots carry a different stale clock per stop (12:48 to 23:33 in one sample), so some stop always matches real time. Also require the stop's implied clock to advance between fetches.
- Expected: A frozen feed is rejected at every stop and every moment.
- Actual: The skew check alone would have labelled a frozen stop live for about six minutes around the moment its stale clock equalled real time.
- Why: Freshness must be derived from how the feed's clock moves, not from one reading of it.
- tags: live-freshness,frozen-feed

## 2026-10-23 -- Before calling a failure "upstream, not a code bug", check whether the code needs that upstream at all; the schedule fallback scraped smtt.ro although the official GTFS (stop_times, calendar) was available to bundle.
- Expected: The fallback works offline from bundled GTFS data, as the tool documentation implied.
- Actual: smtt.ro refused connections from every host, so the fallback failed on every call (45 of 45 page loads on the deployed host) and the frozen feed had no alternative.
- Why: A runtime dependency on a website was accepted for data that is published as a static feed.
- tags: fallback,schedule,offline

## 2026-10-23 -- When the upstream live arrivals feed recovers from freezing, re-run the live check before closing any release: call get_service_status and expect live_feed.skew_seconds near 0 and recent_stops mostly fresh; call get_arrivals(stop_id="135", line="14") twice at least three minutes apart and expect source live, freshness fresh, a smaller eta_minutes on the second call, and arrival_time minus local now equal to eta_minutes within one minute. Record the result in docs/EXECUTION_LOG.md.
- Expected: A frozen feed skews by minutes; after upstream recovery the server should report skew near zero and ETAs should decrease between calls.
- Actual: The feed was frozen through the 0.3.6 and 0.3.7 work (byte-identical payloads on every sampled stop), so the countdown proof first ran against a deterministic fake feed (tests/integration/test_freshness.py). After the 0.3.7 deploy the feed recovered intermittently and the live check passed (19:35 trip: 35 min at 19:00, 18 min at 19:17); repeat it once the feed is stable, because it still alternated with HTTP 503.
- Why: Live recovery was never re-verified after the fix, so the release proof was conditional on an upstream that had not recovered.
- tags: live-freshness,live-eta,recovery

## 2026-10-23 -- The bundled network snapshot and timetable (GTFS feed 4.28) end on 2026-12-31. Regenerate both from the new official GTFS before then; afterwards the schedule fallback returns schedule_unavailable and the stdio e2e test that expects a schedule result fails.
- Expected: The fallback always has timetable data for the current date.
- Actual: The timetable is static package data with a fixed service range.
- Why: Offline data trades runtime dependencies for an expiry date that must be tracked.
- tags: schedule,maintenance
