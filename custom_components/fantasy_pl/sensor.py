"""Sensor platform for the Fantasy Premier League integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType
from homeassistant.util import dt as dt_util

from .coordinator import (
    FplConfigEntry,
    FplData,
    FplDataUpdateCoordinator,
    selected_league_ids,
)
from .entity import FplEntity

# The coordinator does all I/O; entities never talk to the API themselves.
PARALLEL_UPDATES = 0

POINTS = "pts"
# FPL money is quoted in tenths of a million pounds; _tenths() converts to
# millions, so team_value and bank publish a bare number ("100.0"). No unit is
# set deliberately: "m" is HA's symbol for metres and "£m" is not a unit HA
# knows, so either would misread. suggested_display_precision keeps the tenth.

GW_STATE_SCHEDULED = "scheduled"
GW_STATE_IN_PROGRESS = "in_progress"
GW_STATE_PROVISIONAL = "provisional"
GW_STATE_FINAL = "final"


def _tenths(value: object) -> float | None:
    """Convert an FPL money value (tenths of a million) to millions."""
    if value is None:
        return None
    try:
        return round(float(value) / 10, 1)
    except (TypeError, ValueError):
        return None


def _entry_int(data: FplData, key: str) -> StateType:
    """Read an integer field from the manager summary."""
    return _positive_int(data.entry.get(key))


def _next_deadline(data: FplData) -> datetime | None:
    """Return the deadline of the upcoming gameweek, as an aware datetime."""
    event = data.next_event
    if event is None:
        return None
    return dt_util.parse_datetime(event.get("deadline_time") or "")


def _gameweek_state(data: FplData) -> StateType:
    """Derive a coarse status for the current gameweek."""
    event = data.current_event
    if event is None:
        return None
    if event.get("data_checked"):
        return GW_STATE_FINAL
    if event.get("finished"):
        return GW_STATE_PROVISIONAL
    deadline = dt_util.parse_datetime(event.get("deadline_time") or "")
    if deadline is not None and deadline > dt_util.utcnow():
        return GW_STATE_SCHEDULED
    return GW_STATE_IN_PROGRESS


def _positive_int(value: object) -> int | None:
    """Return ``value`` when it is a real int, else None.

    ``bool`` is a subclass of ``int``, so a boolean is excluded explicitly —
    otherwise True would publish as the state 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _event_average_score(data: FplData) -> StateType:
    """Read the current gameweek's average score off the event list.

    Goes through the same guard as every other numeric sensor: the value comes
    from bootstrap-static, so a malformed payload must not reach a sensor that
    declares a numeric state class.
    """
    event = data.current_event
    if event is None:
        return None
    return _positive_int(event.get("average_entry_score"))


CHIP_NONE = "none"


def _picks_gameweek(data: FplData) -> int | None:
    """Return the gameweek the held picks belong to."""
    history = (data.picks or {}).get("entry_history")
    if not isinstance(history, dict):
        return None
    return _positive_int(history.get("event"))


def _captain_name(data: FplData) -> StateType:
    """Return the captain's short name for the picks currently held.

    Between a gameweek settling and the next deadline these are still the last
    gameweek's picks - the `gameweek` attribute says which - because four days
    of "unknown" answers a question the user can still ask.
    """
    pick = data.pick_with("is_captain")
    if pick is None:
        return None
    return data.player_name(pick.get("element"))


def _captain_attributes(data: FplData) -> dict[str, Any] | None:
    """Return the context around the captain that needs no entity of its own."""
    pick = data.pick_with("is_captain")
    if pick is None:
        return None
    vice = data.pick_with("is_vice_captain")
    return {
        "vice_captain": data.player_name(vice.get("element")) if vice else None,
        # 2 normally, 3 under the triple-captain chip, 0 when the captain did
        # not play and was substituted out automatically.
        "multiplier": _positive_int(pick.get("multiplier")),
        "player_id": _positive_int(pick.get("element")),
        "gameweek": _picks_gameweek(data),
    }


def _active_chip(data: FplData) -> StateType:
    """Return the chip played this gameweek, or "none".

    Deliberately NOT a SensorDeviceClass.ENUM. FPL has introduced new chips
    mid-era, and a state outside a declared `options` list makes Home Assistant
    log an error and drop it. A plain string absorbs a chip we have not heard
    of. `gameweek_state` can be an enum because its four values are ours.
    """
    if not isinstance(data.picks, dict):
        return None
    chip = data.picks.get("active_chip")
    return chip if isinstance(chip, str) and chip else CHIP_NONE


def _league_movement(league: dict[str, Any]) -> int | None:
    """Return places gained since the last gameweek; positive means moved up.

    FPL uses ``entry_last_rank == 0`` to mean "no previous rank" — a league
    joined this gameweek, or gameweek 1 — not "finished zeroth". Treating it as
    a real rank would report a movement of ``0 - entry_rank``, i.e. a plunge of
    the entire league, on every manager's first gameweek. So 0 yields None,
    which renders as "unknown" rather than as a number that looks meaningful.
    """
    rank = _positive_int(league.get("entry_rank"))
    last = _positive_int(league.get("entry_last_rank"))
    if rank is None or last is None or last == 0:
        return None
    return last - rank


@dataclass(frozen=True, kw_only=True)
class FplSensorEntityDescription(SensorEntityDescription):
    """Describes an FPL sensor and how to read its value."""

    value_fn: Callable[[FplData], StateType | datetime]
    attrs_fn: Callable[[FplData], dict[str, Any] | None] | None = None


SENSORS: tuple[FplSensorEntityDescription, ...] = (
    FplSensorEntityDescription(
        key="overall_points",
        translation_key="overall_points",
        native_unit_of_measurement=POINTS,
        # TOTAL, not TOTAL_INCREASING: the season total can fall. A -4 transfer
        # hit is applied the moment the gameweek starts, and bonus or
        # dubious-goal corrections can revise a score down afterwards.
        # TOTAL_INCREASING would read each of those as a counter reset and add
        # the whole running total to long-term statistics again.
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: _entry_int(data, "summary_overall_points"),
    ),
    FplSensorEntityDescription(
        key="overall_rank",
        translation_key="overall_rank",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _entry_int(data, "summary_overall_rank"),
    ),
    FplSensorEntityDescription(
        key="gameweek_points",
        translation_key="gameweek_points",
        native_unit_of_measurement=POINTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _entry_int(data, "summary_event_points"),
    ),
    FplSensorEntityDescription(
        key="gameweek_rank",
        translation_key="gameweek_rank",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: _entry_int(data, "summary_event_rank"),
    ),
    FplSensorEntityDescription(
        key="team_value",
        translation_key="team_value",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: _tenths(data.entry.get("last_deadline_value")),
    ),
    FplSensorEntityDescription(
        key="bank",
        translation_key="bank",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda data: _tenths(data.entry.get("last_deadline_bank")),
    ),
    FplSensorEntityDescription(
        key="total_transfers",
        translation_key="total_transfers",
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda data: _entry_int(data, "last_deadline_total_transfers"),
    ),
    FplSensorEntityDescription(
        key="current_gameweek",
        translation_key="current_gameweek",
        value_fn=lambda data: _entry_int(data, "current_event"),
    ),
    FplSensorEntityDescription(
        key="next_deadline",
        translation_key="next_deadline",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_deadline,
    ),
    FplSensorEntityDescription(
        key="gameweek_average_score",
        translation_key="gameweek_average_score",
        native_unit_of_measurement=POINTS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_event_average_score,
    ),
    FplSensorEntityDescription(
        key="gameweek_state",
        translation_key="gameweek_state",
        device_class=SensorDeviceClass.ENUM,
        options=[
            GW_STATE_SCHEDULED,
            GW_STATE_IN_PROGRESS,
            GW_STATE_PROVISIONAL,
            GW_STATE_FINAL,
        ],
        value_fn=_gameweek_state,
    ),
    FplSensorEntityDescription(
        key="captain",
        translation_key="captain",
        value_fn=_captain_name,
        attrs_fn=_captain_attributes,
    ),
    FplSensorEntityDescription(
        key="active_chip",
        translation_key="active_chip",
        value_fn=_active_chip,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FplConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the FPL sensors from a config entry."""
    coordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        FplSensor(coordinator, description) for description in SENSORS
    ]

    # One entity per *selected* league, not per league currently in the
    # payload: selection alone decides which entities exist, so leaving a
    # league on the FPL site marks its sensor unavailable rather than making it
    # vanish. Names come from the payload when it has them.
    data = coordinator.data
    for league_id in selected_league_ids(entry):
        league = data.league_by_id(league_id) if data else None
        name = (league or {}).get("name") or f"League {league_id}"
        entities.append(FplLeagueSensor(coordinator, league_id, name))

    async_add_entities(entities)


class FplSensor(FplEntity, SensorEntity):
    """A single value read from the FPL API."""

    entity_description: FplSensorEntityDescription

    def __init__(
        self,
        coordinator: FplDataUpdateCoordinator,
        description: FplSensorEntityDescription,
    ) -> None:
        """Initialise the sensor."""
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> StateType | datetime:
        """Return the current value, or None when the field is missing."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the description's attributes, when it declares any."""
        attrs_fn = self.entity_description.attrs_fn
        if attrs_fn is None or self.coordinator.data is None:
            return None
        return attrs_fn(self.coordinator.data)


class FplLeagueSensor(FplEntity, SensorEntity):
    """The manager's rank in one classic mini-league.

    Built from the ``leagues.classic[]`` array that already arrives with every
    ``entry/{id}/`` poll, so a league sensor adds no HTTP request of its own.

    The name is not a ``translation_key``: it is the league's own name, which
    comes from the API and cannot be translated. It is read from the current
    snapshot on every state write, so renaming a league on the FPL site is
    picked up on the next poll. The ``unique_id`` stays keyed to the league id,
    so a rename moves the label without touching the entity's identity or its
    recorded history.
    """

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:trophy-outline"

    def __init__(
        self,
        coordinator: FplDataUpdateCoordinator,
        league_id: int,
        league_name: str,
    ) -> None:
        """Initialise the sensor for one league."""
        super().__init__(coordinator, f"league_{league_id}")
        self._league_id = league_id
        self._fallback_name = league_name

    @property
    def name(self) -> str:
        """Return the league's current name, following a rename on the site."""
        league = self._league
        name = league.get("name") if league else None
        if not isinstance(name, str) or not name:
            name = self._fallback_name
        return f"{name} rank"

    @property
    def _league(self) -> dict[str, Any] | None:
        """Return this league's slice of the current snapshot."""
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.league_by_id(self._league_id)

    @property
    def available(self) -> bool:
        """Report unavailable when the manager is no longer in this league.

        The league simply stops appearing in the payload; that is a real
        "no value" rather than a zero, so the entity goes unavailable instead
        of publishing a misleading rank.
        """
        return super().available and self._league is not None

    @property
    def native_value(self) -> StateType:
        """Return the manager's rank in this league."""
        league = self._league
        if league is None:
            return None
        return _positive_int(league.get("entry_rank"))

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return the league context that does not deserve its own entity."""
        league = self._league
        if league is None:
            return None
        # Guarded like the numeric fields: the API is unofficial, and an
        # attribute of "false" or 0 would read as a real answer in a template.
        is_admin = league.get("entry_can_admin")
        return {
            "league_id": self._league_id,
            "league_name": league.get("name"),
            "entries": _positive_int(league.get("rank_count")),
            "previous_rank": _positive_int(league.get("entry_last_rank")) or None,
            "movement": _league_movement(league),
            "percentile": _positive_int(league.get("entry_percentile_rank")),
            "is_admin": is_admin if isinstance(is_admin, bool) else None,
        }
