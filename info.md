# Fantasy Premier League

Track your FPL team in Home Assistant — points, ranks, team value, transfers,
the current gameweek and the next deadline, as real entities with long-term
statistics.

Public FPL API only. No login, no credentials.

## What you get

One device per manager, with 11 sensors:

- Overall points, overall rank
- Gameweek points, gameweek rank, gameweek average score
- Team value, bank (millions of pounds), total transfers
- Current gameweek, gameweek state, next deadline (timestamp)

## Setup

1. Restart Home Assistant after downloading.
2. **Settings → Devices & Services → Add Integration → Fantasy Premier League**
3. Enter your manager ID — the number in the URL of your Points tab:
   `fantasy.premierleague.com/entry/`**`1234567`**`/event/1`

Polling interval is configurable (5–360 minutes, default 30).

Not affiliated with the Premier League. The API is public but undocumented.
