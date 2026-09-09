# backend/tests/test_podcast_rss_parser.py
"""
`rss_parser` — the normalizers that replaced Podcast Index's.

Podcast Index used to absorb the variety of real feeds on Milō's behalf. It no
longer does, so a field this parser reads wrongly is a field that renders empty
on the appliance with nothing in the log. The three shapes pinned hardest below
are the three that were measured going wrong on live feeds:

- the iTunes namespace declared with and without the trailing `.dtd`
- `itunes:duration` as `HH:MM:SS`, `MM:SS` and bare seconds
- items with no enclosure, which are not episodes

Every extraction test asserts a real value, never merely that a key exists: a
parser that returned `""` for everything would satisfy a presence check and
empty the whole episode list.
"""
import hashlib

import pytest

from backend.sources.podcast.rss_parser import (
    ID_SEPARATOR,
    make_episode_id,
    parse_feed,
    split_episode_id,
)


ITUNES_DTD = "http://www.itunes.com/dtds/podcast-1.0.dtd"
ITUNES_SLASH = "http://www.itunes.com/dtds/podcast-1.0/"


def feed(items="", *, itunes_ns=ITUNES_DTD, channel_extra="") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{itunes_ns}">
  <channel>
    <title>Les Odyss&#233;es</title>
    <link>https://www.radiofrance.fr</link>
    <description>&lt;p&gt;Un podcast &amp;amp; des r&#233;cits.&lt;/p&gt;</description>
    <language>fr</language>
    <itunes:author>France Inter</itunes:author>
    <itunes:explicit>no</itunes:explicit>
    <itunes:image href="https://cdn.example/series-600.jpg"/>
    <itunes:category text="Kids &amp; Family">
      <itunes:category text="Stories for Kids"/>
    </itunes:category>
    {channel_extra}
    {items}
  </channel>
</rss>""".encode("utf-8")


def item(
    *,
    guid="2b7b7ad9-19e1-40c5-b053-d1d66de28c92",
    title="Camille du Gast",
    pub="Fri, 24 Jul 2026 04:42:00 +0200",
    duration="00:12:08",
    enclosure='<enclosure url="https://cdn.example/ep1.mp3" length="5822400" type="audio/mpeg"/>',
    extra="",
) -> str:
    guid_tag = f"<guid>{guid}</guid>" if guid is not None else ""
    return f"""<item>
      <title>{title}</title>
      <link>https://www.radiofrance.fr/ep1</link>
      <description>&lt;b&gt;dur&#233;e&lt;/b&gt; : 00:12:08</description>
      {guid_tag}
      <pubDate>{pub}</pubDate>
      {enclosure}
      <itunes:duration>{duration}</itunes:duration>
      {extra}
    </item>"""


class TestTheFeedParsesToSomethingReal:
    """The non-triviality floor. If this class passes on empty values, every
    other assertion below is meaningless."""

    def test_a_realistic_feed_yields_a_populated_series(self):
        series = parse_feed(feed(item()), "1469858852", "https://cdn.example/f.xml")

        assert series is not None
        assert series["total_episodes"] == 1
        assert series["name"] == "Les Odyssées"
        assert series["publisher"] == "France Inter"
        assert series["language"] == "fr"
        assert series["image_url"] == "https://cdn.example/series-600.jpg"
        assert series["rss_url"] == "https://cdn.example/f.xml"
        assert series["website_url"] == "https://www.radiofrance.fr"

    def test_the_series_uuid_is_the_apple_id(self):
        """The lazy `lookup/itunes` round-trip disappeared because the two are
        now the same value."""
        series = parse_feed(feed(item()), "1469858852")

        assert series["uuid"] == "1469858852"
        assert series["itunes_id"] == "1469858852"

    def test_an_episode_carries_every_field_the_players_read(self):
        episode = parse_feed(feed(item()), "1469858852")["episodes"][0]

        assert episode["name"] == "Camille du Gast"
        assert episode["audio_url"] == "https://cdn.example/ep1.mp3"
        assert episode["duration"] == 728
        assert episode["file_length"] == 5822400
        assert episode["file_type"] == "audio/mpeg"
        assert episode["website_url"] == "https://www.radiofrance.fr/ep1"
        # 2026-07-24 04:42:00 +0200 is 02:42 UTC — the offset in the pubDate is
        # applied, not dropped. A parser reading the wall clock would answer
        # 1784868120 and shift every French episode by two hours.
        assert episode["date_published"] == 1784860920
        assert episode["podcast"]["name"] == "Les Odyssées"

    def test_html_is_stripped_and_entities_resolved(self):
        series = parse_feed(feed(item()), "1469858852")

        assert series["description"] == "Un podcast & des récits."
        assert series["episodes"][0]["description"] == "durée : 00:12:08"


class TestTheNamespaceTrap:
    """Radio France declares the iTunes namespace as `…podcast-1.0.dtd`; other
    hosts use `…podcast-1.0/`. A parser matching on the URI reported duration
    missing on 100% of Radio France episodes — measured, not hypothetical."""

    @pytest.mark.parametrize("ns", [ITUNES_DTD, ITUNES_SLASH, "urn:something-else"])
    def test_itunes_fields_survive_any_namespace_spelling(self, ns):
        series = parse_feed(feed(item(), itunes_ns=ns), "1469858852")

        assert series["episodes"][0]["duration"] == 728
        assert series["publisher"] == "France Inter"
        assert series["image_url"] == "https://cdn.example/series-600.jpg"


class TestDuration:
    @pytest.mark.parametrize("raw,seconds", [
        ("00:12:08", 728),
        ("57:11", 3431),
        ("3431", 3431),
        ("01:00:00", 3600),
        ("", 0),
        ("unknown", 0),
        ("12:ab", 0),
    ])
    def test_the_three_shapes_and_the_junk(self, raw, seconds):
        series = parse_feed(feed(item(duration=raw)), "1469858852")
        assert series["episodes"][0]["duration"] == seconds


class TestWhatIsNotAnEpisode:
    """An item Milō cannot play must not reach the episode list — it would
    render a row that does nothing when pressed."""

    def test_an_item_without_an_enclosure_is_skipped(self):
        """Two Spanish top-30 feeds carry exactly one such item each."""
        series = parse_feed(feed(item() + item(guid="x", enclosure="")), "1469858852")

        assert series["total_episodes"] == 1

    def test_an_item_without_a_guid_is_still_an_episode(self):
        """`<guid>` is optional in RSS. Every one of 45 measured feeds publishes
        it, but dropping the item when it is absent would render the whole
        podcast empty with nothing in the log — so the enclosure URL stands in
        as the identity."""
        series = parse_feed(feed(item(guid=None)), "1469858852")

        assert series["total_episodes"] == 1
        assert series["episodes"][0]["guid"] == "https://cdn.example/ep1.mp3"
        assert series["episodes"][0]["uuid"] == make_episode_id(
            "1469858852", "https://cdn.example/ep1.mp3"
        )

    def test_an_enclosure_with_no_url_is_skipped(self):
        series = parse_feed(
            feed(item(guid="a", enclosure='<enclosure length="1" type="audio/mpeg"/>')),
            "1469858852",
        )

        assert series["total_episodes"] == 0

    def test_a_feed_with_no_items_is_an_empty_podcast_not_a_failure(self):
        """`Bliss Stories` serves a channel and no items; Podcast Index reports
        episodeCount 0 for the same URL. The screen shows an empty podcast."""
        series = parse_feed(feed(), "1365837531")

        assert series is not None
        assert series["total_episodes"] == 0
        assert series["name"] == "Les Odyssées"


class TestEpisodeIdentity:
    """`{itunes_id}:{sha1(guid)[:16]}`. Of 16 091 real guids, 9.6% contain `/`
    and 11.8% contain `:` — a raw composite would break the URL path for one
    episode in ten."""

    @pytest.mark.parametrize("guid", [
        "2b7b7ad9-19e1-40c5-b053-d1d66de28c92",
        "https://podcast.bfmbusiness.com/channel222/20220701_seq.mp3",
        "UA-312987",
        "tag:example.com,2026:/ep/1",
    ])
    def test_the_identifier_is_url_safe_whatever_the_guid(self, guid):
        episode_id = make_episode_id("1469858852", guid)

        assert not any(c in episode_id for c in "/?# %")
        assert episode_id.count(ID_SEPARATOR) == 1

    def test_the_identifier_is_derived_from_the_guid_not_the_position(self):
        """Feeds reorder and drop items. An id derived from the guid keeps a
        saved position pointing at the same episode across refreshes."""
        expected = hashlib.sha1(b"guid-a").hexdigest()[:16]

        assert make_episode_id("111", "guid-a") == f"111{ID_SEPARATOR}{expected}"
        assert make_episode_id("111", "guid-a") != make_episode_id("111", "guid-b")
        assert make_episode_id("111", "guid-a") != make_episode_id("222", "guid-a")

    def test_it_splits_back_into_the_feed_and_the_episode(self):
        episode_id = make_episode_id("1469858852", "guid-a")
        itunes_id, digest = split_episode_id(episode_id)

        assert itunes_id == "1469858852"
        assert digest == hashlib.sha1(b"guid-a").hexdigest()[:16]

    @pytest.mark.parametrize("junk", [
        "", None, "no-separator", ":onlydigest", "abc:digest", "1469858852:",
    ])
    def test_anything_that_is_not_an_identifier_splits_to_nothing(self, junk):
        """The route turns (None, None) into a 404 instead of fetching a feed
        that cannot exist."""
        assert split_episode_id(junk) == (None, None)

    def test_the_identifier_survives_a_round_trip(self):
        """`PodcastCatalog.get_episode` splits a stored id, refetches the feed
        and matches on the uuid this parser stamps. The two halves must agree
        or a saved position resolves to nothing."""
        listed = parse_feed(feed(item(guid="guid-a")), "1469858852")["episodes"][0]
        itunes_id, digest = split_episode_id(listed["uuid"])

        assert f"{itunes_id}{ID_SEPARATOR}{digest}" == listed["uuid"]
        assert itunes_id == "1469858852"


class TestOrderingAndOptionalFields:
    def test_episodes_come_back_newest_first(self):
        raw = feed(
            item(guid="old", title="Old", pub="Mon, 01 Jan 2024 10:00:00 +0000")
            + item(guid="new", title="New", pub="Wed, 01 Jan 2025 10:00:00 +0000")
        )

        names = [ep["name"] for ep in parse_feed(raw, "1")["episodes"]]

        assert names == ["New", "Old"]

    def test_an_unparseable_date_does_not_drop_the_episode(self):
        series = parse_feed(feed(item(pub="not a date")), "1")

        assert series["total_episodes"] == 1
        assert series["episodes"][0]["date_published"] is None

    def test_an_episode_without_artwork_inherits_the_series_cover(self):
        """Radio France publishes no per-item image. The card must still draw
        something rather than a hole."""
        episode = parse_feed(feed(item()), "1")["episodes"][0]

        assert episode["image_url"] == "https://cdn.example/series-600.jpg"

    def test_the_itunes_artwork_wins_over_the_legacy_rss_logo(self):
        """`itunes:image` is the square artwork, up to 3000px; RSS `<image>` is
        the legacy channel logo capped at 144px. 24 of 70 live feeds declare
        the legacy one first, so reading in document order hands a 144px logo
        to a full-screen player. Those feeds point both at the same file today,
        which is what would keep the mistake invisible."""
        legacy_first = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_DTD}"><channel>
  <title>Les Odyssées</title>
  <image>
    <url>https://cdn.example/logo-144.jpg</url>
    <title>Les Odyssées</title>
  </image>
  <itunes:image href="https://cdn.example/series-600.jpg"/>
  {item()}
</channel></rss>""".encode("utf-8")

        assert parse_feed(legacy_first, "1")["image_url"] == (
            "https://cdn.example/series-600.jpg"
        )

    def test_the_legacy_logo_is_still_used_when_it_is_all_there_is(self):
        no_itunes_image = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_DTD}"><channel>
  <title>Legacy</title>
  <image><url>https://cdn.example/logo-144.jpg</url></image>
  {item()}
</channel></rss>""".encode("utf-8")

        assert parse_feed(no_itunes_image, "1")["image_url"] == (
            "https://cdn.example/logo-144.jpg"
        )

    def test_rsss_own_title_wins_over_the_itunes_one(self):
        """Both elements exist on most items and can disagree. RSS's is
        canonical, and it must win whatever order the publisher wrote them in —
        otherwise the list shows one name and the player another."""
        itunes_first = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_DTD}"><channel>
  <itunes:title>Marketing Name</itunes:title>
  <title>Real Name</title>
  <item>
    <itunes:title>Episode Marketing</itunes:title>
    <title>Episode Real</title>
    <guid>g1</guid>
    <pubDate>Fri, 24 Jul 2026 04:42:00 +0200</pubDate>
    <enclosure url="https://cdn.example/a.mp3" length="1" type="audio/mpeg"/>
  </item>
</channel></rss>""".encode("utf-8")

        series = parse_feed(itunes_first, "1")

        assert series["name"] == "Real Name"
        assert series["episodes"][0]["name"] == "Episode Real"

    def test_the_itunes_title_is_used_when_rss_has_none(self):
        only_itunes = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="{ITUNES_DTD}"><channel>
  <itunes:title>Only Name</itunes:title>
  {item()}
</channel></rss>""".encode("utf-8")

        assert parse_feed(only_itunes, "1")["name"] == "Only Name"

    def test_per_item_artwork_wins_when_the_feed_has_it(self):
        episode = parse_feed(
            feed(item(extra='<itunes:image href="https://cdn.example/ep.jpg"/>')), "1"
        )["episodes"][0]

        assert episode["image_url"] == "https://cdn.example/ep.jpg"

    def test_season_and_episode_numbers_are_absent_not_zero(self):
        """Most French feeds publish neither. Zero would render as 'Season 0'."""
        episode = parse_feed(feed(item()), "1")["episodes"][0]

        assert episode["season_number"] is None
        assert episode["episode_number"] is None

    def test_season_and_episode_numbers_are_read_when_present(self):
        episode = parse_feed(
            feed(item(extra="<itunes:season>2</itunes:season>"
                            "<itunes:episode>7</itunes:episode>"
                            "<itunes:episodeType>trailer</itunes:episodeType>")),
            "1",
        )["episodes"][0]

        assert episode["season_number"] == 2
        assert episode["episode_number"] == 7
        assert episode["episode_type"] == "trailer"

    def test_nested_categories_are_flattened_into_genres(self):
        assert parse_feed(feed(item()), "1")["genres"] == [
            "Kids & Family", "Stories for Kids"
        ]

    @pytest.mark.parametrize("raw,expected", [
        ("yes", True), ("true", True), ("explicit", True),
        ("no", False), ("clean", False), ("", False),
    ])
    def test_explicit_is_read_in_every_spelling_publishers_use(self, raw, expected):
        series = parse_feed(
            feed(item(), channel_extra=""), "1"
        )
        assert series["is_explicit"] is False

        episode = parse_feed(
            feed(item(extra=f"<itunes:explicit>{raw}</itunes:explicit>")), "1"
        )["episodes"][0]
        assert episode["is_explicit"] is expected

    def test_the_new_episode_token_follows_the_newest_item(self):
        """`children_hash` drives the 'new episodes' badge. `lastBuildDate` is
        bumped by some hosts on every rebuild, which would announce episodes
        that do not exist — so it tracks the newest item date instead."""
        one = parse_feed(feed(item(pub="Mon, 01 Jan 2024 10:00:00 +0000")), "1")
        two = parse_feed(
            feed(item(guid="a", pub="Mon, 01 Jan 2024 10:00:00 +0000")
                 + item(guid="b", pub="Wed, 01 Jan 2025 10:00:00 +0000")),
            "1",
        )

        assert one["children_hash"] != two["children_hash"]
        assert two["children_hash"] == str(two["episodes"][0]["date_published"])


class TestWhatIsNotAFeed:
    @pytest.mark.parametrize("raw", [
        b"", b"not xml at all", b"<rss><channel><title>unclosed",
        b'{"json": true}',
    ])
    def test_malformed_input_answers_none(self, raw):
        assert parse_feed(raw, "1") is None

    def test_well_formed_xml_that_is_not_a_feed_answers_none(self):
        assert parse_feed(b"<html><body>Not found</body></html>", "1") is None
