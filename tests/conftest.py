"""Shared fixtures for the Fantasy Premier League tests."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fantasy_pl.const import CONF_MANAGER_ID, DOMAIN

MANAGER_ID = 1234567


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> None:
    """Enable loading of the custom integration in every test."""
    return


@pytest.fixture
def entry_payload() -> dict[str, Any]:
    """Return a trimmed `entry/{id}/` response."""
    return {
        "id": MANAGER_ID,
        "name": "Example Team",
        "player_first_name": "Test",
        "player_last_name": "Manager",
        "summary_overall_points": 47,
        "summary_overall_rank": 5273665,
        "summary_event_points": 47,
        "summary_event_rank": 5273665,
        "last_deadline_value": 1000,
        "last_deadline_bank": 10,
        "last_deadline_total_transfers": 0,
        "current_event": 1,
    }


@pytest.fixture
def leagues_payload() -> list[dict[str, Any]]:
    """Return a `leagues.classic[]` array: two invitational, one automatic.

    Field names and the `entry_last_rank == 0` convention mirror the real API.
    """
    return [
        {
            "id": 14,
            "name": "Automatic League",
            "league_type": "s",
            "scoring": "c",
            "entry_rank": 842731,
            "entry_last_rank": 840112,
            "rank_count": 1089086,
            "entry_percentile_rank": 80,
            "entry_can_admin": False,
        },
        {
            "id": 555001,
            "name": "Work League",
            "league_type": "x",
            "scoring": "c",
            "entry_rank": 4,
            "entry_last_rank": 7,
            "rank_count": 21,
            "entry_percentile_rank": 20,
            "entry_can_admin": True,
        },
        {
            "id": 555002,
            "name": "Friends League",
            "league_type": "x",
            "scoring": "c",
            "entry_rank": 2,
            # 0 means "no previous rank" (joined this gameweek), not "zeroth".
            "entry_last_rank": 0,
            "rank_count": 8,
            "entry_percentile_rank": 25,
            "entry_can_admin": False,
        },
    ]


@pytest.fixture
def entry_payload_with_leagues(
    entry_payload: dict[str, Any], leagues_payload: list[dict[str, Any]]
) -> dict[str, Any]:
    """Return the manager summary with its classic leagues attached."""
    return {**entry_payload, "leagues": {"classic": leagues_payload, "h2h": []}}


@pytest.fixture
def events_payload() -> list[dict[str, Any]]:
    """Return a trimmed, already-pruned `bootstrap-static/` event list."""
    return [
        {
            "id": 1,
            "name": "Gameweek 1",
            "deadline_time": "2026-08-14T17:30:00Z",
            "average_entry_score": 50,
            "highest_score": 102,
            "finished": True,
            "data_checked": True,
            "is_previous": True,
            "is_current": False,
            "is_next": False,
        },
        {
            "id": 2,
            "name": "Gameweek 2",
            "deadline_time": "2026-08-28T17:30:00Z",
            "average_entry_score": 0,
            "finished": False,
            "data_checked": False,
            "is_previous": False,
            "is_current": True,
            "is_next": False,
        },
        {
            "id": 3,
            "name": "Gameweek 3",
            "deadline_time": "2026-09-05T17:30:00Z",
            "average_entry_score": 0,
            "finished": False,
            "data_checked": False,
            "is_previous": False,
            "is_current": False,
            "is_next": True,
        },
    ]


@pytest.fixture
def mock_client(
    entry_payload: dict[str, Any], events_payload: list[dict[str, Any]]
) -> Generator[AsyncMock]:
    """Patch FplClient everywhere it is constructed."""
    client = AsyncMock()
    client.async_get_entry.return_value = entry_payload
    client.async_get_events.return_value = events_payload
    with (
        patch("custom_components.fantasy_pl.FplClient", return_value=client),
        patch(
            "custom_components.fantasy_pl.config_flow.FplClient",
            return_value=client,
        ),
    ):
        yield client


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    """Return a config entry for manager 1234567."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        unique_id=str(MANAGER_ID),
    )


async def setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Add and set up a config entry."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
