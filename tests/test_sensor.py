"""Tests for the sensor value functions.

These are pure functions of an FplData snapshot, so they need no Home Assistant
instance — the same reasoning as the TTL tests in test_coordinator.py.
"""

from __future__ import annotations

from typing import Any

import pytest

from custom_components.fantasy_pl.coordinator import FplData
from custom_components.fantasy_pl.sensor import (
    GW_STATE_FINAL,
    GW_STATE_PROVISIONAL,
    GW_STATE_SCHEDULED,
    _entry_int,
    _gameweek_state,
    _tenths,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (5, 5),
        (0, 0),
        (-3, -3),
        (True, None),
        (False, None),
        ("5", None),
        (5.0, None),
        (None, None),
    ],
    ids=[
        "int",
        "zero",
        "negative",
        "true",
        "false",
        "numeric_string",
        "float",
        "missing",
    ],
)
def test_entry_int(value: Any, expected: Any) -> None:
    """Bool is a subclass of int, so True must not publish as the state 1."""
    assert _entry_int(FplData(entry={"k": value}), "k") == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1000, 100.0), (10, 1.0), (0, 0.0), (995, 99.5), (None, None), ("x", None)],
    ids=["100m", "1m", "zero", "rounding", "missing", "not_a_number"],
)
def test_tenths(value: Any, expected: Any) -> None:
    """FPL quotes money in tenths of a million; zero is a real value."""
    assert _tenths(value) == expected


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        ({"data_checked": True, "finished": True}, GW_STATE_FINAL),
        ({"data_checked": False, "finished": True}, GW_STATE_PROVISIONAL),
        (
            {"deadline_time": "2099-01-01T00:00:00Z"},
            GW_STATE_SCHEDULED,
        ),
    ],
    ids=["final", "provisional", "scheduled"],
)
def test_gameweek_state(event: dict[str, Any], expected: str) -> None:
    """data_checked outranks finished, which outranks the deadline."""
    data = FplData(entry={"current_event": 1}, events=[{"id": 1, **event}])
    assert _gameweek_state(data) == expected


def test_gameweek_state_without_an_event() -> None:
    """No current gameweek means no state, not a crash."""
    assert _gameweek_state(FplData()) is None
