"""Constants for the Fantasy Premier League integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "fantasy_pl"

CONF_MANAGER_ID: Final = "manager_id"
CONF_LEAGUES: Final = "leagues"

# FPL classifies a classic league by how it was created: "x" is an invitational
# league someone made and shared a code for (a mini-league), "s" is one FPL
# generates for everyone — Overall, your club, your region, your country. Only
# the invitational ones are interesting by default; the rest have millions of
# entries and are the same for every manager.
LEAGUE_TYPE_INVITATIONAL: Final = "x"

DEFAULT_SCAN_INTERVAL: Final = timedelta(minutes=30)
MIN_SCAN_INTERVAL_MINUTES: Final = 5
MAX_SCAN_INTERVAL_MINUTES: Final = 360
CONF_SCAN_INTERVAL_MINUTES: Final = "scan_interval_minutes"

# bootstrap-static is a ~3 MB document. It only changes when a gameweek rolls
# over, so it is cached far more aggressively than the (tiny) entry endpoint.
#
# The cache TTL is state-dependent. Between gameweeks nothing in the event list
# moves and 6 h is generous. While a gameweek is actually being played, three
# sensors (gameweek_state, gameweek_average_score, next_deadline) read straight
# out of this cache, so a 6 h TTL would freeze them for the whole of a Saturday.
BOOTSTRAP_MAX_AGE: Final = timedelta(hours=6)
BOOTSTRAP_LIVE_MAX_AGE: Final = timedelta(minutes=15)

# A gameweek counts as "live" from its deadline until FPL sets data_checked.
# The window caps that: if FPL never sets data_checked (a stall on their side),
# the gameweek stops counting as live after this and the TTL falls back to
# BOOTSTRAP_MAX_AGE. Without it a stalled flag would hold the short TTL for
# ever. A gameweek's fixtures span at most ~4 days.
GAMEWEEK_LIVE_WINDOW: Final = timedelta(days=5)

# After a failed bootstrap-static fetch, serve the stale cache for this long
# before trying again. Without it a failure leaves the cache stale, so every
# poll cycle re-attempts the ~3 MB download for the whole of an FPL outage.
# Kept below BOOTSTRAP_LIVE_MAX_AGE so a failure cannot more than double the
# worst-case staleness of the live-gameweek sensors.
BOOTSTRAP_RETRY_COOLDOWN: Final = timedelta(minutes=10)

# A 429 is FPL asking for less traffic, so it earns a longer cooldown than an
# ordinary failure. Only the ~3 MB endpoint gets one: entry/ is ~4 KB and the
# scan interval already floors it at 5 minutes.
#
# This one deliberately breaks the "stay under BOOTSTRAP_LIVE_MAX_AGE" rule
# above: during a live gameweek the three event-derived sensors can sit 30 min
# stale rather than 15. Backing off is the point — being rate-limited again
# would cost more than the extra staleness.
BOOTSTRAP_RATE_LIMIT_COOLDOWN: Final = timedelta(minutes=30)

MANUFACTURER: Final = "Premier League"
MODEL: Final = "FPL Manager"

ATTRIBUTION: Final = "Data provided by the Fantasy Premier League API"
