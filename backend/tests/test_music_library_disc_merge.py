"""Unit tests for multi-disc album merging (disc_merge).

Pure logic — no Navidrome, no I/O — plus the async expand_merged_album fed by a
fake ``get_album``. Covers the two detection tiers (safe CD/Disc marker vs.
guarded bare number), every guard that keeps unrelated "… 1"/"… 2" releases
apart, the synthetic-id codec, and detail expansion (concatenated, disc-tagged
tracklist).
"""
import pytest

from backend.sources.music_library.disc_merge import (
    build_merged_id,
    expand_merged_album,
    is_merged_id,
    merge_albums,
    parse_disc_suffix,
    parse_merged_id,
)


def _album(id, name, artist="A", artist_id="a1", year=2000, song_count=1,
           duration=100, starred=None):
    d = {
        "id": id, "name": name, "artist": artist, "artistId": artist_id,
        "year": year, "songCount": song_count, "duration": duration,
        "coverArt": id,
    }
    if starred is not None:
        d["starred"] = starred
    return d


# =============================================================================
# parse_disc_suffix
# =============================================================================

class TestParseDiscSuffix:
    @pytest.mark.parametrize("title,base,disc,tier", [
        ("At Carnegie Hall CD 1", "At Carnegie Hall", 1, "marker"),
        ("At Carnegie Hall CD 2", "At Carnegie Hall", 2, "marker"),
        ("Rhapsody CD1", "Rhapsody", 1, "marker"),
        ("The Wall (Disc 2)", "The Wall", 2, "marker"),
        ("Mezzanine [Disc 01]", "Mezzanine", 1, "marker"),
        ("Days to Come Disque 2", "Days to Come", 2, "marker"),
        ("Rhapsody 1", "Rhapsody", 1, "number"),
        ("Rhapsody - 2", "Rhapsody", 2, "number"),
    ])
    def test_matches(self, title, base, disc, tier):
        s = parse_disc_suffix(title)
        assert (s.base, s.disc, s.tier) == (base, disc, tier)

    @pytest.mark.parametrize("title", [
        "Tourist", "Tourist - Remixes", "Greatest Hits",
        "Blade Runner 2049",           # 4-digit → never a disc
        "Symphony No. 5",              # enumeration stopword
        "Greatest Hits Vol. 2",
        "Kill Bill Vol 1",
        "Part 2", "Chapter 1", "Book 2", "Live Pt. 2",
        "CD 1", "Disc 2",              # only-a-marker → base would be empty
    ])
    def test_no_disc_suffix(self, title):
        assert parse_disc_suffix(title).disc is None

    def test_only_marker_keeps_full_title(self):
        # "CD 1" must not collapse to an empty base (which would merge unrelated
        # albums into one blank-named blob).
        assert parse_disc_suffix("CD 1").base == "CD 1"


# =============================================================================
# merge_albums — the two tiers + guards
# =============================================================================

class TestMergeAlbums:
    def test_marker_merges_same_artist(self):
        out = merge_albums([
            _album("id1", "At Carnegie Hall CD 1", "BVSC", "bvsc", 2008, 8, 1600),
            _album("id2", "At Carnegie Hall CD 2", "BVSC", "bvsc", 2008, 8, 1500),
        ])
        assert len(out) == 1
        m = out[0]
        assert is_merged_id(m["id"])
        assert parse_merged_id(m["id"]) == ["id1", "id2"]
        assert m["name"] == "At Carnegie Hall"
        assert m["songCount"] == 16
        assert m["duration"] == 3100
        assert m["_discCount"] == 2
        assert m["coverArt"] == "id1"  # disc 1 art

    def test_number_tier_merges_when_guards_pass(self):
        out = merge_albums([
            _album("r1", "Rhapsody 1", "X", "x", 2010),
            _album("r2", "Rhapsody 2", "X", "x", 2010),
        ])
        assert len(out) == 1 and out[0]["name"] == "Rhapsody"

    def test_number_tier_blocked_by_year_mismatch(self):
        out = merge_albums([
            _album("r1", "Rhapsody 1", "X", "x", 2010),
            _album("r2", "Rhapsody 2", "X", "x", 2011),
        ])
        assert len(out) == 2

    def test_number_tier_blocked_without_artist_id(self):
        out = merge_albums([
            _album("r1", "Vol 1", "X", None, 2010),
            _album("r2", "Vol 2", "X", None, 2010),
        ])
        assert len(out) == 2

    def test_number_tier_blocked_by_gap(self):
        out = merge_albums([
            _album("s1", "Series 1", "X", "x", 2010),
            _album("s3", "Series 3", "X", "x", 2010),
        ])
        assert len(out) == 2

    def test_marker_blocked_by_duplicate_disc(self):
        out = merge_albums([
            _album("d1", "Live CD 1", "X", "x", 2010),
            _album("d1b", "Live CD 1", "X", "x", 2010),
        ])
        assert len(out) == 2

    def test_different_artist_not_merged(self):
        out = merge_albums([
            _album("a1", "Live CD 1", "A", "a", 2010),
            _album("b2", "Live CD 2", "B", "b", 2010),
        ])
        assert len(out) == 2

    def test_stopword_titles_never_merge(self):
        out = merge_albums([
            _album("h1", "Hits Vol. 1", "B", "b", 2005),
            _album("h2", "Hits Vol. 2", "B", "b", 2005),
        ])
        assert len(out) == 2

    def test_three_discs_ordered_and_summed(self):
        out = merge_albums([
            _album("c2", "Box Disc 2", "Q", "q", 2000, 10, 600),
            _album("c1", "Box Disc 1", "Q", "q", 2000, 12, 700),
            _album("c3", "Box Disc 3", "Q", "q", 2000, 8, 500),
        ])
        assert len(out) == 1
        assert parse_merged_id(out[0]["id"]) == ["c1", "c2", "c3"]
        assert out[0]["songCount"] == 30

    def test_non_disc_albums_pass_through_in_order(self):
        albums = [
            _album("x", "Alpha"),
            _album("c1", "Set CD 1", "Q", "q", 2001),
            _album("c2", "Set CD 2", "Q", "q", 2001),
            _album("y", "Zeta"),
        ]
        out = merge_albums(albums)
        assert [a["name"] for a in out] == ["Alpha", "Set", "Zeta"]

    def test_starred_only_when_all_discs_starred(self):
        merged_all = merge_albums([
            _album("s1", "Set CD 1", "Q", "q", 2001, starred="2024"),
            _album("s2", "Set CD 2", "Q", "q", 2001, starred="2024"),
        ])[0]
        assert merged_all.get("starred") == "2024"
        merged_partial = merge_albums([
            _album("s1", "Set CD 1", "Q", "q", 2001, starred="2024"),
            _album("s2", "Set CD 2", "Q", "q", 2001),
        ])[0]
        assert "starred" not in merged_partial


# =============================================================================
# synthetic-id codec
# =============================================================================

class TestMergedIdCodec:
    def test_round_trip(self):
        mid = build_merged_id(["abc", "def", "ghi"])
        assert is_merged_id(mid)
        assert parse_merged_id(mid) == ["abc", "def", "ghi"]

    def test_plain_id_is_not_merged(self):
        assert not is_merged_id("6Mx7pbzncMpf6HJkWvuSi3")
        assert parse_merged_id("6Mx7pbzncMpf6HJkWvuSi3") == []


# =============================================================================
# expand_merged_album (async, fake get_album)
# =============================================================================

class TestExpandMergedAlbum:
    def _member(self, id, name, songs):
        alb = _album(id, name, "BVSC", "bvsc", 2008, len(songs), 100 * len(songs))
        alb["song"] = songs
        return alb

    async def test_concatenates_and_disc_tags(self):
        catalog = {
            "id1": self._member("id1", "At Carnegie Hall CD 1", [
                {"id": "s1", "title": "A"}, {"id": "s2", "title": "B"},
            ]),
            "id2": self._member("id2", "At Carnegie Hall CD 2", [
                {"id": "s3", "title": "C"},
            ]),
        }

        async def get_album(aid):
            return catalog.get(aid)

        album = await expand_merged_album(get_album, build_merged_id(["id1", "id2"]))
        assert album["name"] == "At Carnegie Hall"
        assert [s["id"] for s in album["song"]] == ["s1", "s2", "s3"]
        assert [s["discNumber"] for s in album["song"]] == [1, 1, 2]
        assert album["songCount"] == 3

    async def test_existing_disc_number_is_respected(self):
        catalog = {
            "id1": self._member("id1", "Box CD 1", [{"id": "s1", "discNumber": 5}]),
        }

        async def get_album(aid):
            return catalog.get(aid)

        album = await expand_merged_album(get_album, build_merged_id(["id1"]))
        assert album["song"][0]["discNumber"] == 5  # not overwritten

    async def test_missing_members_return_none(self):
        async def get_album(aid):
            return None

        assert await expand_merged_album(get_album, build_merged_id(["x", "y"])) is None
