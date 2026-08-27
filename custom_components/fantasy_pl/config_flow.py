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
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .api import FplClient, FplConnectionError, FplManagerNotFound
from .const import (
    CONF_LEAGUES,
    CONF_MANAGER_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    LEAGUE_TYPE_INVITATIONAL,
    MAX_SCAN_INTERVAL_MINUTES,
    MIN_SCAN_INTERVAL_MINUTES,
)
from .coordinator import FplConfigEntry, classic_leagues, selected_league_ids

# Range(min=1): FPL manager IDs are positive. Without it, 0 and negatives pass
# the coerce and fail one API round-trip later as "manager not found", which
# reads like an FPL problem rather than a typo.
STEP_USER_SCHEMA = vol.Schema(
    {vol.Required(CONF_MANAGER_ID): vol.All(vol.Coerce(int), vol.Range(min=1))}
)


def _league_label(league: dict[str, Any]) -> str:
    """Return the picker label for one league: its name, and how big it is."""
    name = league.get("name") or f"League {league.get('id')}"
    entries = league.get("rank_count")
    if isinstance(entries, int) and not isinstance(entries, bool):
        return f"{name} ({entries:,} managers)"
    return str(name)


def _league_selector(leagues: list[dict[str, Any]]) -> SelectSelector:
    """Build the multi-select of the manager's classic leagues.

    ``sort=False`` keeps FPL's own ordering, which puts the automatic leagues
    first and the invitational ones after; re-sorting alphabetically would mix
    them together and make the pre-ticked set look arbitrary.
    """
    return SelectSelector(
        SelectSelectorConfig(
            options=[
                SelectOptionDict(value=str(league["id"]), label=_league_label(league))
                for league in leagues
            ],
            multiple=True,
            mode=SelectSelectorMode.LIST,
            sort=False,
        )
    )


def _default_league_ids(leagues: list[dict[str, Any]]) -> list[str]:
    """Pre-tick the invitational leagues — the ones a person actually joined.

    FPL's automatic leagues (Overall, your club, your region) have millions of
    entries and are identical for every manager, so they are offered but left
    unticked.
    """
    return [
        str(league["id"])
        for league in leagues
        if league.get("league_type") == LEAGUE_TYPE_INVITATIONAL
    ]


def _league_schema(leagues: list[dict[str, Any]], selected: list[str]) -> vol.Schema:
    """Return the league-picker schema, pre-filled with ``selected``."""
    return vol.Schema(
        {vol.Optional(CONF_LEAGUES, default=selected): _league_selector(leagues)}
    )


class FplConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the UI setup flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the per-flow state carried between the two steps."""
        self._manager_id: int | None = None
        self._title: str = ""
        self._leagues: list[dict[str, Any]] = []

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
                self._manager_id = manager_id
                self._title = entry.get("name") or f"FPL {manager_id}"
                # The validation call already returned the league list, so the
                # picker costs no second request.
                self._leagues = classic_leagues(entry)
                if not self._leagues:
                    return self._async_create(_default_league_ids(self._leagues))
                return await self.async_step_leagues()

        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_leagues(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Let the user pick which mini-leagues get a rank sensor."""
        if user_input is not None:
            return self._async_create(user_input.get(CONF_LEAGUES, []))

        return self.async_show_form(
            step_id="leagues",
            data_schema=_league_schema(
                self._leagues, _default_league_ids(self._leagues)
            ),
        )

    def _async_create(self, leagues: list[str]) -> ConfigFlowResult:
        """Create the entry, storing the league choice in options.

        It goes in ``options`` rather than ``data`` because the options flow
        has to be able to change it later.
        """
        return self.async_create_entry(
            title=self._title,
            data={CONF_MANAGER_ID: self._manager_id},
            options={CONF_LEAGUES: leagues},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: FplConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return FplOptionsFlow()


class FplOptionsFlow(OptionsFlowWithReload):
    """Let the user change the poll interval and which leagues are tracked."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            # Merge rather than replace: async_create_entry overwrites the
            # whole options dict, so any key not on the form — the league
            # selection when the payload was unavailable, or anything a future
            # version adds — would be silently dropped.
            return self.async_create_entry(
                data={**self.config_entry.options, **user_input}
            )

        current = self.config_entry.options.get(
            CONF_SCAN_INTERVAL_MINUTES,
            int(DEFAULT_SCAN_INTERVAL.total_seconds() // 60),
        )
        fields: dict[Any, Any] = {
            vol.Required(CONF_SCAN_INTERVAL_MINUTES, default=current): NumberSelector(
                NumberSelectorConfig(
                    min=MIN_SCAN_INTERVAL_MINUTES,
                    max=MAX_SCAN_INTERVAL_MINUTES,
                    step=5,
                    mode=NumberSelectorMode.BOX,
                    unit_of_measurement="min",
                )
            )
        }

        # The league list comes from the coordinator's last payload, so opening
        # the options dialog costs no request. When the entry is not loaded
        # there is nothing to offer, so the field is omitted entirely — and the
        # merge above is what stops that omission wiping the stored choice.
        leagues = self._available_leagues()
        if leagues:
            selected = [
                str(league_id) for league_id in selected_league_ids(self.config_entry)
            ]
            fields[vol.Optional(CONF_LEAGUES, default=selected)] = _league_selector(
                leagues
            )

        return self.async_show_form(step_id="init", data_schema=vol.Schema(fields))

    def _available_leagues(self) -> list[dict[str, Any]]:
        """Return the classic leagues from the coordinator's last snapshot."""
        coordinator = getattr(self.config_entry, "runtime_data", None)
        data = getattr(coordinator, "data", None)
        if data is None:
            return []
        return data.classic_leagues
