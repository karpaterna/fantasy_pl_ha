"""The Fantasy Premier League integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FplClient
from .const import (
    CONF_MANAGER_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import (
    FplConfigEntry,
    FplDataUpdateCoordinator,
    FplEventCache,
    selected_league_ids,
)

PLATFORMS: list[Platform] = [Platform.CALENDAR, Platform.SENSOR]


DATA_EVENT_CACHE = "event_cache"


def _shared_cache(hass: HomeAssistant) -> FplEventCache:
    """Return the one event cache shared by every config entry.

    Stored under a key inside ``hass.data[DOMAIN]`` rather than *as*
    ``hass.data[DOMAIN]``, so a future platform can keep its own domain-level
    state without colliding with the cache.
    """
    domain_data = hass.data.setdefault(DOMAIN, {})
    cache = domain_data.get(DATA_EVENT_CACHE)
    if cache is None:
        cache = domain_data[DATA_EVENT_CACHE] = FplEventCache()
    return cache


def _async_purge_deselected_leagues(
    hass: HomeAssistant, entry: FplConfigEntry, manager_id: int
) -> None:
    """Drop league sensors for leagues the user has unticked.

    Deselecting a league only stops the entity being created; without this the
    old registry entry survives as a permanently unavailable sensor. Changing
    the options reloads the entry, so this runs on every selection change.

    Only ``{manager_id}_league_*`` unique_ids are considered — the eleven fixed
    sensors use keys like ``overall_points`` and can never match the prefix.
    """
    prefix = f"{manager_id}_league_"
    keep = {f"{prefix}{league_id}" for league_id in selected_league_ids(entry)}
    registry = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = reg_entry.unique_id
        if unique_id.startswith(prefix) and unique_id not in keep:
            registry.async_remove(reg_entry.entity_id)


async def async_setup_entry(hass: HomeAssistant, entry: FplConfigEntry) -> bool:
    """Set up Fantasy Premier League from a config entry."""
    manager_id = int(entry.data[CONF_MANAGER_ID])
    minutes = entry.options.get(CONF_SCAN_INTERVAL_MINUTES)
    # Compare to None, not truthiness: 0 is a value, not "unset". The options
    # flow's min=5 makes 0 unreachable through the UI, but a hand-edited entry
    # would otherwise get 30 minutes while the stored option said 0.
    interval = DEFAULT_SCAN_INTERVAL if minutes is None else timedelta(minutes=minutes)

    client = FplClient(async_get_clientsession(hass))
    coordinator = FplDataUpdateCoordinator(
        hass, entry, client, manager_id, _shared_cache(hass), update_interval=interval
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    # Before the platforms add entities, so a deselected league's sensor is
    # gone rather than briefly re-registered.
    _async_purge_deselected_leagues(hass, entry, manager_id)
    # Options changes reload the entry via OptionsFlowWithReload; no manual
    # update listener is needed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FplConfigEntry) -> bool:
    """Unload a config entry, dropping the shared cache with the last one."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and len(hass.config_entries.async_entries(DOMAIN)) <= 1:
        # async_entries() counts *registered* entries, loaded or not, and the
        # one being unloaded is still listed — so <= 1 means this is the only
        # entry. With a second manager configured the cache is simply never
        # dropped, which is harmless: hass.data does not outlive hass.
        hass.data.pop(DOMAIN, None)
    return unloaded
