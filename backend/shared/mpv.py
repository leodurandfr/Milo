"""
mpv controller via IPC socket, shared by the four mpv sources
"""
import asyncio
import contextlib
import json
import logging
import time
from typing import Any, Callable, Dict, List, Optional
from pathlib import Path


# Reply deadline for a normal command.
COMMAND_TIMEOUT = 5.0

# Reply deadline for the liveness probe in connect(). A property read on an
# idle mpv answers immediately or not at all — waiting COMMAND_TIMEOUT for it
# only inflates the connect budget.
PROBE_TIMEOUT = 1.0

# Total wall-clock budget for connect(). Sized to fit inside the caller's own
# budget: _do_start runs under AudioStateMachine.TRANSITION_TIMEOUT, of
# which _start_service_and_wait already spends its settle delay.
CONNECT_TIMEOUT = 6.0

# Budget for the deliberate re-attach in ensure_connected(). Distinct from
# CONNECT_TIMEOUT, which buys patience for a cold start where mpv was forked half
# a second ago and its socket may not exist yet. A play command has nothing to
# wait for: either mpv is listening now or systemd has not restarted it yet, and
# an honest immediate failure beats a frozen button.
#
# "One attempt" is spent, not inferred: ensure_connected passes this as the retry
# delay as well, so can_retry's `retry_delay + attempt_cost < timeout` is false on
# the first pass for every branch and for any attempt cost. It used to rest on
# PROBE_TIMEOUT being the reserve on all three branches, which stopped being true
# the moment the cheap branch got the reserve it actually costs.
RECONNECT_TIMEOUT = PROBE_TIMEOUT

# The event subscribers receive when a link ends, whoever ended it. mpv never
# sends it; nothing more arrives on that link afterwards.
LINK_LOST = "link-lost"

EventCallback = Callable[[Dict[str, Any], "MpvLink"], None]


class MpvLink:
    """One IPC connection to mpv.

    Its identity is the generation token: every connect makes a new one, so an
    event or a reply is only as current as the link that carried it. Compare
    with `is`, never by value.
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.pending: Dict[int, asyncio.Future] = {}
        self.closed = False
        self.reader_task: Optional[asyncio.Task] = None


class MpvController:
    """
    Controls mpv via its JSON IPC socket.

    One reader task per link reads every line mpv sends: a reply resolves the
    future its `request_id` names, an event goes to every subscriber. Commands
    therefore never wait on each other, and events are delivered whether or not
    a command is in flight.
    """

    def __init__(self, ipc_socket_path: str = "/tmp/milo-radio-ipc.sock"):
        self.ipc_socket_path = ipc_socket_path
        self.logger = logging.getLogger(__name__)
        self._link: Optional[MpvLink] = None
        self._request_id = 0
        self._subscribers: List[EventCallback] = []
        # observe id -> property name, re-issued on every new link: mpv forgets
        # observations with the connection that made them.
        self._observed: Dict[int, str] = {}
        self._observe_id = 0
        # Launch-time --stream-lavf-o (HTTP reconnect options), captured on
        # first connect and toggled off for HLS in load_stream. None until captured.
        self._default_stream_lavf_o: Optional[Any] = None
        # Which scope (HLS or not) the current link's stream options are set
        # for: a queue appends one entry per track, and re-sending an
        # unchanged option doubled the round-trips before its first sound.
        self._stream_options_hls: Optional[bool] = None

    async def connect(
        self, timeout: float = CONNECT_TIMEOUT, retry_delay: float = 0.5
    ) -> bool:
        """
        Connects to mpv IPC socket, retrying until `timeout` elapses.

        Bounded by a wall-clock deadline, not an attempt count: an attempt
        against a socket that exists but never answers costs a whole probe
        timeout, so counting attempts gave no usable upper bound (10 attempts
        could run for ~55s under a 10s caller budget). What the caller has is
        time, so that is what this spends.

        Running out of that time is a warning, not an error. It is the one log
        line on this path under the `backend` hierarchy, which is what
        WebSocketLogHandler forwards to the UI banner wholesale (main.py) -- the
        source's own logger is rooted at `source` and raises nothing. So an
        ERROR here is a *second* user-facing report of a failure the state
        machine already broadcasts as a typed SystemErrorEvent, and being a raw
        log line it races that event for App.vue's single banner slot. The same
        reasoning, and the same conclusion, as AudioStateMachine's own
        "Transition failed" warning. What is genuinely broken still shouts: the
        unexpected-exception arm below stays an error.

        Every give-up says how long it actually waited, because that number is
        the only evidence from which the budget could ever be re-sized, and it
        is only produced by the boots that fail.

        Args:
            timeout: Total budget for the whole retry loop (seconds)
            retry_delay: Delay between attempts (seconds)

        Returns:
            True if connection successful
        """
        started = time.monotonic()
        deadline = started + timeout

        # A connect over a live link replaces it rather than opening a second
        # socket beside it: each link carries a reader task, so a leaked link
        # would be a leaked task still delivering events.
        if self._link is not None:
            await self.disconnect()

        def can_retry(attempt_cost: float) -> bool:
            """Room for another delay plus what the *next* attempt will cost.

            Per branch, not one reserve for all of them. An attempt that opens
            the socket and waits on mpv can burn a whole PROBE_TIMEOUT, so those
            branches keep it — and they are not the rare case: mpv does not
            unlink its IPC socket on SIGTERM and the unit carries
            RuntimeDirectoryPreserve=yes, so every restart that is not a first
            boot finds the previous file sitting there and is refused by it.

            The branch that only asks whether a path exists costs a stat. Made
            to reserve a probe as well, it stopped polling a whole PROBE_TIMEOUT
            early and gave up holding budget it was never going to spend: on the
            boot of 2026-09-01 the connect abandoned a cold mpv 5.08s into a
            6.0s budget. Same constants, the whole of them.
            """
            return time.monotonic() + retry_delay + attempt_cost < deadline

        while True:
            try:
                if not Path(self.ipc_socket_path).exists():
                    if can_retry(0.0):
                        await asyncio.sleep(retry_delay)
                        continue
                    self.logger.warning(
                        f"IPC socket never appeared in "
                        f"{time.monotonic() - started:.1f}s: {self.ipc_socket_path}"
                    )
                    return False

                reader, writer = await asyncio.open_unix_connection(self.ipc_socket_path)
                self._open_link(reader, writer)

                # Verify mpv responds to commands before declaring connected
                # Use get_property with idle-active (always available even when idle)
                test_response = await self._send_command(
                    "get_property", "idle-active", timeout=PROBE_TIMEOUT
                )
                if test_response is None:
                    self.logger.debug("mpv socket connected but not responding, retrying...")
                    await self.disconnect()
                    if can_retry(PROBE_TIMEOUT):
                        await asyncio.sleep(retry_delay)
                        continue
                    self.logger.warning(
                        f"mpv never answered a command in "
                        f"{time.monotonic() - started:.1f}s"
                    )
                    return False

                for observe_id, name in self._observed.items():
                    response = await self._send_command(
                        "observe_property", observe_id, name, timeout=PROBE_TIMEOUT
                    )
                    if response is None or response.get("error") != "success":
                        self.logger.warning(f"mpv did not re-observe '{name}' on the new link")

                # Capture the launch-time reconnect options once, while mpv is
                # pristine — load_stream clears them for HLS and restores this.
                if self._default_stream_lavf_o is None:
                    self._default_stream_lavf_o = await self.get_property(
                        "stream-lavf-o", timeout=PROBE_TIMEOUT
                    )

                self.logger.info(
                    f"Connected to mpv IPC socket in "
                    f"{time.monotonic() - started:.1f}s: {self.ipc_socket_path}"
                )
                return True

            except (ConnectionRefusedError, FileNotFoundError) as e:
                if can_retry(PROBE_TIMEOUT):
                    self.logger.debug(f"Retry: {e}")
                    await asyncio.sleep(retry_delay)
                    continue
                self.logger.warning(
                    f"Failed to connect to mpv in "
                    f"{time.monotonic() - started:.1f}s: {e}"
                )
                return False
            except Exception as e:
                self.logger.error(f"Unexpected error connecting to mpv: {e}")
                return False

    def _open_link(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> MpvLink:
        """Make `reader`/`writer` the current link and start its reader task."""
        link = MpvLink(reader, writer)
        link.reader_task = asyncio.get_running_loop().create_task(self._read_loop(link))
        self._link = link
        self._stream_options_hls = None
        return link

    async def _read_loop(self, link: MpvLink) -> None:
        """Route every line mpv sends on `link`, until the link ends.

        A reply resolves the future its `request_id` names; a reply nobody waits
        for any more (its caller timed out) is dropped. An event goes to every
        subscriber. This is the link's only reader, so the end of this loop is
        the end of the link, whatever ended it.
        """
        try:
            while True:
                try:
                    line = await link.reader.readline()
                except ValueError:
                    # Over the StreamReader limit: readline() has discarded it
                    # and the next line is intact.
                    self.logger.warning("mpv sent a line over the read limit; skipped")
                    continue
                if not line:
                    return
                try:
                    message = json.loads(line)
                except ValueError:
                    message = None
                if not isinstance(message, dict):
                    self.logger.warning(f"mpv sent a line that is not a JSON object: {line[:80]!r}")
                    continue
                if "event" in message:
                    self._dispatch(message, link)
                    continue
                future = link.pending.get(message.get("request_id"))
                if future is not None and not future.done():
                    future.set_result(message)
        except ConnectionError as e:
            self.logger.debug(f"mpv IPC read ended: {e}")
        except Exception as e:
            self.logger.error(f"mpv IPC reader failed, link dropped: {e}")
        finally:
            self._close_link(link)

    def _close_link(self, link: MpvLink) -> None:
        """End `link` once: release every caller waiting on it, then announce it.

        A waiting caller gets None at once rather than its whole reply deadline.
        """
        if link.closed:
            return
        link.closed = True
        if self._link is link:
            self._link = None
        for future in link.pending.values():
            if not future.done():
                future.set_result(None)
        link.writer.close()
        self._dispatch({"event": LINK_LOST}, link)

    def _dispatch(self, event: Dict[str, Any], link: MpvLink) -> None:
        """Hand one event to every subscriber, on the reader task.

        A subscriber that raises costs itself, not the link: this loop is the
        only thing routing replies, so letting the exception through would stop
        every command. Logged at error, because it is a bug in that consumer.
        """
        for callback in list(self._subscribers):
            try:
                callback(event, link)
            except Exception as e:
                self.logger.error(f"mpv event subscriber failed on '{event.get('event')}': {e}")

    def subscribe(self, callback: EventCallback) -> Callable[[], None]:
        """Receive every event mpv sends, and LINK_LOST when a link ends.

        `callback(event, link)` runs on the reader task, synchronously, so it must
        not block: a consumer with work to do queues it. `link` is the link the
        event arrived on — compare it with `is` against `self.link` to tell a
        current event from one of a link since replaced. A subscription outlives
        links. Returns the function that ends it.
        """
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return unsubscribe

    async def disconnect(self) -> None:
        """Close the current link, if any; subscribers get LINK_LOST."""
        link = self._link
        if link is None:
            return
        self._close_link(link)
        if link.reader_task is not None:
            link.reader_task.cancel()
            await asyncio.gather(link.reader_task, return_exceptions=True)
        try:
            await link.writer.wait_closed()
        except Exception as e:
            self.logger.debug(f"Error closing writer: {e}")
        self.logger.info("Disconnected from mpv IPC")

    @property
    def link(self) -> Optional[MpvLink]:
        """The live link, or None. Every connect makes a new one."""
        return self._link if self.is_connected else None

    @property
    def is_connected(self) -> bool:
        """Checks if connected to IPC socket.

        The reader task closes the link the moment it reads mpv's EOF, and
        at_eof() covers the instant before it has run: asyncio's eof_received()
        leaves the transport half-closed, so is_closing() alone stays False after
        mpv dies. That matters because three of the four monitor ticks issue no
        mpv I/O on an idle source, so nothing else would notice — leaving
        ensure_connected() blind on exactly the link it exists to repair.
        """
        link = self._link
        return (
            link is not None
            and not link.writer.is_closing()
            and not link.reader.at_eof()
        )

    async def ensure_connected(self) -> bool:
        """Re-open the IPC link before starting a playback session.

        The counterpart to is_connected: reads and transport commands go silent
        when the link is down (see _send_command), so starting playback is the
        one act that picks a fresh mpv back up — systemd puts one on the same
        socket path a few seconds after a crash. The short-circuit keeps a play
        command from replacing a link that works.
        """
        return self.is_connected or await self.connect(
            timeout=RECONNECT_TIMEOUT, retry_delay=RECONNECT_TIMEOUT
        )

    async def _send_command(
        self, command: str, *args, timeout: Optional[float] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Sends a JSON IPC command to mpv and waits for its own reply.

        mpv IPC format: {"command": ["command_name", "arg1", "arg2"], "request_id": 1}
        The frame is written with no await before it, so frames leave in call
        order; the reply comes back through the reader task, matched by id, so
        commands in flight never wait on each other.

        Args:
            command: mpv command name
            *args: Command arguments
            timeout: Reply deadline (seconds), COMMAND_TIMEOUT when omitted;
                connect() passes a shorter one for its liveness probe so a
                wedged mpv can't eat its budget.

        Returns:
            JSON response from mpv, or None if error — including immediately
            when the link is down. See ensure_connected() for the re-attach.
        """
        link = self._link
        if link is None or not self.is_connected:
            # Deliberately does NOT re-open the link. A read that reconnects can
            # succeed against the *fresh idle* mpv systemd restarts seconds
            # later: is_connected then reads True, MpvAudioSource's disconnect
            # fallback never fires, and the rest of the tick answers from that
            # idle mpv. That is how podcast's `idle_active is True` branch
            # persisted a two-minutes-in episode as completed, with no banner and
            # no trace. Re-attaching belongs to a play command, which has a user
            # waiting and a way to report failure.
            self.logger.debug(f"mpv link down, dropping: {command}")
            return None

        self._request_id += 1
        request_id = self._request_id
        future = asyncio.get_running_loop().create_future()
        link.pending[request_id] = future
        try:
            try:
                frame = json.dumps({"command": [command, *args], "request_id": request_id})
                link.writer.write((frame + "\n").encode("utf-8"))
                await link.writer.drain()
            except Exception as e:
                self.logger.error(f"Error sending command to mpv: {e}")
                self._close_link(link)
                return None

            try:
                async with asyncio.timeout(COMMAND_TIMEOUT if timeout is None else timeout):
                    response = await future
            except TimeoutError:
                self.logger.debug(f"Timeout waiting for mpv response to: {command}")
                return None
        finally:
            link.pending.pop(request_id, None)

        if response is None:
            self.logger.debug(f"mpv socket closed while awaiting request {request_id}")
            return None
        error = response.get("error")
        # Only log real errors, not transient errors
        if error not in ("success", None, "null", "property unavailable"):
            self.logger.warning(f"mpv command error: {error}")
        return response

    @staticmethod
    def _is_hls(url: str) -> bool:
        """True if the URL is an HLS playlist (.m3u8), ignoring query/fragment."""
        path = url.split("?", 1)[0].split("#", 1)[0]
        return path.lower().endswith(".m3u8")

    async def _apply_stream_options(self, url: str) -> None:
        """
        Scope mpv's --stream-lavf-o reconnect options per stream.

        The reconnect options are HTTP-stream options that keep an Icecast
        stream alive, but they stall HLS: ffmpeg reconnects at every segment
        EOF instead of advancing, so mpv never produces a first frame and the
        stream hangs in "loading" forever. Suppress them for .m3u8 (the HLS
        demuxer handles its own segment retries) and keep the launch defaults
        (captured in connect) otherwise. The systemd unit stays the single
        source of truth.
        """
        if not url:
            return
        hls = self._is_hls(url)
        if hls == self._stream_options_hls:
            return
        if hls:
            applied = await self.set_property("stream-lavf-o", "")
        elif self._default_stream_lavf_o is not None:
            applied = await self.set_property("stream-lavf-o", self._default_stream_lavf_o)
        else:
            return
        self._stream_options_hls = hls if applied else None

    async def _prepare_load(self, url: str, quiet: bool = False) -> bool:
        """Re-attach, scope the stream options, log the load. False: no mpv.

        `quiet` logs at debug: a queue appends one entry per track, and the
        caller says once what it is loading.
        """
        # Before _apply_stream_options, which always issues a round-trip: on a
        # link that dropped since the last command, that round-trip would be the
        # one to discover the death and every command after it would be dropped.
        if not await self.ensure_connected():
            return False
        await self._apply_stream_options(url)
        # Query string dropped, not truncated: the Music Library streams from
        # Navidrome over Subsonic token auth, so its URL carries
        # `u=<user>&t=<md5(password+salt)>&s=<salt>` — the token and the salt
        # that cracks it, on one INFO line, once per track. The path alone is
        # what a stream failure is diagnosed from.
        (self.logger.debug if quiet else self.logger.info)(
            "Loading stream: %s", url.split("?")[0][:100]
        )
        return True

    async def load_stream(self, url: str) -> bool:
        """
        Loads and plays a radio stream

        Args:
            url: Radio stream URL

        Returns:
            True if command sent successfully
        """
        if not await self._prepare_load(url):
            return False
        response = await self._send_command("loadfile", url, "replace")

        # mpv can return transient errors (None, "property unavailable")
        # during initial stream loading. We accept these errors.
        if response is None:
            self.logger.info("loadfile returned None")
            return False

        error = response.get('error')
        # Accept 'success' AND transient errors (None, null, property unavailable)
        # "property unavailable" happens when quickly changing stations
        # Only real errors ("file not found", etc.) cause failure
        if error in ('success', None, 'null', 'property unavailable'):
            return True

        # Log only real errors
        self.logger.error(f"loadfile failed with error: {error}")
        return False

    async def loadfile(
        self,
        url: str,
        *,
        start_s: Optional[float] = None,
        pause: bool = False,
        mode: str,
    ) -> Optional[int]:
        """Load `url` and return the playlist entry id mpv gave it.

        The id is what `start-file` and `end-file` name, so it is how an event
        is matched to the load that caused it. `start_s` and `pause` ride on the
        load as per-file options: one command lands paused at the right second
        (measured on mpv 0.40), no wait-then-seek. mpv wants the index slot once
        options follow, and ignores it for `replace` and `append`.

        Same preparation as load_stream (re-attach, HLS options, redacted log).

        Returns:
            The entry id, or None when there is no mpv, no answer or a refusal.
        """
        if not await self._prepare_load(url, quiet=mode == "append"):
            return None
        options = []
        if start_s is not None:
            options.append(f"start={start_s}")
        if pause:
            options.append("pause=yes")
        args = [url, mode, -1, ",".join(options)] if options else [url, mode]
        response = await self._send_command("loadfile", *args)

        if response is None:
            self.logger.info("loadfile returned None")
            return None
        error = response.get("error")
        if error != "success":
            if error in (None, "null", "property unavailable"):
                self.logger.info(f"loadfile answered without an entry: {error}")
            else:
                self.logger.error(f"loadfile failed with error: {error}")
            return None
        data = response.get("data")
        return data.get("playlist_entry_id") if isinstance(data, dict) else None

    async def observe(self, property_name: str) -> Optional[int]:
        """Have mpv push `property_name` as `property-change` events.

        mpv sends the current value at once, then every change, each carrying
        the returned id. The observation outlives the link — connect() re-issues
        it on every new one, since mpv forgets it with the connection — and
        observing a name twice is the same observation.

        Returns:
            The observation id, or None if mpv did not take it.
        """
        for observe_id, name in self._observed.items():
            if name == property_name:
                return observe_id
        self._observe_id += 1
        observe_id = self._observe_id
        response = await self._send_command("observe_property", observe_id, property_name)
        if response is None or response.get("error") != "success":
            return None
        self._observed[observe_id] = property_name
        return observe_id

    async def stop(self) -> bool:
        """
        Stops current playback

        Returns:
            True if command sent successfully
        """
        self.logger.info("Stopping playback")
        response = await self._send_command("stop")
        return response is not None and response.get('error') == 'success'

    async def get_property(
        self, property_name: str, timeout: Optional[float] = None
    ) -> Optional[Any]:
        """
        Gets an mpv property

        Args:
            property_name: Property name (e.g.: "pause", "volume", "metadata")
            timeout: Reply deadline (seconds), COMMAND_TIMEOUT when omitted

        Returns:
            Property value or None
        """
        response = await self._send_command("get_property", property_name, timeout=timeout)
        if response and response.get('error') == 'success':
            return response.get('data')
        return None

    async def get_metadata(self) -> Dict[str, str]:
        """
        Get current in-band stream metadata (ICY / HLS tags).

        Polled from the monitor tick (cheap local IPC); `observe("metadata")`
        could push it instead.
        mpv exposes the ICY StreamTitle as `icy-title` and the station name as
        `icy-name`; HLS ID3 tags surface under their own keys.

        Returns:
            Lowercased-key dict of string metadata values (non-string values
            dropped), or an empty dict when mpv reports no metadata.
        """
        raw = await self.get_property("metadata")
        if not isinstance(raw, dict):
            return {}
        return {
            str(key).lower(): value
            for key, value in raw.items()
            if isinstance(value, str)
        }

    async def set_property(self, property_name: str, value: Any) -> bool:
        """
        Sets an mpv property

        Args:
            property_name: Property name
            value: New value

        Returns:
            True if successful
        """
        response = await self._send_command("set_property", property_name, value)
        return response is not None and response.get('error') == 'success'

    async def wait_until_advancing(
        self, timeout: float = 3.0, poll_interval: float = 0.05
    ) -> bool:
        """Wait until mpv's playhead actually advances past 0.

        After un-pausing, mpv's audio output has a startup latency during which
        `time-pos` stays at 0 for up to ~1s — a mere "time-pos is a number"
        check (file loaded) fires immediately and is NOT real playback. Callers
        gate UI/buffering state on this so a progress bar doesn't run ahead of a
        not-yet-moving playhead. Bounded by `timeout` so a stalled source can't
        hang the caller. Returns True once advancing, False on timeout.
        """
        started = time.monotonic()
        deadline = started + timeout
        while time.monotonic() < deadline:
            time_pos = await self.get_property("time-pos")
            if isinstance(time_pos, (int, float)) and time_pos > 0:
                return True
            await asyncio.sleep(poll_interval)
        return False

    async def pause(self) -> bool:
        """
        Pauses playback

        Returns:
            True if successful
        """
        return await self.set_property("pause", True)

    async def resume(self) -> bool:
        """
        Resumes playback

        Returns:
            True if successful
        """
        return await self.set_property("pause", False)

    async def seek(self, position: float) -> bool:
        """
        Seeks to a specific position

        Args:
            position: Position in seconds

        Returns:
            True if successful
        """
        response = await self._send_command("seek", position, "absolute")
        return response is not None and response.get('error') == 'success'

    # === Playlist (gapless queue) ===

    async def play_index(self, index: int) -> bool:
        """Start playlist entry `index` (0-based). mpv ends the current entry
        with `end-file reason=stop` and sends `start-file` for this one."""
        response = await self._send_command("playlist-play-index", index)
        return response is not None and response.get('error') == 'success'

    async def remove_entry(self, index: int) -> bool:
        """Remove playlist entry `index` (0-based) without touching the others."""
        response = await self._send_command("playlist-remove", index)
        return response is not None and response.get('error') == 'success'
