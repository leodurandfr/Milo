# backend/tests/test_music_library_artist_filter.py
"""The rule that keeps a compilation credit out of the Artists tab.

What breaks when these fail: "Various Artists" comes back as a browsable name
with a page behind it, or — far worse in the other direction — a real band whose
name merely starts with "Various" disappears from the library with nothing to
say it was dropped.

The denylist is deliberately literal, so the tests that matter are the ones
pinning its edges: what folding it is compared through, and what it must not
swallow.
"""
from backend.sources.music_library.artist_filter import (
    VARIOUS_ARTISTS_ID,
    drop_placeholder_artists,
    is_placeholder_artist,
)


class TestPlaceholderArtists:
    def test_the_four_spellings_this_library_carries_are_all_caught(self):
        """Read off the unit's own catalog — one is Navidrome's, three are the
        user's tags, and the Artists tab listed all four as people."""
        for name in ("Various Artists", "Various artistes", "VA", "[Unknown Artist]"):
            assert is_placeholder_artist({"id": "x", "name": name}), name

    def test_navidrome_s_own_id_is_caught_whatever_the_row_is_called(self):
        """The id is a constant in the Navidrome binary, so it is the half of
        the rule that survives upstream renaming or localising the name."""
        assert is_placeholder_artist({"id": VARIOUS_ARTISTS_ID, "name": "Interpretes"})

    def test_the_name_is_matched_through_the_same_folding_as_everywhere_else(self):
        """Case, accents and doubled spaces are what tags actually differ by."""
        assert is_placeholder_artist({"id": "x", "name": "VARIOUS  ARTISTS"})
        assert is_placeholder_artist({"id": "x", "name": "Vàrious Artists"})

    def test_a_real_band_whose_name_begins_with_various_is_kept(self):
        """The shortcut this rule refuses: a substring match on "various" would
        delete Various Cruelties from the library, silently and permanently."""
        assert not is_placeholder_artist({"id": "x", "name": "Various Cruelties"})

    def test_an_ordinary_artist_is_kept(self):
        assert not is_placeholder_artist({"id": "x", "name": "Amy Winehouse"})

    def test_a_row_with_no_name_is_not_claimed(self):
        """Defensive: a nameless row is a catalog fault, not a compilation
        credit, and swallowing it here would hide it."""
        assert not is_placeholder_artist({"id": "x"})

    def test_dropping_keeps_the_order_of_what_remains(self):
        rows = [
            {"id": "a", "name": "Adele"},
            {"id": "b", "name": "Various Artists"},
            {"id": "c", "name": "Zebda"},
        ]
        assert [row["id"] for row in drop_placeholder_artists(rows)] == ["a", "c"]
