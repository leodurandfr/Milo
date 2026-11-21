# backend/api/programs.py
"""
API routes for program management — Full version with satellites
"""
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Awaitable, Callable, Dict, Optional
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from backend.core.models.audio_state import AudioSource
from backend.core.models.presence import PresenceAction
from backend.core.models.ws_events import (
    MiloUpdateNoticeDismissed,
    ProgramsCompleteEvent,
    ProgramsProgressEvent,
    ProgramUpdateComplete,
    ProgramUpdateProgress,
    SatelliteAppUpdateComplete,
    SatelliteAppUpdateProgress,
    SatelliteCamillaDspUpdateComplete,
    SatelliteCamillaDspUpdateProgress,
    SatelliteUpdateComplete,
    SatelliteUpdateProgress,
)
from backend.api.models import LastUpdatePatch, ProgramUpdateRequest
from backend.api.route_helpers import PresenceAnswer, presence_answer, require_presence

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine
    from backend.core.system.presence import PresenceService
    from backend.core.updates.satellite import SatelliteUpdateService
    from backend.core.updates.update import UpdateService


logger = logging.getLogger(__name__)

# The source each program's daemon plays for: its session ends before an install.
PROGRAM_TO_AUDIO_SOURCE = {
    'go-librespot': AudioSource.SPOTIFY,
    'shairport-sync': AudioSource.AIRPLAY,
    'qobuz-proxy': AudioSource.QOBUZ,
    # Stopping Navidrome mid-update kills the localhost stream mpv is playing.
    'navidrome': AudioSource.MUSIC_LIBRARY,
}


def refuse_while_milo_updates(update_service: Optional["UpdateService"], action: str) -> None:
    """HTTP 409 for a reboot or a power-off asked while a Milō update holds the unit.

    The update survives being cut — the guard puts the previous release back
    at the next start — but cutting it is never what the press meant: during
    the install it throws the update away, during the trial it counts as a
    failed start of the new release. The shutdown inhibitor the update holds
    (milo-update-inhibit.service) is gone once the update reboots, while the
    trial and the post-commit phase still hold the unit, and while it holds,
    logind refuses with an error that names nothing.
    """
    if update_service is None or not update_service.milo_update_active:
        return
    state = update_service.milo_update_state() or {}
    versions = " → ".join(v for v in (state.get("from_version"), state.get("to_version")) if v)
    update = f"a Milō update ({versions})" if versions else "a Milō update"
    # Warning, not error: the refusal is the route working, and an error
    # would raise the backend-error banner over the update screen.
    logger.warning(f"Refusing the {action}: {update} is in progress")
    raise HTTPException(
        status_code=409,
        detail=f"The {action} is refused while {update} is in progress; it must finish first",
    )


def create_programs_router(
    update_service: "UpdateService",
    satellite_update_service: "SatelliteUpdateService",
    state_machine: "AudioStateMachine",
    presence_service: Optional["PresenceService"] = None,
):
    """Router for local and satellite programs

    Args:
        update_service: Singleton service for version checks and updates
        satellite_update_service: Singleton service for satellite updates
        state_machine: AudioStateMachine for broadcasting events and deactivating active sources
        presence_service: the proof of presence an upstream install waits for
    """
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    satellite_service = satellite_update_service

    # The in-flight update keys. A set, not a map: the only questions asked of
    # it are membership and which keys are local, and a per-key status/progress
    # payload lived here unread for as long as it existed.
    active_updates: set[str] = set()

    # Set, then replaced, whenever a claim may have ended — a key released, a
    # Milō update finished —, so a push the admission owes a satellite waits
    # for its blocker instead of being dropped (`_push_app_at_admission`).
    # Every path that ends a claim goes through `_release` or the Milō
    # update's completion listener: a reboot is the only other end, and it
    # takes the waiter with it.
    claims_moved = asyncio.Event()

    def _claims_moved() -> None:
        nonlocal claims_moved
        claims_moved.set()
        claims_moved = asyncio.Event()

    def _release(update_key: str) -> None:
        """Give a claimed key back, and wake whoever waits on a claim."""
        active_updates.discard(update_key)
        _claims_moved()

    # An update key belongs to the server ("local") or to one satellite. The
    # longest prefix first: a MAC never contains "app_" or "camilladsp_", but
    # the bare "satellite_" would swallow both.
    satellite_key_prefixes = ("satellite_app_", "satellite_camilladsp_", "satellite_")

    def _running() -> set[str]:
        """The in-flight keys, `milo` included while the update service says a
        Milō update holds the installs: its journal outlives this process, and
        the release that arrives inherits the claim with it. A trial let go of
        still holds it (`milo_update_holds_installs`): only a restart frees it."""
        if update_service.milo_update_holds_installs:
            return active_updates | {"milo"}
        return set(active_updates)

    def _scope(update_key: str) -> str:
        """"local", or the satellite this key drives."""
        for prefix in satellite_key_prefixes:
            if update_key.startswith(prefix):
                return update_key[len(prefix):]
        return "local"

    def _claim_update(update_key: str) -> Optional[str]:
        """Reserve an update key, or name the in-flight update that forbids it.

        Synchronous on purpose, and called by the route rather than by
        _create_background_update: `update_program` awaits GitHub
        (`can_update_program`, up to ~20 s) between deciding and starting, and
        a check separated from its write by an await lets two clients both
        pass. The frontend store blocks a same-client double click; it cannot
        block a second device. Every path out of a route between the claim and
        `background_tasks.add_task` must release the key.

        The policy is the one the update screen draws, enforced here because
        that is the side a second device cannot walk around. `milo` blocks
        everything and is blocked by everything: it reconciles the whole
        dependency set — so a program update running beside it is installed
        twice, each run stopping the service the other started — pushes the
        client app to every satellite, colliding with that satellite's own 409,
        and then reboots the unit out from under the task polling it. Two local
        programs block each other for the deploy wrapper they share. A
        satellite is a separate machine, so it blocks only its own three keys —
        except that two app pushes block each other whatever the satellite: each
        restarts a speaker, and one room is cut at a time.

        Returns None once the key is claimed.
        """
        blocker = _blocker(update_key)
        if blocker is None:
            active_updates.add(update_key)
        return blocker

    def _blocker(update_key: str) -> Optional[str]:
        """The in-flight update that forbids `update_key` (`_claim_update`'s
        policy), or None. Claims nothing."""
        scope = _scope(update_key)
        for running in _running():
            if "milo" in (running, update_key) or _scope(running) == scope:
                return running
            if running.startswith("satellite_app_") and update_key.startswith("satellite_app_"):
                return running
        return None

    def _refusal(message: str) -> HTTPException:
        """An update the unit will not start now: 409, like the power routes'
        `refuse_while_milo_updates`. Warning, not error: the refusal is the
        route working, and whatever failed behind it has logged its own line."""
        logger.warning(f"Update refused: {message}")
        return HTTPException(status_code=409, detail=message)

    def _cannot_update(check: dict) -> HTTPException:
        """The refusal of a check `can_update_program` did not pass."""
        return _refusal(check.get("reason", "Cannot update"))

    def _refuse(blocker: str, update_key: str) -> HTTPException:
        """The refusal naming what is in the way, when it is not this key."""
        if blocker == "milo" and "milo" not in active_updates and not update_service.milo_update_active:
            # Only the journal claims it: a trial let go of. Nothing runs, and
            # waiting finishes nothing — the restart it was let go of for does.
            return _refusal(
                "The Milō update on trial could not be committed; no other update "
                "starts before the unit is restarted"
            )
        if blocker == update_key:
            return _refusal(f"Update already in progress for {update_key}")
        return _refusal(f"An update is already in progress for {blocker}; it must finish first")

    def _create_background_update(
        update_key: str,
        update_fn,
        progress_event_cls: type[ProgramsProgressEvent],
        complete_event_cls: type[ProgramsCompleteEvent],
        identifier: dict,
        complete_fields: Optional[Callable[[dict], dict]] = None,
    ):
        """Create the background task that runs one update and broadcasts it.

        The key is already claimed by the route through _claim_update().
        `complete_fields` maps the result onto the completion event's fields
        beyond `success`, for the events that carry more.
        Returns an async do_update coroutine to pass to background_tasks.add_task().
        """
        def _completion(result: dict):
            extra = complete_fields(result) if complete_fields else {}
            return complete_event_cls(**identifier, success=result["success"], **extra)

        async def do_update():
            # The claim is given back exactly once. The run gives it back as
            # soon as the update returns, before the completion is broadcast,
            # and a second claim on the same key may land during that
            # broadcast — a press on another device, or the admission push the
            # release just woke: releasing again would discard that claim and
            # leave its push running unclaimed.
            held = True

            def release_once() -> None:
                nonlocal held
                if held:
                    held = False
                    _release(update_key)

            try:
                await _run_update(release_once)
            finally:
                # Whatever ended it — a cancellation included, which skips
                # every `except Exception` —, the key is free: a claim left
                # behind refuses every app push and the Milō update itself
                # until the backend restarts.
                release_once()

        async def _run_update(release: Callable[[], None]):
            try:
                # Announced once, at the start: the event's whole payload is the
                # identifier plus a constant status, so firing it again at each
                # phase said nothing a client did not already know.
                await state_machine.broadcast(progress_event_cls(**identifier))

                result = await update_fn()
                release()

                if result.get("pending"):
                    # A Milō update handed the unit to a restart: the release
                    # that starts next decides how it ended, and announces it
                    # (or GET /api/programs' `last_update` says so).
                    return

                if not result["success"]:
                    logger.error(f"Update {update_key} failed: {result.get('error', 'Update failed')}")

                await state_machine.broadcast(_completion(result))

            except Exception as e:
                logger.error(f"Update {update_key} failed: {e}")
                release()
                # Whatever raised, no final state was seen.
                await state_machine.broadcast(_completion({
                    "success": False,
                    "verdict": "did_not_return",
                    "detail": f"{type(e).__name__}: {e}",
                    "error": f"{type(e).__name__}: {e}",
                }))

        return do_update

    async def _announce_milo_progress(state: Dict[str, Any]) -> None:
        await state_machine.broadcast(ProgramUpdateProgress(
            program="milo",
            phase=state["phase"],
            from_version=state["from_version"],
            to_version=state["to_version"],
            item=state["item"],
            index=state["index"],
            total=state["total"],
        ))

    async def _announce_milo_complete(success: bool) -> None:
        _claims_moved()
        await state_machine.broadcast(ProgramUpdateComplete(program="milo", success=success))

    async def _end_session_of(program_key: str) -> None:
        """End the session of the source `program_key`'s daemon plays for, when
        it is the active one — before its install stops that daemon."""
        audio_source = PROGRAM_TO_AUDIO_SOURCE.get(program_key)
        if audio_source and state_machine.system_state.active_source == audio_source:
            await state_machine.transition_to_source(AudioSource.NONE)

    update_service.set_session_ender(_end_session_of)

    # The install, the reboot and the post-commit phase run in two processes,
    # so a Milō update's progress cannot ride on the background task the route
    # starts: the update service announces it, and this is where it is heard.
    update_service.set_milo_update_listener(_announce_milo_progress, _announce_milo_complete)

    # === SPECIFIC ROUTES (must come BEFORE generic routes) ===

    async def _all_programs() -> dict:
        """The body of GET /api/programs and of POST /api/programs/recheck."""
        # In-flight local update keys (satellite keys are prefixed, excluded here).
        # Lets a freshly loaded client reconstruct "updating" state it never saw the
        # WS progress deltas for — after a reload, on a second device, or when the
        # backend restarted mid-update (e.g. a milo self-update). Read after the
        # version probes, never before: an update claimed or finished during
        # that await would otherwise be listed as it stood when the read began.
        # `milo_update` is where a Milō update stands (the shape of
        # program_update_progress), `last_update` how the last one ended.
        # `milo` is listed while a Milō update runs (`milo_update_active`), not
        # while a trial let go of still holds the installs: nothing runs then,
        # and listed, every client drew the Milō row "Updating…" for good and
        # disabled every other row, read as an update to wait for when the
        # restart is the gesture the let-go frees. `_blocker` still refuses
        # the installs, with a 409 that says so.
        def _in_flight() -> dict:
            running = active_updates | ({"milo"} if update_service.milo_update_active else set())
            return {
                "active_updates": sorted(k for k in running if not k.startswith("satellite_")),
                "milo_update": update_service.milo_update_state(),
                "last_update": update_service.last_update_record(),
            }

        try:
            results = await update_service.get_all_program_status()
            return {
                "status": "success",
                "programs": results,
                "count": len(results),
                **_in_flight(),
            }
        except Exception as e:
            # HTTP 200 + status:error is the documented resilience pattern for a
            # /status-style read — the settings screen must render with whatever
            # it has rather than collapsing on a GitHub timeout. It is not a
            # reason to keep the failure out of the journal and the banner.
            logger.error(f"Error listing program status: {e}")
            return {
                "status": "error",
                "message": str(e),
                "programs": {},
                "count": 0,
                **_in_flight(),
            }

    @router.get("")
    async def get_all_programs():
        """Retrieve the status of all local programs (installed + GitHub).

        A row whose GitHub read failed carries `check_failed: true`.
        """
        return await _all_programs()

    @router.post("/recheck")
    async def recheck_programs():
        """Ask GitHub again for every row whose check failed, and answer the GET's body.

        The screen's Retry on such a row: the failure is otherwise remembered
        for its retry delay, and a read answered from that memory would repeat
        it. An action — it drops the failures the next reads would have
        answered, the satellites' screen included — hence a POST, not a GET flag.
        """
        update_service.forget_failed_checks()
        return await _all_programs()

    @router.get("/milo/update")
    async def get_milo_update():
        """Where an update of Milō itself stands (`milo_update`), and how the last one ended (`last_update`).

        The two keys GET /api/programs carries, without its version probes and
        GitHub reads: a client reads this at every resync, and before the first
        frame after its boot screen, which must already be the update screen
        while one runs.
        """
        return {
            "status": "success",
            "milo_update": update_service.milo_update_state(),
            "last_update": update_service.last_update_record(),
        }

    @router.patch("/milo/last-update")
    async def patch_milo_last_update(payload: LastUpdatePatch):
        """Close the last Milō update's banner for every screen (`{"dismissed": true}`).

        The outcome is read with GET /api/programs/milo/update (`last_update`).
        Idempotent: with no outcome to close, one already closed, or an update
        running (it replaces the outcome itself), nothing changes. Answers the
        outcome as it now stands.
        """
        if await update_service.dismiss_last_update():
            await state_machine.broadcast(MiloUpdateNoticeDismissed())
        return {"status": "success", "last_update": update_service.last_update_record()}

    # === SATELLITE ROUTES (specific, before generic routes) ===

    @router.get("/satellites")
    async def get_satellites():
        """Retrieve the list of detected satellites with their versions"""
        try:
            # Fetch all data in parallel: satellite discovery + cached version lookups
            # Online satellites, plus the ones that hold an app push they did
            # not take: a failure must not vanish with the unit it is about.
            satellites_task = satellite_service.list_satellites()
            snapclient_task = update_service.get_latest_github_version("multiroom")
            payload_task = satellite_service.get_client_payload_version()
            version_task = satellite_service.get_server_version()

            camilladsp_task = update_service.get_latest_github_version("camilladsp")

            satellites, snapclient_github, server_payload, server_version, camilladsp_github = (
                await asyncio.gather(
                    satellites_task, snapclient_task, payload_task, version_task, camilladsp_task
                )
            )

            latest_version = snapclient_github.get("version") if snapclient_github.get("status") == "success" else None
            camilladsp_latest = camilladsp_github.get("version") if camilladsp_github.get("status") == "success" else None

            for satellite in satellites:
                mac = satellite.get("mac_id")
                # In-flight update flags, keyed to the same active_updates entries
                # the POST routes register — so a freshly loaded client can show
                # "updating" without having seen the WS progress deltas.
                satellite["updating"] = f"satellite_{mac}" in active_updates
                satellite["app_updating"] = f"satellite_app_{mac}" in active_updates
                satellite["camilladsp_updating"] = f"satellite_camilladsp_{mac}" in active_updates

                # Not "is the target newer": the server now sends a version
                # below the installed one too — ending a trial of an unvalidated
                # release, or following a manifest rolled back. Asking only
                # about newer leaves such a satellite reading "up to date" with
                # a disabled button and no way back.
                online = satellite.get("online", False)
                satellite["latest_version"] = latest_version
                satellite["update_available"] = online and bool(latest_version) and (
                    satellite.get("snapclient_version") != latest_version
                )
                # Displayed and decided are two different values. The row
                # shows the server's own version string — the same numbering as
                # its row, because both halves ship from one commit — while the
                # button is lit by the payload, the fingerprint of the
                # `milo-client/` tree the tarball actually carries. Most
                # releases never touch that directory, and deciding on the
                # displayed version lit the button across the whole fleet for a
                # byte-identical push every time.
                satellite["server_version"] = server_version
                satellite["app_update_available"] = (
                    bool(server_payload) and satellite.get("app_payload") != server_payload
                )
                # CamillaDSP update
                satellite["camilladsp_latest_version"] = camilladsp_latest
                satellite["camilladsp_update_available"] = online and bool(camilladsp_latest) and (
                    satellite.get("camilladsp_version") != camilladsp_latest
                )

            return {
                "status": "success",
                "satellites": satellites,
                "count": len(satellites)
            }
        except Exception as e:
            # Same resilience pattern as GET "" above, same duty to log: the
            # satellite list is built from four awaits, and a discovery that
            # throws used to leave the screen showing "no satellite" with
            # nothing anywhere to say why.
            logger.error(f"Error listing satellites: {e}")
            return {
                "status": "error",
                "message": str(e),
                "satellites": [],
                "count": 0
            }

    async def _fleet_target(program_key: str, asset: str) -> Optional[tuple[str, Optional[str]]]:
        """The version the fleet must run — the server's, resolved once here —
        and the digest `dependencies.env` declares for the file `asset` that
        version downloads, None for a version past the manifest.

        A satellite has no manifest and no GitHub token; letting it read
        `releases/latest` for itself is what put a client on a version the row
        that started it never named. Unresolvable means the update does not
        start: an unpinned install is worse than a refusal. The digest travels
        with the version for the same reason: the satellite checks its download
        against it, and its root installer checks its own copy again.
        """
        latest = await update_service.get_latest_github_version(program_key)
        if latest.get("status") != "success":
            logger.error(
                f"Cannot resolve the {program_key} version for the fleet: "
                f"{latest.get('message', 'unknown error')}"
            )
            return None
        version = latest.get("version")
        if not version:
            logger.error(f"Cannot resolve the {program_key} version for the fleet: GitHub named none")
            return None
        return version, update_service.declared_sha256(program_key, version, asset)

    async def _claim_fleet_push(
        satellite_key: str, program_key: str, asset: str, label: str, presence: PresenceAnswer,
    ) -> tuple[str, Optional[str]]:
        """What a satellite push installs, `(version, sha256)`, with its key
        claimed — or the refusal, raised.

        A version with no digest is one past the manifest: the fleet follows the
        server, and the server runs one while the owner tries it
        (`updates.forced_versions`). The speaker would install it as root with
        nothing to check it against, so the push waits for a proof of presence,
        as the server's own `target: upstream` install does. As there, every
        refusal comes before the gesture and the key is claimed after it: a
        request waiting for a gesture holds nothing.
        """
        blocker = _blocker(satellite_key)
        if blocker:
            raise _refuse(blocker, satellite_key)
        target = await _fleet_target(program_key, asset)
        if target is None:
            raise _refusal(f"Could not resolve the {label} version to install")
        if target[1] is None:
            await require_presence(presence_service, PresenceAction.SATELLITE_PROGRAM_UPSTREAM, presence)
        # The claim checks and writes with no await between: a second device
        # that passed the same checks meanwhile is refused here.
        blocker = _claim_update(satellite_key)
        if blocker:
            raise _refuse(blocker, satellite_key)
        return target

    def _verdict_fields(result: dict) -> dict:
        """What a satellite update's completion carries beyond `success`."""
        return {"verdict": result.get("verdict"), "detail": result.get("detail")}

    def _app_push(mac_id: str):
        """The background update that pushes the client app to one satellite:
        the row's button and the satellite's admission run the same one."""
        return _create_background_update(
            update_key=f"satellite_app_{mac_id}",
            update_fn=lambda: satellite_service.update_satellite_app(mac_id),
            progress_event_cls=SatelliteAppUpdateProgress,
            complete_event_cls=SatelliteAppUpdateComplete,
            identifier={"mac_id": mac_id},
            complete_fields=_verdict_fields,
        )

    # The satellites whose admission push waits for a claim to end: a second
    # admission of one of them (a reconnect meanwhile) adds nothing.
    admissions_waiting: set[str] = set()

    async def _push_app_at_admission(mac_id: str, owed: Callable[[], Awaitable[bool]]) -> None:
        """The push a satellite's admission owes it (`catch_up_app`), under the
        claim the row's button takes.

        A Milō update — its trial, its own fleet push, which read the fleet
        before this satellite was back — or another app push in flight holds
        it back, as it would hold back a press, and it waits for that claim to
        end rather than being dropped: two speakers plugged back in together
        are pushed one after the other. Each time the way is clear the
        satellite is asked again whether it is owed one (the claim that held
        it back may have pushed it), and the claim is taken after, with no
        await between its check and its write.
        """
        if mac_id in admissions_waiting:
            return
        satellite_key = f"satellite_app_{mac_id}"
        admissions_waiting.add(mac_id)
        try:
            waiting_for = None
            while True:
                # Taken before the checks: a claim that ends during `owed()`
                # has already set it, so the wait below cannot miss it.
                moved = claims_moved
                blocker = _blocker(satellite_key)
                if blocker is None:
                    if not await owed():
                        return
                    blocker = _claim_update(satellite_key)
                    if blocker is None:
                        break
                if blocker != waiting_for:
                    logger.info(
                        f"Satellite {mac_id}: its client app push waits for {blocker} to finish"
                    )
                    waiting_for = blocker
                await moved.wait()
        finally:
            admissions_waiting.discard(mac_id)
        await _app_push(mac_id)()

    satellite_service.set_admission_push_runner(_push_app_at_admission)

    @router.post("/satellites/{mac_id}/update")
    async def update_satellite(
        mac_id: str,
        background_tasks: BackgroundTasks,
        presence: PresenceAnswer = Depends(presence_answer),
    ):
        """Launch a satellite's snapclient update in the background: the .deb
        the server's own multiroom update installs, and its digest."""

        satellite_key = f"satellite_{mac_id}"

        target = await _claim_fleet_push(satellite_key, "multiroom", "snapclient", "snapclient", presence)
        target_version, sha256 = target

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite(mac_id, target_version, sha256),
            progress_event_cls=SatelliteUpdateProgress,
            complete_event_cls=SatelliteUpdateComplete,
            identifier={"mac_id": mac_id},
            complete_fields=_verdict_fields,
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for satellite {mac_id}"
        }

    @router.post("/satellites/{mac_id}/update-app")
    async def update_satellite_app(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite app update in the background"""

        satellite_key = f"satellite_app_{mac_id}"

        blocker = _claim_update(satellite_key)
        if blocker:
            raise _refuse(blocker, satellite_key)

        background_tasks.add_task(_app_push(mac_id))

        return {
            "status": "success",
            "message": f"App update started for satellite {mac_id}"
        }

    @router.post("/satellites/{mac_id}/update-camilladsp")
    async def update_satellite_camilladsp(
        mac_id: str,
        background_tasks: BackgroundTasks,
        presence: PresenceAnswer = Depends(presence_answer),
    ):
        """Launch a satellite CamillaDSP update in the background: the release
        archive the server's own update installs, and its digest."""

        satellite_key = f"satellite_camilladsp_{mac_id}"

        target = await _claim_fleet_push(satellite_key, "camilladsp", "main", "CamillaDSP", presence)
        target_version, sha256 = target

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite_camilladsp(
                mac_id, target_version, sha256
            ),
            progress_event_cls=SatelliteCamillaDspUpdateProgress,
            complete_event_cls=SatelliteCamillaDspUpdateComplete,
            identifier={"mac_id": mac_id},
            complete_fields=_verdict_fields,
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"CamillaDSP update started for satellite {mac_id}"
        }

    # === GENERIC ROUTES (must come AFTER specific routes) ===

    @router.get("/{program_key}/installed")
    async def get_program_installed_version(program_key: str):
        """Retrieve only the installed version of a program"""
        try:
            result = await update_service.get_installed_version(program_key)
            return {
                "status": "success",
                "installed": result
            }
        except Exception as e:
            # Same resilience pattern as the two reads above, same duty to log.
            logger.error(f"Error reading installed version of {program_key}: {e}")
            return {
                "status": "error",
                "message": str(e),
                "installed": None
            }

    @router.post("/{program_key}/update")
    async def update_program(
        program_key: str,
        payload: ProgramUpdateRequest,
        background_tasks: BackgroundTasks,
        presence: PresenceAnswer = Depends(presence_answer),
    ):
        """Launch a local program update in the background.

        `target: "upstream"` installs, as root, a release nothing in this
        checkout pinned — whatever GitHub serves under that name — so it
        waits for a proof of presence (`core/system/presence.py`). The
        validated target is the manifest's own pin, and stays open. Every
        refusal the route can answer comes before the gesture — the owner
        is never asked for one the request then refuses — and the key is
        claimed after it: a request waiting for a gesture holds nothing.
        """
        if payload.target == "upstream":
            blocker = _blocker(program_key)
            if blocker:
                raise _refuse(blocker, program_key)
            can_update = await update_service.can_update_program(
                program_key, payload.target, payload.version
            )
            if not can_update.get("can_update"):
                raise _cannot_update(can_update)
            await require_presence(presence_service, PresenceAction.PROGRAM_UPSTREAM, presence)
            # The claim checks and writes with no await between: a second
            # device that passed the same checks meanwhile is refused here.
            blocker = _claim_update(program_key)
            if blocker:
                raise _refuse(blocker, program_key)
        else:
            blocker = _claim_update(program_key)
            if blocker:
                raise _refuse(blocker, program_key)

            try:
                can_update = await update_service.can_update_program(
                    program_key, payload.target, payload.version
                )
            except Exception:
                _release(program_key)
                raise

            if not can_update.get("can_update"):
                _release(program_key)
                raise _cannot_update(can_update)

        do_update = _create_background_update(
            update_key=program_key,
            update_fn=lambda: update_service.update_program(
                program_key, payload.target, payload.version
            ),
            progress_event_cls=ProgramUpdateProgress,
            complete_event_cls=ProgramUpdateComplete,
            identifier={"program": program_key},
            complete_fields=lambda result: {"error": result.get("error")},
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for {program_key}",
            "available_version": can_update.get("available_version")
        }

    return router
