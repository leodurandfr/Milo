"""mpv as the four mpv sources see it, modelled on what mpv 0.40 was measured to do.

One in-memory player behind MpvController's public surface — both the polled
reads the sources used to make and the events they now consume — so a test
drives the outside world once and any source implementation sees one
consistent truth. Every behaviour below was observed on the unit
(docs/plans/source-architecture.md, phase 0a and phase 1 measurements):

- `loadfile … append` plays nothing and answers the new entry's id;
  `replace` clears the playlist and starts the file; per-entry `start=` and
  `pause=yes` apply when that entry starts, and the pause is the entry's own
  (the next entry starts unpaused).
- An entry that starts sends `start-file`; opening it sends `file-loaded`, a
  `seek` when it starts past 0, and `playback-restart` — paused or not.
- A jump (`playlist-play-index`) or a `stop` ends the current entry with
  `end-file reason=stop`; a file that cannot be read ends with
  `reason=error` before any `playback-restart`; the last entry ending sends
  `idle`.
- A seek before the file is open is refused (E57, measured 3/3).
- A stream that stops delivering sets `paused-for-cache` and sends nothing
  else (measured: 155 s before an `eof`, never for a stalled socket).
- A link that dies sends LINK_LOST to subscribers; reads answer None.

Events reach subscribers synchronously, as the controller's reader task hands
them over. What happens *when* is the test's to say: an entry that starts
waits for `opens()` unless `auto_open` is set, a stall waits for `stalls()`.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from backend.shared.mpv import LINK_LOST


@dataclass(eq=False)
class Entry:
    id: int
    url: str
    options: Dict[str, str] = field(default_factory=dict)


class SimLink:
    """One IPC connection; compared by identity like MpvLink."""


class MpvSim:
    def __init__(self, *, auto_open: bool = True) -> None:
        self.auto_open = auto_open
        self.accept = True                 # False: every command is refused
        self.broken: Dict[str, str] = {}   # url fragment -> file_error
        self.durations: Dict[str, float] = {}
        self.default_duration: Optional[float] = 1800.0
        self.metadata: Dict[str, str] = {}
        self.playlist: List[Entry] = []
        self.current: Optional[Entry] = None
        self.opened = False
        self.position: Optional[float] = None
        self.paused = False
        self._pause_by_entry = False
        self.stalled = False
        self.speed = 1.0
        self.props: Dict[str, Any] = {}    # anything else a source sets
        self._next_id = 0
        self._link: Optional[SimLink] = None
        self._subscribers: List[Callable] = []
        self._observed: List[str] = []
        self.sent: List[tuple] = []        # every command, for assertions
        self.connected_once = False

    # === Scenario side: what the test says the world does ===

    async def opens(self) -> None:
        """The current entry's file opens and playback restarts."""
        if self.current is None or self.opened:
            return
        self.opened = True
        self.stalled = False
        self._emit({"event": "file-loaded"})
        if float(self.current.options.get("start", 0) or 0) > 0:
            self._emit({"event": "seek"})
        self._emit_prop("paused-for-cache", False)
        self._emit({"event": "playback-restart"})

    async def ends(self, reason: str = "eof", file_error: Optional[str] = None) -> None:
        """The current entry ends; mpv moves to the next one or goes idle."""
        if self.current is None:
            return
        self._end_current(reason, file_error)
        self._advance()

    async def fails(self, file_error: str = "loading failed") -> None:
        await self.ends("error", file_error)

    async def stalls(self) -> None:
        """The stream stops delivering: the cache runs dry."""
        self.stalled = True
        self._emit_prop("paused-for-cache", True)

    async def recovers(self) -> None:
        self.stalled = False
        self._emit_prop("paused-for-cache", False)

    def playhead(self, seconds: float) -> None:
        self.position = seconds

    async def dies(self) -> None:
        """mpv goes away: the link ends, everything after it answers None."""
        link, self._link = self._link, None
        self.playlist, self.current, self.opened = [], None, False
        if link is not None:
            self._dispatch({"event": LINK_LOST}, link)

    # === MpvController's surface ===

    async def connect(self, *a, **k) -> bool:
        self._link = SimLink()
        self.connected_once = True
        return True

    async def ensure_connected(self) -> bool:
        return self.is_connected or await self.connect()

    async def disconnect(self) -> None:
        link, self._link = self._link, None
        if link is not None:
            self._dispatch({"event": LINK_LOST}, link)

    @property
    def is_connected(self) -> bool:
        return self._link is not None

    @property
    def link(self) -> Optional[SimLink]:
        return self._link

    def subscribe(self, callback: Callable) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            if callback in self._subscribers:
                self._subscribers.remove(callback)
        return unsubscribe

    async def observe(self, name: str) -> Optional[int]:
        if not self.is_connected:
            return None
        if name not in self._observed:
            self._observed.append(name)
        self._emit({"event": "property-change", "name": name, "data": self._read(name)})
        return self._observed.index(name) + 1

    async def get_property(self, name: str, timeout: Optional[float] = None) -> Any:
        if not self.is_connected:
            return None
        return self._read(name)

    async def set_property(self, name: str, value: Any) -> bool:
        self.sent.append(("set_property", name, value))
        if not self._ok():
            return False
        if name == "pause":
            self._set_pause(bool(value))
        elif name == "speed":
            self.speed = value
        elif name == "playlist-pos":
            return self._play_index(int(value))
        else:
            self.props[name] = value
        return True

    async def get_metadata(self) -> Dict[str, str]:
        return dict(self.metadata) if self.is_connected else {}

    async def is_playing(self) -> bool:
        return isinstance(self._read("playback-time"), (int, float))

    async def pause(self) -> bool:
        return await self.set_property("pause", True)

    async def resume(self) -> bool:
        return await self.set_property("pause", False)

    async def seek(self, position: float) -> bool:
        self.sent.append(("seek", position))
        if not self._ok() or self.current is None or not self.opened:
            return False
        self.position = float(position)
        self._emit({"event": "seek"})
        self._emit({"event": "playback-restart"})
        return True

    async def stop(self) -> bool:
        self.sent.append(("stop",))
        if not self._ok():
            return False
        if self.current is not None:
            self._end_current("stop")
        self.playlist = []
        self._go_idle()
        return True

    async def loadfile(self, url: str, *, start_s: Optional[float] = None,
                       pause: bool = False, mode: str) -> Optional[int]:
        self.sent.append(("loadfile", url, mode, start_s, pause))
        if not self._ok():
            return None
        options = {}
        if start_s is not None:
            options["start"] = str(start_s)
        if pause:
            options["pause"] = "yes"
        self._next_id += 1
        entry = Entry(self._next_id, url, options)
        if mode == "replace":
            if self.current is not None:
                self._end_current("stop")
            self.playlist = [entry]
            self._start(entry)
        else:
            self.playlist.append(entry)
        return entry.id

    async def play_index(self, index: int) -> bool:
        self.sent.append(("play_index", index))
        if not self._ok():
            return False
        return self._play_index(index)

    async def remove_entry(self, index: int) -> bool:
        self.sent.append(("remove_entry", index))
        if not self._ok() or not 0 <= index < len(self.playlist):
            return False
        self.playlist.pop(index)
        return True

    # The calls the sources made before they listened to events.

    async def load_stream(self, url: str) -> bool:
        return await self.loadfile(url, mode="replace") is not None

    async def load_playlist(self, urls: list, start_index: int = 0) -> bool:
        if not urls or not self._ok():
            return False
        await self.set_property("pause", True)
        await self.loadfile(urls[0], mode="replace")
        for url in urls[1:]:
            await self.loadfile(url, mode="append")
        if start_index:
            self._play_index(start_index)
        await self.set_property("pause", False)
        return True

    async def set_playlist_pos(self, index: int) -> bool:
        return await self.set_property("playlist-pos", index)

    async def replace_playlist_tail(self, keep_count: int, urls: list) -> bool:
        if not self._ok():
            return False
        self.playlist = self.playlist[:keep_count]
        for url in urls:
            await self.loadfile(url, mode="append")
        return True

    async def wait_until_advancing(self, *a, **k) -> bool:
        return self.opened

    # === The model ===

    def _ok(self) -> bool:
        return self.accept and self.is_connected

    def _read(self, name: str) -> Any:
        playing_file = self.current is not None and self.opened
        values = {
            "pause": self.paused,
            "idle-active": self.current is None,
            "core-idle": self.paused or not playing_file or self.stalled,
            "paused-for-cache": self.stalled if self.current is not None else None,
            "playback-time": self.position if playing_file else None,
            "time-pos": self.position if playing_file else None,
            "duration": self._duration() if playing_file else None,
            "playlist-pos": self.playlist.index(self.current) if self.current in self.playlist else -1,
            "playlist-count": len(self.playlist),
            "metadata": dict(self.metadata),
            "speed": self.speed,
        }
        return values[name] if name in values else self.props.get(name)

    def _duration(self) -> Optional[float]:
        for fragment, seconds in self.durations.items():
            if fragment in self.current.url:
                return seconds
        return self.default_duration

    def _play_index(self, index: int) -> bool:
        if not 0 <= index < len(self.playlist):
            return False
        if self.current is not None:
            self._end_current("stop")
        self._start(self.playlist[index])
        return True

    def _start(self, entry: Entry) -> None:
        self.current, self.opened, self.stalled = entry, False, False
        self.position = float(entry.options.get("start", 0) or 0)
        self._emit({"event": "start-file", "playlist_entry_id": entry.id})
        if entry.options.get("pause") == "yes":
            self._pause_by_entry = True
            self._set_pause(True)
        broken = next((err for frag, err in self.broken.items() if frag in entry.url), None)
        if broken is not None:
            self._end_current("error", broken)
            self._advance()
            return
        if self.auto_open:
            self.opened = True
            self._emit({"event": "file-loaded"})
            if self.position:
                self._emit({"event": "seek"})
            self._emit_prop("paused-for-cache", False)
            self._emit({"event": "playback-restart"})

    def _end_current(self, reason: str, file_error: Optional[str] = None) -> None:
        entry = self.current
        event = {"event": "end-file", "reason": reason, "playlist_entry_id": entry.id}
        if file_error:
            event["file_error"] = file_error
        self._emit(event)
        if self._pause_by_entry:
            # A per-file pause is the entry's own: mpv puts the previous value back.
            self._pause_by_entry = False
            self._set_pause(False)

    def _advance(self) -> None:
        index = self.playlist.index(self.current) if self.current in self.playlist else -1
        if 0 <= index < len(self.playlist) - 1:
            self._start(self.playlist[index + 1])
        else:
            self._go_idle()

    def _go_idle(self) -> None:
        was_busy = self.current is not None
        self.current, self.opened, self.position = None, False, None
        if was_busy:
            self._emit({"event": "idle"})

    def _set_pause(self, value: bool) -> None:
        if value != self.paused:
            self.paused = value
            self._emit_prop("pause", value)

    def _emit_prop(self, name: str, value: Any) -> None:
        if name in self._observed:
            self._emit({"event": "property-change", "name": name, "data": value})

    def _emit(self, event: Dict[str, Any]) -> None:
        if self._link is not None:
            self._dispatch(event, self._link)

    def _dispatch(self, event: Dict[str, Any], link: SimLink) -> None:
        for callback in list(self._subscribers):
            callback(event, link)
