"""Thin async client for the public Fantasy Premier League API.

Only public, unauthenticated endpoints are used:

* ``entry/{manager_id}/``  - manager summary (small, polled every cycle)
* ``bootstrap-static/``    - game-wide data incl. the gameweek (event) list
                             (~3 MB, cached; see ``BOOTSTRAP_MAX_AGE``)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

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


class FplManagerNotFound(FplError):
    """Raised when the configured manager (entry) ID does not exist."""


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
                # The response MUST be a context manager: the 404 branch below
                # raises before the body is read, and without `async with` that
                # connection is never released back to the pool. FPL 404s during
                # maintenance windows, so this is a recurring unattended path in
                # Home Assistant's *shared* aiohttp session.
                async with self._session.get(url, headers=HEADERS) as response:
                    if response.status == 404:
                        raise FplManagerNotFound(f"Not found: {url}")
                    response.raise_for_status()
                    # FPL serves JSON as text/html on some edges, so skip the
                    # content-type check rather than trusting the header.
                    return await response.json(content_type=None)
        except FplManagerNotFound:
            raise
        except ClientResponseError as err:
            raise FplConnectionError(f"HTTP {err.status} from {url}") from err
        except (ClientError, TimeoutError) as err:
            raise FplConnectionError(f"Error talking to {url}: {err}") from err
        except ValueError as err:  # malformed JSON
            raise FplConnectionError(f"Invalid JSON from {url}: {err}") from err

    async def async_get_entry(self, manager_id: int) -> dict[str, Any]:
        """Return the manager summary for ``manager_id``."""
        data = await self._get(f"entry/{manager_id}/")
        if not isinstance(data, dict) or "id" not in data:
            raise FplManagerNotFound(f"Unexpected payload for manager {manager_id}")
        return data

    async def async_get_events(self) -> list[dict[str, Any]]:
        """Return the gameweek (event) list from bootstrap-static.

        Only the ``events`` key is kept; the rest of the ~3 MB document
        (players, teams, stats) is discarded immediately so nothing large is
        retained between updates.
        """
        data = await self._get("bootstrap-static/")
        events = data.get("events") if isinstance(data, dict) else None
        if not isinstance(events, list):
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
        # which is outside the exception set the coordinator handles, so it
        # would surface as an unhandled coordinator error rather than a
        # retryable UpdateFailed.
        return [
            {k: event.get(k) for k in keep}
            for event in events
            if isinstance(event, dict)
        ]
