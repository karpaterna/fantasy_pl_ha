# Fantasy Premier League for Home Assistant

[![hacs][hacs-badge]][hacs]
[![Validate][validate-badge]][validate]
[![Release][release-badge]][releases]

Track your Fantasy Premier League team in Home Assistant. Points, ranks, team
value, transfers, the current gameweek and the next deadline — as proper
entities on a device, with long-term statistics and no template YAML.

Uses only the **public, unauthenticated** FPL API. No login, no cookies, no
credentials stored anywhere.

## Entities

Setting up one manager creates one device with these sensors:

| Entity | Example | Notes |
| --- | --- | --- |
| Overall points | `1247 pts` | `state_class: total_increasing` |
| Overall rank | `2229871` | lower is better |
| Gameweek points | `56 pts` | current gameweek only |
| Gameweek rank | `1693842` | |
| Team value | `100.4` | millions of pounds — API tenths ÷ 10, no unit set |
| Bank | `0.5` | millions of pounds |
| Total transfers | `12` | season total at the last deadline |
| Current gameweek | `1` | |
| Next deadline | `2026-08-29T17:30:00+00:00` | `device_class: timestamp` |
| Gameweek average score | `45 pts` | all managers |
| Gameweek state | `in_progress` | `scheduled` / `in_progress` / `provisional` / `final` |

**Team value and bank** are in **millions of pounds** — a `100.4` reading means
£100.4m. No unit is set on purpose: `£` would claim the value is in pounds, and
`m` is Home Assistant's symbol for metres, so it would render as a distance. The
states stay plain numbers, so they chart and carry long-term statistics like any
other numeric sensor.

**Update timing.** Points and ranks come from a small endpoint polled every
cycle (30 min by default). Gameweek state, average score and the next deadline
come from a much larger document that is cached: refreshed every 15 minutes
while a gameweek is being played, every 6 hours otherwise. So a transition such
as `provisional` → `final` can lag the real event by up to 15 minutes, and
`scheduled` → `in_progress` by up to one polling interval. That is expected, not
a fault.

**If Fantasy Premier League is down.** A failure on the small endpoint marks the
sensors unavailable and Home Assistant retries on the next cycle. A failure on
the large one is absorbed: the last known gameweek data keeps being served, so
only gameweek state, average score and next deadline go stale — points, ranks,
team value, bank and transfers carry on as normal.

## Installation

### HACS (recommended)

1. HACS → **⋮** → **Custom repositories**
2. Repository `https://github.com/karpaterna/fantasy_pl_ha`, category
   **Integration**
3. Search for **Fantasy Premier League**, download, restart Home Assistant

### Manual

Copy `custom_components/fantasy_pl/` into your `config/custom_components/`
directory and restart Home Assistant.

## Configuration

**Settings → Devices & Services → Add Integration → Fantasy Premier League.**

You need your **manager ID**: log in at
[fantasy.premierleague.com](https://fantasy.premierleague.com/), open the
**Points** tab, and read it out of the URL:

```
https://fantasy.premierleague.com/entry/1234567/event/1
                                        ^^^^^^^
```

Add the integration once per team you want to track.

### Options

**Update interval** (5–360 minutes, default 30). Scores move roughly every
minute during matches, but this is an unofficial API on someone else's
servers — be considerate. The large `bootstrap-static` document is fetched at
most every 6 hours regardless of this setting.

## Automation examples

Remind yourself two hours before the deadline:

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

Notify when your gameweek is finalised:

```yaml
triggers:
  - trigger: state
    entity_id: sensor.gameweek_state
    to: final
actions:
  - action: notify.mobile_app_phone
    data:
      message: >
        GW{{ states('sensor.current_gameweek') }} final:
        {{ states('sensor.gameweek_points') }} pts
        (average {{ states('sensor.gameweek_average_score') }}).
```

## Troubleshooting

Enable debug logging:

```yaml
logger:
  logs:
    custom_components.fantasy_pl: debug
```

Then attach a diagnostics download to any issue: **Settings → Devices &
Services → Fantasy Premier League → ⋮ → Download diagnostics**. Player name
and region are redacted automatically.

## Contributing

Issues and pull requests welcome. Adding a sensor is usually a single entry in
the `SENSORS` table in `sensor.py` plus one line each in `strings.json` and
`icons.json` — the entity classes are generated from that table, so there is no
per-sensor boilerplate to write.

```bash
pip install -r requirements-test.txt
pytest          # unit tests
ruff check .    # lint
ruff format --check .
```

CI runs the same checks plus `hassfest` and the HACS validator on every push.

## Disclaimer

Not affiliated with, endorsed by, or connected to the Premier League or
Fantasy Premier League. The API used here is public but undocumented and can
change without notice.

[hacs]: https://github.com/hacs/integration
[hacs-badge]: https://img.shields.io/badge/HACS-Custom-41BDF5.svg
[validate]: https://github.com/karpaterna/fantasy_pl_ha/actions/workflows/validate.yml
[validate-badge]: https://github.com/karpaterna/fantasy_pl_ha/actions/workflows/validate.yml/badge.svg
[releases]: https://github.com/karpaterna/fantasy_pl_ha/releases
[release-badge]: https://img.shields.io/github/v/release/karpaterna/fantasy_pl_ha
