# backend/sources/radio/stream_agent.py
"""
The User-Agent a connection to a station carries, chosen by the station's host.

Infomaniak's AIS9 streaming server (Nova among the stations it hosts) splices a
15-30 s ad - a jingle and spots - over the first seconds of every new
connection whose agent is a known player, and the ad replaces the live audio
rather than delaying it. Measured 2026-11-27 on nova-odn.ice.infomaniak.ch, by
aligning each connection's audio against a continuous reference, and again
from the unit's own mpv: `libmpv` (mpv's default), `Lavf/…` (ffmpeg's), VLC,
GStreamer, Chrome, Firefox, a bare `Mozilla/5.0` and `Milo/1.0` all get the ad;
`curl/…` and `python-requests/…` get the live stream from the first second.

So the agent cannot be one value for every station. The same day, over the 52
distinct hosts of radio-browser's most-played stations, two refuse `curl/`:
ice2.somafm.com answers 403 and icecast.walmradio.com drops the connection
(it refuses `python-requests/` too), while both play under `libmpv`. Only a
host measured to splice gets the splice-free agent; every other station keeps
the player's own default, which is what played everywhere before.

Both of the radio's connections go through `agent_for`: mpv's load (a per-file
option, so the next station is back on mpv's default) and each Shazam capture
(ffmpeg's `-user_agent`), since every capture is a new connection too.
"""
from typing import Optional
from urllib.parse import urlparse

# An agent Infomaniak's server sends the live stream to.
SPLICE_FREE_AGENT = "curl/8.7.1"

# The domains whose stream hosts were measured to splice an ad for a player's
# agent. A host matches its domain or any name under it.
SPLICING_DOMAINS = ("infomaniak.ch",)


def agent_for(url: str) -> Optional[str]:
    """The agent to connect to `url` with, or None for the player's default."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    for domain in SPLICING_DOMAINS:
        if host == domain or host.endswith("." + domain):
            return SPLICE_FREE_AGENT
    return None
