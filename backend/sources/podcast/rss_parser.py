"""
Parse a podcast RSS feed into the series and episode shapes Milō already reads.

This replaced the Podcast Index normalizers, and it inherits their contract: the
keys produced here are the ones `routes.py`, the Pinia store and the two shared
players consume, unchanged. What changed is where they come from — the
publisher's own feed instead of a third party's copy of it.

Three things learned from the feeds themselves, each of which silently emptied a
field when got wrong:

- **The iTunes namespace has two spellings in the wild.** Radio France declares
  `http://www.itunes.com/dtds/podcast-1.0.dtd`, others the trailing-slash form.
  Matching on the namespace URI reported `duration` missing on 100% of Radio
  France episodes. Everything here matches on the *local* tag name instead.
- **Duration comes in three shapes**: `00:12:08`, `57:11`, and bare seconds.
- **An item without an enclosure is not an episode.** Two of the Spanish top-30
  feeds carry exactly one such item each; Podcast Index counted them too. A
  missing `<guid>`, on the other hand, is not disqualifying: it is optional in
  RSS, so the enclosure URL stands in as the episode's identity.

`season`, `episode` and per-item artwork are simply absent from many feeds —
Radio France publishes none of the three. That is not a parse failure, and
Podcast Index reported them empty for the same feeds.

Episode identity is `{itunes_id}:{sha1(guid)[:16]}`. The hash is not decoration:
of 16 091 real guids, 9.6% contain `/` — which breaks a URL path segment — and
11.8% contain `:`, which would make the separator ambiguous. The prefix gave
16 091/16 091 unique ids with no intra-feed collision.
"""
import hashlib
import html
import logging
import re
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional
from xml.etree import ElementTree


logger = logging.getLogger("source.podcast.rss_parser")

ID_SEPARATOR = ":"
_GUID_HASH_LENGTH = 16

# itunes:explicit has been spelled every one of these ways.
_EXPLICIT_TRUE = {"yes", "true", "explicit"}

_DURATION_PART = re.compile(r"^\d+$")


def make_episode_id(itunes_id: str, guid: str) -> str:
    """Compose the episode identifier the API and the stored progress use."""
    digest = hashlib.sha1(guid.encode("utf-8")).hexdigest()[:_GUID_HASH_LENGTH]
    return f"{itunes_id}{ID_SEPARATOR}{digest}"


def split_episode_id(episode_id: str) -> tuple[Optional[str], Optional[str]]:
    """Split an episode identifier back into (itunes_id, guid hash).

    Returns (None, None) for anything that is not one — the routes turn that
    into a 404 rather than fetching a feed that cannot exist.
    """
    itunes_id, separator, digest = (episode_id or "").partition(ID_SEPARATOR)
    if not separator or not itunes_id.isdigit() or not digest:
        return None, None
    return itunes_id, digest


def parse_feed(
    raw: bytes,
    itunes_id: str,
    feed_url: str = "",
) -> Optional[Dict[str, Any]]:
    """Parse a feed into a normalized series carrying its full episode list.

    Returns None when the bytes are not a podcast feed at all. A feed that
    parses but holds no playable item is *not* None: it is a real, empty
    podcast, and the screen says so rather than reporting an error.
    """
    channel = _channel(raw)
    if channel is None:
        return None

    series = _series(channel, itunes_id, feed_url)
    series["episodes"] = _episodes(channel, itunes_id, series)
    series["total_episodes"] = len(series["episodes"])
    return series


# ========== XML HELPERS ==========
#
# Everything below matches on the *local* tag name. See the module docstring:
# the iTunes namespace URI is not one value in the wild.


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _children(parent, name: str):
    return (child for child in parent if _local(child.tag) == name)


def _first(parent, name: str):
    return next(_children(parent, name), None)


def _text(parent, name: str, prefer_plain: bool = False) -> str:
    """Text of the first matching child, skipping empty ones.

    `prefer_plain` resolves the one ambiguity that matters: `<title>` and
    `<itunes:title>` both exist on most items and may carry different text.
    RSS's own element is the canonical one, so it wins whatever the document
    order — a namespaced element is only used when no plain one has content.
    """
    namespaced = ""
    for child in _children(parent, name):
        value = (child.text or "").strip()
        if not value:
            continue
        is_plain = "}" not in str(child.tag)
        if not prefer_plain or is_plain:
            return value
        namespaced = namespaced or value
    return namespaced


def _channel(raw: bytes):
    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError as exc:
        logger.error("Feed is not well-formed XML: %s", exc)
        return None
    if _local(root.tag) == "channel":
        return root
    channel = _first(root, "channel")
    if channel is None:
        logger.error("Feed has no <channel> element")
    return channel


# ========== NORMALIZATION ==========


def _strip_html(text: Optional[str]) -> str:
    """Descriptions reach the UI as plain text."""
    if not text:
        return ""
    return html.unescape(re.sub(r"<[^>]+>", "", text)).strip()


def _image_url(element) -> str:
    """Artwork, preferring `<itunes:image href>` over RSS's own `<image><url>`.

    The preference is a rule, not document order: `itunes:image` is the square
    artwork Apple requires at up to 3000px, while RSS `<image>` is the legacy
    channel logo capped at 144px wide. 24 of 70 live feeds declare the RSS one
    first, and reading in order would hand the small logo to a full-screen
    player. Those 24 happen to point both elements at the same file today,
    which is exactly why the wrong order would go unnoticed until a publisher
    stops doing that.
    """
    fallback = ""
    for child in _children(element, "image"):
        href = (child.get("href") or "").strip()
        if href:
            return href
        fallback = fallback or _text(child, "url")
    return fallback


def _explicit(element) -> bool:
    return _text(element, "explicit").strip().lower() in _EXPLICIT_TRUE


def _int_or_none(value: str) -> Optional[int]:
    return int(value) if value.isdigit() else None


def _duration_seconds(value: str) -> int:
    """`itunes:duration` is `HH:MM:SS`, `MM:SS`, or bare seconds."""
    value = value.strip()
    if not value:
        return 0
    parts = value.split(":")
    if not all(_DURATION_PART.match(part.strip()) for part in parts):
        return 0
    total = 0
    for part in parts:
        total = total * 60 + int(part)
    return total


def _published_epoch(value: str) -> Optional[int]:
    """`pubDate` is RFC 2822; the UI and the sort want epoch seconds."""
    if not value:
        return None
    try:
        return int(parsedate_to_datetime(value).timestamp())
    except (TypeError, ValueError):
        return None


def _series(channel, itunes_id: str, feed_url: str) -> Dict[str, Any]:
    author = _text(channel, "author") or _text(channel, "managingEditor")
    image = _image_url(channel)
    newest = max(
        (_published_epoch(_text(item, "pubDate")) or 0
         for item in _children(channel, "item")),
        default=0,
    )
    return {
        "uuid": str(itunes_id),
        "itunes_id": str(itunes_id),
        "name": _text(channel, "title", prefer_plain=True) or "Unknown Podcast",
        "description": _strip_html(
            _text(channel, "description") or _text(channel, "summary")
        ),
        "image_url": image,
        "publisher": author,
        "author": author,
        "genres": _genres(channel),
        "language": _text(channel, "language"),
        "is_explicit": _explicit(channel),
        # "New episodes" token: the newest item's date moves whenever the
        # publisher adds one. `lastBuildDate` is bumped by some feeds on every
        # rebuild, which would report new episodes that do not exist.
        "children_hash": str(newest),
        "website_url": _text(channel, "link"),
        "rss_url": feed_url,
    }


def _genres(channel) -> List[str]:
    """`<itunes:category text="…">`, sometimes nested one level."""
    out: List[str] = []
    for category in _children(channel, "category"):
        label = (category.get("text") or category.text or "").strip()
        if label and label not in out:
            out.append(label)
        for sub in _children(category, "category"):
            label = (sub.get("text") or "").strip()
            if label and label not in out:
                out.append(label)
    return out


def _episodes(channel, itunes_id: str, series: Dict[str, Any]) -> List[Dict[str, Any]]:
    episodes = []
    for item in _children(channel, "item"):
        episode = _episode(item, itunes_id, series)
        if episode:
            episodes.append(episode)
    # Newest first, matching the order the episode list has always rendered in.
    episodes.sort(key=lambda ep: ep["date_published"] or 0, reverse=True)
    return episodes


def _episode(item, itunes_id: str, series: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    enclosure = _first(item, "enclosure")
    audio_url = (enclosure.get("url") or "").strip() if enclosure is not None else ""
    if not audio_url:
        # Not an episode: a chapter marker, a note, a feed's own trailer stub.
        # Two of the Spanish top-30 feeds carry exactly one such item each.
        return None

    # `<guid>` is optional in RSS. Every one of 45 measured feeds publishes it,
    # but a feed that does not must not render as an empty podcast with nothing
    # in the log — the enclosure URL identifies the episode just as well, and
    # is what a saved position then keys on.
    guid = _text(item, "guid") or audio_url

    length = (enclosure.get("length") or "").strip()
    return {
        "uuid": make_episode_id(itunes_id, guid),
        "guid": guid,
        "name": _text(item, "title", prefer_plain=True) or "Unknown Episode",
        "description": _strip_html(
            _text(item, "description") or _text(item, "summary")
        ),
        "date_published": _published_epoch(_text(item, "pubDate")),
        "duration": _duration_seconds(_text(item, "duration")),
        "audio_url": audio_url,
        "image_url": _image_url(item) or series["image_url"],
        "episode_type": _text(item, "episodeType") or "full",
        "season_number": _int_or_none(_text(item, "season")),
        "episode_number": _int_or_none(_text(item, "episode")),
        "is_explicit": _explicit(item),
        "website_url": _text(item, "link"),
        "file_length": int(length) if length.isdigit() else 0,
        "file_type": (enclosure.get("type") or "").strip(),
        "podcast": {
            "uuid": series["uuid"],
            "name": series["name"],
            "image_url": series["image_url"],
        },
    }
