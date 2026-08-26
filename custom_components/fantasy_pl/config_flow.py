"""Config and options flow for the Fantasy Premier League integration."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)

from .api import FplClient, FplConnectionError, FplManagerNotFound
from .const import (
    CONF_MANAGER_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .coordinator import FplConfigEntry

# Range(min=1): FPL manager IDs are positive. Without it, 0 and negatives pass
# the coerce and fail one API round-trip later as "manager not found", which
# reads like an FPL problem rather than a typo.
STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_MANAGER_ID): vol.All(vol.Coerce(int), vol.Range(min=1))}
)


class FplConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the manager ID and verify it against the API."""
        errors: dict[str, str] = {}

        if user_input is not None:
            manager_id = int(user_input[CONF_MANAGER_ID])
            await self.async_set_unique_id(str(manager_id))
            self._abort_if_unique_id_configured()

            client = FplClient(async_get_clientsession(self.hass))
            try:
                entry = await client.async_get_entry(manager_id)
            except FplManagerNotFound:
                errors["base"] = "manager_not_found"
            except FplConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title=entry.get("name") or f"FPL {manager_id}",
                    data={CONF_MANAGER_ID: manager_id},
                )

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: FplConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return FplOptionsFlow()


class FplOptionsFlow(OptionsFlowWithReload):
    """Let the user change how often the API is polled."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES,
            int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60),
        )
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL_MINUTES, default=current
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MINUTES,
                        max=MAX_SCAN_INTERVAL_MINUTES,
                        step=5,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="min",
                    )
                )
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
