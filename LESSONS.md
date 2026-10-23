# Lessons

Written by /aar-loop after each session's After Action Review. Read this file before starting a new task in this project. Every entry should be concrete and checkable, never vague.

## 2026-10-23 -- An upstream payload without an observation timestamp must not be treated as fresh; derive the implied clock from display time minus remaining minutes and compare it with the real clock before labelling data live.
- Expected: Live arrival payload carries HH:MM plus minutes; the server needs the implied observation instant.
- Actual: The live proxy returned a frozen snapshot with no timestamp and the server copied the fetch time into observed_at and labelled arrivals live, freezing ETAs.
- Why: Missing freshness metadata was treated as satisfied by an invented timestamp instead of a derived or unverified clock.
- tags: live-freshness,stale-data

## 2026-10-23 -- When a live feed is stale and the schedule fallback also fails, serve the stale predictions as reference only via source live, is_stale true, and live false on each arrival, instead of relabelling the source schedule.
- Expected: Stale live predictions with no usable schedule fallback should be returned for reference with a data_stale error.
- Actual: The server returned status partial with source live, live false, and data_stale after the fallback failed.
- Why: The stale feed still held usable-looking predictions, but only the schedule fallback could present them as authoritative.
- tags: live-freshness,fallback

## 2026-10-23 -- Live arrival ETAs are anchored at fetch time and recomputed against the current instant on every materialization, never copied from upstream minutes as absolute arrivals.
- Expected: arrival_time and eta_minutes stay coherent and the countdown decreases with the wall clock.
- Actual: The frozen feed produced static ETAs labelled live; after the fix the invariant arrival_time minus now equals eta_minutes holds.
- Why: Upstream minutes are offsets from an upstream clock that may be wrong, so absolute instants must be derived from the fetch time.
- tags: live-eta
