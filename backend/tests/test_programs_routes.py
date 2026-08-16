"""
Tests for the in-flight guard on POST /api/programs/{program_key}/update.

What breaks when these fail: two clients can start the same program update at
once. The guard used to read `active_updates` and only write it ~20 s later,
after `can_update_program()` had been to GitHub — a check-then-act with the
slowest await in the router sitting inside the window. The frontend store
blocks a same-client double click; it cannot block a second device.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import BackgroundTasks

from backend.api.programs import create_programs_router


def _endpoint(router, path: str, method: str = "POST"):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"no {method} {path} in the router")


@pytest.fixture
def router():
    update_service = MagicMock()
    update_service.can_update_program = AsyncMock(
        return_value={"can_update": True, "available_version": "1.2.3"}
    )
    update_service.update_program = AsyncMock(return_value={"success": True})

    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()

    r = create_programs_router(update_service, MagicMock(), state_machine)
    r.update_service = update_service
    return r


async def test_two_clients_racing_the_same_update_start_only_one(router):
    """The claim must land before the GitHub round-trip, not after it."""
    gate = asyncio.Event()

    async def slow_check(program_key):
        await gate.wait()
        return {"can_update": True, "available_version": "1.2.3"}

    router.update_service.can_update_program = slow_check
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")

    first = asyncio.create_task(endpoint("go-librespot", BackgroundTasks()))
    await asyncio.sleep(0)  # first request is parked on the GitHub call
    second = asyncio.create_task(endpoint("go-librespot", BackgroundTasks()))
    await asyncio.sleep(0)
    gate.set()

    results = [r["status"] for r in await asyncio.gather(first, second)]
    assert sorted(results) == ["error", "success"]


async def test_a_refused_update_releases_the_key(router):
    """
    Claiming before the await means every path out has to release, or a
    program that once answered "already up to date" is locked out until the
    backend restarts.
    """
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")

    router.update_service.can_update_program = AsyncMock(
        return_value={"can_update": False, "reason": "Already up to date"}
    )
    refused = await endpoint("go-librespot", BackgroundTasks())
    assert refused["status"] == "error"

    router.update_service.can_update_program = AsyncMock(
        return_value={"can_update": True, "available_version": "1.2.3"}
    )
    assert (await endpoint("go-librespot", BackgroundTasks()))["status"] == "success"


async def test_a_failing_check_releases_the_key(router):
    """Same duty when can_update_program raises instead of refusing."""
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")

    router.update_service.can_update_program = AsyncMock(side_effect=RuntimeError("GitHub down"))
    with pytest.raises(RuntimeError):
        await endpoint("go-librespot", BackgroundTasks())

    router.update_service.can_update_program = AsyncMock(
        return_value={"can_update": True, "available_version": "1.2.3"}
    )
    assert (await endpoint("go-librespot", BackgroundTasks()))["status"] == "success"


async def test_the_satellite_routes_claim_their_key_too(router):
    """
    The three satellite routes have no await in the window, but they share the
    claim helper — a second request must still be refused.
    """
    for path, key_prefix in (
        ("/api/programs/satellites/{mac_id}/update", "satellite_"),
        ("/api/programs/satellites/{mac_id}/update-app", "satellite_app_"),
        ("/api/programs/satellites/{mac_id}/update-camilladsp", "satellite_camilladsp_"),
    ):
        endpoint = _endpoint(router, path)
        assert (await endpoint("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "success"
        assert (await endpoint("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "error"
