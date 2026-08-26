"""Tests for the FPL API client's payload handling.

The HTTP layer itself is not exercised here — these cover the parsing and
pruning that runs on whatever `bootstrap-static/` happens to return.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.fantasy_pl.api import (
    FplClient,
    FplConnectionError,
    FplManagerNotFound,
)


def _client() -> FplClient:
    """Return a client whose session is never touched."""
    return FplClient(session=None)  # type: ignore[arg-type]


async def _with_payload(payload: Any) -> Any:
    """Run async_get_events against a canned _get response."""
    with patch.object(FplClient, "_get", AsyncMock(return_value=payload)):
        return await _client().async_get_events()


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

    Regression test for review item #11: AttributeError is outside the set the
    coordinator handles, so it surfaced as an unhandled coordinator error
    instead of a retryable UpdateFailed.
    """
    events = await _with_payload({"events": [{"id": 1}, "junk", None, 42, {"id": 2}]})
    assert [event["id"] for event in events] == [1, 2]


@pytest.mark.parametrize(
    "payload",
    [{}, {"events": None}, {"events": "nope"}, []],
    ids=["no_key", "null", "not_a_list", "not_a_dict"],
)
async def test_get_events_rejects_a_missing_event_list(payload: Any) -> None:
    """A payload without a usable event list is a connection-level failure."""
    with pytest.raises(FplConnectionError):
        await _with_payload(payload)


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
