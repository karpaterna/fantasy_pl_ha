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

Plus an optional rank sensor for each classic mini-league you pick, with your
movement, the league size and your percentile. These cost no extra API calls —
the data already arrives with every update.

## Setup

1. Restart Home Assistant after downloading.
2. **Settings → Devices & Services → Add Integration → Fantasy Premier League**
3. Enter your manager ID — the number in the URL of your Points tab:
   `fantasy.premierleague.com/entry/`**`1234567`**`/event/1`
4. Pick the mini-leagues you want tracked. Changeable later under Configure.

Polling interval is configurable (5–360 minutes, default 30).

Built with substantial AI assistance (Claude). Every change is human-reviewed,
and CI runs hassfest, the HACS validator, ruff and the full test suite on every
push. Issues and corrections are welcome.

Not affiliated with the Premier League. The API is public but undocumented.
