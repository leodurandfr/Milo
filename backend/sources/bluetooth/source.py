# backend/sources/bluetooth/source.py
"""
Bluetooth audio source using BlueALSA for audio and BlueZ AVRCP for control.

Family C (active player): UI control, rich metadata. A phone holding the A2DP
link is a session a daemon holds (docs: source architecture, phase 4), and
three feeds describe it, none of which can answer another's question:

  - `monitor.py` watches BlueALSA — is a sender *linked* (PCM added/removed),
    is its stream *flowing* (`Running`), and is BlueALSA itself still there.
  - `avrcp.py` watches BlueZ's org.bluez.MediaPlayer1 — what is *playing*, and
    how Milō drives the sender's transport; and bluetoothd itself.
  - systemd, through the base's process watch on BlueALSA (SESSION_DAEMON).

What was measured on the unit (2026-09-24, the owner's iPhone and Mac mini)
decides how they combine into the session's phase:

  - A pause stops nothing: the player says `paused` and the stream keeps
    running (the Mac streams silence). So a pause is the player's word alone.
  - A sender can say `playing` while sending nothing — the Mac switching its
    output to its own speakers keeps the link, the PCM and a `playing` player,
    and only BlueALSA's `Running` goes false. The owner's reading: that is
    "connected to X", at once.
  - The Mac publishes no Status for 100 s after connecting; a phone may publish
    no player at all. Both are "connected": nothing says whether it plays.

Hence: paused → PAUSED; playing and flowing → PLAYING; anything else →
CONNECTED. A paused phone keeps the link for as long as it wants
(KEEP_WHILE_LINKED, owner decision): a pause here is resumed in an instant and
tearing the link down would make the player's own pause button undo itself.

The AVRCP player counts only for the phone holding the link: a player can
outlive its link (1.0 s measured) or belong to a phone the appliance turned
away, and neither may name, or take the commands of, the one connected (E30).

There is no seek. AVRCP offers only hold-style FastForward/Rewind, not a
position command, so the progress bar is read-only (no `seek` in `controls`)
and `seek` is deliberately absent from COMMANDS — the same shape Tidal lands on
for its own protocol's reasons.

No album art comes over the link either (see avrcp.py), so the only image the
player can show is one resolved from the track text — the shared
`ArtworkResolver`, the same one radio uses for its in-band stations. It is
best-effort and asynchronous: a miss leaves the player's source glyph.

The playhead is the one thing no feed reports reliably (again, see avrcp.py:
BlueZ signals it only when it re-anchors, and between those it extrapolates —
sometimes from an anchor that is minutes wrong). `refresh_metadata()` is what
moves it for a client arriving mid-track: nothing notifies a moved playhead
over AVRCP, and the stored one is whatever the last track change captured.

Features:
- Multi-service management: bluetooth, bluealsa, bluealsa-aplay
- D-Bus agent for automatic pairing (NoInputNoOutput mode)
- Single device enforcement (a second phone's link is dropped)
- Recovery from a bluetoothd or BlueALSA that died under the source
- AVRCP metadata + transport via BlueZ
"""
import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from backend.core.audio_source import BaseAudioSource
from backend.core.models.session import (
    CommandScope, DaemonSnapshot, EndReason, IdlePolicy, Phase, ResumePolicy,
    ReroutePolicy, Session,
)
from backend.core.models.ws_events import SourceErrorReason
from backend.sources.bluetooth.adapter import BluetoothAdapter
from backend.sources.bluetooth.agent import BluetoothAgent
from backend.sources.bluetooth.avrcp import PLAYING_STATES, AvrcpController
from backend.sources.bluetooth.monitor import BlueAlsaMonitor
from backend.shared.artwork_resolver import ArtworkResolver
from backend.shared.decorators import handle_errors

# A new bluetoothd's adapter is announced while AutoEnable may still be
# powering it, and BlueZ answers Busy to a write meanwhile: the configuration
# is tried again, a few times, before the failure is reported.
ADAPTER_CONFIGURE_ATTEMPTS = 5
ADAPTER_CONFIGURE_RETRY_S = 0.5


@dataclass(eq=False)
class BluetoothSession(Session):
    """One phone holding the A2DP link; `sender` is its address.

    `playback` is its AVRCP player's snapshot (avrcp.snapshot()), `status`
    that player's Status, `running` whether BlueALSA says its stream flows.
    Cover art is resolved from the track text and held apart from `playback`:
    the position poll replaces that dict wholesale, and the key it was
    resolved for is what keeps a new track from inheriting it.
    """
    name: str = ""
    playback: Dict[str, Any] = field(default_factory=dict)
    has_player: bool = False
    status: str = ""
    running: bool = False
    artwork_url: Optional[str] = None
    artwork_key: tuple = ()


def _same_device(a: Optional[str], b: Optional[str]) -> bool:
    return bool(a and b) and a.upper() == b.upper()


class BluetoothSource(BaseAudioSource):
    """
    Bluetooth audio source using BlueALSA.

    Family C (active player): the sender starts playback, Milō displays it and
    drives its transport back over AVRCP. Commands route through
    `/api/audio/control/bluetooth` to `_handle_command`.
    """

    AUTO_STOP_SUPPORTED = False
    # A paused phone keeps the link (owner decision): see the module docstring.
    IDLE_POLICY = IdlePolicy.KEEP_WHILE_LINKED
    # bluez + bluealsa hold the link; only bluealsa-aplay, the writer, moves.
    REROUTE = ReroutePolicy.KEEP_SESSION
    # Milō cannot start a phone's playback: nothing is kept.
    RESUME_POLICY = ResumePolicy(capture_on=frozenset(), forget_on=frozenset(EndReason))
    # The session's link lives in BlueALSA: its death ends the session.
    SESSION_DAEMON = True

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

        # The service axis, which the session cannot carry: started and
        # waiting for a sender is READY exactly like stopped is. Half of the
        # exposure authority below.
        self._running = False
        # bluealsa-aplay is off on purpose while a multiroom reroute holds it.
        self._released = False
        # Whether BlueALSA is there: without it there is no A2DP sink, so a
        # phone must not be offered one (half of the exposure rule too).
        self._bluealsa_up = False

        self.adapter = BluetoothAdapter()
        self.agent = BluetoothAgent()
        self.monitor = BlueAlsaMonitor()
        self.avrcp = AvrcpController()
        self._artwork = ArtworkResolver(self._settings_service)

    async def _do_start(self) -> bool:
        """Start Bluetooth services and monitoring."""
        try:
            self._running = True
            self._released = False
            self._bluealsa_up = True

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

            # 5. BlueALSA: who is linked, whose stream flows, whether it lives.
            self.monitor.set_callbacks(
                on_connect=self._on_pcm_added,
                on_disconnect=self._on_pcm_removed,
                on_lost=self._on_monitor_lost,
                on_running=self._on_stream,
                on_service=self._on_bluealsa,
            )
            if not await self.monitor.start():
                raise RuntimeError("BlueALSA monitor failed to start")

            # 6. The AVRCP player feed (metadata + transport) and bluetoothd.
            # Best-effort: a sender that exposes no AVRCP target, or a BlueZ
            # that will not answer, costs the metadata and nothing else.
            self.avrcp.set_callbacks(on_update=self._on_avrcp_update, on_daemon=self._on_bluez)
            if not await self.avrcp.start():
                self._logger.warning("AVRCP feed unavailable — no track metadata")

            # 7. A link that predates us (backend restart during a stream)
            await self._adopt_linked_device()

            # 8. Re-evaluate exposure: finding a sender here means the appliance
            # must already be hidden, and step 3 opened it.
            await self._apply_exposure()

            self._publish()
            return True

        except Exception as e:
            self._logger.error(f"Start failed: {e}")
            await self._cleanup()
            await self.end_session(EndReason.USER_STOP)
            # A failure past step 3 leaves the adapter open and the senders
            # unblocked while the source settles in ERROR — the one state that
            # is neither started nor stopped, and the one _apply_exposure would
            # never be called from again.
            self._running = False
            await self._apply_exposure()
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
        await self.end_session(EndReason.SOURCE_SWITCH)
        await self._apply_exposure()

        # Stop BlueALSA services
        if self.stop_bluetooth_on_exit:
            await self._stop_service(self.bluealsa_aplay_service)
            await self._stop_service(self.bluealsa_service)

            # Keep bluetooth.service running if BT remote controller needs it
            bt_remote = await self._settings_service.get_setting('hardware.bt_remote')
            if not (bt_remote and bt_remote.get('enabled')):
                await self._stop_service(self.bluetooth_service)

        # Released last: _apply_exposure above needed it, and _cleanup runs
        # before that on purpose — blocking a peer while the monitor is still
        # reading would echo back as a disconnect event.
        await self.adapter.close()

        return True

    async def _do_release(self) -> bool:
        """Multiroom reroute (release half): stop ONLY bluealsa-aplay so the
        CamillaDSP input it feeds in direct mode is freed for the snapcast
        reconcile (snapclient feeds that same CamillaDSP in multiroom mode).

        bluealsa + bluetooth.service keep running, so the A2DP link — and the
        session — survive; unlike _do_stop(), which tears the whole stack down
        and kicks the phone off. The BlueALSA monitor tracks PCM add/remove
        driven by the bluealsa daemon (i.e. the phone's A2DP transport), not by
        the bluealsa-aplay consumer, so bouncing the writer alone never
        surfaces as a disconnect.
        """
        self._released = True
        return await self._stop_service(self.bluealsa_aplay_service)

    async def _do_acquire(self) -> bool:
        """Multiroom reroute (acquire half): restart bluealsa-aplay under the
        new MILO_MODE and re-publish state (the transition set it to STARTING).
        """
        self._released = False
        if not await self._start_service(self.bluealsa_aplay_service):
            return False
        self._publish()
        return True

    async def shutdown(self) -> None:
        await super().shutdown()
        await self._cleanup()

    COMMANDS = {
        "disconnect": None,
        "pause": None,
        "resume": None,
        "next": None,
        "prev": None,
    }
    # The transport acts on the phone holding the link; disconnect acts on the
    # link itself, and says so when there is none.
    COMMAND_SCOPES = {
        **{name: CommandScope.SESSION for name in COMMANDS},
        "disconnect": CommandScope.DEVICE,
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
        session = self._session
        if cmd == "disconnect":
            if not isinstance(session, BluetoothSession):
                return self.error_response("No device connected")
            return await self._cmd_disconnect(session)

        if not session.has_player or not _same_device(self.avrcp.device_address, session.sender):
            return self.error_response("Connected device exposes no AVRCP player")

        # An AVRCP target answers NotSupported per method — a sender may take
        # Play/Pause and refuse Next — so a refusal is this command's failure,
        # not the source's.
        if not await self.avrcp.send(self.AVRCP_COMMANDS[cmd]):
            return self.error_response(f"'{cmd}' was refused by the device")

        return self.success_response()

    async def _cmd_disconnect(self, session: BluetoothSession) -> Dict[str, Any]:
        """Drop the sender currently holding the source.

        Logged at info, which is not decoration: this is the only command a
        person issues against the Bluetooth link, `command()` traces at debug,
        and an evening was spent unable to tell a button that did nothing from
        one whose link was taken back seconds later by a second paired device.
        The line names the sender, so the journal says which one left.
        """
        self._logger.info(f"Disconnect requested for {session.name} ({session.sender})")
        session.end_requested = EndReason.USER_STOP
        if not await self.adapter.disconnect_device(session.sender):
            session.end_requested = None
            return self.error_response(f"{session.name} did not release the link")

        return self.success_response("Device disconnected")

    # === Feeds: every callback posts; the mailbox applies ===

    async def _on_pcm_added(self, address: str, name: str) -> None:
        self._post_feed(("linked", address, name))

    async def _on_pcm_removed(self, address: str, name: str) -> None:
        self._post_feed(("unlinked", address, name))

    async def _on_stream(self, address: str, running: bool) -> None:
        self._post_feed(("stream", address, running))

    async def _on_bluealsa(self, up: bool) -> None:
        self._post_feed(("bluealsa", up))

    async def _on_monitor_lost(self, reason: str) -> None:
        self._post_feed(("monitor_lost", reason))

    async def _on_avrcp_update(self, address: str, snapshot: Dict[str, Any]) -> None:
        self._post_feed(("player", address))

    def _on_bluez(self, up: bool) -> None:
        self._post_feed(("bluez", up))

    async def _handle_feed(self, events) -> None:
        """What BlueALSA, BlueZ and bluetoothd announced, in order, then one
        publish (the state machine sends a state, or a position alone)."""
        if not self._running:
            return
        arrived = False
        banners = []
        # A "player" event is read below, with the rest: an event is when to
        # look, the controller is what to believe.
        for event in events:
            kind = event[0]
            if kind == "linked":
                arrived = await self._link_up(event[1], event[2]) or arrived
            elif kind == "unlinked":
                await self._link_down(event[1])
            elif kind == "stream":
                self._stream_moved(event[1], event[2])
            elif kind == "bluealsa":
                banners += await self._bluealsa_moved(event[1])
            elif kind == "bluez":
                banners += await self._bluez_moved(event[1])
            elif kind == "monitor_lost":
                banners += await self._monitor_lost(event[1])
        session = self._session
        if isinstance(session, BluetoothSession):
            self._read_player(session)
            await self._follow(session)
            self._read_playhead(session)
        self._publish_changes()
        # The state first, then the banner: an event carrying the state it
        # reports on must carry the state after it.
        for reason in banners:
            self.broadcast_error(reason)
        if arrived:
            self.broadcast_error_cleared()

    async def _follow(self, session: "BluetoothSession") -> None:
        """Move the session where its facts say it stands."""
        await self.reconcile(DaemonSnapshot(session.sender, self._phase_of(session)))

    async def _link_up(self, address: str, name: str, running: Optional[bool] = None) -> bool:
        """A phone's PCM: its session opens, unless another phone holds the
        link — then it is turned away. True when a session opened."""
        session = self._session
        if session is not None:
            if not _same_device(session.sender, address):
                self._logger.info(f"Disconnecting {name} ({address}) - another device already connected")
                # Beside the mailbox: a Disconnect was measured at ~3 s, and the
                # phone holding the link must not wait behind it. Its answer
                # changes nothing here — the PCM leaving says the rest.
                self._bg.spawn(self.adapter.disconnect_device(address), label="turn_away")
            return False
        session = await self.reconcile(DaemonSnapshot(address.upper(), Phase.CONNECTED))
        if not isinstance(session, BluetoothSession):
            return False
        session.name = name
        session.running = bool(running)
        # Only this phone's players count from now on (E30).
        self.avrcp.follow_device(session.sender)
        self._read_player(session)
        await self._follow(session)
        self._read_playhead(session)
        self._logger.info(f"Device connected: {name} ({address})")
        # Hide first: the appliance now has a sender, so it must stop
        # offering itself to a second one instead of kicking it afterwards.
        await self._apply_exposure()
        return True

    async def _link_down(self, address: str) -> None:
        session = self._session
        if not isinstance(session, BluetoothSession) or not _same_device(session.sender, address):
            return
        if await self._unit_stopped_on_purpose():
            # Measured on a backend restart: BlueALSA stopping cleanly removes
            # its PCMs before it says it stopped. That is not a phone leaving,
            # and the appliance must not reopen behind a backend going away —
            # bluetooth.service outlives it.
            self._bluealsa_up = False
            self._logger.info(f"{self.bluealsa_service} is stopping — ending {session.name}'s session")
            await self.reconcile(None, gone=EndReason.USER_STOP)
            return
        self._logger.info(f"Device disconnected: {session.name} ({address})")
        await self.reconcile(None)

    def _stream_moved(self, address: str, running: bool) -> None:
        session = self._session
        if isinstance(session, BluetoothSession) and _same_device(session.sender, address):
            session.running = running

    def _read_player(self, session: BluetoothSession) -> None:
        """The AVRCP player, if it is this phone's (E30), and a cover lookup
        when its track changed."""
        mine = self.avrcp.has_player and _same_device(self.avrcp.device_address, session.sender)
        before = self._track_key(session.playback)
        session.has_player = mine
        session.playback = self.avrcp.snapshot() if mine else {}
        session.status = self.avrcp.status if mine else ""
        track = self._track_key(session.playback)
        if track != before and any(track) and self._session_bg is not None:
            self._session_bg.spawn(self._resolve_artwork(session, track), label="avrcp_artwork")

    def _phase_of(self, session: BluetoothSession) -> Phase:
        """Paused is the player's word; playing needs the stream too (a Mac
        switched to its own speakers says `playing` over nothing, measured);
        anything else — no player, no Status yet, stopped — cannot be told."""
        if session.status == "paused":
            return Phase.PAUSED
        if session.status in PLAYING_STATES and session.running:
            return Phase.PLAYING
        return Phase.CONNECTED

    async def _bluealsa_moved(self, up: bool) -> list:
        """BlueALSA died (every link with it, measured: no PCMRemoved) or came
        back. systemd restarts it, but not bluealsa-aplay, which `BindsTo=` it
        (measured) — without the writer, a phone that comes back is silent."""
        if not up:
            self._bluealsa_up = False
            session = self._session
            if session is not None:
                await self._daemon_gone(session)
            return []
        self._bluealsa_up = True
        banners = []
        if not self._released and not await self._start_service(self.bluealsa_aplay_service):
            self._logger.error(
                f"{self.bluealsa_aplay_service} could not be started after BlueALSA came back "
                f"— a phone linking now would be silent"
            )
            banners.append(SourceErrorReason.SERVICE_UNREACHABLE)
        # A sink again: the appliance may offer itself.
        await self._apply_exposure()
        return banners

    async def _daemon_gone(self, session: Session) -> None:
        """BlueALSA's process ended under the session (its watch, or the
        monitor's `ServiceStopped`, whichever is heard first): no sink until it
        is back, so nothing is offered meanwhile — not even after a backend
        restart, which stops BlueALSA and leaves bluetooth.service running."""
        self._bluealsa_up = False
        await super()._daemon_gone(session)

    async def _bluez_moved(self, up: bool) -> list:
        """bluetoothd left the bus or a new one's adapter is up.

        Killed, it announced nothing (measured): a session still open here is
        a death. Back, it knows nothing of what Milō set on its predecessor —
        measured, it starts closed and with no agent, and refuses every audio
        connection while the screen says ready (E75).
        """
        if not up:
            if self._session is None:
                return []
            if await self._bluez_stopped_on_purpose():
                self._logger.info("bluetoothd was stopped under the session — ending it")
                await self.end_session(EndReason.USER_STOP)
                return []
            self._logger.error("bluetoothd exited under the session — ending it; the sender has to reconnect")
            await self.end_session(EndReason.DAEMON_DIED)
            return [SourceErrorReason.STREAM_DISCONNECTED]
        self._logger.info("bluetoothd is back — applying the adapter configuration and the agent again")
        # Beside the mailbox: a daemon still powering up answers Busy, each
        # write can wait its D-Bus timeout, and a few are retried.
        self._bg.spawn(self._reconfigure_bluez(), label="reconfigure_bluez")
        return []

    async def _reconfigure_bluez(self) -> None:
        """Power the new daemon's adapter and give it the agent; the exposure,
        which reads the source's state, is applied back in the mailbox."""
        powered = False
        for attempt in range(ADAPTER_CONFIGURE_ATTEMPTS):
            if attempt:
                await asyncio.sleep(ADAPTER_CONFIGURE_RETRY_S)
            if await self.adapter.power_on() and await self.adapter.set_discoverable_timeout(0):
                powered = True
                break
        agent_ok = not self.auto_agent or await self.agent.register_again()
        self._post_result(lambda: self._bluez_reconfigured(powered, agent_ok))

    async def _bluez_reconfigured(self, powered: bool, agent_ok: bool) -> None:
        exposed = powered and await self._apply_exposure()
        if exposed and agent_ok:
            return
        self._logger.error(
            "A restarted bluetoothd could not be "
            f"{'configured' if not exposed else 'given the pairing agent'} — "
            "phones will be refused until the source is selected again"
        )
        self.broadcast_error(SourceErrorReason.SERVICE_UNREACHABLE)

    async def _bluez_stopped_on_purpose(self) -> bool:
        """Whether bluetooth.service ended as asked (a `systemctl restart`),
        not killed. Its Result is the one reliable word: read a few ms after
        the name left the bus, a restart is already `activating` again, while
        a kill is `activating` with Result `signal` (Restart=on-failure). A
        clean SIGTERM from elsewhere is not restarted at all (on-failure), so
        it too reads as a stop someone chose. Unreadable: a death."""
        state = await self._unit_state(self.bluetooth_service)
        return bool(state) and state[1] == "success"

    async def _monitor_lost(self, reason: str) -> list:
        """The BlueALSA feed died — say what it costs, and leave the state alone.

        Nothing is transitioned here. The monitor is the only thing that knows a
        sender is connected, so dropping the session would be guessing: the
        audio may well still be flowing through bluealsa-aplay. Reported on
        screen, since `source.*` loggers never reach the banner — unless
        BlueALSA is being stopped on purpose: a backend restart signals the
        monitor child with everything else in its cgroup (measured).
        """
        if await self._unit_stopped_on_purpose():
            self._logger.info(f"BlueALSA feed ended with {self.bluealsa_service} ({reason})")
            return []
        self._logger.error(
            f"BlueALSA feed lost ({reason}) — connect/disconnect will no longer be "
            f"detected; switch away from Bluetooth and back to restart it"
        )
        return [SourceErrorReason.SERVICE_UNREACHABLE]

    def _daemon_session(self, snapshot: DaemonSnapshot) -> Session:
        return BluetoothSession(phase=snapshot.phase, sender=snapshot.sender)

    async def _session_ended(self, session: Session, reason: EndReason) -> None:
        """Nothing holds the appliance any more: offer it again, to any phone."""
        self.avrcp.follow_device(None)
        if self._running:
            await self._apply_exposure()

    # === AVRCP helpers ===

    @staticmethod
    def _track_key(playback: Dict[str, Any]) -> tuple:
        """What identifies a track for artwork purposes."""
        return (playback.get("title"), playback.get("artist"), playback.get("album"))

    async def _resolve_artwork(self, session: BluetoothSession, track: tuple) -> None:
        """Look a cover up from the track text, beside the mailbox.

        AVRCP carries no image (see avrcp.py), so the only thing left is the
        text it does carry. A miss is silent — the player draws its glyph.
        """
        title, artist, album = track
        url = await self._artwork.resolve(artist or "", title or "", album or "")
        if url:
            self._post_result(lambda: self._artwork_found(session, track, url), token=session)

    async def _artwork_found(self, session: BluetoothSession, track: tuple, url: str) -> None:
        # A newer track that arrived during the lookup must not be given the
        # old one's cover.
        if track != self._track_key(session.playback):
            return
        session.artwork_url = url
        session.artwork_key = track
        self._publish()

    async def refresh_metadata(self) -> bool:
        """Re-read the playhead for `GET /api/audio/state` and the WS handshake.

        The source this hook matters most to. Nothing notifies a moved playhead
        over AVRCP, so the stored position is the one captured at the last track
        change — near zero for the whole song. A client arriving or reloading
        mid-track would be handed that and interpolate from it, which is a
        progress bar that restarts at 0:00 on every refresh.
        """
        session = self._session
        if not isinstance(session, BluetoothSession) or not session.has_player:
            return False

        await self.avrcp.read_position()
        self._read_player(session)
        self._read_playhead(session)
        self._publish_changes()
        return True

    def _read_playhead(self, session: BluetoothSession) -> None:
        """The player's playhead to the position axis; none without a player."""
        position = session.playback.get("position")
        if session.has_player and position is not None:
            self._observe_position(position)
        else:
            self._clear_position()

    # === Helper Methods ===

    def _may_accept_sender(self) -> bool:
        """The one authority for "may a sender connect right now?".

        The rule, in full: Milō is discoverable and connectable only while the
        Bluetooth source is running, BlueALSA is there to be the sink, *and* no
        phone holds it. Every exposure
        decision reads this, so the transitions cannot drift apart.
        """
        return self._running and self._bluealsa_up and self._session is None

    async def _apply_exposure(self) -> bool:
        """Make the appliance's Bluetooth exposure match the state it is in.

        Called from every transition that can change the answer — source
        start, a phone linking, a session ending, source stop, a new
        bluetoothd — rather than being set once at start and cleared once at
        stop, which is how the appliance came to keep advertising while a
        sender already held it.

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
        holder = self._session.sender if self._session is not None else None

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
    async def _adopt_linked_device(self) -> None:
        """Adopt an A2DP link that predates the monitor (BlueALSA's PCM list).

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

        if proc.returncode != 0:
            return
        for line in stdout.decode().splitlines():
            device_info = self.monitor.parse_pcm_path(line.strip())
            if device_info:
                address = device_info["address"]
                name = await self.monitor.resolve_device_name(address)
                running = await self.monitor.read_running(device_info["path"])
                # The monitor's collection is the one that authorises a
                # departure — a PCM adopted here and not handed over is a
                # sender that can never be seen leaving.
                self.monitor.adopt_device(device_info, name)
                await self._link_up(address, name, running)
                return

    async def _cleanup(self) -> None:
        """Stop the feeds and the agent, and forget what they said."""
        await self.monitor.stop()
        await self.avrcp.stop()
        if self.auto_agent:
            await self.agent.unregister()
        self._discard_feed()

    # === The view (docs: "le fil") ===

    def _session_fields(self, session: BluetoothSession) -> Dict[str, Any]:
        """Whatever the phone's AVRCP player supplied, plus a cover resolved
        from the track text (AVRCP never carries one — see avrcp.py). The
        phone's name is the sender the status card draws when it publishes no
        track."""
        playback = session.playback
        artwork = (
            session.artwork_url
            if session.artwork_url and session.artwork_key == self._track_key(playback) else None
        )
        return {
            "title": playback.get("title"),
            "artist": playback.get("artist"),
            "album": playback.get("album"),
            "artwork": artwork,
            "senders": [session.name] if session.name else [],
            "duration_ms": playback.get("duration"),
        }

    def _controls(self) -> List[str]:
        """The transport only when the phone holding the link has a player
        (E33): a Mac's publishes its Status 100 s late, and its transport
        works meanwhile."""
        session = self._session
        if not isinstance(session, BluetoothSession):
            return []
        if not session.has_player:
            return ["disconnect"]
        if session.phase is Phase.PLAYING:
            return ["pause", "next", "prev", "disconnect"]
        return ["resume", "next", "prev", "disconnect"]
