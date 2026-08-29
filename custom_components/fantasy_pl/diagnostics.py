"""Diagnostics support for the Fantasy Premier League integration."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import REDACTED
from homeassistant.core import HomeAssistant
from homeassistant.helpers.redact import async_redact_data

from .coordinator import FplConfigEntry

# The manager ID is redacted along with the name fields, not kept as a handy
# label: https://fantasy.premierleague.com/entry/{id}/ renders the manager's
# real first and last name publicly, so an ID in a diagnostics dump attached to
# a GitHub issue publishes the name just as surely as the name field would.
TO_REDACT = {
    "id",
    "player_first_name",
    "player_last_name",
    "player_region_id",
    "player_region_name",
    "player_region_iso_code_short",
    "player_region_iso_code_long",
    "leagues",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: FplConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "options": dict(entry.options),
        "manager_id": REDACTED,
        "entry": async_redact_data(data.entry if data else {}, TO_REDACT),
        "events": (data.events if data else [])[:5],
    }
