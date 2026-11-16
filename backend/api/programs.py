# backend/api/programs.py
"""
API routes for program management — Full version with satellites
"""
import asyncio
import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, Optional
from fastapi import APIRouter, BackgroundTasks, HTTPException
from backend.core.models.audio_state import AudioSource
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

if TYPE_CHECKING:
    from backend.core.state import AudioStateMachine
    from backend.core.updates.satellite import SatelliteUpdateService
    from backend.core.updates.update import UpdateService


logger = logging.getLogger(__name__)

# Mapping from program key to AudioSource for pre-update deactivation
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
    state_machine: "AudioStateMachine"
):
    """Router for local and satellite programs

    Args:
        update_service: Singleton service for version checks and updates
        satellite_update_service: Singleton service for satellite updates
        state_machine: AudioStateMachine for broadcasting events and deactivating active sources
    """
    router = APIRouter(prefix="/api/programs", tags=["programs"])

    satellite_service = satellite_update_service

    # The in-flight update keys. A set, not a map: the only questions asked of
    # it are membership and which keys are local, and a per-key status/progress
    # payload lived here unread for as long as it existed.
    active_updates: set[str] = set()

    # An update key belongs to the server ("local") or to one satellite. The
    # longest prefix first: a MAC never contains "app_" or "camilladsp_", but
    # the bare "satellite_" would swallow both.
    satellite_key_prefixes = ("satellite_app_", "satellite_camilladsp_", "satellite_")

    def _running() -> set[str]:
        """The in-flight keys, `milo` included while the update service says a
        Milō update holds the unit: its journal outlives this process, and the
        release that arrives inherits the claim with it."""
        if update_service.milo_update_active:
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
        scope = _scope(update_key)
        for running in _running():
            if "milo" in (running, update_key) or _scope(running) == scope:
                return running
            if running.startswith("satellite_app_") and update_key.startswith("satellite_app_"):
                return running
        active_updates.add(update_key)
        return None

    def _refuse(blocker: str, update_key: str) -> dict:
        """The refusal envelope, naming what is in the way when it is not this key."""
        if blocker == update_key:
            return {
                "status": "error",
                "message": f"Update already in progress for {update_key}",
            }
        return {
            "status": "error",
            "message": f"An update is already in progress for {blocker}; it must finish first",
        }

    def _create_background_update(
        update_key: str,
        update_fn,
        progress_event_cls: type[ProgramsProgressEvent],
        complete_event_cls: type[ProgramsCompleteEvent],
        identifier: dict,
        pre_update_fn=None,
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
            try:
                # Announced once, at the start: the event's whole payload is the
                # identifier plus a constant status, so firing it again at each
                # phase said nothing a client did not already know.
                await state_machine.broadcast(progress_event_cls(**identifier))

                if pre_update_fn:
                    await pre_update_fn()

                result = await update_fn()
                active_updates.discard(update_key)

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
                active_updates.discard(update_key)
                # Whatever raised, no final state was seen.
                await state_machine.broadcast(_completion({
                    "success": False,
                    "verdict": "did_not_return",
                    "detail": f"{type(e).__name__}: {e}",
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
        await state_machine.broadcast(ProgramUpdateComplete(program="milo", success=success))

    # The install, the reboot and the post-commit phase run in two processes,
    # so a Milō update's progress cannot ride on the background task the route
    # starts: the update service announces it, and this is where it is heard.
    update_service.set_milo_update_listener(_announce_milo_progress, _announce_milo_complete)

    # === SPECIFIC ROUTES (must come BEFORE generic routes) ===

    @router.get("")
    async def get_all_programs():
        """Retrieve the status of all local programs (installed + GitHub)"""
        # In-flight local update keys (satellite keys are prefixed, excluded here).
        # Lets a freshly loaded client reconstruct "updating" state it never saw the
        # WS progress deltas for — after a reload, on a second device, or when the
        # backend restarted mid-update (e.g. a milo self-update). Read after the
        # version probes, never before: an update claimed or finished during
        # that await would otherwise be listed as it stood when the read began.
        # `milo_update` is where a Milō update stands (the shape of
        # program_update_progress), `last_update` how the last one ended.
        def _in_flight() -> dict:
            return {
                "active_updates": sorted(k for k in _running() if not k.startswith("satellite_")),
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

    async def _fleet_target(program_key: str):
        """The version the fleet must run — the server's, resolved once here.

        A satellite has no manifest and no GitHub token; letting it read
        `releases/latest` for itself is what put a client on a version the row
        that started it never named. Unresolvable means the update does not
        start: an unpinned install is worse than a refusal.
        """
        latest = await update_service.get_latest_github_version(program_key)
        if latest.get("status") != "success":
            logger.error(
                f"Cannot resolve the {program_key} version for the fleet: "
                f"{latest.get('message', 'unknown error')}"
            )
            return None
        return latest.get("version")

    @router.post("/satellites/{mac_id}/update")
    async def update_satellite(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite update in the background"""

        satellite_key = f"satellite_{mac_id}"

        blocker = _claim_update(satellite_key)
        if blocker:
            return _refuse(blocker, satellite_key)

        target_version = await _fleet_target("multiroom")
        if not target_version:
            active_updates.discard(satellite_key)
            return {
                "status": "error",
                "message": "Could not resolve the snapclient version to install"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite(mac_id, target_version),
            progress_event_cls=SatelliteUpdateProgress,
            complete_event_cls=SatelliteUpdateComplete,
            identifier={"mac_id": mac_id},
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
            return _refuse(blocker, satellite_key)

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite_app(mac_id),
            progress_event_cls=SatelliteAppUpdateProgress,
            complete_event_cls=SatelliteAppUpdateComplete,
            identifier={"mac_id": mac_id},
            complete_fields=lambda result: {
                "verdict": result.get("verdict"),
                "detail": result.get("detail"),
            },
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"App update started for satellite {mac_id}"
        }

    @router.post("/satellites/{mac_id}/update-camilladsp")
    async def update_satellite_camilladsp(mac_id: str, background_tasks: BackgroundTasks):
        """Launch a satellite CamillaDSP update in the background"""

        satellite_key = f"satellite_camilladsp_{mac_id}"

        blocker = _claim_update(satellite_key)
        if blocker:
            return _refuse(blocker, satellite_key)

        target_version = await _fleet_target("camilladsp")
        if not target_version:
            active_updates.discard(satellite_key)
            return {
                "status": "error",
                "message": "Could not resolve the CamillaDSP version to install"
            }

        do_update = _create_background_update(
            update_key=satellite_key,
            update_fn=lambda: satellite_service.update_satellite_camilladsp(mac_id, target_version),
            progress_event_cls=SatelliteCamillaDspUpdateProgress,
            complete_event_cls=SatelliteCamillaDspUpdateComplete,
            identifier={"mac_id": mac_id},
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
        background_tasks: BackgroundTasks
    ):
        """Launch a local program update in the background"""

        blocker = _claim_update(program_key)
        if blocker:
            return _refuse(blocker, program_key)

        try:
            can_update = await update_service.can_update_program(
                program_key, payload.target, payload.version
            )
        except Exception:
            active_updates.discard(program_key)
            raise

        if not can_update.get("can_update"):
            active_updates.discard(program_key)
            return {
                "status": "error",
                "message": can_update.get("reason", "Cannot update")
            }

        async def _deactivate_if_needed():
            audio_source = PROGRAM_TO_AUDIO_SOURCE.get(program_key)
            if audio_source and state_machine.system_state.active_source == audio_source:
                await state_machine.transition_to_source(AudioSource.NONE)

        do_update = _create_background_update(
            update_key=program_key,
            update_fn=lambda: update_service.update_program(
                program_key, payload.target, payload.version
            ),
            progress_event_cls=ProgramUpdateProgress,
            complete_event_cls=ProgramUpdateComplete,
            identifier={"program": program_key},
            pre_update_fn=_deactivate_if_needed,
        )

        background_tasks.add_task(do_update)

        return {
            "status": "success",
            "message": f"Update started for {program_key}",
            "available_version": can_update.get("available_version")
        }

    return router
