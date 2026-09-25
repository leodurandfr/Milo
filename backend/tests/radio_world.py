"""What the three radio test files share, on top of RadioRig.

RadioRig (tests/test_mpv_sessions.py) is the source on a real state machine
with mpv simulated. A radio scenario also needs to see the recognition service
it drives and to shape the favorites list its knob walks, and some need the
source's own timers (the loading watchdog, the idle timeout) to run on a clock
the scenario advances instead of the wall clock.
"""
from typing import Any, Dict, List, Optional
from unittest.mock import Mock

from backend.core import audio_source
from backend.shared.mpv_audio_source import MpvAudioSource
from backend.sources.radio import source as radio_module
from backend.tests.golden.harness import (
    AsyncioProxy, VirtualClock, instant_short_sleep, settle, use_virtual_wall,
)
from backend.tests.golden.test_wire_radio import FakeShazam
from backend.tests.test_mpv_sessions import RadioRig

# Read at import, before any rig shortens it.
STALL_TIMEOUT_S = MpvAudioSource.STALL_TIMEOUT_S


class RadioWorld(RadioRig):
    """RadioRig, plus the handles a radio scenario observes the world through.

    `clock=True` puts the source's timers on a VirtualClock at their real
    values (the rig shortens the watchdog so it fires at once): a scenario
    then says when seconds pass, and a station can stay loading.
    """

    def __init__(self, monkeypatch, settings: Optional[Dict[str, Any]] = None,
                 clock: bool = False):
        super().__init__(monkeypatch, settings)
        self.shazams: List[FakeShazam] = []
        self.shazam_enabled = True             # the global recognition toggle
        world = self

        class Shazam(FakeShazam):
            def __init__(self, *a, **k):
                super().__init__(*a, **k)
                world.shazams.append(self)

            async def is_enabled(self) -> bool:
                return world.shazam_enabled

        monkeypatch.setattr(radio_module, "ShazamRecognitionService", Shazam)
        # The fakes RadioRig installed: the station store, the directory, the
        # cover lookup.
        self.data = self.source._station_data
        self.api = self.source._radio_api
        self.artwork = self.source._artwork

        self.clock: Optional[VirtualClock] = None
        if clock:
            self.clock = VirtualClock()
            use_virtual_wall(monkeypatch, self.clock)
            monkeypatch.setattr(MpvAudioSource, "STALL_TIMEOUT_S", STALL_TIMEOUT_S)

            async def sleep(delay: float, *a: Any, **k: Any) -> Any:
                if delay <= 1.0:               # a unit's settle delay
                    return await instant_short_sleep(delay)
                return await self.clock.sleep(delay)

            monkeypatch.setattr(audio_source, "asyncio", AsyncioProxy(sleep))
            self.mpv.auto_open = False

    async def advance(self, seconds: float) -> None:
        await self.clock.advance(seconds)

    # What mpv announces, each handled by the source before the step returns.

    async def opens(self) -> None:
        await self.mpv.opens()
        await settle()

    async def stalls(self) -> None:
        await self.mpv.stalls()
        await settle()

    async def recovers(self) -> None:
        await self.mpv.recovers()
        await settle()

    async def fails(self, file_error: str = "loading failed") -> None:
        await self.mpv.fails(file_error)
        await settle()

    async def ends(self, reason: str = "eof") -> None:
        await self.mpv.ends(reason)
        await settle()

    async def paused(self, value: bool) -> None:
        """mpv's pause set from outside the source (another IPC client)."""
        await self.mpv.set_property("pause", value)
        await settle()

    def track_title(self) -> Optional[str]:
        """The song recognized in the stream, as `details.track` carries it."""
        return (self.details().get("track") or {}).get("title")

    def shazam_running(self) -> bool:
        return bool(self.shazams) and self.shazams[-1].is_running

    def stream_title(self, title: Optional[str]) -> None:
        """What the stream carries in-band from now on (None: nothing)."""
        self.mpv.metadata = {"icy-title": title} if title else {}

    def favorites(self, *stations: Dict[str, Any]) -> None:
        """The favorites list, in display order, with a local record each."""
        records = {s["id"]: s for s in stations}
        self.data.favorite_ids = [s["id"] for s in stations]
        self.data.is_favorite = Mock(side_effect=lambda sid: sid in records)
        self.data.get_favorite_metadata_local = Mock(side_effect=records.get)
