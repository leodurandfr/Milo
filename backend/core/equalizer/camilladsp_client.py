# backend/core/equalizer/camilladsp_client.py
"""Async WebSocket client for the CamillaDSP daemon.

Milo's own, replacing `pycamilladsp`: that library is synchronous (a blocking
socket behind a thread lock), so every DSP call had to cross a single-thread
executor — a hop on the path that carries volume, mute and EQ — and the
satellite had to reach into `client._ws.sock` to get a socket timeout at all.

The protocol, captured from CamillaDSP 4.1.3 on 127.0.0.1:1234 (2026-09-14),
not reconstructed from memory. A param-less command is a bare JSON string, a
parameterised one an object, and the answer comes back under the command's own
name:

    ->  "GetState"                     <-  {"GetState":{"result":"Ok","value":"Running"}}
    ->  {"SetVolume":-12.0}            <-  {"SetVolume":{"result":"Ok"}}

Three answer shapes, all three measured:

* `{"<Command>":{"result":"Ok","value":...}}` — done. `value` is absent for the
  setters.
* `{"<Command>":{"result":{"<Kind>":"<message>"}}}` — the command was understood
  and refused, e.g. `ConfigReadError` for a file that does not exist. There is
  no `value` key in this shape, so nothing may require one.
* `{"Invalid":{"error":"<message>"}}` — the frame never became a command:
  malformed JSON, an unknown command name, or an argument of the wrong type.
  **The key is `Invalid`, not the command**, which is why a reply that does not
  carry the command's name cannot simply be read as "answer to something else".

The daemon answers WebSocket pings (0.25 ms round trip, measured over a 45 s
idle connection), so the library's own keepalive works and an idle connection
survives; `CamillaDSPService._probe_connection` still asks `GetState` on top,
because a daemon whose command loop is wedged answers pings all the same.
"""
import asyncio
import contextlib
import json
import logging
from typing import Any, Dict, List, Optional

import yaml
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import WebSocketException

logger = logging.getLogger(__name__)

# The rates a measured capture rate is rounded to, as pycamilladsp did it: the
# daemon reports what it counted (48127 while playing 48 kHz on this unit), and
# that raw figure on screen reads as a fault.
STANDARD_RATES = (
    8000, 11025, 16000, 22050, 32000, 44100, 48000, 88200,
    96000, 176400, 192000, 352800, 384000, 705600, 768000,
)

_NO_ARG = object()


class CamillaDspError(Exception):
    """The daemon answered, and the answer was a refusal."""


class CamillaDspClient:
    """One WebSocket to one CamillaDSP daemon, one command in flight at a time.

    The daemon answers on a single socket in order, so `_lock` is what makes a
    reply belong to the command that is waiting for it. A caller never sees a
    default: a refusal raises `CamillaDspError`, a socket that went away raises
    `ConnectionError`, and a daemon that says nothing raises `TimeoutError`
    rather than hanging.
    """

    TIMEOUT = 5.0

    def __init__(self, host: str, port: int, timeout: float = TIMEOUT):
        self.host = host
        self.port = int(port)
        self.timeout = timeout
        self.version: Optional[str] = None
        self._ws = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._ws is not None

    async def connect(self) -> None:
        """Open the socket and read the daemon's version back.

        The version query is the handshake: a port that accepts a connection
        without being CamillaDSP fails here rather than at the first command.
        """
        self._ws = await ws_connect(
            f"ws://{self.host}:{self.port}", open_timeout=self.timeout
        )
        try:
            self.version = await self.get_version()
        except Exception:
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Close the socket, whatever state it is in."""
        ws, self._ws = self._ws, None
        if ws is not None:
            # A closing handshake that fails changes nothing here: the socket is
            # being dropped either way, and the caller is on its way out.
            with contextlib.suppress(Exception):
                await ws.close()

    # === The wire ===

    async def _query(self, command: str, arg: Any = _NO_ARG) -> Any:
        frame = json.dumps(command if arg is _NO_ARG else {command: arg})

        async with self._lock:
            ws = self._ws
            if ws is None:
                raise ConnectionError("Not connected to CamillaDSP")

            try:
                await ws.send(frame)
                raw = await asyncio.wait_for(ws.recv(), self.timeout)
            except TimeoutError:
                # A late reply would answer the *next* command. The stream can
                # no longer be trusted, so it goes rather than the command.
                await self._drop(ws, f"{command} went unanswered")
                raise TimeoutError(
                    f"CamillaDSP did not answer {command} within {self.timeout:.0f}s"
                ) from None
            except (OSError, WebSocketException) as err:
                await self._drop(ws, f"the socket went away during {command}")
                raise ConnectionError(
                    f"Lost connection to CamillaDSP during {command}: {err}"
                ) from err

            return await self._parse(ws, command, raw)

    async def _drop(self, ws, reason: str) -> None:
        """Forget a socket that can no longer carry a conversation.

        Logged here rather than left to the caller: several of them answer a
        failed DSP call with a cached value and no log of their own, so a stream
        that went out of step would otherwise be repaired in complete silence.
        """
        if self._ws is ws:
            self._ws = None
            logger.warning("Dropping the CamillaDSP connection: %s", reason)
        with contextlib.suppress(Exception):
            await ws.close()

    async def _parse(self, ws, command: str, raw) -> Any:
        try:
            reply = json.loads(raw)
        except (TypeError, ValueError) as err:
            raise CamillaDspError(
                f"CamillaDSP answered {command} with a frame that is not JSON: {raw!r}"
            ) from err

        if not isinstance(reply, dict):
            raise CamillaDspError(f"CamillaDSP answered {command} with {raw!r}")

        invalid = reply.get("Invalid")
        if isinstance(invalid, dict):
            raise CamillaDspError(
                f"CamillaDSP rejected {command}: {invalid.get('error')}"
            )

        body = reply.get(command)
        if not isinstance(body, dict):
            # Neither this command's answer nor a rejection: the socket is one
            # reply out of step and every later command would read the wrong one.
            await self._drop(ws, f"the reply to {command} was a reply to something else")
            raise CamillaDspError(
                f"CamillaDSP answered {command} with a reply to something else: {raw!r}"
            )

        result = body.get("result")
        if result == "Ok":
            return body.get("value")

        if isinstance(result, dict) and result:
            kind, message = next(iter(result.items()))
        else:
            kind, message = result, body.get("value")
        raise CamillaDspError(f"CamillaDSP refused {command}: {kind}: {message}")

    # === Commands ===

    async def get_version(self) -> str:
        return await self._query("GetVersion")

    async def get_state(self) -> Optional[str]:
        """The processing state: `Running`, `Paused`, `Inactive`, `Starting`, `Stalled`."""
        return await self._query("GetState")

    async def get_volume(self) -> float:
        return float(await self._query("GetVolume"))

    async def set_volume(self, volume: float) -> None:
        await self._query("SetVolume", float(volume))

    async def get_mute(self) -> bool:
        return bool(await self._query("GetMute"))

    async def set_mute(self, muted: bool) -> None:
        await self._query("SetMute", bool(muted))

    async def get_playback_peak(self) -> List[float]:
        return await self._query("GetPlaybackSignalPeak")

    async def get_capture_peak(self) -> List[float]:
        return await self._query("GetCaptureSignalPeak")

    async def get_config_file_path(self) -> Optional[str]:
        return await self._query("GetConfigFilePath")

    async def get_config(self) -> Optional[Dict[str, Any]]:
        """The graph the daemon is running, or None while it has none."""
        raw = await self._query("GetConfigJson")
        return json.loads(raw) if raw else None

    async def set_config(self, config: Dict[str, Any]) -> None:
        await self._query("SetConfigJson", json.dumps(config))

    async def read_config_file(self, path: str) -> Optional[Dict[str, Any]]:
        """Parse a config file the daemon reads for us — YAML on the wire, not JSON."""
        raw = await self._query("ReadConfigFile", path)
        return yaml.safe_load(raw) if raw else None

    async def get_capture_rate(self) -> Optional[int]:
        """The measured capture rate, rounded to a standard rate, or None."""
        rate = int(await self._query("GetCaptureRate"))
        if not 0.96 * STANDARD_RATES[0] < rate < 1.04 * STANDARD_RATES[-1]:
            return None
        nearest = min(STANDARD_RATES, key=lambda standard: abs(standard - rate))
        return nearest if 0.96 < rate / nearest < 1.04 else None
