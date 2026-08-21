# backend/sources/dlna/server_resolver.py
"""Name the UPnP media server that serves the currently playing track.

UPnP tells a renderer nothing about the control point that pushed the audio, so
there is no sender name to show (see DLNA_CLIENT_NAME) — but the push does carry
URLs, and those point at the *media server*: the CurrentTrackURI the renderer was
told to play, and the DIDL-Lite album art. That host is identifiable: a
MediaServer advertises a friendlyName in its device description, which is the
name its owner recognises ("Freebox Server", a Synology, a Plex box) and a far
better source-bar label than a static "DLNA".

Resolution is an SSDP M-SEARCH plus a description fetch — seconds, not
milliseconds — so it can never sit in the metadata path: DlnaSource runs it in
the background and keeps the static label until it answers. Results are cached
per host, misses included: a sweep costs the full MX wait whether or not anything
replies, and a host's name does not change from one track to the next.

Two constraints below were measured on the test LAN, not assumed:

- **A device answers an M-SEARCH for MediaServer even when it is not one.** A Hue
  bridge replied to a `device:MediaServer:1` search with ST `upnp:rootdevice` and
  `device:basic:1`. So the filter is applied to each *response*, never to the
  search target — trusting the target alone would have labelled DLNA playback
  "Hue Bridge".
- **One device answers several times.** That same Hue sent six responses for one
  LOCATION. Ambiguity is therefore counted in distinct LOCATIONs per host, not in
  responses: two of those means two servers on one host, and nothing says which
  one served the track — a fallback, not a coin toss.
"""
import asyncio
import logging
from collections import defaultdict
from typing import Dict, Optional, Set
from urllib.parse import urlparse

from async_upnp_client.aiohttp import AiohttpRequester
from async_upnp_client.client_factory import UpnpFactory
from async_upnp_client.search import async_search

logger = logging.getLogger("source.dlna.server")

_MEDIA_SERVER_ST = "urn:schemas-upnp-org:device:MediaServer:1"
# Matched against the responder's own ST/USN. Kept version-agnostic on purpose:
# the search asks for :1, but what a device answers with is its business.
_MEDIA_SERVER_MARKER = "device:mediaserver:"

# async_search sends this as the SSDP MX and then waits it out in full, so it is
# the floor of every sweep — devices are entitled to spread replies across it.
_SEARCH_TIMEOUT = 3
_DESCRIPTION_TIMEOUT = 5
# Whole-sweep ceiling. Descriptions are fetched concurrently, so this is
# search + one fetch + slack, and it does not grow with the number of servers on
# the LAN. It exists for the host that accepts a connection and then says
# nothing: without it that task would sit open for the rest of the session.
_RESOLVE_TIMEOUT = _SEARCH_TIMEOUT + _DESCRIPTION_TIMEOUT + 4


def host_of(url: str) -> Optional[str]:
    """The hostname a media URL points at, or None if there is none to read.

    urlparse only raises on a malformed IPv6 literal, and it raises from
    .hostname rather than from the parse — hence the guard around both.
    """
    try:
        return urlparse(url).hostname
    except ValueError:
        return None


class MediaServerResolver:
    """host → media-server friendlyName, one SSDP sweep per unknown host."""

    def __init__(self) -> None:
        # host → name, or None for a host that answered nothing usable. Misses
        # are cached too — re-sweeping on every track would spend the MX wait
        # again to reach the same answer. Only a *clean* negative is stored: a
        # sweep that failed or timed out leaves the host unknown, so a transient
        # network fault does not pin the static label for the whole session.
        self._cache: Dict[str, Optional[str]] = {}
        # One sweep at a time. Two concurrent M-SEARCHes would each pay the MX
        # wait to collect the same responses, and the second would usually be
        # answering the first's question.
        self._lock = asyncio.Lock()

    async def resolve(self, host: str) -> Optional[str]:
        """Return the friendly name of the media server at host, or None."""
        if not host:
            return None
        if host in self._cache:
            return self._cache[host]

        async with self._lock:
            # A sweep that finished while we queued may have answered already.
            if host in self._cache:
                return self._cache[host]

            try:
                resolved = await asyncio.wait_for(self._sweep(), timeout=_RESOLVE_TIMEOUT)
            except asyncio.TimeoutError:
                logger.warning("SSDP sweep exceeded %ss; %s stays unnamed", _RESOLVE_TIMEOUT, host)
                return None
            except Exception as e:
                logger.warning("SSDP sweep failed (%s); %s stays unnamed", e, host)
                return None

            # One sweep sees the whole LAN: bank every name it found, not just
            # the one asked for, so a second server costs no second sweep.
            self._cache.update(resolved)
            # Nothing claimed this host — that is the clean negative.
            self._cache.setdefault(host, None)
            return self._cache[host]

    async def _sweep(self) -> Dict[str, str]:
        """M-SEARCH the LAN, then name every host that offers exactly one server."""
        locations = await self._search_locations()

        unambiguous: Dict[str, str] = {}
        for host, urls in locations.items():
            if len(urls) > 1:
                logger.info(
                    "%s advertises %d media servers; nothing says which one served "
                    "the track, keeping the static label", host, len(urls)
                )
                continue
            unambiguous[host] = next(iter(urls))

        if not unambiguous:
            return {}

        hosts = list(unambiguous)
        names = await asyncio.gather(*(self._friendly_name(unambiguous[h]) for h in hosts))
        return {host: name for host, name in zip(hosts, names) if name}

    async def _search_locations(self) -> Dict[str, Set[str]]:
        """Collect the LOCATIONs of responders that really are media servers."""
        locations: Dict[str, Set[str]] = defaultdict(set)

        async def _on_response(headers) -> None:
            # Runs inside the SSDP protocol's datagram handling: it reads, it
            # does not reach out, so there is nothing here that can throw at it.
            identity = f"{headers.get('st') or ''}\n{headers.get('usn') or ''}".lower()
            if _MEDIA_SERVER_MARKER not in identity:
                return
            location = headers.get("location")
            host = host_of(location) if location else None
            if host:
                locations[host].add(location)

        await async_search(
            async_callback=_on_response,
            timeout=_SEARCH_TIMEOUT,
            search_target=_MEDIA_SERVER_ST,
        )
        return locations

    async def _friendly_name(self, location: str) -> Optional[str]:
        """Fetch a device description and read its friendlyName (best-effort).

        The name wanted is the root device's: an embedded MediaServer is usually
        left unnamed or named generically, while the root carries the label the
        owner actually recognises ("Freebox Server" for the box whose
        MediaServer is an embedded service).
        """
        requester = AiohttpRequester(timeout=_DESCRIPTION_TIMEOUT)
        factory = UpnpFactory(requester, non_strict=True)
        try:
            device = await factory.async_create_device(location)
        except Exception as e:
            logger.warning("Media server description %s unreadable: %s", location, e)
            return None
        return (device.friendly_name or "").strip() or None
