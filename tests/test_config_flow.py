"""Tests for the config flow."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
import voluptuous as vol
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from custom_components.fantasy_pl.api import FplConnectionError, FplManagerNotFound
from custom_components.fantasy_pl.config_flow import STEP_USER_SCHEMA
from custom_components.fantasy_pl.const import CONF_MANAGER_ID, DOMAIN

from .conftest import MANAGER_ID


@pytest.mark.parametrize("manager_id", [0, -1, "nope", ""])
def test_schema_rejects_invalid_manager_ids(manager_id: Any) -> None:
    """A typo should fail at the form, not one API round-trip later."""
    with pytest.raises(vol.Invalid):
        STEP_USER_SCHEMA({CONF_MANAGER_ID: manager_id})


def test_schema_coerces_a_numeric_string() -> None:
    """The UI submits text; the entry must store an int."""
    assert STEP_USER_SCHEMA({CONF_MANAGER_ID: "1234567"})[CONF_MANAGER_ID] == 1234567


async def test_user_flow_creates_entry(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A valid manager ID creates an entry titled with the team name."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: MANAGER_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Example Team"
    assert result["data"] == {CONF_MANAGER_ID: MANAGER_ID}
    assert result["result"].unique_id == str(MANAGER_ID)


async def test_user_flow_manager_not_found(
    hass: HomeAssistant, mock_client: AsyncMock, entry_payload: dict[str, Any]
) -> None:
    """A 404 shows an error and lets the user retry."""
    mock_client.async_get_entry.side_effect = FplManagerNotFound

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: 1}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "manager_not_found"}

    # Recovers on the next attempt.
    mock_client.async_get_entry.side_effect = None
    mock_client.async_get_entry.return_value = entry_payload
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: MANAGER_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_user_flow_cannot_connect(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A connection failure shows cannot_connect."""
    mock_client.async_get_entry.side_effect = FplConnectionError

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: MANAGER_ID}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_duplicate_manager_id_aborts(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry
) -> None:
    """The same manager cannot be added twice."""
    mock_config_entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: MANAGER_ID}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
