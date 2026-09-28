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
from typing import Any, Awaitable, Callable, Dict, List, Optional, TYPE_CHECKING

from backend.shared.background import BackgroundTaskSet

if TYPE_CHECKING:
    from backend.core.multiroom.client_registry import ClientRegistryService
    from backend.core.multiroom.equalizer_router import EqualizerRouter


class _Channel:
    """One setting of one speaker: the latest value asked for, and who waits.

    A speaker is sent one command at a time, and each command carries the value
    that is latest *when it leaves*. A burst therefore reaches the speaker as
    the command in flight plus one more with the final value, and two commands
    for one speaker can never overtake each other on two pooled connections.
    """

    __slots__ = ("value", "force", "waiters", "task")

    def __init__(self) -> None:
        self.value: Any = None
        self.force = False
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
        self._bg = BackgroundTaskSet(self.logger, "equalizer_controller")

    def set_registry(self, registry):
        """Set the client registry (for dependency injection after init)."""
        self._registry = registry

    @staticmethod
    def _is_success(result: dict) -> bool:
        """Interpret an EqualizerRouter result dict as "the command was applied".

        The router skips for two opposite reasons and only one of them is an
        outcome. `external_volume_control` means there was nothing to send — a
        DAC client owns its own volume, and the caller must still record the
        level it asked for. `client_offline` means the command was *not*
        delivered; counting it as applied is what wrote the store for a
        satellite that never heard the change and then reported success.
        """
        if not result:
            return False
        status = result.get("status", "")
        if status == "skipped":
            return result.get("reason") != "client_offline"
        return status == "success"

    # ========== Single Client Operations ==========

    def submit_volume(self, mac_id: str, volume_db: float, force: bool = False) -> asyncio.Future:
        """Ask for `volume_db` on a speaker; resolves True once a command carrying it,
        or a later value, was applied.

        Synchronous on purpose: a caller that writes a level to the store and
        submits it in the same step, with no await in between, submits in the
        order it wrote. The last value submitted is therefore the stored one, and
        it is the one the speaker ends on.
        """
        return self._submit(self._volume_channels, mac_id, self._clamp(volume_db), force,
                            self._send_volume)

    async def set_equalizer_volume(self, mac_id: str, volume_db: float, force: bool = False) -> bool:
        """
        Set volume for a single client via EqualizerRouter.

        Args:
            mac_id: Client identifier (mac_id from registry)
            volume_db: Target volume in dB, clamped to the operator's limits here
            force: Bypass online check in router (for reconnection sync)

        Returns:
            True if successful, False otherwise
        """
        return await self.submit_volume(mac_id, volume_db, force=force)

    async def _send_volume(self, mac_id: str, channel: _Channel, force: bool) -> bool:
        """One volume command, retried on timeout with the value latest at each try."""
        if not self._router:
            self.logger.warning(f"Cannot set volume for {mac_id}: router not configured")
            return False
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
                self.logger.error(f"Timeout setting volume for {mac_id}")
                return False
            except Exception as e:
                self.logger.warning(f"Failed to set volume for {mac_id}: {e}")
                return False
        return False

    def _outcome(self, mac_id: str, result: dict, what: str) -> bool:
        """Read a router answer, and say once when a speaker that is still
        online starts refusing — not on every step of a turn it keeps refusing.

        The stored value is not touched either way: a satellite that answers
        a refusal has cached the value and applies it when its CamillaDSP comes
        back (`_restore_after_reconnect`), and the local one is re-applied from
        the store by `reapply_current_volume`. Taking the old value back here
        would show a level the speaker is about to leave.
        """
        ok = self._is_success(result)
        spell = (what, mac_id)
        if ok:
            self._refusing.discard(spell)
        elif (result or {}).get("reason") != "client_offline" and spell not in self._refusing:
            online = self._registry.is_client_online(mac_id) if self._registry else True
            if online:
                self._refusing.add(spell)
                self.logger.error(f"{what} not applied to {mac_id}: {result}")
        return ok

    def _submit(self, channels: Dict[str, _Channel], mac_id: str, value: Any, force: bool,
                send: Callable[[str, _Channel, bool], Awaitable[bool]]) -> asyncio.Future:
        future = asyncio.get_running_loop().create_future()
        if force:
            # A forced command is an admission or a restore: a new session with
            # the speaker, whose first refusal is news again.
            self._refusing.discard(("Volume" if channels is self._volume_channels else "Mute", mac_id))
        channel = channels.setdefault(mac_id, _Channel())
        channel.value = value
        channel.force = channel.force or force
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
                waiter.set_result(False)
        channel.waiters = []

    async def _drain(self, mac_id: str, channel: _Channel,
                     send: Callable[[str, _Channel, bool], Awaitable[bool]]) -> None:
        """Send until nobody waits. Each send answers every caller that asked before it left."""
        waiters: List[asyncio.Future] = []
        try:
            while channel.waiters:
                waiters, channel.waiters = channel.waiters, []
                force, channel.force = channel.force, False
                applied = await send(mac_id, channel, force)
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
                    waiter.set_result(False)
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

    def submit_mute(self, mac_id: str, mute: bool, force: bool = False) -> asyncio.Future:
        """Ask for `mute` on a speaker; resolves like `submit_volume`, and for the
        same reason synchronous: a caller that stores then submits keeps the
        order it stored in."""
        return self._submit(self._mute_channels, mac_id, mute, force, self._send_mute)

    async def set_equalizer_mute(self, mac_id: str, mute: bool, force: bool = False) -> bool:
        """Set mute state for a client's equalizer via EqualizerRouter.

        Ordered per speaker like the volume, with no retry by design: the boot
        push calls it once per client, and a raise there must answer False
        rather than abort the pass.
        """
        return await self.submit_mute(mac_id, mute, force=force)

    async def _send_mute(self, mac_id: str, channel: _Channel, force: bool) -> bool:
        try:
            if not self._router:
                self.logger.warning(f"Cannot set mute for {mac_id}: router not configured")
                return False
            result = await asyncio.wait_for(
                self._router.set_mute(mac_id, channel.value, force=force),
                timeout=self._timeout
            )
            return self._outcome(mac_id, result, "Mute")
        except Exception as e:
            self.logger.warning(f"Failed to set mute for {mac_id}: {e}")
            return False

    async def idle(self, mac_id: str) -> None:
        """Wait until nothing is in flight to this speaker, volume or mute."""
        for channels in (self._volume_channels, self._mute_channels):
            channel = channels.get(mac_id)
            if channel is not None and channel.task is not None:
                await asyncio.wait({channel.task})

    async def cleanup(self) -> None:
        """Cancel every command still in flight; their callers are answered False."""
        await self._bg.cancel_all()
