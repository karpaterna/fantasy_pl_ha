"""Tests for the deadline calendar platform.

`_deadline_events` is a pure function of the cached event list, so most of this
file needs no Home Assistant instance — the same split the coordinator tests use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from custom_components.fantasy_pl.calendar import _deadline_events
from custom_components.fantasy_pl.const import DOMAIN

from .conftest import MANAGER_ID, setup_entry


def test_every_gameweek_becomes_one_event(
    events_payload: list[dict[str, Any]],
) -> None:
    events = _deadline_events(events_payload)

    assert [event.summary for event in events] == [
        "Gameweek 1 deadline",
        "Gameweek 2 deadline",
        "Gameweek 3 deadline",
    ]
    assert events[0].start == datetime(2026, 8, 14, 17, 30, tzinfo=UTC)
    assert events[0].end == datetime(2026, 8, 14, 18, 30, tzinfo=UTC)
    assert events[0].uid == "fantasy_pl-gw-1"


def test_only_finished_gameweeks_describe_their_scores(
    events_payload: list[dict[str, Any]],
) -> None:
    """Gameweek 1 is finished; 2 and 3 have no scores to report yet."""
    events = _deadline_events(events_payload)

    assert events[0].description == "Average score 50, highest 102"
    assert events[1].description is None


def test_a_finished_gameweek_with_only_an_average_still_describes_it() -> None:
    payload = [
        {
            "id": 4,
            "name": "Gameweek 4",
            "deadline_time": "2026-09-12T17:30:00Z",
            "average_entry_score": 61,
            "finished": True,
        }
    ]

    assert _deadline_events(payload)[0].description == "Average score 61"


def test_a_finished_gameweek_with_no_scores_has_no_description() -> None:
    payload = [
        {
            "id": 4,
            "name": "Gameweek 4",
            "deadline_time": "2026-09-12T17:30:00Z",
            "average_entry_score": None,
            "highest_score": None,
            "finished": True,
        }
    ]

    assert _deadline_events(payload)[0].description is None


def test_events_without_a_usable_deadline_are_skipped() -> None:
    assert _deadline_events([{"id": 9, "deadline_time": "not-a-date"}]) == []
    assert _deadline_events([{"id": 9}]) == []
    assert _deadline_events([]) == []


def test_events_come_back_in_chronological_order() -> None:
    payload = [
        {"id": 2, "name": "Gameweek 2", "deadline_time": "2026-08-28T17:30:00Z"},
        {"id": 1, "name": "Gameweek 1", "deadline_time": "2026-08-14T17:30:00Z"},
    ]

    assert [event.uid for event in _deadline_events(payload)] == [
        "fantasy_pl-gw-1",
        "fantasy_pl-gw-2",
    ]


def test_a_nameless_gameweek_falls_back_to_its_id() -> None:
    payload = [{"id": 7, "deadline_time": "2026-10-03T17:30:00Z"}]

    assert _deadline_events(payload)[0].summary == "Gameweek 7 deadline"


async def test_the_calendar_entity_is_registered(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_client: AsyncMock,
) -> None:
    await setup_entry(hass, mock_config_entry)

    entity_id = er.async_get(hass).async_get_entity_id(
        "calendar", DOMAIN, f"{MANAGER_ID}_deadlines"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state is not None
    # "on" while a deadline hour is in progress, "off" otherwise. Either is a
    # healthy calendar; "unavailable" would not be.
    assert state.state in ("on", "off")


async def test_get_events_returns_the_deadlines_in_range(
    hass: HomeAssistant,
    mock_config_entry: ConfigEntry,
    mock_client: AsyncMock,
) -> None:
    """Drive the entity through the public calendar.get_events service.

    The fixture deadlines are fixed dates, so this does not depend on the clock.
    """
    await setup_entry(hass, mock_config_entry)
    entity_id = er.async_get(hass).async_get_entity_id(
        "calendar", DOMAIN, f"{MANAGER_ID}_deadlines"
    )

    response = await hass.services.async_call(
        "calendar",
        "get_events",
        {
            "entity_id": entity_id,
            "start_date_time": "2026-08-20 00:00:00",
            "end_date_time": "2026-08-31 00:00:00",
        },
        blocking=True,
        return_response=True,
    )

    events = response[entity_id]["events"]
    assert [event["summary"] for event in events] == ["Gameweek 2 deadline"]
