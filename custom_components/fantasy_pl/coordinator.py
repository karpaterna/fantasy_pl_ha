"""Data update coordinator for the Fantasy Premier League integration."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    FplBootstrap,
    FplClient,
    FplConnectionError,
    FplError,
    FplManagerNotFound,
    FplRateLimitedError,
)
from .const import (
    BOOTSTRAP_LIVE_MAX_AGE,
    BOOTSTRAP_MAX_AGE,
    BOOTSTRAP_RATE_LIMIT_COOLDOWN,
    BOOTSTRAP_RETRY_COOLDOWN,
    CONF_LEAGUES,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    GAMEWEEK_LIVE_WINDOW,
)

_LOGGER = logging.getLogger(__name__)

FplConfigEntry = ConfigEntry["FplDataUpdateCoordinator"]


def selected_league_ids(entry: FplConfigEntry) -> list[int]:
    """Return the league IDs the user picked, de-duplicated, in stored order.

    They are stored as strings because a ``SelectSelector`` round-trips its
    option values as strings; they are parsed back to ``int`` here so callers
    can compare them against the API's numeric ids without each doing its own
    conversion. Unparseable values are skipped rather than raising — options
    can be hand-edited, and one bad entry should not break setup.
    """
    raw = entry.options.get(CONF_LEAGUES)
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for value in raw:
        try:
            league_id = int(value)
        except (TypeError, ValueError):
            continue
        if league_id not in ids:
            ids.append(league_id)
    return ids


def gameweek_is_live(events: list[dict[str, Any]], now: datetime) -> bool:
    """Return True while a gameweek is being played.

    "Live" runs from the moment a gameweek's deadline passes until FPL sets
    ``data_checked`` on it — which spans the flag rollover, so this also covers
    the window where the deadline has passed but ``is_current``/``is_next``
    have not moved yet.

    ``GAMEWEEK_LIVE_WINDOW`` bounds it: a gameweek whose deadline passed longer
    ago than that is not considered live even if ``data_checked`` never arrived,
    so an FPL-side stall cannot pin the cache to the short TTL indefinitely.

    Pure function of its arguments — ``now`` is passed in rather than read, so
    this is testable without a Home Assistant instance.
    """
    for event in events:
        if event.get("data_checked"):
            continue
        deadline = dt_util.parse_datetime(event.get("deadline_time") or "")
        if deadline is None:
            continue
        if deadline <= now <= deadline + GAMEWEEK_LIVE_WINDOW:
            return True
    return False


def classic_leagues(entry: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the classic leagues carried by an ``entry/{id}/`` payload.

    The manager summary already lists every classic league the manager is in,
    together with their rank in it (``entry_rank``), the previous gameweek's
    rank (``entry_last_rank``) and the league size (``rank_count``). League
    sensors are built from this, so they cost no extra HTTP request —
    ``leagues-classic/{id}/standings/`` is only needed for the table of *other*
    managers, which is deliberately out of scope here.

    Defensive at every level: the API is unofficial, so a missing or
    wrong-typed key yields an empty list rather than an exception. Entries
    without an ``id`` are dropped because the id is the sensor's identity.

    A module-level function rather than only an ``FplData`` property: the
    config flow needs the same extraction from a raw payload, before any
    ``FplData`` exists.
    """
    leagues = entry.get("leagues")
    if not isinstance(leagues, dict):
        return []
    classic = leagues.get("classic")
    if not isinstance(classic, list):
        return []
    return [
        league
        for league in classic
        # `bool` is excluded, or an id of True would produce the unique_id
        # "…_league_True".
        if isinstance(league, dict)
        and isinstance(league.get("id"), int)
        and not isinstance(league.get("id"), bool)
    ]


@dataclass(slots=True)
class FplData:
    """Everything the entities need, in one immutable-ish snapshot."""

    entry: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    players: dict[int, str] = field(default_factory=dict)

    @property
    def classic_leagues(self) -> list[dict[str, Any]]:
        """Return the manager's classic leagues from the current snapshot."""
        return classic_leagues(self.entry)

    def league_by_id(self, league_id: int) -> dict[str, Any] | None:
        """Return one classic league, or None if the manager is not in it."""
        return next(
            (
                league
                for league in self.classic_leagues
                if league.get("id") == league_id
            ),
            None,
        )

    def event_by_flag(self, flag: str) -> dict[str, Any] | None:
        """Return the event marked with ``flag`` (is_current/is_next/...)."""
        return next((event for event in self.events if event.get(flag)), None)

    @property
    def current_event(self) -> dict[str, Any] | None:
        """Return the live gameweek, falling back to the manager's own view."""
        if event := self.event_by_flag("is_current"):
            return event
        current_id = self.entry.get("current_event")
        if current_id is None:
            return None
        return next((e for e in self.events if e.get("id") == current_id), None)

    @property
    def next_event(self) -> dict[str, Any] | None:
        """Return the upcoming gameweek."""
        return self.event_by_flag("is_next")


class FplBootstrapCache:
    """Instance-wide cache of the bootstrap-static slices.

    ``bootstrap-static/`` is game-wide: identical for every manager. One cache
    is therefore shared by every config entry rather than held per coordinator,
    so adding a second manager costs one extra ~4 KB ``entry/`` call per cycle
    instead of a second ~3 MB download every TTL.
    """

    def __init__(self) -> None:
        """Initialise an empty cache."""
        self.events: list[dict[str, Any]] = []
        self.players: dict[int, str] = {}
        self.fetched: datetime | None = None
        self.failed_at: datetime | None = None
        self.retry_after: timedelta = BOOTSTRAP_RETRY_COOLDOWN
        self._lock = asyncio.Lock()

    def is_stale(self, now: datetime) -> bool:
        """Decide whether bootstrap-static needs re-fetching.

        Every branch compares the cache age against a TTL, so the re-fetch rate
        is bounded by the shorter of the two TTLs no matter which signals fire.
        No branch may return True on a signal alone — "a deadline has passed"
        stays true until FPL moves the flags some minutes later, which would
        pull the ~3 MB document on every cycle in between.
        """
        if not self.events or self.fetched is None:
            # Nothing usable to serve, so a recent failure must not stop us
            # retrying — the cooldown below deliberately does not apply here.
            return True
        if self.failed_at is not None and now - self.failed_at < self.retry_after:
            return False
        age = now - self.fetched
        if gameweek_is_live(self.events, now):
            return age >= BOOTSTRAP_LIVE_MAX_AGE
        return age >= BOOTSTRAP_MAX_AGE

    async def async_get_bootstrap(self, client: FplClient) -> FplBootstrap:
        """Return the bootstrap slices, re-fetching only when the TTL says so.

        The lock makes a concurrent refresh from a second config entry wait and
        then observe the fresh cache, rather than issuing a second download.

        Raises ``FplError`` only when a fetch was actually attempted and failed;
        the caller decides whether that is fatal. While the cooldown is in
        effect the stale cache is returned instead, so an FPL-side outage costs
        one attempt per cooldown rather than one per poll cycle.
        """
        async with self._lock:
            now = dt_util.utcnow()
            if not self.is_stale(now):
                return FplBootstrap(self.events, self.players)
            _LOGGER.debug("Refreshing the bootstrap-static cache")
            try:
                bootstrap = await client.async_get_bootstrap()
            except FplError as err:
                # Stamped when the failure happened, not when the request
                # started: a request that runs to the 30 s timeout would
                # otherwise begin its cooldown half a minute in the past.
                self.failed_at = dt_util.utcnow()
                self.retry_after = (
                    BOOTSTRAP_RATE_LIMIT_COOLDOWN
                    if isinstance(err, FplRateLimitedError)
                    else BOOTSTRAP_RETRY_COOLDOWN
                )
                raise
            self.events = bootstrap.events
            self.players = bootstrap.players
            self.fetched = now
            self.failed_at = None
            self.retry_after = BOOTSTRAP_RETRY_COOLDOWN
            return bootstrap


class FplDataUpdateCoordinator(DataUpdateCoordinator[FplData]):
    """Poll the FPL API and hand a single snapshot to every entity."""

    config_entry: FplConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: FplConfigEntry,
        client: FplClient,
        manager_id: int,
        cache: FplBootstrapCache,
        update_interval: timedelta = DEFAULT_SCAN_INTERVAL,
    ) -> None:
        """Initialise the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {manager_id}",
            update_interval=update_interval,
        )
        self.client = client
        self.manager_id = manager_id
        self.cache = cache
        self._device_name: str | None = None

    def _async_update_device_name(self, entry: dict[str, Any]) -> None:
        """Push a team rename through to the device registry.

        ``DeviceInfo`` is read only when an entity is *added*, so a rename after
        setup would otherwise never reach the registry. ``async_get_or_create``
        matches on ``identifiers`` and updates ``name``; the user's own
        ``name_by_user`` override is a separate field and is left alone.

        The first refresh only seeds the remembered name — at that point the
        entities have not been added yet and their ``DeviceInfo`` will carry the
        current name anyway.
        """
        name = entry.get("name")
        if not name or name == self._device_name:
            return
        first_seen = self._device_name is None
        self._device_name = name
        if first_seen:
            return
        _LOGGER.debug("Team renamed to %s; updating device registry", name)
        dr.async_get(self.hass).async_get_or_create(
            config_entry_id=self.config_entry.entry_id,
            identifiers={(DOMAIN, str(self.manager_id))},
            name=name,
        )

    async def _async_update_data(self) -> FplData:
        """Fetch the manager summary, and the event list when it is stale.

        The two fetches are deliberately *not* gathered. ``entry/`` alone decides
        whether this update succeeded: 8 of the 11 sensors read only from it, so
        a ``bootstrap-static/`` failure must not take them down. When the event
        fetch fails the last good cache is served instead and the update still
        succeeds — only the three event-derived sensors go stale, and they hold
        their previous values rather than going unavailable.

        ``async_get_bootstrap`` performs no I/O while the cache is fresh, so on
        most cycles this is still a single request.
        """
        try:
            entry = await self.client.async_get_entry(self.manager_id)
        except FplManagerNotFound as err:
            # 404 can also happen during FPL maintenance windows, so this is
            # treated as a retryable failure rather than a dead config entry.
            raise UpdateFailed(f"Manager ID {self.manager_id} was not found") from err
        except FplConnectionError as err:
            raise UpdateFailed(str(err)) from err

        try:
            bootstrap = await self.cache.async_get_bootstrap(self.client)
        except FplError as err:
            bootstrap = FplBootstrap(self.cache.events, self.cache.players)
            if not bootstrap.events:
                # Nothing cached from a previous cycle, so there is no degraded
                # mode to fall back to — three sensors would have no source.
                raise UpdateFailed(f"No gameweek data available: {err}") from err
            _LOGGER.warning(
                "Serving cached gameweek data; bootstrap-static fetch failed: %s",
                err,
            )

        self._async_update_device_name(entry)
        return FplData(entry=entry, events=bootstrap.events, players=bootstrap.players)
