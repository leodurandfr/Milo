#!/usr/bin/env python3
"""Apply Milō's edits to the vendored qobuz-proxy install (two, both unavoidable).

1. **Volume policy** (`backends/local/stream.py`). CamillaDSP is the volume
   authority in Milō's audio path — exactly the role external_volume plays for
   go-librespot and ignore_volume_control for shairport-sync — so by default the
   app slider must not attenuate (and lose bits) before CamillaDSP. qobuz-proxy
   has no config knob for this on the local backend (fixed_volume is DLNA-only)
   and its stream even defaults to 50%. We edit the two spots that set the
   software gain: the __init__ default becomes unity, and set_volume() reads a
   one-byte flag file ($QOBUZPROXY_DATA_DIR/allow_app_volume, written by the
   backend from the "allow app volume" setting) — '1' honors the slider,
   anything else stays at unity.

2. **Progress in the local status API** (`speaker.py`). The proxy tracks the
   playback position (buffer-latency corrected in the local backend, then
   wall-clock extrapolated between reports) and the track duration, and ships
   both to the Qobuz cloud in its state reports — but `Speaker.get_status()`
   leaves them out of `now_playing`, the only surface Milō can read. We add the
   two fields so the status poll carries progress, like AirPlay's metadata pipe.

Each edit is idempotent (re-running is a no-op) but fails loudly if the anchors
move on a version bump, forcing a conscious re-check. This MUST run with the
qobuz-proxy venv's Python so `import qobuz_proxy` resolves. Single source of
truth shared by install/qobuz-proxy.sh (fresh install) and the in-app updater
(backend/core/updates/update.py) — do not duplicate the anchors elsewhere.
"""
import io

from qobuz_proxy import speaker as speaker_module
from qobuz_proxy.backends.local import stream as stream_module

set_volume_body = (
    "        # Milo: CamillaDSP owns volume. Honor the Qobuz app slider only when\n"
    "        # Milo's \"allow app volume\" setting wrote a '1' flag; otherwise stay at\n"
    "        # unity so nothing attenuates before CamillaDSP.\n"
    "        import os as _os\n"
    "        _flag = _os.path.join(_os.environ.get(\"QOBUZPROXY_DATA_DIR\", \".\"), \"allow_app_volume\")\n"
    "        try:\n"
    "            _allow = open(_flag).read().strip() == \"1\"\n"
    "        except OSError:\n"
    "            _allow = False\n"
    "        self._volume = max(0.0, min(1.0, level / 100.0)) if _allow else 1.0"
)

STREAM_EDITS = [
    # __init__ default gain (0.0-1.0 float) → unity
    ("        self._volume: float = 0.5  # 0.0 to 1.0",
     "        self._volume: float = 1.0  # Milo: default to unity; policy applied in set_volume"),
    # set_volume() body → flag-gated: app slider honored only when allowed
    ("        self._volume = max(0.0, min(1.0, level / 100.0))", set_volume_body),
]

# get_status() only builds now_playing for a loaded track on a playing/paused
# speaker, so both player properties are safe to read at that point.
SPEAKER_EDITS = [
    ('                "volume": self._player._volume,\n',
     '                "volume": self._player._volume,\n'
     '                # Milo: expose progress locally. current_position_ms\n'
     '                # extrapolates from the last backend report, so it stays\n'
     '                # smooth between Milo\'s ~1 Hz status polls.\n'
     '                "position_ms": self._player.current_position_ms,\n'
     '                "duration_ms": self._player.duration_ms,\n'),
]


def apply(module, edits, what):
    """Rewrite `module`'s source file with `edits`, skipping any already applied."""
    path = module.__file__
    src = io.open(path, encoding="utf-8").read()

    changed = False
    for old, new in edits:
        if new in src:
            continue  # already applied (idempotent re-install)
        if old not in src:
            raise SystemExit(
                f"qobuz-proxy {what}: anchor not found in {path!r}:\n  {old!r}\n"
                "Upstream changed (version bump?) — re-verify the edit."
            )
        src = src.replace(old, new, 1)
        changed = True

    if changed:
        io.open(path, "w", encoding="utf-8").write(src)


apply(stream_module, STREAM_EDITS, "volume-policy wiring")
apply(speaker_module, SPEAKER_EDITS, "status-progress wiring")
print("qobuz-proxy patched: unity-default volume policy + position/duration in /api/status")
