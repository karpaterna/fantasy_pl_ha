"""Tests for the FPL API client.

The first half covers the parsing and pruning that runs on whatever
`bootstrap-static/` happens to return, against a canned `_get`. The second half
drives the real `async with session.get(...)` through a mocked HTTP transport,
because the status handling, the timeout and the JSON decode all live in that
block and none of them are reachable with `_get` stubbed out.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import ClientError
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.fantasy_pl.api import (
    API_BASE,
    FplClient,
    FplConnectionError,
    FplManagerNotFound,
    FplNotFoundError,
)

from .conftest import MANAGER_ID

ENTRY_URL = f"{API_BASE}/entry/{MANAGER_ID}/"
BOOTSTRAP_URL = f"{API_BASE}/bootstrap-static/"


def _client() -> FplClient:
    """Return a client whose session is never touched."""
    return FplClient(session=None)  # type: ignore[arg-type]


async def _with_payload(payload: Any) -> Any:
    """Run async_get_bootstrap against a canned _get response, returning events."""
    with patch.object(FplClient, "_get", AsyncMock(return_value=payload)):
        return (await _client().async_get_bootstrap()).events


async def test_get_events_prunes_to_the_keep_list() -> None:
    """The ~3 MB document must not survive past this call."""
    events = await _with_payload(
        {
            "events": [{"id": 1, "name": "Gameweek 1", "chip_plays": [1] * 1000}],
            "elements": [{"huge": True}] * 1000,
        }
    )
    assert len(events) == 1
    assert events[0]["id"] == 1
    assert "chip_plays" not in events[0]


async def test_get_events_skips_non_dict_entries() -> None:
    """A malformed element must not raise AttributeError past the client.

    A non-dict element raises AttributeError, which the coordinator does not
    handle: it would surface as an unhandled error instead of a retryable
    UpdateFailed.
    """
    events = await _with_payload({"events": [{"id": 1}, "junk", None, 42, {"id": 2}]})
    assert [event["id"] for event in events] == [1, 2]


@pytest.mark.parametrize(
    "payload",
    [{}, {"events": None}, {"events": "nope"}, [], {"events": []}],
    ids=["no_key", "null", "not_a_list", "not_a_dict", "empty_list"],
)
async def test_get_events_rejects_a_missing_event_list(payload: Any) -> None:
    """A payload without a usable event list is a connection-level failure.

    `empty_list` is the efficiency case: the cache treats "no events" as stale,
    so caching an empty list as a success would re-download the ~3 MB document
    on every poll cycle for as long as FPL kept returning it.
    """
    with pytest.raises(FplConnectionError):
        await _with_payload(payload)


async def test_get_events_rejects_an_all_malformed_list() -> None:
    """Entries that all prune away are a corrupt payload, not an empty season.

    Individual junk entries are dropped and logged, but a list that yields
    nothing usable must not be cached as a fresh success.
    """
    with pytest.raises(FplConnectionError):
        await _with_payload({"events": ["junk", None, 42]})


@pytest.mark.parametrize(
    "payload",
    [{}, {"name": "no id"}, [], "nope"],
    ids=["empty", "no_id", "list", "string"],
)
async def test_get_entry_rejects_an_unexpected_payload(payload: Any) -> None:
    """An entry payload without an id is treated as "no such manager"."""
    with (
        patch.object(FplClient, "_get", AsyncMock(return_value=payload)),
        pytest.raises(FplManagerNotFound),
    ):
        await _client().async_get_entry(1234567)


# --- the HTTP boundary -------------------------------------------------------


def _http_client(hass: HomeAssistant) -> FplClient:
    """Return a client on Home Assistant's session, as the integration builds it."""
    return FplClient(async_get_clientsession(hass))


async def test_get_entry_over_http(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The happy path, end to end through the transport."""
    aioclient_mock.get(ENTRY_URL, json={"id": MANAGER_ID, "name": "Example Team"})

    data = await _http_client(hass).async_get_entry(MANAGER_ID)

    assert data["name"] == "Example Team"
    assert len(aioclient_mock.mock_calls) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"id": 7654321, "name": "Someone Else"},
        {"id": str(MANAGER_ID)},
        {"id": True},
        {"id": None},
        {"name": "no id"},
    ],
    ids=["different_manager", "string_id", "bool_id", "null_id", "no_id"],
)
async def test_get_entry_rejects_a_foreign_or_malformed_identity(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, payload: dict[str, Any]
) -> None:
    """A payload must prove it is the manager that was asked for.

    Without the equality check, a response for a different manager would be
    published under this config entry's sensors as if it were the user's own.
    """
    aioclient_mock.get(ENTRY_URL, json=payload)

    with pytest.raises(FplManagerNotFound):
        await _http_client(hass).async_get_entry(MANAGER_ID)


async def test_get_entry_translates_404(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 404 on the manager endpoint is the one case that means "no manager"."""
    aioclient_mock.get(ENTRY_URL, status=404)

    with pytest.raises(FplManagerNotFound):
        await _http_client(hass).async_get_entry(MANAGER_ID)


async def test_bootstrap_404_stays_generic(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The HTTP layer must not claim a missing manager for every 404."""
    aioclient_mock.get(BOOTSTRAP_URL, status=404)

    with pytest.raises(FplNotFoundError) as caught:
        await _http_client(hass).async_get_bootstrap()
    assert not isinstance(caught.value, FplManagerNotFound)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": 429},
        {"status": 500},
        {"status": 503},
        {"exc": ClientError()},
        {"exc": TimeoutError()},
        {"text": "<html>maintenance</html>"},
    ],
    ids=["429", "500", "503", "connection", "timeout", "invalid_json"],
)
async def test_http_failures_become_connection_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    kwargs: dict[str, Any],
) -> None:
    """Everything that is not a 404 is a retryable transport failure."""
    aioclient_mock.get(ENTRY_URL, **kwargs)

    with pytest.raises(FplConnectionError):
        await _http_client(hass).async_get_entry(MANAGER_ID)


async def test_a_404_releases_the_connection(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The 404 branch raises before the body is read.

    Without `async with` around the response that connection is never returned
    to the pool, and the pool is Home Assistant's shared one. A second request
    succeeding is the observable proof that the first was released.
    """
    aioclient_mock.get(ENTRY_URL, status=404)
    aioclient_mock.get(BOOTSTRAP_URL, json={"events": [{"id": 1, "name": "GW1"}]})
    client = _http_client(hass)

    with pytest.raises(FplManagerNotFound):
        await client.async_get_entry(MANAGER_ID)

    bootstrap = await client.async_get_bootstrap()
    assert [event["id"] for event in bootstrap.events] == [1]


async def test_get_events_over_http_prunes_the_payload(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The ~3 MB document is pruned in the client, not carried into the cache."""
    aioclient_mock.get(
        BOOTSTRAP_URL,
        json={
            "events": [{"id": 1, "name": "Gameweek 1", "chip_plays": [1] * 100}],
            "elements": [{"huge": True}] * 100,
        },
    )

    bootstrap = await _http_client(hass).async_get_bootstrap()

    assert bootstrap.events == [
        {
            "id": 1,
            "name": "Gameweek 1",
            "deadline_time": None,
            "average_entry_score": None,
            "highest_score": None,
            "finished": None,
            "data_checked": None,
            "is_previous": None,
            "is_current": None,
            "is_next": None,
        }
    ]


async def test_bootstrap_keeps_a_player_name_map() -> None:
    payload = {
        "events": [
            {"id": 1, "name": "Gameweek 1", "deadline_time": "2026-08-14T17:30:00Z"}
        ],
        "elements": [
            {"id": 351, "web_name": "Haaland", "now_cost": 150},
            {"id": 427, "web_name": "Salah"},
        ],
    }

    with patch.object(FplClient, "_get", AsyncMock(return_value=payload)):
        bootstrap = await _client().async_get_bootstrap()

    assert bootstrap.players == {351: "Haaland", 427: "Salah"}
    # Only the two keys are kept; `now_cost` and the rest of the ~3 MB document
    # must not be retained.
    assert [event["id"] for event in bootstrap.events] == [1]


@pytest.mark.parametrize(
    "elements",
    [
        None,
        [],
        "not-a-list",
        [{"id": 1}],  # no web_name
        [{"web_name": "Nameless"}],  # no id
        [{"id": True, "web_name": "Boolean"}],  # bool is an int subclass
        [{"id": 1, "web_name": ""}],  # empty name
        ["not-a-dict"],
    ],
    ids=[
        "missing",
        "empty",
        "wrong_type",
        "no_web_name",
        "no_id",
        "bool_id",
        "empty_name",
        "non_dict_entry",
    ],
)
async def test_a_broken_elements_array_yields_no_names_but_does_not_fail(
    elements: Any,
) -> None:
    """`events` alone decides success - eleven sensors depend on it, two on names."""
    payload = {
        "events": [
            {"id": 1, "name": "Gameweek 1", "deadline_time": "2026-08-14T17:30:00Z"}
        ],
        "elements": elements,
    }

    with patch.object(FplClient, "_get", AsyncMock(return_value=payload)):
        bootstrap = await _client().async_get_bootstrap()

    assert bootstrap.players == {}
    assert len(bootstrap.events) == 1


async def test_get_picks_returns_the_payload() -> None:
    payload = {"active_chip": None, "picks": [{"element": 351, "is_captain": True}]}

    with patch.object(FplClient, "_get", AsyncMock(return_value=payload)) as get:
        picks = await _client().async_get_picks(1234567, 2)

    assert picks == payload
    get.assert_awaited_once_with("entry/1234567/event/2/picks/")


@pytest.mark.parametrize("payload", [None, [], "picks", 3])
async def test_get_picks_rejects_a_non_dict_payload(payload: Any) -> None:
    with (
        patch.object(FplClient, "_get", AsyncMock(return_value=payload)),
        pytest.raises(FplConnectionError),
    ):
        await _client().async_get_picks(1234567, 2)
