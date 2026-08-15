# backend/sources/bluetooth/source.py
"""
Bluetooth audio source using BlueALSA for audio and BlueZ AVRCP for control.

Family C (active player): UI control, rich metadata. Two independent feeds
answer two different questions, and neither can answer the other's:

  - `monitor.py` watches BlueALSA PCM add/remove — is a sender *connected*,
    and what is it called. This is the one that decides ACTIVE vs READY.
  - `avrcp.py` watches BlueZ's org.bluez.MediaPlayer1 — what is *playing*, and
    it is also how Milō drives the sender's transport.

They arrive in either order and the AVRCP one is optional: an AVRCP target is
not mandatory and plenty of senders publish an empty track, so the source stays
perfectly usable with no metadata at all. That is exactly what the frontend's
rich-display gate keys on — no title/artist means the device-name status card,
a title means the shared player.

There is no seek. AVRCP offers only hold-style FastForward/Rewind, not a
position command, so the progress bar is read-only (`:seekable="false"` on the
player) and `seek` is deliberately absent from COMMANDS — the same shape Tidal
lands on for its own protocol's reasons.

No album art comes over the link either (see avrcp.py), so the only image the
player can show is one resolved from the track text — the shared
`ArtworkResolver`, the same one radio uses for its in-band stations. It is
best-effort and asynchronous: a miss leaves the player's source glyph.

The playhead is the one thing no feed reports reliably (again, see avrcp.py:
BlueZ signals it only when it re-anchors, and between those it extrapolates —
sometimes from an anchor that is minutes wrong). Five sources implement
`refresh_metadata()`; this is the one that would have no playhead at all
without it. The other four re-read a position they also publish periodically
(mpv's `time-pos` for CD/Podcast/Music Library, go-librespot's /status for
Spotify), so their hook sharpens a value a reconnecting client already has —
here it is the only thing that ever moves it, since nothing notifies a moved
playhead over AVRCP and the stored one is whatever the last track change
captured.

Features:
- Multi-service management: bluetooth, bluealsa, bluealsa-aplay
- D-Bus agent for automatic pairing (NoInputNoOutput mode)
- Single device connection enforcement (via BlueALSA monitor callbacks)
- BlueALSA PCM monitoring for real-time connection events
- AVRCP metadata + transport via BlueZ
"""
import asyncio
from typing import Dict, Any, Optional

from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource
from backend.core.models.source_metadata import PlaybackMetadata
from backend.sources.bluetooth.adapter import BluetoothAdapter
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.avrcp import AvrcpController
from backend.sources.bluetooth.monitor import BlueAlsaMonitor
from backend.shared.artwork_resolver import ArtworkResolver
from backend.shared.decorators import handle_errors

# A floor on position-only broadcasts, not the cadence: the cadence belongs to
# avrcp's POSITION_POLL_INTERVAL, since no sender observed here pushes a moved
# playhead at all. This only stops a sender that *does* notify one — the
# protocol allows it — from broadcasting at whatever rate it chooses, and is
# therefore kept below the poll so our own ticks always get through.
POSITION_BROADCAST_MIN_INTERVAL = 2.0


class BluetoothSource(BaseAudioSource):
    """
    Bluetooth audio source using BlueALSA.

    Family C (active player): the sender starts playback, Milō displays it and
    drives its transport back over AVRCP. Commands route through
    `/api/audio/control/bluetooth` to `_handle_command`. Extends
    BaseAudioSource — implements `_do_start / _do_stop / _handle_command`.
    """

    # AVRCP does report a pause (BlueZ signals `Status = paused`, which is what
    # the player's own pause button reads back), so the reason there is no
    # auto-stop here is not that the signal is missing — it is that acting on it
    # would be wrong: a paused phone is still connected and resumes instantly,
    # and tearing the link down would make the player's pause button undo
    # itself. The 12 h INACTIVITY_TIMEOUT in AudioStateMachine is the backstop.
    AUTO_STOP_SUPPORTED = False

    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        state_machine=None,
        settings_service=None,
        systemd_manager=None
    ):
        super().__init__(
            source_id="bluetooth",
            service_name="milo-bluealsa.service",
            state_machine=state_machine,
            systemd_manager=systemd_manager,
            settings_service=settings_service,
            config=config
        )

        self.bluetooth_service = self._config.get("bluetooth_service", "bluetooth.service")
        self.bluealsa_service = self.service_name
        self.bluealsa_aplay_service = self._config.get(
            "bluealsa_aplay_service", "milo-bluealsa-aplay.service"
        )
        self.stop_bluetooth_on_exit = self._config.get("stop_bluetooth_on_exit", True)
        self.auto_agent = self._config.get("auto_agent", True)

        self.connected_device: Optional[Dict[str, str]] = None
        # Half of the exposure authority below. SourceState cannot carry it:
        # READY means both "started, waiting for a sender" and "stopped".
        self._running = False

        self.adapter = BluetoothAdapter()
        self.agent = BluetoothAgent()
        self.monitor = BlueAlsaMonitor()
        self.avrcp = AvrcpController()

        # Last AVRCP snapshot, keyed like PlaybackMetadata. Kept even while no
        # PCM is up: the player object and the PCM appear in either order, and
        # _update_connection_state reads this whichever arrives second.
        self._playback: Dict[str, Any] = {}
        self._last_progress_broadcast = 0.0

        # Cover art resolved from the track text, and the track it belongs to.
        # Held apart from _playback rather than written into it: the position
        # poll replaces that dict wholesale every few seconds with a fresh AVRCP
        # snapshot, which carries no artwork and would wipe it. Keeping the key
        # alongside is also what expires it — a new track cannot inherit the
        # previous one's cover.
        self._artwork = ArtworkResolver()
        self._artwork_url: Optional[str] = None
        self._artwork_key: tuple = ()

    def _reset_playback_state(self) -> None:
        super()._reset_playback_state()
        self.connected_device = None
        self._playback = {}
        self._artwork_url = None
        self._artwork_key = ()

    async def _do_start(self) -> bool:
        """Start Bluetooth services and monitoring."""
        try:
            self._running = True

            # 1. Start system services
            for service in [self.bluetooth_service, self.bluealsa_service]:
                if not await self._start_service(service):
                    raise RuntimeError(f"Failed to start {service}")

            # 2. Start playback service
            if not await self._start_service(self.bluealsa_aplay_service):
                raise RuntimeError(f"Failed to start {self.bluealsa_aplay_service}")

            # 3. Configure Bluetooth adapter
            if not await self._configure_adapter():
                self._logger.warning("Adapter configuration failed")

            # 4. Register D-Bus agent
            if self.auto_agent:
                if not await self.agent.register():
                    self._logger.warning("Agent registration failed")

            # 5. Set up and start BlueALSA monitor (event-based connection detection)
            self.monitor.set_callbacks(
                self._on_device_connected,
                self._on_device_disconnected,
                self._on_monitor_lost
            )
            if not await self.monitor.start():
                raise RuntimeError("BlueALSA monitor failed to start")

            # 6. Start the AVRCP player feed (metadata + transport). Best-effort:
            # a sender that exposes no AVRCP target, or a BlueZ that will not
            # answer, costs the metadata and nothing else — the audio path and
            # the connection state come from BlueALSA, not from here.
            self.avrcp.set_callback(self._on_avrcp_update)
            if not await self.avrcp.start():
                self._logger.warning("AVRCP feed unavailable — no track metadata")

            # 7. Detect already-connected device (e.g. backend restart during active stream)
            await self._detect_connected_device()

            # 8. Re-evaluate exposure: finding a sender here means the appliance
            # must already be hidden, and step 3 opened it.
            await self._apply_exposure()

            # 9. Update state
            self._update_connection_state()

            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            return False

    @handle_errors(default=False)
    async def _do_stop(self) -> bool:
        """Stop monitoring and services."""
        await self._cleanup()

        # Close the exposure before the services go, and block the senders with
        # it: with the HID remote enabled bluetooth.service deliberately keeps
        # running, so the adapter stays powered and a paired phone would
        # otherwise still be able to dial in with the source off.
        self._running = False
        self.connected_device = None
        await self._apply_exposure()

        # Stop BlueALSA services
        if self.stop_bluetooth_on_exit:
            await self._stop_service(self.bluealsa_aplay_service)
            await self._stop_service(self.bluealsa_service)

            # Keep bluetooth.service running if BT remote controller needs it
            bt_remote = await self._settings_service.get_setting('hardware.bt_remote')
            if not (bt_remote and bt_remote.get('enabled')):
                await self._stop_service(self.bluetooth_service)

        self._reset_playback_state()
        # Released last: _apply_exposure above needed it, and _cleanup runs
        # before that on purpose — blocking a peer while the monitor is still
        # reading would echo back as a disconnect event.
        await self.adapter.close()

        return True

    async def release_for_reroute(self) -> bool:
        """Multiroom reroute (release half): stop ONLY bluealsa-aplay so the
        CamillaDSP input it feeds in direct mode is freed for the snapcast
        reconcile (snapclient feeds that same CamillaDSP in multiroom mode).

        bluealsa + bluetooth.service keep running, so the A2DP link — and
        self.connected_device — survive; unlike _do_stop(), which tears the
        whole stack down and kicks the phone off. The BlueALSA monitor tracks
        PCM add/remove driven by the bluealsa daemon (i.e. the phone's A2DP
        transport), not by the bluealsa-aplay consumer, so bouncing the writer
        alone never surfaces as a disconnect.
        """
        return await self._stop_service(self.bluealsa_aplay_service)

    async def acquire_after_reroute(self) -> bool:
        """Multiroom reroute (acquire half): restart bluealsa-aplay under the
        new MILO_MODE and re-publish state. The device stayed connected and the
        monitor kept self.connected_device current, so re-broadcasting the
        connection state restores ACTIVE (the transition set it to STARTING).
        """
        if not await self._start_service(self.bluealsa_aplay_service):
            return False
        self._update_connection_state()
        return True

    COMMANDS = {
        "disconnect": None,
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
    }

    # Milō command -> AVRCP method on org.bluez.MediaPlayer1. The two spellings
    # differ on purpose, same split as Tidal: Milō's vocabulary is canonical
    # across sources (`resume`, `prev`), AVRCP's is its own (`Play`,
    # `Previous`), and mapping here is what keeps the difference out of the API.
    AVRCP_COMMANDS = {
        "pause": "Pause",
        "resume": "Play",
        "next": "Next",
        "prev": "Previous",
    }

    async def _handle_command(self, cmd: str, params: Optional[BaseModel]) -> Dict[str, Any]:
        """Handle Bluetooth-specific commands."""
        if cmd == "disconnect":
            return await self._cmd_disconnect()

        if not self.avrcp.has_player:
            return self.error_response("Connected device exposes no AVRCP player")

        # An AVRCP target answers NotSupported per method — a sender may take
        # Play/Pause and refuse Next — so a refusal is this command's failure,
        # not the source's.
        if not await self.avrcp.send(self.AVRCP_COMMANDS[cmd]):
            return self.error_response(f"'{cmd}' was refused by the device")

        return self.success_response()

    async def _cmd_disconnect(self) -> Dict[str, Any]:
        """Disconnect current device."""
        if not self.connected_device:
            return self.error_response("No device connected")

        address = self.connected_device.get("address")
        try:
            proc = await asyncio.create_subprocess_exec(
                "bluetoothctl", "disconnect", address,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)

            if proc.returncode != 0:
                return self.error_response(stderr.decode().strip())

            return self.success_response("Device disconnecting")

        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error(f"Timeout disconnecting device {address}")
            return self.error_response("Disconnect timed out")
        except Exception as e:
            return self.error_response(str(e))

    # === BlueALSA Monitor Callbacks ===

    async def _on_device_connected(self, address: str, name: str) -> None:
        """Handle device connection from BlueALSA monitor."""
        # Single device enforcement: disconnect if another device is already connected
        if self.connected_device and self.connected_device.get("address") != address:
            self._logger.info(f"Disconnecting {name} ({address}) - another device already connected")
            await self._disconnect_device(address)
            return

        if not self.connected_device:
            self.connected_device = {"address": address, "name": name}
            self._logger.info(f"Device connected: {name} ({address})")
            # Hide first: the appliance now has a sender, so it must stop
            # offering itself to a second one instead of kicking it afterwards.
            await self._apply_exposure()
            self._update_connection_state()

    async def _on_monitor_lost(self, reason: str) -> None:
        """The BlueALSA feed died — say what it costs, and leave the state alone.

        Nothing is transitioned here. The monitor is the only thing that knows a
        sender is connected, so a source that reacted by dropping
        `connected_device` would be guessing: the audio may well still be
        flowing through bluealsa-aplay. The monitor already logged the failure at
        error level (hence the UI banner); this names the source it belongs to,
        which is what tells the owner *which* card has stopped updating.
        """
        self._logger.error(
            f"BlueALSA feed lost ({reason}) — connect/disconnect will no longer be "
            f"detected; switch away from Bluetooth and back to restart it"
        )

    @handle_errors(default=False)
    async def _disconnect_device(self, address: str) -> bool:
        """Disconnect a device by address."""
        proc = await asyncio.create_subprocess_exec(
            "bluetoothctl", "disconnect", address,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE
        )
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error(f"Timeout disconnecting device {address}")
            return False

        if proc.returncode != 0:
            self._logger.error(f"Disconnect failed: {stderr.decode().strip()}")
            return False

        return True

    async def _on_device_disconnected(self, address: str, name: str) -> None:
        """Handle device disconnection from BlueALSA monitor."""
        # Check if current device
        if not self.connected_device:
            return
        if self.connected_device.get("address") != address:
            return

        self.connected_device = None
        # Drop the track with the link. emit_connection_state already withholds
        # media fields on READY, but a device reconnecting before its AVRCP
        # player is back would otherwise re-publish the previous track.
        self._playback = {}
        self._logger.info(f"Device disconnected: {name} ({address})")
        # Nothing holds the appliance any more: offer it again.
        await self._apply_exposure()
        self._update_connection_state()

    # === AVRCP Callbacks ===

    # What the frontend cannot interpolate: any change here owes a full
    # broadcast, a moved position alone owes only a drift correction.
    SUBSTANTIVE_FIELDS = ("title", "artist", "album", "duration", "is_playing")

    async def _on_avrcp_update(self, address: str, snapshot: Dict[str, Any]) -> None:
        """Apply one coalesced AVRCP player change.

        The snapshot is stored even when no PCM is up yet — the player object
        and the BlueALSA PCM race, and whichever lands second publishes both.
        """
        connected = (self.connected_device or {}).get("address")
        if connected and connected.upper() != address.upper():
            return

        before = tuple(self._playback.get(k) for k in self.SUBSTANTIVE_FIELDS)
        before_track = self._track_key(self._playback)
        self._playback = snapshot
        self._is_playing = bool(snapshot.get("is_playing"))

        track = self._track_key(snapshot)
        if track != before_track and any(track):
            self._bg.spawn(self._resolve_artwork(track), label="avrcp_artwork")

        if before != tuple(snapshot.get(k) for k in self.SUBSTANTIVE_FIELDS):
            self._update_connection_state()
        else:
            self._broadcast_progress()

    @staticmethod
    def _track_key(playback: Dict[str, Any]) -> tuple:
        """What identifies a track for artwork purposes."""
        return (playback.get("title"), playback.get("artist"), playback.get("album"))

    async def _resolve_artwork(self, track: tuple) -> None:
        """Look a cover up from the track text and publish it if still current.

        AVRCP carries no image (see avrcp.py), so the only thing left is the
        text it does carry. Runs off the AVRCP feed via `_bg`; a newer track
        that arrived during the lookup must not be given the old one's cover,
        hence the re-check. A miss is silent — the player draws its glyph.
        """
        title, artist, album = track
        url = await self._artwork.resolve(artist or "", title or "", album or "")
        if not url or track != self._track_key(self._playback):
            return

        self._artwork_url = url
        self._artwork_key = track
        self._update_connection_state()

    async def refresh_metadata(self) -> bool:
        """Re-read the playhead for `GET /api/audio/state` and the WS handshake.

        The source this hook matters most to. Nothing notifies a moved playhead
        over AVRCP, so the stored position is the one captured at the last track
        change — near zero for the whole song. A client arriving or reloading
        mid-track would be handed that and interpolate from it, which is a
        progress bar that restarts at 0:00 on every refresh.
        """
        if not self.avrcp.has_player:
            return False

        await self.avrcp.read_position()
        self._playback = self.avrcp.snapshot()
        self._update_connection_state()
        return True

    def _broadcast_progress(self) -> None:
        """Drift-correct the playhead, at most every POSITION_BROADCAST_INTERVAL."""
        position = self._playback.get("position")
        duration = self._playback.get("duration")
        if position is None or not duration:
            return

        now = asyncio.get_running_loop().time()
        if now - self._last_progress_broadcast < POSITION_BROADCAST_MIN_INTERVAL:
            return

        self._last_progress_broadcast = now
        self.broadcast_position_update(position, duration)

    # === Helper Methods ===

    def _may_accept_sender(self) -> bool:
        """The one authority for "may a sender connect right now?".

        The rule, in full: Milō is discoverable and connectable only while the
        Bluetooth source is running *and* nothing holds it. Every exposure
        decision reads this, so the four transitions cannot drift apart.
        """
        return self._running and self.connected_device is None

    async def _apply_exposure(self) -> bool:
        """Make the appliance's Bluetooth exposure match the state it is in.

        Called from the four transitions that can change the answer — source
        start, sender connected, sender disconnected, source stop — rather than
        being set once at start and cleared once at stop, which is how the
        appliance came to keep advertising while a sender already held it.

        Two mechanisms, because one does not cover the other's case:
          - Discoverable/Pairable stop a *new* device finding or pairing with
            Milō. They say nothing to a device that is already paired.
          - Blocked on each known A2DP sender refuses the link itself. That is
            the only thing that stops a paired phone dialling a known address
            while `bluetooth.service` stays up for the HID remote. The sender
            currently connected is exempt: blocking it would drop the audio it
            is playing.

        Blocking writes durable per-device state, so a backend that dies leaves
        senders blocked; the unblock half runs on every source start, which is
        the reconciliation that recovers it.
        """
        may_accept = self._may_accept_sender()
        holder = self.connected_device.get("address") if self.connected_device else None

        exposed = await self.adapter.set_exposure(discoverable=may_accept, pairable=may_accept)
        unblocked = await self.adapter.set_audio_peers_blocked(
            not may_accept, keep_unblocked=holder
        )
        if not (exposed and unblocked):
            self._logger.error(
                f"Bluetooth exposure not applied (accepting={may_accept}) — the "
                f"appliance may be visible or connectable in the wrong state"
            )
            return False

        self._logger.info(
            f"Bluetooth exposure: {'open' if may_accept else 'closed'}"
            f"{f' (held by {holder})' if holder else ''}"
        )
        return True

    async def _configure_adapter(self) -> bool:
        """Power the adapter and apply the exposure its current state calls for."""
        if not await self.adapter.power_on():
            return False
        if not await self.adapter.set_discoverable_timeout(0):
            return False
        return await self._apply_exposure()

    @handle_errors(default=None)
    async def _detect_connected_device(self) -> None:
        """Detect currently connected A2DP device via BlueALSA PCM list.

        Uses bluealsa-cli list-pcms instead of bluetoothctl to only detect
        actual audio devices, filtering out HID devices (e.g. BT remotes).
        """
        proc = await asyncio.create_subprocess_exec(
            "bluealsa-cli", "list-pcms",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), 10.0)
        except asyncio.TimeoutError:
            proc.kill()
            self._logger.error("Timeout listing BlueALSA PCMs")
            return

        if proc.returncode == 0:
            for line in stdout.decode().splitlines():
                device_info = self.monitor.parse_pcm_path(line.strip())
                if device_info:
                    address = device_info["address"]
                    name = await self.monitor.resolve_device_name(address)
                    self.connected_device = {"address": address, "name": name}
                    # The monitor's collection is the one that authorises a
                    # departure — a PCM adopted here and not handed over is a
                    # sender that can never be seen leaving.
                    self.monitor.adopt_device(device_info, name)
                    return

        # No A2DP device found
        self.connected_device = None

    async def _cleanup(self) -> None:
        """Clean up resources."""
        # Stop BlueALSA monitor
        await self.monitor.stop()

        # Stop the AVRCP feed
        await self.avrcp.stop()

        # Unregister agent
        if self.auto_agent:
            await self.agent.unregister()

    def _update_connection_state(self) -> None:
        """Publish connection + playback state.

        Broadcast metadata (WS source/state_changed → system_state.metadata):
        device_name — the extra the status card draws when the sender publishes
        no track — plus whatever AVRCP supplied of title, artist, album,
        position, duration, is_playing, plus album_art_url when a cover was
        resolved from the track text. AVRCP itself never carries one (see
        avrcp.py); the resolver is the only reason that field is ever set, and
        the player draws its source glyph when the lookup found nothing.
        """
        device = self.connected_device or {}
        playback = dict(self._playback)
        if self._artwork_url and self._artwork_key == self._track_key(playback):
            playback["album_art_url"] = self._artwork_url

        self.emit_connection_state(
            self.connected_device is not None,
            PlaybackMetadata.model_validate(playback),
            extras={"device_name": device.get("name")},
        )

