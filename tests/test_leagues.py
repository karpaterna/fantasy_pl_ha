"""Tests for mini-league support.

Most of this is pure functions over a payload dict, so it needs no Home
Assistant instance — the same reasoning as test_sensor.py and the TTL tests in
test_coordinator.py. The flow tests at the bottom do need one.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fantasy_pl.config_flow import (
    _default_league_ids,
    _league_label,
)
from custom_components.fantasy_pl.const import (
    CONF_LEAGUES,
    CONF_MANAGER_ID,
    CONF_SCAN_INTERVAL_MINUTES,
    DOMAIN,
)
from custom_components.fantasy_pl.coordinator import (
    FplData,
    classic_leagues,
    selected_league_ids,
)
from custom_components.fantasy_pl.sensor import _league_movement

from .conftest import MANAGER_ID, setup_entry

# --- classic_leagues() -------------------------------------------------------


def test_classic_leagues_reads_the_array(
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """The happy path returns every classic league, in payload order."""
    leagues = classic_leagues(entry_payload_with_leagues)
    assert [league["id"] for league in leagues] == [14, 555001, 555002]


@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"leagues": None},
        {"leagues": []},
        {"leagues": {"h2h": []}},
        {"leagues": {"classic": None}},
        {"leagues": {"classic": {}}},
    ],
    ids=[
        "no leagues key",
        "leagues is null",
        "leagues is a list",
        "no classic key",
        "classic is null",
        "classic is a dict",
    ],
)
def test_classic_leagues_survives_a_malformed_payload(entry: dict[str, Any]) -> None:
    """The API is unofficial: a wrong shape yields [], never an exception."""
    assert classic_leagues(entry) == []


def test_classic_leagues_drops_members_without_a_usable_id() -> None:
    """The id is the sensor's identity, so a league without one is unusable."""
    entry = {
        "leagues": {
            "classic": [
                {"id": 1, "name": "Good"},
                {"name": "No id"},
                {"id": None, "name": "Null id"},
                {"id": "7", "name": "String id"},
                {"id": True, "name": "Bool id"},
                "not a dict",
            ]
        }
    }
    assert [league["id"] for league in classic_leagues(entry)] == [1]


def test_league_by_id_finds_and_misses(
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """league_by_id returns the league, or None when not a member."""
    data = FplData(entry=entry_payload_with_leagues, events=[])
    found = data.league_by_id(555001)
    assert found is not None
    assert found["name"] == "Work League"
    assert data.league_by_id(999999) is None


# --- selected_league_ids() ---------------------------------------------------


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (None, []),
        ([], []),
        (["555001", "555002"], [555001, 555002]),
        ([555001], [555001]),
        (["555001", "555001"], [555001]),
        (["555001", "nope", None, "", "555002"], [555001, 555002]),
        ("555001", []),
    ],
    ids=[
        "unset",
        "empty",
        "strings",
        "already ints",
        "de-duplicated",
        "garbage skipped",
        "not a list",
    ],
)
def test_selected_league_ids(stored: Any, expected: list[int]) -> None:
    """Options round-trip as strings; callers get ints, or nothing."""
    options = {} if stored is None else {CONF_LEAGUES: stored}
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_MANAGER_ID: MANAGER_ID}, options=options
    )
    assert selected_league_ids(entry) == expected


# --- _league_movement() ------------------------------------------------------


@pytest.mark.parametrize(
    ("rank", "last", "expected"),
    [
        (4, 7, 3),
        (7, 4, -3),
        (4, 4, 0),
        (2, 0, None),
        (None, 7, None),
        (4, None, None),
        (True, 7, None),
        (4, True, None),
        ("4", 7, None),
    ],
    ids=[
        "moved up",
        "moved down",
        "unchanged",
        "no previous rank (0)",
        "rank missing",
        "previous missing",
        "rank is bool",
        "previous is bool",
        "rank is a string",
    ],
)
def test_league_movement(rank: Any, last: Any, expected: int | None) -> None:
    """Positive means moved up; entry_last_rank 0 means 'no previous rank'."""
    league = {"entry_rank": rank, "entry_last_rank": last}
    assert _league_movement(league) == expected


def test_league_movement_zero_does_not_report_a_plunge(
    leagues_payload: list[dict[str, Any]],
) -> None:
    """Regression: a GW1 league must not read as a fall of the whole table.

    Friends League sits at rank 2 with entry_last_rank 0. Treating 0 as a real
    rank would publish a movement of -2 on every manager's first gameweek.
    """
    friends = next(lg for lg in leagues_payload if lg["id"] == 555002)
    assert _league_movement(friends) is None


# --- picker helpers ----------------------------------------------------------


def test_default_selection_is_the_invitational_leagues(
    leagues_payload: list[dict[str, Any]],
) -> None:
    """Automatic leagues are offered but left unticked."""
    assert _default_league_ids(leagues_payload) == ["555001", "555002"]


def test_league_label_includes_the_size(
    leagues_payload: list[dict[str, Any]],
) -> None:
    """The label carries the league size, thousands-separated."""
    automatic, work, _friends = leagues_payload
    assert _league_label(work) == "Work League (21 managers)"
    assert _league_label(automatic) == "Automatic League (1,089,086 managers)"


@pytest.mark.parametrize(
    ("league", "expected"),
    [
        ({"id": 5, "rank_count": None}, "League 5"),
        ({"id": 5, "name": "Named", "rank_count": "many"}, "Named"),
        ({"id": 5, "name": "Named", "rank_count": True}, "Named"),
    ],
    ids=["no name", "non-int size", "bool size"],
)
def test_league_label_degrades_gracefully(
    league: dict[str, Any], expected: str
) -> None:
    """A missing name or a wrong-typed size must not break the picker."""
    assert _league_label(league) == expected


# --- flows -------------------------------------------------------------------


async def test_config_flow_asks_for_leagues_and_stores_the_choice(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """A manager with leagues gets a second step; the choice lands in options."""
    mock_client.async_get_entry.return_value = entry_payload_with_leagues

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: MANAGER_ID}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "leagues"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_LEAGUES: ["555001"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_MANAGER_ID: MANAGER_ID}
    assert result["options"] == {CONF_LEAGUES: ["555001"]}


async def test_config_flow_skips_the_step_without_leagues(
    hass: HomeAssistant, mock_client: AsyncMock
) -> None:
    """A payload with no classic leagues creates the entry in one step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MANAGER_ID: MANAGER_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["options"] == {CONF_LEAGUES: []}


async def test_league_sensors_are_created_for_the_selection(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """One sensor per selected league, and none for the unselected ones."""
    mock_client.async_get_entry.return_value = entry_payload_with_leagues
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001", "555002"]},
        unique_id=str(MANAGER_ID),
    )
    await setup_entry(hass, entry)

    state = hass.states.get("sensor.example_team_work_league_rank")
    assert state is not None
    assert state.state == "4"
    assert state.attributes["movement"] == 3
    assert state.attributes["entries"] == 21
    assert state.attributes["is_admin"] is True

    # entry_last_rank 0 must surface as unknown, not as a rank or a plunge.
    friends = hass.states.get("sensor.example_team_friends_league_rank")
    assert friends is not None
    assert friends.attributes["movement"] is None
    assert friends.attributes["previous_rank"] is None

    # The automatic league was not selected.
    assert hass.states.get("sensor.example_team_automatic_league_rank") is None


async def test_league_sensor_goes_unavailable_when_the_manager_leaves(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload: dict[str, Any],
) -> None:
    """A selected league missing from the payload is unavailable, not wrong."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001"]},
        unique_id=str(MANAGER_ID),
    )
    # entry_payload carries no leagues at all.
    mock_client.async_get_entry.return_value = entry_payload
    await setup_entry(hass, entry)

    state = hass.states.get("sensor.example_team_league_555001_rank")
    assert state is not None
    assert state.state == "unavailable"


async def test_options_flow_preserves_keys_absent_from_the_form(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload: dict[str, Any],
) -> None:
    """Submitting the interval must not wipe the stored league selection.

    With no leagues in the payload the picker is omitted from the form, so a
    replacing (rather than merging) async_create_entry would drop the choice.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001"], CONF_SCAN_INTERVAL_MINUTES: 30},
        unique_id=str(MANAGER_ID),
    )
    mock_client.async_get_entry.return_value = entry_payload
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL_MINUTES: 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_SCAN_INTERVAL_MINUTES] == 60
    assert entry.options[CONF_LEAGUES] == ["555001"]


async def test_league_rename_follows_without_a_reload(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload_with_leagues: dict[str, Any],
    leagues_payload: list[dict[str, Any]],
) -> None:
    """A league renamed on the FPL site must not need a config-entry reload.

    The unique_id is keyed to the league id, so the label moves while the
    entity — and everything the recorder has stored under it — stays put.
    """
    mock_client.async_get_entry.return_value = entry_payload_with_leagues
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001"]},
        unique_id=str(MANAGER_ID),
    )
    await setup_entry(hass, entry)

    entity_id = "sensor.example_team_work_league_rank"
    registry = er.async_get(hass)
    unique_id = registry.async_get(entity_id).unique_id
    friendly_name = hass.states.get(entity_id).attributes["friendly_name"]
    assert friendly_name.endswith("Work League rank")

    renamed = [{**league} for league in leagues_payload]
    renamed[1]["name"] = "Monday Club"
    mock_client.async_get_entry.return_value = {
        **entry_payload_with_leagues,
        "leagues": {"classic": renamed, "h2h": []},
    }
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    state = hass.states.get(entity_id)
    assert state.attributes["friendly_name"].endswith("Monday Club rank")
    assert state.attributes["league_name"] == "Monday Club"
    assert registry.async_get(entity_id).unique_id == unique_id


@pytest.mark.parametrize(
    ("value", "expected"),
    [(True, True), (False, False), ("true", None), (1, None), (None, None)],
    ids=["true", "false", "string", "int", "missing"],
)
async def test_is_admin_is_a_bool_or_nothing(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload_with_leagues: dict[str, Any],
    leagues_payload: list[dict[str, Any]],
    value: Any,
    expected: bool | None,
) -> None:
    """A template reading this attribute must never see "false" or 0."""
    leagues = [{**league} for league in leagues_payload]
    leagues[1]["entry_can_admin"] = value
    mock_client.async_get_entry.return_value = {
        **entry_payload_with_leagues,
        "leagues": {"classic": leagues, "h2h": []},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001"]},
        unique_id=str(MANAGER_ID),
    )
    await setup_entry(hass, entry)

    state = hass.states.get("sensor.example_team_work_league_rank")
    assert state.attributes["is_admin"] is expected


async def test_deselecting_a_league_removes_only_its_entity(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """Deselection must not leave a permanently unavailable sensor behind."""
    mock_client.async_get_entry.return_value = entry_payload_with_leagues
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001", "555002"]},
        unique_id=str(MANAGER_ID),
    )
    await setup_entry(hass, entry)
    registry = er.async_get(hass)
    assert registry.async_get("sensor.example_team_work_league_rank") is not None

    hass.config_entries.async_update_entry(entry, options={CONF_LEAGUES: ["555002"]})
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert registry.async_get("sensor.example_team_work_league_rank") is None
    assert registry.async_get("sensor.example_team_friends_league_rank") is not None
    # The eleven fixed sensors share the device, not the league prefix.
    assert registry.async_get("sensor.example_team_overall_points") is not None


async def test_options_flow_offers_the_picker_when_leagues_are_known(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    entry_payload_with_leagues: dict[str, Any],
) -> None:
    """The picker is built from the last payload, pre-ticked to the choice.

    The counterpart to the test above: there the payload had no leagues and
    the field was omitted, here it has them and the field must appear with the
    stored selection as its default, or reopening the dialog would silently
    present an empty selection as the current one.
    """
    mock_client.async_get_entry.return_value = entry_payload_with_leagues
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Example Team",
        data={CONF_MANAGER_ID: MANAGER_ID},
        options={CONF_LEAGUES: ["555001"], CONF_SCAN_INTERVAL_MINUTES: 30},
        unique_id=str(MANAGER_ID),
    )
    await setup_entry(hass, entry)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    key = next(k for k in result["data_schema"].schema if k == CONF_LEAGUES)
    assert key.default() == ["555001"]

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {CONF_SCAN_INTERVAL_MINUTES: 30, CONF_LEAGUES: ["555001", "555002"]},
    )
    await hass.async_block_till_done()

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options[CONF_LEAGUES] == ["555001", "555002"]
