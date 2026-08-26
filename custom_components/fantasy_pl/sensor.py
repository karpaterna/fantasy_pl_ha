"""Sensor platform for the Fantasy Premier League integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

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

from .coordinator import FplConfigEntry, FplData, FplDataUpdateCoordinator
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
    value = data.entry.get(key)
    # bool is a subclass of int, so isinstance(True, int) is True. Without the
    # second check a boolean field would be published as the state 1 or 0.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


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


@dataclass(frozen=True, kw_only=True)
class FplSensorEntityDescription(SensorEntityDescription):
    """Describes an FPL sensor and how to read its value."""

    value_fn: Callable[[FplData], StateType | datetime]


SENSORS: tuple[FplSensorEntityDescription, ...] = (
    FplSensorEntityDescription(
        key="overall_points",
        translation_key="overall_points",
        native_unit_of_measurement=POINTS,
        state_class=SensorStateClass.TOTAL_INCREASING,
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
        value_fn=lambda data: (data.current_event or {}).get("average_entry_score"),
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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FplConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the FPL sensors from a config entry."""
    coordinator = entry.runtime_data
    async_add_entities(FplSensor(coordinator, description) for description in SENSORS)


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
