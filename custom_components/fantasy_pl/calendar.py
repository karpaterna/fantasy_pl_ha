"""Calendar platform for the Fantasy Premier League integration.

The 38 gameweek deadlines are already in the coordinator's snapshot, cached from
`bootstrap-static`. This platform only reshapes them, so it costs no HTTP
request of its own.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from .const import DEADLINE_EVENT_DURATION
from .coordinator import FplConfigEntry, FplDataUpdateCoordinator
from .entity import FplEntity

# The coordinator does all I/O; entities never talk to the API themselves.
PARALLEL_UPDATES = 0


def _description(event: dict[str, Any]) -> str | None:
    """Summarise a finished gameweek's scores, or return None.

    Only finished gameweeks have scores worth reporting: before the matches are
    played `average_entry_score` is 0, which would read as a real result.
    """
    if not event.get("finished"):
        return None
    parts = []
    average = event.get("average_entry_score")
    if isinstance(average, int) and not isinstance(average, bool):
        parts.append(f"Average score {average}")
    highest = event.get("highest_score")
    if isinstance(highest, int) and not isinstance(highest, bool):
        parts.append(f"highest {highest}")
    return ", ".join(parts) or None


def _deadline_events(events: list[dict[str, Any]]) -> list[CalendarEvent]:
    """Turn the cached gameweek list into calendar events, earliest first.

    A pure function of its argument so it can be tested without a Home
    Assistant instance. Events whose deadline will not parse are dropped rather
    than raising: the API is unofficial, and one malformed gameweek should not
    empty the calendar.
    """
    calendar_events: list[CalendarEvent] = []
    for event in events:
        start = dt_util.parse_datetime(event.get("deadline_time") or "")
        if start is None:
            continue
        name = event.get("name") or f"Gameweek {event.get('id')}"
        calendar_events.append(
            CalendarEvent(
                start=start,
                end=start + DEADLINE_EVENT_DURATION,
                summary=f"{name} deadline",
                description=_description(event),
                # Stable across restarts, and unique per gameweek rather than
                # per manager: the deadlines are game-wide.
                uid=f"fantasy_pl-gw-{event.get('id')}",
            )
        )
    calendar_events.sort(key=lambda event: event.start)
    return calendar_events


async def async_setup_entry(
    hass: HomeAssistant,
    entry: FplConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the deadline calendar from a config entry."""
    async_add_entities([FplDeadlineCalendar(entry.runtime_data)])


class FplDeadlineCalendar(FplEntity, CalendarEntity):
    """Every gameweek deadline of the season, as calendar events."""

    _attr_translation_key = "deadlines"

    def __init__(self, coordinator: FplDataUpdateCoordinator) -> None:
        """Initialise the calendar."""
        super().__init__(coordinator, "deadlines")

    @property
    def _events(self) -> list[CalendarEvent]:
        """Return the whole season, rebuilt from the current snapshot."""
        if self.coordinator.data is None:
            return []
        return _deadline_events(self.coordinator.data.events)

    @property
    def event(self) -> CalendarEvent | None:
        """Return the deadline in progress, or the next one.

        Keyed on the event's end rather than its start, so the entity reads "on"
        for the deadline hour instead of flipping to the following gameweek the
        moment the deadline lands.
        """
        now = dt_util.utcnow()
        return next((event for event in self._events if event.end > now), None)

    async def async_get_events(
        self,
        hass: HomeAssistant,
        start_date: datetime,
        end_date: datetime,
    ) -> list[CalendarEvent]:
        """Return the deadlines overlapping the requested range."""
        return [
            event
            for event in self._events
            if event.start < end_date and event.end > start_date
        ]
