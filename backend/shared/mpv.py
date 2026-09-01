"""
mpv controller via IPC socket for playing radio streams
"""
import asyncio
import json
import logging
import time
from typing import Optional, Dict, Any
from pathlib import Path


# Reply deadline for a normal command. Generous on purpose: mpv interleaves
# event lines on the same socket and can burst many of them before answering
# during a stream load.
COMMAND_TIMEOUT = 5.0

# Reply deadline for the liveness probe in connect(). A property read on an
# idle mpv answers immediately or not at all — waiting COMMAND_TIMEOUT for it
# only inflates the connect budget.
PROBE_TIMEOUT = 1.0

# Total wall-clock budget for connect(). Sized to fit inside the caller's own
# budget: _do_start runs under AudioStateMachine.TRANSITION_TIMEOUT (10s), of
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


class MpvController:
    """
    Controls mpv via IPC socket for playing radio streams

    Asynchronous communication via Unix socket with mpv in JSON IPC mode.
    Pattern inspired by libmpv and python-mpv.
    """

    def __init__(self, ipc_socket_path: str = "/tmp/milo-radio-ipc.sock"):
        self.ipc_socket_path = ipc_socket_path
        self.logger = logging.getLogger(__name__)
        self.reader: Optional[asyncio.StreamReader] = None
        self.writer: Optional[asyncio.StreamWriter] = None
        self._command_id = 0
        self._connected = False
        self._command_lock = asyncio.Lock()
        # Launch-time --stream-lavf-o (HTTP reconnect options), captured on
        # first connect and toggled off for HLS in load_stream. None until captured.
        self._default_stream_lavf_o: Optional[Any] = None

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

                self.reader, self.writer = await asyncio.open_unix_connection(self.ipc_socket_path)
                self._connected = True

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

    async def disconnect(self) -> None:
        """Disconnects from IPC socket"""
        if self.writer:
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception as e:
                self.logger.debug(f"Error closing writer: {e}")

        self.reader = None
        self.writer = None
        self._connected = False
        self.logger.info("Disconnected from mpv IPC")

    @property
    def is_connected(self) -> bool:
        """Checks if connected to IPC socket.

        at_eof() is load-bearing, not belt-and-braces: asyncio's eof_received()
        leaves the transport half-closed, so is_closing() stays False after mpv
        dies and the link reads as up until some command fails. Three of the four
        monitor ticks return before issuing any mpv I/O (podcast with no episode,
        music_library with an empty queue, CD when not playing), so on an active
        but idle source nothing ever writes to the socket and that stale True can
        last across mpv's death *and* its restart — leaving ensure_connected()
        blind on exactly the link it exists to repair. at_eof() is
        `_eof and not _buffer`, so a True cannot be a false alarm.
        """
        return (
            self._connected
            and self.writer is not None
            and not self.writer.is_closing()
            and self.reader is not None
            and not self.reader.at_eof()
        )

    async def ensure_connected(self) -> bool:
        """Re-open the IPC link before starting a playback session.

        The counterpart to is_connected: reads and transport commands go silent
        when the link is down (see _send_command), so starting playback is the
        one act that picks a fresh mpv back up — systemd puts one on the same
        socket path a few seconds after a crash. connect() is not idempotent (it
        would open a second socket and leak the first), hence the short-circuit.
        """
        return self.is_connected or await self.connect(
            timeout=RECONNECT_TIMEOUT, retry_delay=RECONNECT_TIMEOUT
        )

    async def _send_command(
        self, command: str, *args, timeout: float = COMMAND_TIMEOUT
    ) -> Optional[Dict[str, Any]]:
        """
        Sends a JSON IPC command to mpv

        mpv IPC format: {"command": ["command_name", "arg1", "arg2"], "request_id": 1}
        Serialized via _command_lock to prevent concurrent socket access.

        Args:
            command: mpv command name
            *args: Command arguments
            timeout: Reply deadline (seconds); connect() passes a shorter one
                for its liveness probe so a wedged mpv can't eat its budget.

        Returns:
            JSON response from mpv, or None if error — including immediately
            when the link is down. See ensure_connected() for the re-attach.
        """
        if not self.is_connected:
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

        # The check above must stay OUTSIDE this lock: connect()'s liveness probe
        # goes back through _send_command, which would await the same
        # non-reentrant lock forever. The ordering is deliberate.
        async with self._command_lock:
            try:
                self._command_id += 1
                request_id = self._command_id
                request = {
                    "command": [command, *args],
                    "request_id": request_id
                }

                command_json = json.dumps(request) + "\n"
                self.writer.write(command_json.encode('utf-8'))
                await self.writer.drain()

                # Read the response by matching request_id. mpv interleaves async
                # event lines (no request_id) on the same socket; during a stream
                # load or rapid station change it can burst many events before the
                # reply, so bound the search by a wall-clock deadline rather than a
                # fixed line count and keep skipping events until our reply arrives.
                started = time.monotonic()
                deadline = started + timeout
                try:
                    while True:
                        timeout = deadline - time.monotonic()
                        if timeout <= 0:
                            raise asyncio.TimeoutError
                        response_line = await asyncio.wait_for(self.reader.readline(), timeout=timeout)
                        if not response_line:
                            self.logger.debug(f"mpv socket closed while awaiting request {request_id}")
                            await self.disconnect()
                            return None

                        response = json.loads(response_line.decode('utf-8'))

                        # Ignore mpv events (no request_id)
                        if 'event' in response:
                            continue

                        # If it's the response to our request, return it
                        if response.get('request_id') == request_id:
                            error = response.get('error')
                            # Only log real errors, not transient errors
                            if error not in ('success', None, 'null', 'property unavailable'):
                                self.logger.warning(f"mpv command error: {error}")
                            return response
                        # Else: a reply to a stale/earlier request (shouldn't happen
                        # under _command_lock) — skip and keep reading until ours
                        # or the deadline.

                except asyncio.TimeoutError:
                    self.logger.debug(f"Timeout waiting for mpv response to: {command}")
                    return None

            except Exception as e:
                self.logger.error(f"Error sending command to mpv: {e}")
                await self.disconnect()
                return None

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
        if self._is_hls(url):
            await self.set_property("stream-lavf-o", "")
        elif self._default_stream_lavf_o is not None:
            await self.set_property("stream-lavf-o", self._default_stream_lavf_o)

    async def load_stream(self, url: str) -> bool:
        """
        Loads and plays a radio stream

        Args:
            url: Radio stream URL

        Returns:
            True if command sent successfully
        """
        # Before _apply_stream_options, which always issues a round-trip: on a
        # link that dropped since the last command, that round-trip would be the
        # one to discover the death and every command after it would be dropped.
        if not await self.ensure_connected():
            return False
        await self._apply_stream_options(url)
        self.logger.info(f"Loading stream: {url[:100]}...")
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
        self, property_name: str, timeout: float = COMMAND_TIMEOUT
    ) -> Optional[Any]:
        """
        Gets an mpv property

        Args:
            property_name: Property name (e.g.: "pause", "volume", "metadata")
            timeout: Reply deadline (seconds)

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

        mpv's IPC read loop skips async event lines, so metadata is not
        push-observed — this polls the `metadata` property (cheap local IPC).
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

    async def is_playing(self) -> bool:
        """
        Checks if mpv is playing via playback-time

        Returns:
            True if playing (playback-time exists)
        """
        # playback-time is the most reliable property for streams
        # It exists as soon as mpv starts decoding, and disappears when stopped
        playback_time = await self.get_property("playback-time")

        # If playback-time is a number (even 0), the stream is playing
        return isinstance(playback_time, (int, float))

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

    async def load_playlist(self, urls: list, start_index: int = 0) -> bool:
        """Build mpv's native playlist from an ordered list of stream URLs and
        start playing at ``start_index``.

        With the unit's ``--gapless-audio=yes`` a native playlist plays truly
        gapless across the queue. Loads paused so the first entry doesn't blip
        before jumping to ``start_index``, then unpauses. The first URL uses
        ``loadfile … replace`` (which clears any previous queue); the rest are
        appended in order. Returns False if the initial load fails; a single
        failed append is logged but doesn't abort the whole queue.
        """
        if not urls:
            return False

        # Before the priming pause, not just before the loads: that pause is what
        # stops entry 0 blipping before the jump to start_index, and a
        # set_property dropped on a down link would let the queue load unpaused —
        # audibly, with nothing reporting a failure.
        if not await self.ensure_connected():
            return False

        await self.set_property("pause", True)
        if not await self.load_stream(urls[0]):
            return False
        for url in urls[1:]:
            response = await self._send_command("loadfile", url, "append")
            if response is None:
                self.logger.warning("playlist append failed for an entry")

        if start_index:
            await self.set_property("playlist-pos", start_index)
        await self.set_property("pause", False)
        return True

    async def set_playlist_pos(self, index: int) -> bool:
        """Jump to a 0-based entry in the current playlist (mpv loads + plays it,
        honoring the current pause state)."""
        return await self.set_property("playlist-pos", index)

    async def replace_playlist_tail(self, keep_count: int, urls: list) -> bool:
        """Replace every playlist entry at index >= ``keep_count`` with ``urls``,
        leaving entries ``[0, keep_count)`` — including the one currently playing —
        untouched.

        Because the current entry is never reloaded, playback continues without a
        restart (and gaplessly into the new tail). Used by the live shuffle toggle
        to reorder only the upcoming tracks. Returns False if the playlist length
        can't be read; a single failed append is logged but doesn't abort.
        """
        count = await self.get_property("playlist-count")
        if count is None:
            return False
        # Drop the old tail from the end down to keep_count (stable indices).
        for index in range(int(count) - 1, keep_count - 1, -1):
            await self._send_command("playlist-remove", index)
        # Append the new tail in order.
        for url in urls:
            response = await self._send_command("loadfile", url, "append")
            if response is None:
                self.logger.warning("playlist tail append failed for an entry")
        return True
