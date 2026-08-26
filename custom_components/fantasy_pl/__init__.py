"""The Fantasy Premier League integration."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import FplClient
from .const import (
    CONF_MANAGER_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import FplConfigEntry, FplDataUpdateCoordinator, FplEventCache

PLATFORMS: list[Platform] = [Platform.SENSOR]


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
    # Options changes reload the entry via OptionsFlowWithReload; no manual
    # update listener is needed.
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: FplConfigEntry) -> bool:
    """Unload a config entry, dropping the shared cache with the last one."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and len(hass.config_entries.async_entries(DOMAIN)) <= 1:
        # async_entries() counts *registered* entries, loaded or not — the one
        # being unloaded is still listed, so <= 1 means it is the only entry
        # this integration has. With a second manager configured the cache is
        # simply never dropped, which is harmless: hass.data does not outlive
        # hass. A reload (options change) does drop it, costing one extra
        # bootstrap fetch on the way back up — rare enough not to be worth
        # reference-counting.
        hass.data.pop(DOMAIN, None)
    return unloaded
