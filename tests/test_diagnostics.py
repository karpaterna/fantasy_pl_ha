"""Tests for the diagnostics dump.

The manager ID is identifying information, not a convenient label:
https://fantasy.premierleague.com/entry/{id}/ renders the manager's real first
and last name publicly, so an ID left in a dump attached to a GitHub issue
publishes the name just as surely as the name field would.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

from homeassistant.components.diagnostics import REDACTED
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import MANAGER_ID, setup_entry


async def test_diagnostics_redact_the_manager_identity(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: ConfigEntry,
) -> None:
    """Neither the ID nor the name fields may reach a public issue tracker."""
    await setup_entry(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["manager_id"] == REDACTED
    assert result["entry"]["id"] == REDACTED
    assert result["entry"]["player_first_name"] == REDACTED
    assert result["entry"]["player_last_name"] == REDACTED
    # The strong assertion: the ID must not survive anywhere in the document,
    # however the shape of the payload changes.
    assert str(MANAGER_ID) not in json.dumps(result)


async def test_diagnostics_keep_what_makes_them_useful(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    mock_client: AsyncMock,
    mock_config_entry: ConfigEntry,
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """Redaction must not empty the dump out.

    The team name stays: it is user-chosen, it is the label that makes a dump
    readable, and it is not the manager's own name. The whole `leagues` key
    goes, which is why league sensors cannot be debugged from a dump.
    """
    mock_client.async_get_entry.return_value = entry_payload_with_leagues
    await setup_entry(hass, mock_config_entry)

    result = await get_diagnostics_for_config_entry(
        hass, hass_client, mock_config_entry
    )

    assert result["entry"]["name"] == "Example Team"
    assert result["entry"]["summary_overall_points"] == 47
    assert result["entry"]["leagues"] == REDACTED
    assert [event["id"] for event in result["events"]] == [1, 2, 3]
