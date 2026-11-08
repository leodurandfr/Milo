# backend/sources/radio/radiofrance.py
"""Radio France's own now-playing, for the stations Radio France streams.

Radio France streams carry no in-band title (no `icy-metaint` on icecast, an
empty timed ID3 on HLS), so before this feed every one of them was named by
Shazam. Radio France publishes what it plays instead, per station, on an
unauthenticated endpoint its own player reads (`livemeta`, rule
`fip_extended`: the only rule with separate title / interpreters / album). It
answers with the song on air (`now`), usually the next one (`next[0]`), and
when to ask again (`delayToRefresh`). A station with no song layer — a talk
station, a show — answers "Le direct" with every field null, which names
nothing: the source then falls back to Shazam as before.

Everything here is pure except `RadioFranceClient`: the source asks it for
one answer, parses it, merges it into what the session knows, and asks
`schedule` which song the listener hears now. The source owns the timers.
"""
import logging
import re
from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("source.radio.radiofrance")

API_URL = "https://api.radiofrance.fr/livemeta/live/{station}/fip_extended"
COVER_URL = "https://www.radiofrance.fr/pikapi/images/{cover}/1000x1000"
# Where a song's cover is served to every client: Milō's own copy of COVER_URL.
#
# Radio France answers WebP whatever `Accept` says (measured 2026-11-08,
# `image/webp` under `Accept: image/jpeg`), and the iPhone's Lock Screen does
# not draw WebP. Its extension converts one, but only when its process lives
# long enough to download and decode it, which is rare: the system wakes it for
# a few milliseconds per push, and every FIP song stayed without its cover
# while Shazam's JPEGs had shown. Through Milō it is a JPEG on the LAN, fetched
# the moment the feed announces the song (`RadioSource._warm_covers`), ~37 s
# before it is heard. Browsers still get WebP (`StationLogos.get`).
COVER_ROUTE = "/api/radio/covers/{cover}"
# A cover id as Radio France spells them (a UUID): path-safe, nothing to escape.
_COVER_ID = re.compile(r"^[0-9A-Za-z][0-9A-Za-z-]{0,63}$")
USER_AGENT = "Milo/1.0"
TIMEOUT_S = 5.0

# `delayToRefresh` is about (end of the song on air − now) + 10-30 s, 10 s
# once that end is past, and up to an hour on a talk station: followed between
# these bounds it costs 2-3 requests per song and catches a talk station that
# starts a music hour within a minute. 30 s when it is absent or not a number.
MIN_REFRESH_S = 10.0
MAX_REFRESH_S = 60.0
DEFAULT_REFRESH_S = 30.0

# When the listener hears a song change, from its announced `startTime`
# (measured 2026-11-08 on 31 transitions, FIP and its webradios):
# - the announced start comes ~6 s before the audible boundary (loudness dips
#   at +4.8…+7.2 s in the HLS audio, stamped by its PROGRAM-DATE-TIME);
# - icecast delivers 33.3 s of audio in burst on connect (ADTS frames counted)
#   and keeps that lead, and its live edge sits ~2 s behind HLS's — mpv plays
#   from the start of the burst, so a change is heard at startTime + 37 s ±3;
# - HLS joins 3 segments from the edge (PDT ≈ wall − 19.5 s), so a change is
#   heard at startTime + ~24 s.
# The data itself lands 9-37 s after `startTime`, so on icecast it is
# normally there before the song is heard. Snapcast and CamillaDSP add under
# a second, ignored.
ICECAST_LEAD_S = 37.0
HLS_LEAD_S = 24.0

# How long a song stays on screen past its announced end (shifted by the lead)
# when nothing is known to follow it. The planned end is corrected by 6-14 s
# against the actual playout, and between two songs the feed answers "Le
# direct" for up to ~20 s while the next one is already on air.
HOLD_S = 30.0

# How far two consecutive songs' planned times may overlap and still be two
# songs in a row: real answers end one a second past the next one's start.
# Well below the 6-14 s by which a correction moves a planned time.
_TOUCH_S = 2.0

# The placeholder title of an entry that names no song.
_NO_SONG_TITLE = "Le direct"

# Radio France's stream slug → its livemeta station id, all 78 stations that
# carry a stream. Each id's `https://www.radiofrance.fr/transistor/live/{id}`
# names its slug in `sources[].url` (measured 2026-11-08 over ids 0-130,
# 395-420, 600-720, 1000-1010, 1100-1110, 5600-5615). There is no listing
# endpoint, so this is data, refreshed by hand when Radio France adds a
# station. Talk stations are included: they answer "Le direct", which names
# nothing, and Shazam keeps naming their music.
STATIONS: Dict[str, int] = {
    "franceinter": 1,                        # France Inter
    "franceinfo": 2,                         # franceinfo
    "francemusique": 4,                      # France Musique
    "franceculture": 5,                      # France Culture
    "mouv": 6,                               # Mouv'
    "fip": 7,                                # FIP
    "fbfrequenzamora": 11,                   # ICI RCFM
    "fbalsace": 12,                          # ICI Alsace
    "fbarmorique": 13,                       # ICI Armorique
    "fbauxerre": 14,                         # ICI Auxerre
    "fbbearn": 15,                           # ICI Béarn Bigorre
    "fbbelfort": 16,                         # ICI Belfort-Montbéliard
    "fbberry": 17,                           # ICI Berry
    "fbbesancon": 18,                        # ICI Besançon
    "fbbourgogne": 19,                       # ICI Bourgogne
    "fbbreizizel": 20,                       # ICI Breizh Izel
    "fbchampagne": 21,                       # ICI Champagne-Ardenne
    "fbcotentin": 22,                        # ICI Cotentin
    "fbcreuse": 23,                          # ICI Creuse
    "fbdromeardeche": 24,                    # ICI Drôme Ardèche
    "fbgardlozere": 25,                      # ICI Gard Lozère
    "fbgascogne": 26,                        # ICI Gascogne
    "fbgironde": 27,                         # ICI Gironde
    "fbherault": 28,                         # ICI Hérault
    "fbisere": 29,                           # ICI Isère
    "fblarochelle": 30,                      # ICI La Rochelle
    "fblimousin": 31,                        # ICI Limousin
    "fbloireocean": 32,                      # ICI Loire Océan
    "fbsudlorraine": 33,                     # ICI Lorraine (Meurthe-et-Moselle et Vosges)
    "fbmayenne": 34,                         # ICI Mayenne
    "fbnord": 36,                            # ICI Nord
    "fbbassenormandie": 37,                  # ICI Normandie (Calvados - Orne)
    "fbhautenormandie": 38,                  # ICI Normandie (Seine-Maritime - Eure)
    "fborleans": 39,                         # ICI Orléans
    "fbpaysdauvergne": 40,                   # ICI Pays d'Auvergne
    "fbpaysbasque": 41,                      # ICI Pays Basque
    "fbpaysdesavoie": 42,                    # ICI Pays de Savoie
    "fbperigord": 43,                        # ICI Périgord
    "fbpicardie": 44,                        # ICI Picardie
    "fbprovence": 45,                        # ICI Provence
    "fbroussillon": 46,                      # ICI Roussillon
    "fbtouraine": 47,                        # ICI Touraine
    "fbvaucluse": 48,                        # ICI Vaucluse
    "fbazur": 49,                            # ICI Azur
    "fblorrainenord": 50,                    # ICI Lorraine (Moselle et Pays Haut)
    "fbpoitou": 54,                          # ICI Poitou
    "fiprock": 64,                           # FIP Rock
    "fipjazz": 65,                           # FIP Jazz
    "fipgroove": 66,                         # FIP Groove
    "fb1071": 68,                            # ICI Paris Île-de-France
    "fipworld": 69,                          # FIP Monde
    "fipnouveautes": 70,                     # FIP Nouveautés
    "fipreggae": 71,                         # FIP Reggae
    "fipelectro": 74,                        # FIP Electro
    "fipmetal": 77,                          # FIP Metal
    "fippop": 78,                            # FIP Pop
    "fbelsass": 90,                          # ICI Elsass
    "fbmaine": 91,                           # ICI Maine
    "fbtoulouse": 92,                        # ICI Occitanie
    "fbstetienne": 93,                       # ICI Saint-Étienne Loire
    "fiphiphop": 95,                         # FIP Hip-Hop
    "fipsacrefrancais": 96,                  # FIP Sacré français !
    "francemusiqueeasyclassique": 401,       # Classique Easy
    "francemusiqueclassiqueplus": 402,       # Classique Plus
    "francemusiqueconcertsradiofrance": 403,  # Concerts Radio France
    "francemusiqueocoramonde": 404,          # Ocora Musiques du Monde
    "francemusiquelajazz": 405,              # La Jazz
    "francemusiquelacontemporaine": 406,     # La Contemporaine
    "francemusiquelabo": 407,                # Musique de Films
    "francemusiquebaroque": 408,             # La Baroque
    "francemusiqueopera": 409,               # Opéra
    "francemusiquepianozen": 410,            # Piano Zen
    "francemusiqueclassiquelove": 411,       # Classique Love
    "fipcultes": 709,                        # FIP Cultes
    "franceinterlamusiqueinter": 1101,       # La musique d'Inter
    "monpetitfranceinter": 1102,             # Mon petit France Inter
    "fbchansonfrancaise": 5601,              # 100% chanson française
    "fb100pour100annees80": 5602,            # 100% années 80
}

# direct.fipradio.fr's numbered webradio mounts, by the icecast mount each one
# redirects to (measured 301 targets, 2026-11-08).
_FIP_WEBRADIOS = {
    "1": "fiprock", "2": "fipjazz", "3": "fipgroove", "4": "fipworld",
    "5": "fipnouveautes", "6": "fipreggae", "7": "fippop", "8": "fipelectro",
}

# The hosts that 301 to icecast.radiofrance.fr with the same mount name.
_ALIAS_HOSTS = frozenset({
    "direct.francebleu.fr", "direct.fipradio.fr", "direct.franceinter.fr",
    "direct.francemusique.fr", "direct.mouv.fr",
})

_ICECAST_MOUNT_RE = re.compile(r"^/([a-z0-9]+)-(?:hifi|midfi|lofi)\.(?:aac|mp3)$")
_ALIAS_MOUNT_RE = re.compile(r"^/live/([a-z0-9]+)-(?:hifi|midfi|lofi)\.(?:aac|mp3)$")
_FIP_WEBRADIO_RE = re.compile(r"^/live/fip-webradio(\d)\.mp3$")
_HLS_PATH_RE = re.compile(r"^/([a-z0-9]+)/([a-z0-9]+?)(?:_hifi|_midfi|_lofi)?\.m3u8$")


def station_for(url: str) -> Optional[int]:
    """The livemeta station id of a Radio France stream URL, else None.

    Reads the URL alone, without a request: the aliases on `direct.*` hosts
    are mapped by their known redirect, never resolved. The host is matched
    case-insensitively and the slug lower-cased, as icecast does on redirect
    (`Fbelsass` → `fbelsass`); a query string is ignored.
    """
    try:
        parsed = urlparse((url or "").strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    slug: Optional[str] = None
    if host == "icecast.radiofrance.fr":
        match = _ICECAST_MOUNT_RE.match(path)
        slug = match.group(1) if match else None
    elif host == "stream.radiofrance.fr":
        match = _HLS_PATH_RE.match(path)
        if match and match.group(1) == match.group(2):
            slug = match.group(1)
    elif host in _ALIAS_HOSTS:
        webradio = _FIP_WEBRADIO_RE.match(path) if host == "direct.fipradio.fr" else None
        if webradio:
            slug = _FIP_WEBRADIOS.get(webradio.group(1))
        else:
            match = _ALIAS_MOUNT_RE.match(path)
            slug = match.group(1) if match else None
    return STATIONS.get(slug) if slug else None


def lead_for(url: str) -> float:
    """How long after a song's announced start the listener hears it, by
    the stream's transport (see ICECAST_LEAD_S)."""
    try:
        path = urlparse(url or "").path.lower()
    except ValueError:
        path = ""
    return HLS_LEAD_S if path.endswith(".m3u8") else ICECAST_LEAD_S


@dataclass(frozen=True)
class Song:
    """One song the feed announced, its times in epoch seconds."""
    title: str
    artist: str
    start: float
    end: float
    artwork: Optional[str]

    @property
    def key(self) -> Tuple[str, str]:
        return (_normalized(self.title), _normalized(self.artist))

    def track(self) -> Dict[str, Any]:
        """The song as the radio track the view carries."""
        return {"title": self.title, "artist": self.artist, "artwork": self.artwork}


@dataclass(frozen=True)
class Answer:
    """One parsed answer: its songs, when to ask again, and — for an answer
    not shaped as expected — what was wrong (its songs are then none)."""
    songs: Tuple[Song, ...]
    refresh_s: float
    malformed: Optional[str] = None


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def _number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _song(entry: Any) -> Optional[Song]:
    """An entry as a song, or None for the "Le direct" placeholder, a show,
    or anything without a title and times. `interpreters` may be null or
    empty on a song (classical and film music: the performer or nothing)."""
    if not isinstance(entry, dict):
        return None
    title = entry.get("title")
    if not isinstance(title, str) or not title.strip() or title.strip() == _NO_SONG_TITLE:
        return None
    start, end = _number(entry.get("startTime")), _number(entry.get("endTime"))
    if start is None or end is None:
        return None
    artist = entry.get("interpreters")
    cover = entry.get("cover")
    return Song(
        title=title.strip(),
        artist=artist.strip() if isinstance(artist, str) else "",
        start=start,
        end=end,
        artwork=cover_artwork(cover) if isinstance(cover, str) and cover else None,
    )


def cover_artwork(cover: str) -> str:
    """The URL a client fetches a song's cover from: Milō's copy, or Radio
    France's own for an id that would not make a path."""
    if _COVER_ID.match(cover):
        return COVER_ROUTE.format(cover=cover)
    return COVER_URL.format(cover=cover)


def cover_source(cover: str) -> Optional[str]:
    """Radio France's URL for a cover id, or None if it is not one. What
    /api/radio/covers fetches: built here, never taken from the caller, so
    the route fetches Radio France covers and nothing else."""
    return COVER_URL.format(cover=cover) if _COVER_ID.match(cover) else None


def cover_of(artwork: Optional[str]) -> Optional[str]:
    """The cover id inside a URL `cover_artwork` made, or None."""
    prefix = COVER_ROUTE.format(cover="")
    if not artwork or not artwork.startswith(prefix):
        return None
    cover = artwork[len(prefix):]
    return cover if _COVER_ID.match(cover) else None


def _refresh_s(payload: Dict[str, Any]) -> float:
    delay_ms = _number(payload.get("delayToRefresh"))
    if delay_ms is None:
        return DEFAULT_REFRESH_S
    return min(MAX_REFRESH_S, max(MIN_REFRESH_S, delay_ms / 1000))


def parse(payload: Any) -> Answer:
    """The songs among an answer's `prev[]`, `now` and `next[]`. Never
    raises: an answer of another shape is one with no songs, and says why."""
    if not isinstance(payload, dict):
        return Answer((), DEFAULT_REFRESH_S, malformed=f"a {type(payload).__name__}, not an object")
    prev, now, upcoming = payload.get("prev", []), payload.get("now"), payload.get("next", [])
    if not isinstance(prev, list) or not isinstance(upcoming, list) or not isinstance(now, dict):
        return Answer((), _refresh_s(payload), malformed="no prev/now/next")
    songs = tuple(song for song in map(_song, [*prev, now, *upcoming]) if song is not None)
    return Answer(songs, _refresh_s(payload))


def _announced_over(fresh: Song, known: Song) -> bool:
    """Whether `fresh` takes the place of `known`, another song: announced
    at the same start, or running over the time `known` was planned to start
    at. Consecutive songs touch by a second in real answers (an `endTime`
    one past the next `startTime`), which is not another song: only an
    overlap past _TOUCH_S counts."""
    return fresh.start == known.start or fresh.start < known.start < fresh.end - _TOUCH_S


def merge(known: List[Song], fresh: Tuple[Song, ...], now: float, lead: float) -> List[Song]:
    """What the session knows after an answer, by start time.

    A song the answer names again replaces what was known of it (its planned
    times corrected by 6-14 s), except that once heard its start never moves
    later: the title is on screen, and undoing it for the seconds of the
    correction would flash the song before it back. A known song the answer
    does not name is dropped when one it names is announced over its start;
    otherwise it is kept — an answer naming only `now` ("Le direct" next)
    forgets nothing. Songs before the latest one already heard are dropped:
    since that one stays heard, nothing will show them again.
    """
    def heard(s: Song) -> bool:
        return s.start + lead <= now

    by_key = {s.key: s for s in known}
    arrived: Dict[Tuple[str, str], Song] = {}
    for song in fresh:
        before = by_key.get(song.key)
        if before is not None and heard(before) and before.start < song.start:
            song = replace(song, start=before.start)
        arrived[song.key] = song
    songs = [
        s for s in known
        if s.key not in arrived and not any(_announced_over(f, s) for f in arrived.values())
    ]
    songs.extend(arrived.values())
    songs.sort(key=lambda s: s.start)
    latest = [i for i, s in enumerate(songs) if heard(s)]
    return songs[latest[-1]:] if latest else songs


def names_a_song(songs: List[Song], now: float, lead: float) -> bool:
    """Whether any known song is on air or still to come — a stale answer
    naming a song long over does not make a station one the feed names."""
    return any(now < s.end + lead + HOLD_S for s in songs)


def schedule(songs: List[Song], now: float, lead: float) -> Tuple[Optional[Song], Optional[float]]:
    """The song the listener hears at `now`, and when to look again.

    The song shown is the known one with the latest start already heard
    (`start + lead <= now`), held past its own end (+ lead) for HOLD_S, or
    for as long as a later known song is not heard yet. A "Le direct" answer
    removes nothing, so only this rule clears a title. The next look is the
    earliest of the next song becoming heard and the shown one expiring;
    None when neither will happen without a new answer.
    """
    heard = [s for s in songs if s.start + lead <= now]
    upcoming = [s for s in songs if s.start + lead > now]
    current = max(heard, key=lambda s: s.start) if heard else None
    shown = None
    if current is not None and (now < current.end + lead + HOLD_S or upcoming):
        shown = current
    looks = [min(s.start for s in upcoming) + lead] if upcoming else []
    if shown is not None and now < shown.end + lead + HOLD_S:
        looks.append(shown.end + lead + HOLD_S)
    return shown, (min(looks) if looks else None)


class RadioFranceUnanswered(Exception):
    """The feed answered something other than a now-playing (a status)."""


class RadioFranceClient:
    """The one HTTP session to Radio France's now-playing endpoint.

    Built once by the radio source, reopened on the first read after a close
    (the source closes it with mpv, and on shutdown).
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    async def fetch(self, station: int) -> Any:
        """The decoded answer for `station`. Raises when there is none: a
        transport error, a timeout, a status other than 200, a body that is
        not JSON."""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(headers={"User-Agent": USER_AGENT})
        async with self._session.get(
            API_URL.format(station=station), timeout=aiohttp.ClientTimeout(total=TIMEOUT_S),
        ) as response:
            if response.status != 200:
                raise RadioFranceUnanswered(f"HTTP {response.status}")
            return await response.json(content_type=None)

    async def close(self) -> None:
        session, self._session = self._session, None
        if session is not None and not session.closed:
            await session.close()
