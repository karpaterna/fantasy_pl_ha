"""Thin async client for the public Fantasy Premier League API.

Only public, unauthenticated endpoints are used:

* ``entry/{manager_id}/``  - manager summary (small, polled every cycle)
* ``bootstrap-static/``    - the gameweek (event) list and a player-name map
                             (~3 MB, cached; see ``BOOTSTRAP_MAX_AGE``)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, NamedTuple

from aiohttp import ClientError, ClientResponseError, ClientSession

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://fantasy.premierleague.com/api"
REQUEST_TIMEOUT = 30

# The FPL site rejects some default clients; a plain browser UA is enough.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; HomeAssistant-fantasy_pl/"
        "https://github.com/karpaterna/fantasy_pl_ha)"
    ),
    "Accept": "application/json",
}


class FplError(Exception):
    """Base error for this integration."""


class FplConnectionError(FplError):
    """Raised when the API could not be reached."""


class FplRateLimitedError(FplConnectionError):
    """Raised on HTTP 429.

    A subclass of ``FplConnectionError`` so every existing handler keeps
    treating it as a retryable transport failure; callers that want to back off
    further can single it out.
    """


class FplNotFoundError(FplError):
    """Raised when a path returns 404.

    Raised by the generic HTTP layer, which cannot know what was missing.
    ``async_get_entry`` translates it into ``FplManagerNotFound``; on any other
    endpoint a 404 stays generic rather than claiming a manager is gone.
    """


class FplManagerNotFound(FplError):
    """Raised when the configured manager (entry) ID does not exist."""


class FplBootstrap(NamedTuple):
    """The two slices of bootstrap-static this integration keeps.

    Both come out of one ~3 MB download. Everything else - teams, fixtures, the
    per-player stat blocks - is discarded immediately.
    """

    events: list[dict[str, Any]]
    players: dict[int, str]


def _player_names(elements: object) -> dict[int, str]:
    """Map element id to short name from the bootstrap `elements` array.

    Roughly 700 entries, ~30 KB, against the 3 MB thrown away. Anything
    malformed is skipped rather than raising: a broken `elements` array costs
    two sensors their names, while `events` - which eleven sensors need - is
    what decides whether the fetch succeeded.

    `bool` is excluded explicitly because it is a subclass of `int`, so True
    would otherwise register as player 1.
    """
    if not isinstance(elements, list):
        return {}
    names: dict[int, str] = {}
    for element in elements:
        if not isinstance(element, dict):
            continue
        player_id = element.get("id")
        web_name = element.get("web_name")
        if (
            isinstance(player_id, int)
            and not isinstance(player_id, bool)
            and isinstance(web_name, str)
            and web_name
        ):
            names[player_id] = web_name
    return names


class FplClient:
    """Minimal FPL API client built on Home Assistant's shared session."""

    def __init__(self, session: ClientSession) -> None:
        """Initialise the client with a shared aiohttp session."""
        self._session = session

    async def _get(self, path: str) -> Any:
        """GET a JSON document from the FPL API."""
        url = f"{API_BASE}/{path}"
        try:
            async with asyncio.timeout(REQUEST_TIMEOUT):
                # The response MUST be a context manager: the status branches
                # below raise before the body is read, and without `async with`
                # that connection is never returned to the pool — which is Home
                # Assistant's shared one.
                async with self._session.get(url, headers=HEADERS) as response:
                    if response.status == 404:
                        raise FplNotFoundError(f"Not found: {url}")
                    if response.status == 429:
                        raise FplRateLimitedError(f"Rate limited by {url}")
                    response.raise_for_status()
                    # FPL serves JSON as text/html on some edges, so skip the
                    # content-type check rather than trusting the header.
                    return await response.json(content_type=None)
        except FplError:
            # The status branches above raise our own errors; re-raise them
            # rather than letting the aiohttp handlers below reword them.
            raise
        except ClientResponseError as err:
            raise FplConnectionError(f"HTTP {err.status} from {url}") from err
        except (ClientError, TimeoutError) as err:
            raise FplConnectionError(f"Error talking to {url}: {err}") from err
        except ValueError as err:  # malformed JSON
            raise FplConnectionError(f"Invalid JSON from {url}: {err}") from err

    async def async_get_entry(self, manager_id: int) -> dict[str, Any]:
        """Return the manager summary for ``manager_id``.

        The payload's own ``id`` is checked against the one that was asked for,
        so a response for a different manager can never be published under this
        config entry's sensors.
        """
        try:
            data = await self._get(f"entry/{manager_id}/")
        except FplNotFoundError as err:
            raise FplManagerNotFound(f"No manager with ID {manager_id}") from err
        entry_id = data.get("id") if isinstance(data, dict) else None
        if (
            not isinstance(entry_id, int)
            or isinstance(entry_id, bool)
            or entry_id != manager_id
        ):
            raise FplManagerNotFound(f"Unexpected payload for manager {manager_id}")
        return data

    async def async_get_picks(self, manager_id: int, event_id: int) -> dict[str, Any]:
        """Return the manager's team for one gameweek.

        Public only once that gameweek's deadline has passed; before it FPL
        answers 404. The caller treats that as "not published yet" rather than
        an error, so no translation happens here.
        """
        data = await self._get(f"entry/{manager_id}/event/{event_id}/picks/")
        if not isinstance(data, dict):
            raise FplConnectionError(
                f"Unexpected picks payload for manager {manager_id}"
            )
        return data

    async def async_get_bootstrap(self) -> FplBootstrap:
        """Return the gameweek (event) list and player-name map from bootstrap-static.

        Only the ``events`` key and a name map pruned from ``elements`` are
        kept; the rest of the ~3 MB document (teams, fixtures, per-player stat
        blocks) is discarded immediately so nothing large is retained between
        updates.
        """
        data = await self._get("bootstrap-static/")
        events = data.get("events") if isinstance(data, dict) else None
        # An empty list is rejected, not accepted as a valid season with no
        # gameweeks: the cache treats "no events" as stale, so caching an empty
        # success would re-download the ~3 MB document on every poll cycle.
        if not isinstance(events, list) or not events:
            raise FplConnectionError("bootstrap-static returned no event list")
        keep = (
            "id",
            "name",
            "deadline_time",
            "average_entry_score",
            "highest_score",
            "finished",
            "data_checked",
            "is_previous",
            "is_current",
            "is_next",
        )
        # `isinstance` guard: a non-dict element would raise AttributeError,
        # which the coordinator does not handle, so it would surface as an
        # unhandled error rather than a retryable UpdateFailed.
        pruned = [
            {k: event.get(k) for k in keep}
            for event in events
            if isinstance(event, dict)
        ]
        if not pruned:
            raise FplConnectionError("bootstrap-static returned no usable events")
        if len(pruned) != len(events):
            _LOGGER.warning(
                "Dropped %d malformed entries from the bootstrap-static event list",
                len(events) - len(pruned),
            )
        players = _player_names(data.get("elements"))
        if not players:
            _LOGGER.warning(
                "bootstrap-static carried no usable player names; "
                "the captain sensor will show no name this cycle"
            )
        return FplBootstrap(pruned, players)
