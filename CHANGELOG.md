# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

Nothing yet. Next up: mini-league standings.

## [0.1.0] - 2026-08-25

First public release. Supersedes the unpublished 0.1.0 stamped on 2026-08-24 —
that build never left the working directory, so its changes are folded in here
rather than split across two versions.

### Added

- UI config flow taking an FPL manager ID, one service device per manager, and
  11 sensors covering points, ranks, team value, bank, transfers, the current
  gameweek, the next deadline, the gameweek average score and the gameweek
  state.
- Options flow for the polling interval (5-360 minutes, default 30).
- Diagnostics download with player identity fields redacted.
- `icons.json` for entity icons, replacing per-description `icon=` values.
- Test suite (`pytest-homeassistant-custom-component`) covering the cache TTL
  policy, the API payload handling, the sensor value functions and the config
  flow, with a `pytest` job in CI.
- A team rename now reaches the device registry. `DeviceInfo` is read only when
  an entity is added, so the device name previously kept whatever it was called
  at setup.
- `bootstrap-static/` failures now enter a 10-minute cooldown instead of being
  retried on every poll cycle.

### Pre-release hardening

Nothing below shipped to a user — these record what the architecture and code
reviews changed before the first release, so the reasoning survives.

- HTTP responses are released via `async with`. The 404 branch raised before the
  body was read, leaking a connection from Home Assistant's shared aiohttp
  session on every FPL maintenance window.
- The `bootstrap-static/` cache is shared by all config entries rather than held
  per entry, so tracking a second manager does not double the ~3 MB download.
- The cache TTL is state-dependent: 15 minutes while a gameweek is being played,
  6 hours otherwise. A flat 6 hours would have frozen `gameweek_state` and
  `gameweek_average_score` for a whole matchday.
- Every staleness branch compares against a TTL. An earlier form re-fetched on
  every poll cycle during the window between a deadline passing and FPL moving
  the gameweek flags.
- A failed `bootstrap-static/` fetch no longer fails the whole update: the eight
  sensors reading only from `entry/` stay live and the three event-derived ones
  hold their last known values. The two API calls are no longer gathered, so one
  leg failing cannot leave the other's exception unretrieved.
- Team value and bank publish a bare number (`100.4`) with no unit. `£` claimed
  the values were in pounds when they are in millions of pounds; `m` is HA's
  symbol for metres and would render the team value as a distance. The values
  stay numeric with `suggested_display_precision: 1`, so charts and long-term
  statistics are unaffected.
- `configuration_url` points at `/entry/{id}/history` rather than a
  gameweek-scoped URL that would pin itself to whatever gameweek was current at
  setup.
- `manager_id` is validated as a positive integer, so a typo fails at the form
  instead of one API round-trip later.
- A malformed element in the event list raised `AttributeError` instead of a
  retryable update failure.
- A boolean field in the manager summary could be published as the state `1` or
  `0`, because `bool` is a subclass of `int`.
- A stored polling interval of `0` was silently replaced with 30 minutes by a
  truthiness check.
