"""
Tests for the in-flight guard on POST /api/programs/{program_key}/update.

What breaks when these fail: two clients can start the same program update at
once. The guard used to read `active_updates` and only write it ~20 s later,
after `can_update_program()` had been to GitHub — a check-then-act with the
slowest await in the router sitting inside the window. The frontend store
blocks a same-client double click; it cannot block a second device.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import BackgroundTasks

from backend.api.models import LastUpdatePatch, ProgramUpdateRequest
from backend.api.programs import create_programs_router
from backend.api.route_helpers import PresenceAnswer
from backend.core.updates import milo_journal as mj
from backend.core.updates.dependency_versions import load_dependency_manifest
from backend.core.updates.update import UpdateService
from backend.core.models.ws_events import (
    MiloUpdateNoticeDismissed,
    ProgramUpdateComplete,
    ProgramUpdateProgress,
    SatelliteAppUpdateComplete,
)


# The payload every one of these calls carries: they exercise the in-flight
# claim, not the choice of release, so they all ask for the version the
# manifest declares.
VALIDATED = ProgramUpdateRequest(target="validated")


def _endpoint(router, path: str, method: str = "POST"):
    for route in router.routes:
        if route.path == path and method in route.methods:
            return route.endpoint
    raise AssertionError(f"no {method} {path} in the router")


def idle_milo(update_service):
    """The Milō-update surface of an update service with no journal open; a
    bare MagicMock answers a truthy `milo_update_active`, which claims `milo`."""
    update_service.milo_update_active = False
    update_service.milo_update_state = MagicMock(return_value=None)
    update_service.last_update_record = MagicMock(return_value=None)
    return update_service


@pytest.fixture
def router():
    update_service = idle_milo(MagicMock())
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

    # Admits every request: the proof of presence an upstream install waits
    # for is tests/architecture/test_presence_guard.py's.
    presence = MagicMock()
    presence.require = AsyncMock()

    r = create_programs_router(update_service, MagicMock(), state_machine, presence_service=presence)
    r.update_service = update_service
    r.state_machine = state_machine
    return r


async def test_two_clients_racing_the_same_update_start_only_one(router):
    """The claim must land before the GitHub round-trip, not after it."""
    gate = asyncio.Event()

    async def slow_check(program_key, target, version):
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
    Two of the three resolve the version to install before they claim, and
    all three share the claim helper — a second request must still be refused.

    One MAC per route on purpose: the three keys of a single satellite now
    exclude each other (the row's own three buttons), which the next test pins.
    """
    for path, mac in (
        ("/api/programs/satellites/{mac_id}/update", "aa:bb:cc:dd:ee:01"),
        ("/api/programs/satellites/{mac_id}/update-app", "aa:bb:cc:dd:ee:02"),
        ("/api/programs/satellites/{mac_id}/update-camilladsp", "aa:bb:cc:dd:ee:03"),
    ):
        endpoint = _endpoint(router, path)
        assert (await endpoint(mac, BackgroundTasks()))["status"] == "success"
        assert (await endpoint(mac, BackgroundTasks()))["status"] == "error"


async def test_the_milo_update_blocks_every_other_one(router):
    """The one update that must be alone, enforced where a second device cannot walk around it.

    It reconciles the whole dependency set, pushes the client app to every
    satellite and then reboots the unit. The update screen already disables the
    other buttons; that is one client's DOM, and `_claim_update`'s own docstring
    says why it cannot be the enforcement.
    """
    program = _endpoint(router, "/api/programs/{program_key}/update")
    satellite = _endpoint(router, "/api/programs/satellites/{mac_id}/update-app")

    assert (await program("milo", VALIDATED, BackgroundTasks()))["status"] == "success"

    refused = await satellite("aa:bb:cc:dd:ee:ff", BackgroundTasks())
    assert refused["status"] == "error"
    assert "milo" in refused["message"]

    refused = await program("go-librespot", VALIDATED, BackgroundTasks())
    assert refused["status"] == "error"
    assert "milo" in refused["message"]


async def test_a_running_satellite_update_blocks_the_milo_one(router):
    """The reverse direction — the pair `f31048a1` closed on the frontend only.

    Starting the app update mid-satellite-update reaches that satellite's own
    `POST /app/update` a second time (409), then reboots the server out from
    under the task polling it.
    """
    satellite = _endpoint(router, "/api/programs/satellites/{mac_id}/update")
    program = _endpoint(router, "/api/programs/{program_key}/update")

    assert (await satellite("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "success"

    refused = await program("milo", VALIDATED, BackgroundTasks())
    assert refused["status"] == "error"
    assert "aa:bb:cc:dd:ee:ff" in refused["message"]


async def test_two_local_programs_exclude_each_other(router):
    """They share the deploy wrapper, and the screen says they exclude each other."""
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")

    assert (await endpoint("go-librespot", VALIDATED, BackgroundTasks()))["status"] == "success"

    refused = await endpoint("navidrome", VALIDATED, BackgroundTasks())
    assert refused["status"] == "error"
    assert "go-librespot" in refused["message"]


async def test_a_satellite_blocks_its_own_three_keys_and_no_one_else(router):
    """A satellite is a separate machine: it blocks its own row, not the fleet."""
    snapclient = _endpoint(router, "/api/programs/satellites/{mac_id}/update")
    app = _endpoint(router, "/api/programs/satellites/{mac_id}/update-app")
    camilladsp = _endpoint(router, "/api/programs/satellites/{mac_id}/update-camilladsp")

    assert (await snapclient("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "success"

    assert (await app("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "error"
    assert (await camilladsp("aa:bb:cc:dd:ee:ff", BackgroundTasks()))["status"] == "error"

    # A second speaker is untouched — updating one room must not lock the other.
    assert (await app("11:22:33:44:55:66", BackgroundTasks()))["status"] == "success"


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


# (route, program, the dependencies.env line of the file the satellite installs, the push).
SATELLITE_PUSHES = [
    ("/api/programs/satellites/{mac_id}/update", "multiroom", "SNAPCLIENT_SHA256", "update_satellite"),
    ("/api/programs/satellites/{mac_id}/update-camilladsp", "camilladsp", "CAMILLADSP_SHA256",
     "update_satellite_camilladsp"),
]


@pytest.mark.parametrize("path,program,line,push", SATELLITE_PUSHES, ids=["snapclient", "camilladsp"])
async def test_a_satellite_is_sent_the_digest_of_the_file_it_installs(
    tmp_path, mock_settings_service, path, program, line, push,
):
    """A satellite carries no dependencies.env: the digest its installer checks
    is the one this route picks out of the server's. Picking the snapclient
    .deb's and not snapserver's — the other file of the same version — is the
    route's choice, and the wrong one has every speaker refuse its update as a
    checksum mismatch. Built with no presence service, which refuses any
    guarded request: a validated version asks for no gesture."""
    with patch.dict("os.environ", {}, clear=True):
        service = UpdateService(
            systemd_manager=MagicMock(), satellite_update_service=MagicMock(),
            settings_service=mock_settings_service,
        )
    service.programs["milo"]["state_path"] = str(tmp_path / "no-journal")
    validated = service.programs[program]["validated_version"]
    service.get_latest_github_version = AsyncMock(return_value={"status": "success", "version": validated})
    satellites = MagicMock()
    setattr(satellites, push, AsyncMock(return_value={"success": True}))
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    r = create_programs_router(service, satellites, state_machine)

    tasks = BackgroundTasks()
    assert (await _endpoint(r, path)("aa:bb:cc:dd:ee:ff", tasks))["status"] == "success"
    await tasks()

    getattr(satellites, push).assert_awaited_once_with(
        "aa:bb:cc:dd:ee:ff", validated, load_dependency_manifest()[line]
    )


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


async def test_a_chosen_version_reaches_the_check_and_the_install(router):
    """Dropped on either hop, the unit would install upstream's latest instead."""
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")
    tasks = BackgroundTasks()
    payload = ProgramUpdateRequest(target="upstream", version="0.8.1")

    proof = PresenceAnswer(token="proof", follows=None)
    assert (await endpoint("go-librespot", payload, tasks, presence=proof))["status"] == "success"
    await tasks()

    router.update_service.can_update_program.assert_awaited_once_with(
        "go-librespot", "upstream", "0.8.1"
    )
    router.update_service.update_program.assert_awaited_once_with(
        "go-librespot", "upstream", "0.8.1"
    )


def test_a_version_is_only_a_trial():
    """The return to the manifest names no version: the manifest does."""
    with pytest.raises(ValueError):
        ProgramUpdateRequest(target="validated", version="0.8.1")


async def test_an_update_claimed_during_the_inventory_read_is_listed_active(router):
    """A device loading the update screen mid-claim must see the update running.

    The inventory read awaits every program's version probe; an update started
    meanwhile is already claimed. Listing what was in flight before that await
    hands the client an empty set, its reconciliation clears the entry the WS
    progress made, and a second press is offered for an update already running.
    """
    gate = asyncio.Event()

    async def slow_status():
        await gate.wait()
        return {}

    router.update_service.get_all_program_status = slow_status
    listing = asyncio.create_task(_endpoint(router, "/api/programs", "GET")())
    await asyncio.sleep(0)  # the read is parked on the version probes

    update = _endpoint(router, "/api/programs/{program_key}/update")
    assert (await update("shairport-sync", VALIDATED, BackgroundTasks()))["status"] == "success"
    gate.set()

    assert "shairport-sync" in (await listing)["active_updates"]


async def test_an_update_finished_during_the_inventory_read_is_not_listed_active(router):
    """An update that completed while the list was read must not come back.

    Its completion event has already reached the client; a stale `active`
    resurrects the entry with no further event to clear it, and the buttons
    stay greyed until the page is reloaded.
    """
    update = _endpoint(router, "/api/programs/{program_key}/update")
    tasks = BackgroundTasks()
    assert (await update("snapcast", VALIDATED, tasks))["status"] == "success"

    gate = asyncio.Event()

    async def slow_status():
        await gate.wait()
        return {}

    router.update_service.get_all_program_status = slow_status
    listing = asyncio.create_task(_endpoint(router, "/api/programs", "GET")())
    await asyncio.sleep(0)

    await tasks()
    gate.set()

    assert "snapcast" not in (await listing)["active_updates"]


async def test_two_app_pushes_wait_for_each_other_whatever_the_satellite(router):
    """Each push restarts a speaker and can leave it on trial for minutes: two at
    once can cut two rooms, and a release that breaks one breaks both. The
    snapclient row of the other satellite stays free — that is not a restart
    of the app."""
    app = _endpoint(router, "/api/programs/satellites/{mac_id}/update-app")
    snapclient = _endpoint(router, "/api/programs/satellites/{mac_id}/update")

    assert (await app("aa:bb:cc:dd:ee:01", BackgroundTasks()))["status"] == "success"

    refused = await app("aa:bb:cc:dd:ee:02", BackgroundTasks())
    assert refused["status"] == "error"
    assert "satellite_app_aa:bb:cc:dd:ee:01" in refused["message"]

    assert (await snapclient("aa:bb:cc:dd:ee:02", BackgroundTasks()))["status"] == "success"


async def test_an_app_push_that_raised_still_names_a_verdict(router):
    """No verdict would draw the row as if nothing had been tried."""
    satellite_service = MagicMock()
    satellite_service.update_satellite_app = AsyncMock(side_effect=asyncio.TimeoutError())
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    r = create_programs_router(router.update_service, satellite_service, state_machine)

    tasks = BackgroundTasks()
    await _endpoint(r, "/api/programs/satellites/{mac_id}/update-app")("aa:bb:cc:dd:ee:01", tasks)
    await tasks()

    complete = [c.args[0] for c in state_machine.broadcast.await_args_list
                if isinstance(c.args[0], SatelliteAppUpdateComplete)]
    assert complete[0].success is False
    assert complete[0].verdict == "did_not_return"
    assert complete[0].detail.startswith("TimeoutError")


# --------------------------------------------------------------------------- #
# The Milō update: an outcome decided by the release that starts next.
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("result", [
    {"success": True, "pending": True},
    {"success": False, "pending": True, "error": "Update failed: pip install failed"},
], ids=["installed", "handed-to-the-guard"])
async def test_a_milo_update_handed_to_a_restart_announces_no_outcome(router, result, caplog):
    """Both hand the unit to a restart: the reboot into the trial, or the guard
    putting the previous release back. A completion sent now would tell every
    client the update ended — the spinner off, the update screen gone — while
    the unit is about to go dark, and a failure would raise the error banner
    for a rollback that has not happened yet. The release that starts next
    says how it ended."""
    router.update_service.update_program = AsyncMock(return_value=result)
    endpoint = _endpoint(router, "/api/programs/{program_key}/update")
    tasks = BackgroundTasks()

    with caplog.at_level(logging.ERROR, logger="backend.api.programs"):
        assert (await endpoint("milo", VALIDATED, tasks))["status"] == "success"
        await tasks()

    events = [c.args[0] for c in router.state_machine.broadcast.await_args_list]
    assert not [e for e in events if isinstance(e, ProgramUpdateComplete)]
    assert not caplog.records


def _milo_unit(tmp_path, mock_settings_service, step):
    """A real update service over a journal the previous release left at `step`."""
    state = tmp_path / "app-update"
    state.mkdir()
    (state / mj.JOURNAL_NAME).write_text(json.dumps({
        mj.KEY_ORIGIN_COMMIT: "a" * 40, mj.KEY_ORIGIN_TAG: "v0.1.4",
        mj.KEY_TARGET_TAG: "v0.1.5", mj.KEY_TARGET_COMMIT: "b" * 40,
        mj.KEY_STEP: step, mj.KEY_FAILURE: None, mj.KEY_VENV_KEPT: False,
    }))
    with patch.dict("os.environ", {}, clear=True):
        service = UpdateService(
            systemd_manager=MagicMock(), satellite_update_service=MagicMock(),
            settings_service=mock_settings_service,
        )
    service.programs["milo"]["state_path"] = str(state)
    service.programs["milo"]["backup_path"] = str(tmp_path / "backups")
    return service


@pytest.mark.parametrize("step", [mj.TRIAL_STEP, mj.COMMITTED_STEP])
async def test_the_release_that_arrives_inherits_the_milo_claim(tmp_path, mock_settings_service, step):
    """The claim set is memory, and the reboot empties it. During the trial and
    the post-commit phase the journal is the claim: a program update started
    then would be installed beside the dependency set the post-commit phase
    reconciles, and a satellite push would collide with the fleet push."""
    service = _milo_unit(tmp_path, mock_settings_service, step)
    service.can_update_program = AsyncMock(return_value={"can_update": True})
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    r = create_programs_router(service, MagicMock(), state_machine)

    program = _endpoint(r, "/api/programs/{program_key}/update")
    for key in ("go-librespot", "milo"):
        refused = await program(key, VALIDATED, BackgroundTasks())
        assert refused["status"] == "error"
        assert "milo" in refused["message"]
    refused = await _endpoint(r, "/api/programs/satellites/{mac_id}/update-app")(
        "aa:bb:cc:dd:ee:ff", BackgroundTasks()
    )
    assert refused["status"] == "error"
    service.can_update_program.assert_not_awaited()


async def test_the_program_list_says_where_a_milo_update_stands_when_github_fails(
    tmp_path, mock_settings_service,
):
    """The kiosk, rebooted into the trial, draws the update screen from this
    read alone — it saw none of the progress events, which the release that
    left sent. A GitHub outage answering the inventory must not cost it."""
    service = _milo_unit(tmp_path, mock_settings_service, mj.TRIAL_STEP)
    service.get_all_program_status = AsyncMock(side_effect=RuntimeError("api.github.com timed out"))
    r = create_programs_router(service, MagicMock(), MagicMock())

    body = await _endpoint(r, "/api/programs", "GET")()

    assert body["status"] == "error"
    assert body["active_updates"] == ["milo"]
    assert body["milo_update"]["phase"] == "verify"
    assert (body["milo_update"]["from_version"], body["milo_update"]["to_version"]) == (
        "v0.1.4", "v0.1.5",
    )
    assert "last_update" in body


async def test_where_a_milo_update_stands_is_read_without_the_inventory(tmp_path, mock_settings_service):
    """Every client reads it at each resync and before its first frame: it
    must answer from the journal without one version probe or GitHub read,
    which on a freshly rebooted unit take seconds."""
    service = _milo_unit(tmp_path, mock_settings_service, mj.TRIAL_STEP)
    service.get_all_program_status = AsyncMock(side_effect=AssertionError("the inventory was probed"))
    service.get_latest_github_version = AsyncMock(side_effect=AssertionError("GitHub was read"))
    r = create_programs_router(service, MagicMock(), MagicMock())

    body = await _endpoint(r, "/api/programs/milo/update", "GET")()

    assert body["status"] == "success"
    assert body["milo_update"] == service.milo_update_state()
    assert body["milo_update"]["phase"] == "verify"
    assert body["last_update"] is None


async def test_a_banner_closed_on_one_screen_leaves_the_others(tmp_path, mock_settings_service):
    """The kiosk and a phone show the same rollback: closed on one, it is
    announced once so the other drops it too, and a second close, from a
    screen that had not heard yet, announces nothing."""
    service = _milo_unit(tmp_path, mock_settings_service, mj.TRIAL_STEP)
    state = tmp_path / "app-update"
    (state / mj.JOURNAL_NAME).unlink()
    (state / mj.LAST_UPDATE_NAME).write_text(json.dumps({
        mj.KEY_FROM: "v0.1.4", mj.KEY_TO: "v0.1.5", mj.KEY_OUTCOME: "rolled_back",
        mj.KEY_DETAIL: "RuntimeError: boom", mj.KEY_STEP: mj.TRIAL_STEP,
    }))
    state_machine = MagicMock()
    state_machine.broadcast = AsyncMock()
    r = create_programs_router(service, MagicMock(), state_machine)
    close = _endpoint(r, "/api/programs/milo/last-update", "PATCH")

    first = await close(LastUpdatePatch(dismissed=True))
    second = await close(LastUpdatePatch(dismissed=True))

    assert first["last_update"]["dismissed"] is True
    assert second["last_update"]["dismissed"] is True
    sent = [call.args[0] for call in state_machine.broadcast.await_args_list]
    assert [type(event) for event in sent] == [MiloUpdateNoticeDismissed]


async def test_a_milo_update_is_announced_through_the_update_service(router):
    """The install, the reboot and the post-commit phase span two processes,
    so their progress cannot ride on the route's background task: the router
    registers the listener the update service announces through. Unregistered,
    no client would see a phase change, and the update screen would never
    leave the step it was loaded on."""
    router.update_service.set_milo_update_listener.assert_called_once()
    on_progress, on_complete = router.update_service.set_milo_update_listener.call_args.args

    await on_progress({
        "phase": "satellites", "from_version": "v0.1.4", "to_version": "v0.1.5",
        "item": "Canapé", "index": 1, "total": 2,
    })
    await on_complete(True)

    progress, complete = [c.args[0] for c in router.state_machine.broadcast.await_args_list]
    assert progress.to_envelope()["data"] == {
        "source": "program_update", "status": "updating", "program": "milo",
        "phase": "satellites", "from_version": "v0.1.4", "to_version": "v0.1.5",
        "item": "Canapé", "index": 1, "total": 2,
    }
    assert isinstance(complete, ProgramUpdateComplete)
    assert (complete.program, complete.success) == ("milo", True)


async def test_the_retry_forgets_the_failed_checks_before_it_reads_and_a_plain_read_never_does(router):
    """A recheck that read before forgetting would answer the failure it was pressed to get past.

    The screen's Retry on a row whose GitHub read failed is POST /recheck; the
    GET every opening of the screen sends must keep the remembered failure, or
    each opening offline would wait out a fresh GitHub timeout.
    """
    calls = []
    router.update_service.forget_failed_checks = MagicMock(side_effect=lambda: calls.append("forget"))

    async def read():
        calls.append("read")
        return {}

    router.update_service.get_all_program_status = AsyncMock(side_effect=read)

    await _endpoint(router, "/api/programs", "GET")()
    assert calls == ["read"]

    calls.clear()
    body = await _endpoint(router, "/api/programs/recheck")()
    assert calls == ["forget", "read"]
    assert body["status"] == "success"
