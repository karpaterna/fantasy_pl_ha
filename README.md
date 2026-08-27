# Fantasy Premier League for Home Assistant

[![hacs][hacs-badge]][hacs]
[![Validate][validate-badge]][validate]
[![Release][release-badge]][releases]

Track your FPL team in Home Assistant as proper entities on a device, with
long-term statistics and no template YAML. Uses only the public, unauthenticated
FPL API — no login, no cookies, no credentials stored.

## Entities

One manager creates one device with 11 sensors:

| Entity | Example | Notes |
| --- | --- | --- |
| Overall points | `1247 pts` | `state_class: total_increasing` |
| Overall rank | `2229871` | lower is better |
| Gameweek points | `56 pts` | current gameweek only |
| Gameweek rank | `1693842` | |
| Team value | `100.4` | millions of pounds (`100.4` = £100.4m) |
| Bank | `0.5` | millions of pounds |
| Total transfers | `12` | season total at the last deadline |
| Current gameweek | `1` | |
| Next deadline | `2026-08-29T17:30:00+00:00` | `device_class: timestamp` |
| Gameweek average score | `45 pts` | all managers |
| Gameweek state | `in_progress` | `scheduled` / `in_progress` / `provisional` / `final` |

Money sensors carry no unit deliberately — `£` would imply pounds rather than
millions, and `m` is Home Assistant's symbol for metres.

### Mini-league sensors

Optionally, one extra sensor per classic league you pick, showing your rank in
it:

| Entity | Example | Attributes |
| --- | --- | --- |
| `<League name> rank` | `4` | `league_id`, `league_name`, `entries`, `previous_rank`, `movement`, `percentile`, `is_admin` |

`movement` is places gained since the last gameweek — positive means you moved
up. It is unknown rather than a number when there is no previous rank to
compare against, which is the case in gameweek 1 and in any league you joined
this week.

**These cost no extra API calls.** Your rank in every classic league already
arrives inside the manager summary that is polled each cycle, so tracking ten
leagues is exactly as cheap as tracking none. If you leave a league, its sensor
goes unavailable rather than reporting a stale rank.

**Update timing.** Points and ranks are polled every cycle (30 min by default).
Gameweek state, average score and next deadline come from a large cached
document, refreshed every 15 minutes during a live gameweek and every 6 hours
otherwise — so `provisional` → `final` can lag by up to 15 minutes. If FPL is
unreachable the cached gameweek data keeps being served, so only those three
sensors go stale.

## Installation

**HACS** — ⋮ → Custom repositories → `https://github.com/karpaterna/fantasy_pl_ha`,
category **Integration** → search for *Fantasy Premier League*, download, restart.

**Manual** — copy `custom_components/fantasy_pl/` into `config/custom_components/`
and restart.

## Configuration

**Settings → Devices & Services → Add Integration → Fantasy Premier League.**

You need your manager ID — the number in the URL of your Points tab:

```
https://fantasy.premierleague.com/entry/1234567/event/1
                                        ^^^^^^^
```

Setup then offers your classic leagues, with the invitational ones pre-selected
— FPL's automatic leagues (Overall, your club, your region) are listed but left
unticked. Change the selection any time under **Configure**; unticking a league
removes its sensor and its history.

Add the integration once per team. **Update interval** is configurable
(5–360 minutes, default 30); the large document is capped at 6 hours regardless.

## Example automation

```yaml
triggers:
  - trigger: template
    value_template: >
      {{ 0 < (as_timestamp(states('sensor.next_deadline')) - as_timestamp(now())) < 7200 }}
actions:
  - action: notify.mobile_app_phone
    data:
      title: FPL deadline
      message: >
        Deadline in {{ relative_time(states('sensor.next_deadline') | as_datetime) }}.
```

## Troubleshooting

```yaml
logger:
  logs:
    custom_components.fantasy_pl: debug
```

Attach a diagnostics download to any issue: **Settings → Devices & Services →
Fantasy Premier League → ⋮ → Download diagnostics**. Player name and region are
redacted automatically.

## Contributing

Issues and pull requests welcome. Adding a sensor is one entry in the `SENSORS`
table in `sensor.py` plus a line each in `strings.json` and `icons.json`.

```bash
pip install -r requirements-test.txt
pytest
ruff check . && ruff format --check .
```

## AI disclosure

This integration was written with substantial AI assistance (Claude), including
the initial implementation, the test suite and two review passes. Every change
is human-reviewed, and CI runs hassfest, the HACS validator, ruff and the full
pytest suite on every push. Issues and corrections are welcome — if something
here looks wrong, it may well be.

## Disclaimer

Not affiliated with or endorsed by the Premier League or Fantasy Premier League.
The API used here is public but undocumented and can change without notice.

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate]: https://github.com/karpaterna/fantasy_pl_ha/actions/workflows/validate.yml
[validate-badge]: https://github.com/karpaterna/fantasy_pl_ha/actions/workflows/validate.yml/badge.svg
[releases]: https://github.com/karpaterna/fantasy_pl_ha/releases
[release-badge]: https://img.shields.io/github/v/release/karpaterna/fantasy_pl_ha
