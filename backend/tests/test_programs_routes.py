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

from backend.api.models import ProgramUpdateRequest
from backend.api.programs import create_programs_router
from backend.core.models.ws_events import ProgramUpdateComplete, ProgramUpdateProgress


# The payload every one of these calls carries: they exercise the in-flight
# claim, not the choice of release, so they all ask for the version the
# manifest declares.
VALIDATED = ProgramUpdateRequest(target="validated")


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
    # The two satellite routes resolve the version to install before dispatching:
    # a satellite has no manifest of its own, so the server names it.
    update_service.get_latest_github_version = AsyncMock(
        return_value={"status": "success", "version": "0.35.0"}
    )

    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()

    r = create_programs_router(update_service, MagicMock(), state_machine)
    r.update_service = update_service
    r.state_machine = state_machine
    return r


async def test_two_clients_racing_the_same_update_start_only_one(router):
    """The claim must land before the GitHub round-trip, not after it."""
    gate = asyncio.Event()

    async def slow_check(program_key, target):
        await gate.wait()
        return {"can_update": True, "available_version": "1.2.3"}

    router.update_service.can_update_program = slow_check
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")

    first = asyncio.create_task(endpoint("go-librespot", VALIDATED, BackgroundTasks()))
    await asyncio.sleep(0)  # first request is parked on the GitHub call
    second = asyncio.create_task(endpoint("go-librespot", VALIDATED, BackgroundTasks()))
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
    refused = await endpoint("go-librespot", VALIDATED, BackgroundTasks())
    assert refused["status"] == "error"

    router.update_service.can_update_program = AsyncMock(
        return_value={"can_update": True, "available_version": "1.2.3"}
    )
    assert (await endpoint("go-librespot", VALIDATED, BackgroundTasks()))["status"] == "success"


async def test_a_failing_check_releases_the_key(router):
    """Same duty when can_update_program raises instead of refusing."""
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")

    router.update_service.can_update_program = AsyncMock(side_effect=RuntimeError("GitHub down"))
    with pytest.raises(RuntimeError):
        await endpoint("go-librespot", VALIDATED, BackgroundTasks())

    router.update_service.can_update_program = AsyncMock(
        return_value={"can_update": True, "available_version": "1.2.3"}
    )
    assert (await endpoint("go-librespot", VALIDATED, BackgroundTasks()))["status"] == "success"


async def test_the_satellite_routes_claim_their_key_too(router):
    """
    Two of the three now resolve the version to install inside the window, and
    all three share the claim helper — a second request must still be refused.
    """
    for path, key_prefix in (
        ("/api/programs/satellites/{mac_id}/update", "satellite_"),
        ("/api/programs/satellites/{mac_id}/update-app", "satellite_app_"),
        ("/api/programs/satellites/{mac_id}/update-camilladsp", "satellite_camilladsp_"),
    ):
        endpoint = _endpoint(router, path)
        assert (await endpoint("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "success"
        assert (await endpoint("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "error"


async def test_a_satellite_update_with_no_resolvable_version_never_starts(router):
    """No version, no update — and the claim goes back.

    The satellite would otherwise be asked to install nothing, or (as it once
    did) to pick `releases/latest` for itself, which is how a client lands on a
    release the server never validated. GitHub rate-limits unauthenticated
    callers at 60/hour, so an unresolvable answer is an ordinary Tuesday.
    """
    endpoint = _endpoint(router, "/api/programs/satellites/{mac_id}/update")
    router.update_service.get_latest_github_version = AsyncMock(
        return_value={"status": "error", "message": "API rate limit exceeded"}
    )

    refused = await endpoint("aa:bb:cc:dd:ee:ff", BackgroundTasks())
    assert refused["status"] == "error"

    # The key was released, so the update is still startable once GitHub answers.
    router.update_service.get_latest_github_version = AsyncMock(
        return_value={"status": "success", "version": "0.35.0"}
    )
    assert (await endpoint("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "success"


async def test_an_update_announces_itself_once(router):
    """A device that did not click still has to learn an update is running.

    That announcement used to be re-sent at every phase, carrying a message and
    a percentage that nothing on the wire, in the store or in the UI ever read.
    What has to survive the removal is the announcement itself: one progress
    event when the update starts, then the completion event that releases the
    key. Losing it leaves a second device showing "Update" through the whole
    run, and re-scattering it puts back a broadcast that says nothing new.
    """
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")
    tasks = BackgroundTasks()
    assert (await endpoint("go-librespot", VALIDATED, tasks))["status"] == "success"

    await tasks()

    events = [call.args[0] for call in router.state_machine.broadcast.await_args_list]
    progress = [e for e in events if isinstance(e, ProgramUpdateProgress)]
    complete = [e for e in events if isinstance(e, ProgramUpdateComplete)]

    assert len(progress) == 1, f"expected one announcement, got {len(progress)}"
    assert progress[0].program == "go-librespot"
    assert progress[0].status == "updating"
    assert len(complete) == 1
    assert complete[0].success is True

    # The key is released, so the same program can be updated again.
    assert (await endpoint("go-librespot", VALIDATED, BackgroundTasks()))["status"] == "success"
