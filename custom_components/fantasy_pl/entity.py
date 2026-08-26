"""Base entity for the Fantasy Premier League integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, DOMAIN, MANUFACTURER, MODEL
from .coordinator import FplDataUpdateCoordinator


class FplEntity(CoordinatorEntity[FplDataUpdateCoordinator]):
    """Common device wiring for every FPL entity."""

    _attr_attribution = ATTRIBUTION
    _attr_has_entity_name = True

    def __init__(self, coordinator: FplDataUpdateCoordinator, key: str) -> None:
        """Initialise the entity and attach it to the manager device."""
        super().__init__(coordinator)
        manager_id = coordinator.manager_id
        self._attr_unique_id = f"{manager_id}_{key}"
        entry = coordinator.data.entry if coordinator.data else {}
        team_name = entry.get("name") or f"FPL manager {manager_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(manager_id))},
            entry_type=DeviceEntryType.SERVICE,
            manufacturer=MANUFACTURER,
            model=MODEL,
            name=team_name,
            serial_number=str(manager_id),
            # Deliberately NOT /event/{n}: DeviceInfo is read only when the
            # entity is *added*, so a gameweek-scoped URL would pin itself to
            # whatever gameweek was current at setup and rot from there.
            # /history is stable for the whole season.
            #
            # `name` has the same snapshot problem, which is why a later team
            # rename is pushed to the device registry from
            # FplDataUpdateCoordinator._async_update_device_name rather than
            # from here.
            configuration_url=(
                f"https://fantasy.premierleague.com/entry/{manager_id}/history"
            ),
        )
