"""Tests for the sensor value functions and the entity metadata they carry.

The value functions are pure functions of an FplData snapshot, so they need no
Home Assistant instance — the same reasoning as the TTL tests in
test_coordinator.py. The entity tests at the bottom do need one: state classes
decide how the recorder builds long-term statistics, and that is only true of a
real entity, not of the description it was built from.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.sensor import SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.fantasy_pl.api import FplConnectionError
from custom_components.fantasy_pl.const import DOMAIN
from custom_components.fantasy_pl.coordinator import FplData
from custom_components.fantasy_pl.sensor import (
    GW_STATE_FINAL,
    GW_STATE_IN_PROGRESS,
    GW_STATE_PROVISIONAL,
    GW_STATE_SCHEDULED,
    _entry_int,
    _event_average_score,
    _gameweek_state,
    _tenths,
)

from .conftest import MANAGER_ID, setup_entry


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5),
        (0, 0),
        (-3, -3),
        (True, None),
        (False, None),
        ("5", None),
        (5.0, None),
        (None, None),
    ],
    ids=[
        "int",
        "zero",
        "negative",
        "true",
        "false",
        "numeric_string",
        "float",
        "missing",
    ],
)
def test_entry_int(value: Any, expected: Any) -> None:
    """Bool is a subclass of int, so True must not publish as the state 1."""
    assert _entry_int(FplData(entry={"k": value}), "k") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1000, 100.0), (10, 1.0), (0, 0.0), (995, 99.5), (None, None), ("x", None)],
    ids=["100m", "1m", "zero", "rounding", "missing", "not_a_number"],
)
def test_tenths(value: Any, expected: Any) -> None:
    """FPL quotes money in tenths of a million; zero is a real value."""
    assert _tenths(value) == expected


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"data_checked": True, "finished": True}, GW_STATE_FINAL),
        ({"data_checked": False, "finished": True}, GW_STATE_PROVISIONAL),
        (
            {"deadline_time": "2099-01-01T00:00:00Z"},
            GW_STATE_SCHEDULED,
        ),
    ],
    ids=["final", "provisional", "scheduled"],
)
def test_gameweek_state(event: dict[str, Any], expected: str) -> None:
    """data_checked outranks finished, which outranks the deadline."""
    data = FplData(entry={"current_event": 1}, events=[{"id": 1, **event}])
    assert _gameweek_state(data) == expected


def test_gameweek_state_without_an_event() -> None:
    """No current gameweek means no state, not a crash."""
    assert _gameweek_state(FplData()) is None


@pytest.mark.parametrize(
    ("offset", "expected"),
    [
        (timedelta(seconds=-1), GW_STATE_SCHEDULED),
        (timedelta(0), GW_STATE_IN_PROGRESS),
        (timedelta(seconds=1), GW_STATE_IN_PROGRESS),
    ],
    ids=["before", "exactly_at", "after"],
)
def test_gameweek_state_at_the_deadline(offset: timedelta, expected: str) -> None:
    """The deadline itself belongs to the gameweek that has started.

    The comparison is `deadline > now`, so at the deadline to the second the
    team is locked and the gameweek is under way — not still scheduled.
    """
    deadline = datetime(2026, 8, 29, 17, 30, tzinfo=UTC)
    data = FplData(
        entry={"current_event": 1},
        events=[{"id": 1, "deadline_time": deadline.isoformat()}],
    )
    with patch(
        "custom_components.fantasy_pl.sensor.dt_util.utcnow",
        return_value=deadline + offset,
    ):
        assert _gameweek_state(data) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(50, 50), (0, 0), (True, None), ("50", None), (50.0, None), (None, None)],
    ids=["int", "zero", "bool", "numeric_string", "float", "missing"],
)
def test_event_average_score(value: Any, expected: Any) -> None:
    """The gameweek average is guarded like every other numeric sensor.

    Zero is a real value: the average is 0 for a gameweek that has not kicked
    off yet, so it must not be filtered out with the malformed types.
    """
    data = FplData(
        entry={"current_event": 1},
        events=[{"id": 1, "average_entry_score": value}],
    )
    assert _event_average_score(data) == expected


def test_event_average_score_without_an_event() -> None:
    """No current gameweek means no average, not a KeyError."""
    assert _event_average_score(FplData()) is None


# --- entity metadata (needs Home Assistant) ----------------------------------

# The state class of every sensor, asserted as a whole rather than one at a
# time: it is what the recorder reads to decide whether an entity gets
# long-term statistics and of which kind, and a wrong one is silent — nothing
# fails, the statistics just come out wrong months later.
EXPECTED_STATE_CLASSES = {
    "overall_points": SensorStateClass.TOTAL,
    "overall_rank": SensorStateClass.MEASUREMENT,
    "gameweek_points": SensorStateClass.MEASUREMENT,
    "gameweek_rank": SensorStateClass.MEASUREMENT,
    "team_value": SensorStateClass.MEASUREMENT,
    "bank": SensorStateClass.MEASUREMENT,
    "total_transfers": SensorStateClass.TOTAL_INCREASING,
    "gameweek_average_score": SensorStateClass.MEASUREMENT,
    "current_gameweek": None,
    "next_deadline": None,
    "gameweek_state": None,
}


async def test_state_classes_are_what_the_recorder_expects(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: ConfigEntry,
) -> None:
    """overall_points is TOTAL, and nothing else moved with it.

    TOTAL_INCREASING would be wrong for the season total, which falls on a -4
    transfer hit and on a bonus or dubious-goal correction; the recorder reads
    any decrease as a counter reset and adds the whole total to the sum again.
    total_transfers keeps TOTAL_INCREASING deliberately: it only ever rises
    within a season, and the reset to 0 at a new season is exactly what that
    state class is for.
    """
    await setup_entry(hass, mock_config_entry)

    for key, expected in EXPECTED_STATE_CLASSES.items():
        state = hass.states.get(f"sensor.example_team_{key}")
        assert state is not None, key
        assert state.attributes.get("state_class") == expected, key

    points = hass.states.get("sensor.example_team_overall_points")
    assert points.attributes["unit_of_measurement"] == "pts"
    # Money stays a bare number: "m" is HA's symbol for metres.
    team_value = hass.states.get("sensor.example_team_team_value")
    assert "unit_of_measurement" not in team_value.attributes


async def test_overall_points_accepts_a_decrease(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: ConfigEntry,
    entry_payload: dict[str, Any],
) -> None:
    """A -4 transfer hit lowers the season total; that is data, not a reset."""
    await setup_entry(hass, mock_config_entry)
    entity_id = "sensor.example_team_overall_points"
    assert hass.states.get(entity_id).state == "47"

    for points in (53, 49):
        mock_client.async_get_entry.return_value = {
            **entry_payload,
            "summary_overall_points": points,
        }
        await mock_config_entry.runtime_data.async_refresh()
        await hass.async_block_till_done()
        assert hass.states.get(entity_id).state == str(points)

    # The decrease must not have been reached by changing the state class.
    assert (
        hass.states.get(entity_id).attributes["state_class"] == SensorStateClass.TOTAL
    )


async def test_sensors_attach_to_the_manager_device(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: ConfigEntry,
) -> None:
    """Every sensor hangs off the one manager device."""
    await setup_entry(hass, mock_config_entry)

    entity = er.async_get(hass).async_get("sensor.example_team_overall_points")
    assert entity is not None
    assert entity.unique_id == f"{MANAGER_ID}_overall_points"

    device = dr.async_get(hass).async_get(entity.device_id)
    assert device.identifiers == {(DOMAIN, str(MANAGER_ID))}
    assert device.name == "Example Team"


async def test_sensors_go_unavailable_when_the_manager_fetch_fails(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry: ConfigEntry,
) -> None:
    """entry/ decides the update, so its failure takes the sensors with it."""
    await setup_entry(hass, mock_config_entry)
    entity_id = "sensor.example_team_overall_points"
    assert hass.states.get(entity_id).state == "47"

    mock_client.async_get_entry.side_effect = FplConnectionError("FPL is down")
    await mock_config_entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    assert hass.states.get(entity_id).state == "unavailable"
