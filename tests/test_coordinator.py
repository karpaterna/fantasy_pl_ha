"""Tests for the bootstrap cache and its TTL policy.

`gameweek_is_live` and `FplBootstrapCache.is_stale` are pure functions of their
arguments, so most of this file needs no Home Assistant instance. That is
deliberate: the TTL policy is the part of this integration most likely to
regress silently, and it should be cheap to test.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.fantasy_pl.api import (
    FplBootstrap,
    FplConnectionError,
    FplManagerNotFound,
    FplRateLimitedError,
)
from custom_components.fantasy_pl.const import (
    BOOTSTRAP_LIVE_MAX_AGE,
    BOOTSTRAP_MAX_AGE,
    BOOTSTRAP_RATE_LIMIT_COOLDOWN,
    BOOTSTRAP_RETRY_COOLDOWN,
    CONF_MANAGER_ID,
    DOMAIN,
)
from custom_components.fantasy_pl.coordinator import (
    FplBootstrapCache,
    gameweek_is_live,
)

from .conftest import setup_entry

NOW = datetime(2026, 8, 29, 15, 0, tzinfo=UTC)

# A deadline far enough in the future that gameweek_is_live() is False for any
# plausible wall clock, so tests using it get the long TTL deterministically.
NEVER_LIVE = "2099-01-01T00:00:00Z"


def _gw(deadline: str, data_checked: bool = False, **kw: Any) -> dict[str, Any]:
    """Build a minimal event dict."""
    return {"deadline_time": deadline, "data_checked": data_checked, **kw}


@pytest.mark.parametrize(
    ("events", "expected"),
    [
        ([], False),
        ([_gw("2026-09-05T17:30:00Z")], False),
        ([_gw("2026-08-29T14:00:00Z")], True),
        ([_gw("2026-08-29T14:00:00Z", True)], False),
        ([_gw("2026-08-28T17:30:00Z", False, finished=True)], True),
        ([_gw("2026-08-20T17:30:00Z")], False),
        ([_gw("2026-05-01T17:30:00Z", True)], False),
        ([_gw("not-a-date")], False),
        ([{"id": 3}], False),
    ],
    ids=[
        "empty",
        "deadline_in_future",
        "deadline_passed_unchecked",
        "deadline_passed_checked",
        "finished_awaiting_bonus",
        "stalled_beyond_window",
        "season_over",
        "malformed_deadline",
        "missing_deadline",
    ],
)
def test_gameweek_is_live(events: list[dict[str, Any]], expected: bool) -> None:
    """A gameweek is live from its deadline until data_checked, capped."""
    assert gameweek_is_live(events, NOW) is expected


@pytest.mark.parametrize(
    ("live", "age", "expected"),
    [
        (True, timedelta(minutes=5), False),
        (True, BOOTSTRAP_LIVE_MAX_AGE - timedelta(minutes=1), False),
        (True, BOOTSTRAP_LIVE_MAX_AGE, True),
        (False, timedelta(minutes=20), False),
        (False, BOOTSTRAP_MAX_AGE - timedelta(minutes=1), False),
        (False, BOOTSTRAP_MAX_AGE, True),
        (True, BOOTSTRAP_MAX_AGE, True),
    ],
    ids=[
        "live_fresh",
        "live_just_under_ttl",
        "live_at_ttl",
        "idle_fresh",
        "idle_just_under_ttl",
        "idle_at_ttl",
        "live_very_stale",
    ],
)
def test_is_stale_ttl(live: bool, age: timedelta, expected: bool) -> None:
    """The TTL is selected by whether a gameweek is being played."""
    cache = FplBootstrapCache()
    cache.events = [
        _gw("2026-08-29T14:00:00Z") if live else _gw("2026-09-05T17:30:00Z")
    ]
    cache.fetched = NOW - age
    assert cache.is_stale(NOW) is expected


def test_is_stale_without_cache() -> None:
    """An empty cache is always stale."""
    assert FplBootstrapCache().is_stale(NOW) is True


async def test_refetch_rate_is_bounded() -> None:
    """A passed deadline must not pull bootstrap-static on every cycle.

    Regression test: an earlier form returned True outright while a deadline
    had passed but FPL had not yet moved the is_current/is_next flags, so the
    ~3 MB document was re-fetched every scan interval (as low as 5 minutes)
    for the whole of that window.
    """
    client = AsyncMock()
    client.async_get_bootstrap.return_value = FplBootstrap(
        [_gw("2026-08-29T14:00:00Z")], {}
    )
    cache = FplBootstrapCache()

    await cache.async_get_bootstrap(client)
    assert client.async_get_bootstrap.await_count == 1

    # Simulate several poll cycles inside the live TTL.
    for _ in range(5):
        await cache.async_get_bootstrap(client)
    assert client.async_get_bootstrap.await_count == 1


async def test_cache_is_shared_between_entries(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry,
    entry_payload: dict[str, Any],
) -> None:
    """Two managers must not each download bootstrap-static.

    Deliberately not frozen in time: both setups happen microseconds apart, so
    the second is inside either TTL regime and the assertion means the same
    thing whenever it runs. The identity check below pins the actual invariant —
    one cache object, not two — independently of any clock.
    """
    second = MockConfigEntry(
        domain=DOMAIN,
        title="Second Team",
        data={CONF_MANAGER_ID: 501210},
        unique_id="501210",
    )

    await setup_entry(hass, mock_config_entry)
    await setup_entry(hass, second)

    assert mock_client.async_get_entry.await_count == 2
    assert mock_client.async_get_bootstrap.await_count == 1
    assert mock_config_entry.runtime_data.cache is second.runtime_data.cache


async def test_failed_fetch_serves_stale_cache_during_cooldown() -> None:
    """A failed bootstrap fetch must not be retried on every poll cycle."""
    client = AsyncMock()
    client.async_get_bootstrap.side_effect = FplConnectionError("FPL is down")
    cache = FplBootstrapCache()
    cache.events = [_gw(NEVER_LIVE)]
    cache.fetched = dt_util.utcnow() - BOOTSTRAP_MAX_AGE

    # The first attempt is made, fails, and propagates so the caller can decide.
    with pytest.raises(FplConnectionError):
        await cache.async_get_bootstrap(client)
    assert client.async_get_bootstrap.await_count == 1

    # Subsequent cycles inside the cooldown serve the stale cache silently.
    for _ in range(5):
        assert (await cache.async_get_bootstrap(client)).events == cache.events
    assert client.async_get_bootstrap.await_count == 1


async def test_empty_cache_ignores_cooldown() -> None:
    """With nothing cached there is no degraded mode, so keep retrying."""
    client = AsyncMock()
    client.async_get_bootstrap.side_effect = FplConnectionError("FPL is down")
    cache = FplBootstrapCache()

    for expected in (1, 2, 3):
        with pytest.raises(FplConnectionError):
            await cache.async_get_bootstrap(client)
        assert client.async_get_bootstrap.await_count == expected


async def test_bootstrap_failure_does_not_fail_the_update(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry
) -> None:
    """8 of 11 sensors read only from entry/, so they must survive.

    The two fetches are deliberately not gathered: a bootstrap-static failure
    must not fail the whole update and take every sensor unavailable with it.
    """
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    # Expire the cache, then make the refetch fail.
    coordinator.cache.fetched = dt_util.utcnow() - BOOTSTRAP_MAX_AGE
    coordinator.cache.failed_at = None
    mock_client.async_get_bootstrap.side_effect = FplConnectionError("FPL is down")

    await coordinator.async_refresh()

    assert coordinator.last_update_success is True
    assert coordinator.data.entry["summary_overall_points"] == 47
    # The last good event list is still served, so the three event-derived
    # sensors hold their previous values rather than going unavailable.
    assert coordinator.data.events


async def test_entry_failure_still_fails_the_update(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry
) -> None:
    """entry/ alone decides whether the update succeeded."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    mock_client.async_get_entry.side_effect = FplConnectionError("FPL is down")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False


async def test_team_rename_reaches_the_device_registry(
    hass: HomeAssistant,
    mock_client: AsyncMock,
    mock_config_entry,
    entry_payload: dict[str, Any],
) -> None:
    """DeviceInfo is read once at add time, so a rename needs a registry write."""
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    with patch(
        "custom_components.fantasy_pl.coordinator.dr.async_get"
    ) as mock_async_get:
        # Same name as before: nothing to push.
        await coordinator.async_refresh()
        mock_async_get.return_value.async_get_or_create.assert_not_called()

        mock_client.async_get_entry.return_value = {
            **entry_payload,
            "name": "Renamed FC",
        }
        await coordinator.async_refresh()
        mock_async_get.return_value.async_get_or_create.assert_called_once_with(
            config_entry_id=mock_config_entry.entry_id,
            identifiers={(DOMAIN, "1234567")},
            name="Renamed FC",
        )


async def test_concurrent_callers_share_one_download() -> None:
    """The lock is what stops N config entries pulling N copies of ~3 MB.

    Five callers all see an empty (therefore stale) cache at the same moment.
    Without the lock and the re-check that follows it, every one of them would
    start its own fetch; with them, four wait and then read the fresh cache.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def _slow_fetch() -> FplBootstrap:
        started.set()
        await release.wait()
        return FplBootstrap([_gw(NEVER_LIVE)], {})

    client = AsyncMock()
    client.async_get_bootstrap.side_effect = _slow_fetch
    cache = FplBootstrapCache()

    callers = [asyncio.create_task(cache.async_get_bootstrap(client)) for _ in range(5)]
    # One caller is now inside the fetch and the other four are on the lock.
    await started.wait()
    release.set()
    results = await asyncio.gather(*callers)

    assert client.async_get_bootstrap.await_count == 1
    assert all(result == results[0] for result in results)


async def test_failure_cooldown_starts_at_the_failure_not_the_request() -> None:
    """A request may run for the full 30 s timeout before it fails.

    Stamping the cooldown with the time the request *started* would hand that
    time back, shortening the quiet period by however slow FPL was being.
    """
    client = AsyncMock()
    client.async_get_bootstrap.side_effect = FplConnectionError("FPL is down")
    cache = FplBootstrapCache()
    cache.events = [_gw(NEVER_LIVE)]
    cache.fetched = NOW - BOOTSTRAP_MAX_AGE
    failed_at = NOW + timedelta(seconds=30)

    with (
        patch(
            "custom_components.fantasy_pl.coordinator.dt_util.utcnow",
            side_effect=[NOW, failed_at],
        ),
        pytest.raises(FplConnectionError),
    ):
        await cache.async_get_bootstrap(client)

    assert cache.failed_at == failed_at


async def test_rate_limiting_earns_a_longer_cooldown() -> None:
    """A 429 is FPL asking for less traffic, so back off further than usual."""
    client = AsyncMock()
    client.async_get_bootstrap.side_effect = FplRateLimitedError("429")
    cache = FplBootstrapCache()
    cache.events = [_gw(NEVER_LIVE)]
    cache.fetched = NOW - BOOTSTRAP_MAX_AGE

    with (
        patch(
            "custom_components.fantasy_pl.coordinator.dt_util.utcnow",
            return_value=NOW,
        ),
        pytest.raises(FplRateLimitedError),
    ):
        await cache.async_get_bootstrap(client)

    assert cache.retry_after == BOOTSTRAP_RATE_LIMIT_COOLDOWN
    # Still quiet at the point an ordinary failure would already have retried.
    ordinary_retry = NOW + BOOTSTRAP_RETRY_COOLDOWN + timedelta(minutes=1)
    assert cache.is_stale(ordinary_retry) is False
    assert cache.is_stale(NOW + BOOTSTRAP_RATE_LIMIT_COOLDOWN) is True


async def test_a_successful_fetch_clears_the_rate_limit_cooldown() -> None:
    """The longer cooldown must not outlive the rate limiting that caused it."""
    client = AsyncMock()
    client.async_get_bootstrap.return_value = FplBootstrap([_gw(NEVER_LIVE)], {})
    cache = FplBootstrapCache()
    cache.retry_after = BOOTSTRAP_RATE_LIMIT_COOLDOWN
    cache.failed_at = NOW

    await cache.async_get_bootstrap(client)

    assert cache.failed_at is None
    assert cache.retry_after == BOOTSTRAP_RETRY_COOLDOWN


async def test_a_404_on_the_manager_is_retryable_not_fatal(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry
) -> None:
    """FPL 404s during maintenance, so this must not read as a dead entry.

    The update fails and the sensors go unavailable, but the config entry
    stays set up and the next cycle retries.
    """
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    mock_client.async_get_entry.side_effect = FplManagerNotFound("no such manager")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
    assert mock_config_entry.state is ConfigEntryState.LOADED


async def test_a_bootstrap_failure_with_nothing_cached_fails_the_update(
    hass: HomeAssistant, mock_client: AsyncMock, mock_config_entry
) -> None:
    """Without a previous cycle's cache there is no degraded mode.

    Three sensors would have no source at all, so the update fails rather
    than publishing a snapshot with an empty event list.
    """
    await setup_entry(hass, mock_config_entry)
    coordinator = mock_config_entry.runtime_data

    coordinator.cache.events = []
    coordinator.cache.fetched = None
    coordinator.cache.failed_at = None
    mock_client.async_get_bootstrap.side_effect = FplConnectionError("FPL is down")
    await coordinator.async_refresh()

    assert coordinator.last_update_success is False
