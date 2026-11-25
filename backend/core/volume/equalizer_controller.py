# backend/core/volume/equalizer_controller.py
"""
EqualizerController - the one door between a stored level and a speaker.

Delegates local/remote routing to EqualizerRouter and adds, per speaker:
- one command in flight at a time, carrying the latest value asked for;
- the operator's limits, applied here and nowhere else on the way out;
- the timeout and retry policy.
"""

import asyncio
import logging
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING

from backend.core.equalizer.client_proxy import (
    CommandNotApplied,
    SatelliteUnreachable,
    is_satellite_refusal,
    is_satellite_unreachable,
)
from backend.shared.background import BackgroundTaskSet

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.equalizer_router import EqualizerRouter


class Delivery(Enum):
    """What became of one command to one speaker — truthy only when applied.

    A route answers a speaker that refused (502: it holds the value for its
    DSP's return) and one nothing reached (503: it holds nothing, and keeps
    what it plays until the next change or its admission) with two different
    sentences, so the door says which, where a bool said only "not applied"
    and a speaker nothing reached was answered "refused it".
    """

    APPLIED = "applied"          # taken, or nothing to send (an amp owns the level)
    NOT_SENT = "not_sent"        # the router skipped an offline speaker, or no door
    REFUSED = "refused"          # answered and refused, or failed in a way that is no absence
    UNREACHABLE = "unreachable"  # nothing reached a satellite: its 503, or no answer in time

    def __bool__(self) -> bool:
        return self is Delivery.APPLIED


class _Channel:
    """One setting of one speaker: the latest value asked for, and who waits.

    A speaker is sent one command at a time, and each command carries the value
    that is latest *when it leaves*. A burst therefore reaches the speaker as
    the command in flight plus one more with the final value, and two commands
    for one speaker can never overtake each other on two pooled connections.
    """

    __slots__ = ("value", "force", "report", "waiters", "task")

    def __init__(self) -> None:
        self.value: Any = None
        self.force = False
        self.report = False
        self.waiters: List[asyncio.Future] = []
        self.task: Optional[asyncio.Task] = None


class EqualizerController:
    """
    Hardware abstraction layer for CamillaDSP volume control.

    Delegates local/remote routing to EqualizerRouter. Adds, per speaker, one
    ordered command at a time carrying the latest value, the operator's limits,
    and the retry policy.
    """

    DEFAULT_TIMEOUT = 5.0  # seconds
    RETRY_ATTEMPTS = 2
    RETRY_DELAY = 0.5  # seconds

    def __init__(self, equalizer_router=None, client_registry=None,
                 clamp: Optional[Callable[[float], float]] = None):
        """
        Initialize EqualizerController.

        Args:
            equalizer_router: EqualizerRouter for local/remote volume routing
            client_registry: Registry; says whether a refusing speaker is still online
            clamp: The operator's limits, applied here on the way out for every
                caller at once. The store keeps its levels inside them, but not
                every level comes from the store: startup_volume_db is checked
                against the limits only when it is saved.
        """
        self.logger = logging.getLogger(__name__)
        self._router: Optional["EqualizerRouter"] = equalizer_router
        self._registry: Optional["ClientRegistryService"] = client_registry
        self._timeout = self.DEFAULT_TIMEOUT
        self._clamp: Callable[[float], float] = clamp or (lambda volume_db: volume_db)
        self._volume_channels: Dict[str, _Channel] = {}
        self._mute_channels: Dict[str, _Channel] = {}
        self._refusing: set = set()
        # The (what, mac_id) spells of forced sends a satellite refused — an
        # admission's, retried by the reconcile sweep for as long as the
        # satellite's CamillaDSP stays down, or a restore's. Said once, until
        # one is applied or the admission's outage ends (end_refusal_spell).
        self._forced_refusing: set = set()
        self._bg = BackgroundTaskSet(self.logger, "equalizer_controller")

    def set_registry(self, registry):
        """Set the client registry (for dependency injection after init)."""
        self._registry = registry

    @staticmethod
    def _is_success(result: dict) -> bool:
        """Interpret an EqualizerRouter answer as "the command was applied".

        A refusal never reaches here — the router raises `CommandNotApplied`
        for it — so the answer is a success or a skip, and the router skips
        for two opposite reasons, only one of which is an outcome.
        `external_volume_control` means there was nothing to send — a DAC
        client owns its own volume, and the caller must still record the level
        it asked for. `client_offline` means the command was *not* delivered;
        counting it as applied is what wrote the store for a satellite that
        never heard the change and then reported success.
        """
        if not result:
            return False
        status = result.get("status", "")
        if status == "skipped":
            return result.get("reason") != "client_offline"
        return status == "success"

    # ========== Single Client Operations ==========

    def submit_volume(self, mac_id: str, volume_db: float, force: bool = False,
                      report: bool = False) -> asyncio.Future:
        """Ask for `volume_db` on a speaker; resolves to the `Delivery` of the
        command that carried it or a later value — truthy once applied.

        Synchronous on purpose: a caller that writes a level to the store and
        submits it in the same step, with no await in between, submits in the
        order it wrote. The last value submitted is therefore the stored one, and
        it is the one the speaker ends on.

        `report` marks a request an operator is answered for (a route's 502):
        its refusal is logged even inside a spell (`_refused`).
        """
        return self._submit(self._volume_channels, mac_id, self._clamp(volume_db), force,
                            self._send_volume, report)

    async def set_equalizer_volume(self, mac_id: str, volume_db: float, force: bool = False,
                                   report: bool = False) -> Delivery:
        """
        Set volume for a single client via EqualizerRouter.

        Args:
            mac_id: Client identifier (mac_id from registry)
            volume_db: Target volume in dB, clamped to the operator's limits here
            force: Bypass online check in router (for reconnection sync)
            report: An operator's request, whose refusal is always logged

        Returns:
            The command's `Delivery`, truthy only when applied
        """
        return await self.submit_volume(mac_id, volume_db, force=force, report=report)

    async def _send_volume(self, mac_id: str, channel: _Channel, force: bool,
                           report: bool) -> Delivery:
        """One volume command, retried on timeout with the value latest at each try."""
        if not self._router:
            self.logger.warning(f"Cannot set volume for {mac_id}: router not configured")
            return Delivery.NOT_SENT
        for attempt in range(self.RETRY_ATTEMPTS + 1):
            try:
                result = await asyncio.wait_for(
                    self._router.set_volume(mac_id, channel.value, force=force),
                    timeout=self._timeout
                )
                return self._outcome(mac_id, result, "Volume")
            except asyncio.TimeoutError:
                if attempt < self.RETRY_ATTEMPTS:
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue
                return self._timed_out(mac_id, "Volume", report)
            except CommandNotApplied as e:
                return self._refused(mac_id, e, force, "Volume", report)
            except Exception as e:
                self._failed(mac_id, "Volume", e, report)
                return Delivery.REFUSED
        return Delivery.UNREACHABLE

    def _failed(self, mac_id: str, what: str, error: BaseException, report: bool) -> None:
        """A command that failed for no refusal and no absence (a proxy fault):
        a warning, unless an operator's request (`report`) is answered 502 for
        it — a speaker still online (VolumeService._failure) — and then it is
        that answer's one error line."""
        online = self._registry.is_client_online(mac_id) if self._registry else True
        log = self.logger.error if report and online else self.logger.warning
        log(f"Failed to set {what.lower()} for {mac_id}: {error}")

    def _timed_out(self, mac_id: str, what: str, report: bool) -> Delivery:
        """A command nothing answered within `_timeout`.

        A satellite whose packets are dropped (its Wi-Fi gone, its power cut
        before snapserver marks it offline) answers nothing, and this wait ends
        before the proxy's own: nothing reached it, so it is `UNREACHABLE`, a
        warning even for a `report`ed request, which its route answers 503 —
        as the same speaker's equalizer writes are at the same moment. This
        unit's own CamillaDSP has no admission to wait for: its silence is a
        refusal (`_failed`), answered 502 with the request's one error line.
        """
        local = self._registry.is_local_client(mac_id) if self._registry else False
        if local:
            self._failed(mac_id, what, TimeoutError(f"no answer in {self._timeout} s"), report)
            return Delivery.REFUSED
        self.logger.warning(f"Failed to set {what.lower()} for {mac_id}: no answer in {self._timeout} s")
        return Delivery.UNREACHABLE

    def _refused(self, mac_id: str, error: CommandNotApplied, force: bool,
                 what: str, report: bool = False) -> Delivery:
        """A command the router raised for: say once when a speaker that is
        still online starts refusing — not on every step of a turn it keeps
        refusing.

        A `report`ed command is an operator's request the route answers 502
        for, and says its own refusal whatever the spell: a satellite's
        CamillaDSP can stop, come back and stop again while its snapclient
        stays connected, and with nothing sent in between, no applied command
        and no admission ends the spell — the second outage's refused request
        left no line anywhere. It is the route's one line, so it is an error
        whichever arm below it takes: the route logs none.

        This unit's DSP refusing, and a satellite that answered and refused
        (4xx), are the refusals reported. A satellite that could not be
        reached (503) is no refusal: `UNREACHABLE`, a warning even for a
        `report`ed request, which its route answers 503 — a speaker merely
        unplugged is no fault of the appliance. One that refused a forced send
        stays a warning too: an admission
        sends while the satellite's CamillaDSP may still be starting, every
        call answers 400 until it is up, and the admission retries. The forced
        refusal is said once per spell (`_forced_refusing`), until a command
        is applied or the admission service ends the outage
        (`end_refusal_spell`): the reconcile sweep readmits a refusing speaker
        every 30 s.

        The stored value is not touched either way: a satellite that answers
        a refusal has cached the value and applies it when its CamillaDSP comes
        back (`_restore_after_reconnect`), and the local one is re-applied from
        the store by `reapply_current_volume`. Taking the old value back here
        would show a level the speaker is about to leave.
        """
        spell = (what, mac_id)
        if is_satellite_refusal(error) and force and not report:
            # Once per spell, which an applied command or the end of the
            # admission's outage ends: the sweep readmits a speaker whose DSP
            # stays down every 30 s, and a line for each admission's send was
            # a line per pass for the outage.
            if spell in self._forced_refusing:
                self.logger.debug(f"Failed to set {what.lower()} for {mac_id}: {error}")
            else:
                self._forced_refusing.add(spell)
                self.logger.warning(f"Failed to set {what.lower()} for {mac_id}: {error}")
            return Delivery.REFUSED
        if is_satellite_unreachable(error):
            self.logger.warning(f"Failed to set {what.lower()} for {mac_id}: {error}")
            return Delivery.UNREACHABLE
        if isinstance(error, SatelliteUnreachable) and not is_satellite_refusal(error):
            self._failed(mac_id, what, error, report)
            return Delivery.REFUSED
        if report or spell not in self._refusing:
            online = self._registry.is_client_online(mac_id) if self._registry else True
            if online:
                self._refusing.add(spell)
                self.logger.error(f"{what} not applied to {mac_id}: {error}")
        return Delivery.REFUSED

    def end_refusal_spell(self, mac_id: str) -> None:
        """Make a satellite's next forced refusal news again.

        Called by the admission service wherever a refused admission's outage
        ends (the satellite left snapserver's list, connected anew, or was
        forgotten): the outage is one, so its end is one, and a speaker back
        still refusing is told in full, its answer included.
        """
        self._forced_refusing.discard(("Volume", mac_id))
        self._forced_refusing.discard(("Mute", mac_id))

    def _outcome(self, mac_id: str, result: dict, what: str) -> Delivery:
        """Read a router answer: applied, or skipped. A speaker that applies
        again is news again the next time it refuses."""
        if not self._is_success(result):
            return Delivery.NOT_SENT
        self._refusing.discard((what, mac_id))
        self._forced_refusing.discard((what, mac_id))
        return Delivery.APPLIED

    def _submit(self, channels: Dict[str, _Channel], mac_id: str, value: Any, force: bool,
                send: Callable[[str, _Channel, bool, bool], Awaitable[Delivery]],
                report: bool = False) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        if force:
            # A forced command is an admission or a restore: a new session with
            # the speaker, whose first refusal is news again. Its forced spell
            # is not ended here: the sweep readmits a refusing speaker every
            # 30 s, and the admission service says when its outage ends.
            self._refusing.discard(("Volume" if channels is self._volume_channels else "Mute", mac_id))
        channel = channels.setdefault(mac_id, _Channel())
        channel.value = value
        channel.force = channel.force or force
        channel.report = channel.report or report
        channel.waiters.append(future)
        if channel.task is None:
            channel.task = self._bg.spawn(self._drain(mac_id, channel, send), label=f"push_{mac_id}")
            channel.task.add_done_callback(lambda task: self._release(channel, task))
        return future

    @staticmethod
    def _release(channel: _Channel, task: asyncio.Task) -> None:
        """Free the channel when its task ends, however it ends.

        A task cancelled before its first step never runs `_drain`'s cleanup:
        without this, the channel would keep a dead task, every later submit
        would wait on it, and so would every caller already waiting. A task
        that ran has already let go of the channel (it may even hold a newer
        task by now), so this touches nothing then.
        """
        if channel.task is not task:
            return
        channel.task = None
        for waiter in channel.waiters:
            if not waiter.done():
                waiter.set_result(Delivery.NOT_SENT)
        channel.waiters = []

    async def _drain(self, mac_id: str, channel: _Channel,
                     send: Callable[[str, _Channel, bool, bool], Awaitable[Delivery]]) -> None:
        """Send until nobody waits. Each send answers every caller that asked before it left."""
        waiters: List[asyncio.Future] = []
        try:
            while channel.waiters:
                waiters, channel.waiters = channel.waiters, []
                force, channel.force = channel.force, False
                report, channel.report = channel.report, False
                applied = await send(mac_id, channel, force, report)
                for waiter in waiters:
                    if not waiter.done():
                        waiter.set_result(applied)
                waiters = []
        finally:
            # Synchronous with the last `while` check: no submit can slip in
            # between, so a normal exit leaves nobody waiting, and a
            # cancellation answers the batch in flight and those queued after it.
            channel.task = None
            for waiter in waiters + channel.waiters:
                if not waiter.done():
                    waiter.set_result(Delivery.NOT_SENT)
            channel.waiters = []

    async def set_equalizer_gain(self, mac_id: str, gain_db: float, force: bool = False) -> bool:
        """Set a client's level trim via EqualizerRouter.

        Not a volume — a fixed Gain stage balancing this speaker against the
        others — but it travels the same road for the same reasons: one router
        that knows local from remote, one timeout, one retry policy.
        """
        try:
            if not self._router:
                self.logger.warning(f"Cannot set level trim for {mac_id}: router not configured")
                return False
            result = await asyncio.wait_for(
                self._router.set_gain(mac_id, gain_db, force=force),
                timeout=self._timeout
            )
            return self._is_success(result)

        except asyncio.TimeoutError:
            self.logger.error(f"Timeout setting level trim for {mac_id}")
            return False
        except Exception as e:
            self.logger.warning(f"Failed to set level trim for {mac_id}: {e}")
            return False

    def submit_mute(self, mac_id: str, mute: bool, force: bool = False,
                    report: bool = False) -> asyncio.Future:
        """Ask for `mute` on a speaker; resolves like `submit_volume`, and for the
        same reason synchronous: a caller that stores then submits keeps the
        order it stored in. `report` as for `submit_volume`."""
        return self._submit(self._mute_channels, mac_id, mute, force, self._send_mute, report)

    async def set_equalizer_mute(self, mac_id: str, mute: bool, force: bool = False,
                                 report: bool = False) -> Delivery:
        """Set mute state for a client's equalizer via EqualizerRouter.

        Ordered per speaker like the volume, with no retry by design: the boot
        push calls it once per client, and a raise there must answer False
        rather than abort the pass.
        """
        return await self.submit_mute(mac_id, mute, force=force, report=report)

    async def _send_mute(self, mac_id: str, channel: _Channel, force: bool,
                         report: bool) -> Delivery:
        try:
            if not self._router:
                self.logger.warning(f"Cannot set mute for {mac_id}: router not configured")
                return Delivery.NOT_SENT
            result = await asyncio.wait_for(
                self._router.set_mute(mac_id, channel.value, force=force),
                timeout=self._timeout
            )
            return self._outcome(mac_id, result, "Mute")
        except asyncio.TimeoutError:
            return self._timed_out(mac_id, "Mute", report)
        except CommandNotApplied as e:
            return self._refused(mac_id, e, force, "Mute", report)
        except Exception as e:
            self._failed(mac_id, "Mute", e, report)
            return Delivery.REFUSED

    async def cleanup(self) -> None:
        """Cancel every command still in flight; their callers are answered False."""
        await self._bg.cancel_all()
