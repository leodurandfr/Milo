"""The Spotify browser's pure shaping (sources/spotify/catalog.py).

The fixtures have the shapes measured on the owner's account: its library
(2026-10-03) and Spotify's home for it (spclient homeview, 2026-10-04).
"""
from backend.sources.spotify.catalog import (
    SHORTCUTS_SECTION, described_tracks, home_shelves, leading_covers, library_sections, normalize_playlist,
    normalize_track, playlist_cover, thumbnail_url,
)

ACCOUNT = "owner"


def playlist(uri_id, name, owner, image=None, can_edit=False, collaborative=False, length=50):
    return {
        "uri": f"spotify:playlist:{uri_id}", "name": name, "description": "", "owner_username": owner,
        "length": length, "image_url": image, "can_edit": can_edit, "collaborative": collaborative,
        "folder": [],
    }


LIBRARY = [
    playlist("37i9dQZF1E8PE5vUgEe9T7", "Radio Leaves", "spotify", "https://pickasso.spotifycdn.com/x"),
    playlist("37i9dQZF1DX0abcdefgh", "Songs to Test Speakers With", "spotify", "https://i.scdn.co/image/x"),
    playlist("1mineabcdefgh", "Chill appart", ACCOUNT),
    playlist("37i9dQZF1EYkqdzj48dyYq", "", "spotify", length=0),             # an expired mix: no name, no image
    playlist("37i9dQZF1E4mixabcdef", "Mix Jazz", "spotify", "https://seed-mix-image.spotifycdn.com/v6/img/desc/x",
             length=0),                                                      # lists 0, holds 50
    playlist("4emptyabcdefgh", "Pour plus tard", ACCOUNT, length=0),
    playlist("37i9dQZEVXbReleaseRad", "Radar des sorties", "spotify", length=200),   # lists 66
    playlist("37i9dQZF1F0wrapped", "Votre Top Titres 2023", "spotify", "https://wrapped-images.spotifycdn.com/x"),
    playlist("37i9dQZF1E3blendabcd", "Cla + Léo", "spotify", "https://blend-playlist-covers.spotifycdn.com/x"),
    playlist("2collabxyz", "Claléo", "someone", "https://i.scdn.co/image/y", can_edit=True, collaborative=True),
    playlist("3followedxyz", "Funk à l'ancienne!", "furkan_93", "https://i.scdn.co/image/z"),
]


def names(section):
    return [p["name"] for p in section]


def test_the_library_splits_into_the_accounts_own_playlists_and_the_ones_it_saved():
    """Owned or editable is the account's; everything else it saved —
    Spotify's own included — follows, in library order."""
    sections = library_sections(LIBRARY, ACCOUNT)

    assert names(sections["mine"]) == ["Chill appart", "Claléo"]
    assert names(sections["followed"]) == [
        "Radio Leaves", "Songs to Test Speakers With", "Mix Jazz", "Radar des sorties",
        "Votre Top Titres 2023", "Cla + Léo", "Funk à l'ancienne!",
    ]


EMPTY = {"spotify:playlist:37i9dQZF1EYkqdzj48dyYq", "spotify:playlist:4emptyabcdefgh"}


def test_an_empty_playlist_is_left_out_but_a_live_mix_listing_zero_is_not():
    """A playlist a person keeps with no track is hidden whatever its name; a
    Spotify mix lists 0 while it holds 50, and only an expired one (no name,
    no picture) is empty."""
    sections = library_sections(LIBRARY, ACCOUNT)
    placed = {p["uri"] for key in ("mine", "followed") for p in sections[key]}

    assert not placed & EMPTY
    assert "spotify:playlist:37i9dQZF1E4mixabcdef" in placed


def header(section, title):
    return {"id": f"{section}-header", "component": {"id": "glue:sectionHeader", "category": "header"},
            "text": {"title": title, "subtitle": ""}, "metadata": {"sectionId": section}}


def card(section, uri, title, subtitle=None):
    text = {"title": title, **({"subtitle": subtitle} if subtitle else {})}
    return {"id": f"{section}_card", "component": {"id": "glue2:card", "category": "card"}, "text": text,
            "images": {"main": {"uri": f"https://i.scdn.co/image/{title}", "placeholder": "playlist"}},
            "target": {"uri": uri}, "metadata": {"uri": uri, "sectionId": section}}


MIXES = "spotify:section:0JQ5DAnM3wGh0gz1MXnu89"
MADE_FOR = "spotify:section:0JQ5DAUnp4wcj0bCb3wh3S"
SHOWS = "spotify:section:0JQ5DAnM3wGh0gz1MXnu3N"

HOME = {"body": [
    header(SHORTCUTS_SECTION, "Raccourcis"),
    card(SHORTCUTS_SECTION, "spotify:user:%40:collection", "Titres likés"),
    card(SHORTCUTS_SECTION, "spotify:album:79dL7FLiJFOO0EoehUHQBv", "Currents"),
    card(SHORTCUTS_SECTION, "spotify:artist:6nB0iY1cjSY1KyhYyuIIKH", "FKA twigs"),
    header(MIXES, "Vos mix préférés"),
    card(MIXES, "spotify:playlist:37i9dQZF1EQnqst5TRi17F", "Hip Hop Mix", "Kery James, Oxmo et plus"),
    header(MADE_FOR, "Conçu pour Léo"),
    card(MADE_FOR, "spotify:playlist:37i9dQZF1E35HBJ2wuuMhp", "Daily Mix 1"),
    card(MADE_FOR, "spotify:playlist:37i9dQZF1E39eHkn9AYV7U", "Daily Mix 2"),
    header(SHOWS, "Vos émissions"),
    card(SHOWS, "spotify:show:2VRR0TGLn4ckba3J0UyJjq", "Les pieds sur terre", "France Culture"),
]}


def test_the_home_keeps_spotifys_shelves_in_their_order_and_their_titles():
    """The shelves are Spotify's, titled as it titles them: Milō groups
    nothing itself."""
    _, shelves = home_shelves(HOME)

    assert [(shelf["title"], [c["name"] for c in shelf["items"]]) for shelf in shelves] == [
        ("Vos mix préférés", ["Hip Hop Mix"]),
        ("Conçu pour Léo", ["Daily Mix 1", "Daily Mix 2"]),
    ]
    assert shelves[0]["items"][0]["subtitle"] == "Kery James, Oxmo et plus"


def test_the_shortcuts_shelf_is_the_tiles_and_each_card_says_what_it_opens():
    """The browser opens a playlist, an album, an artist or Liked Songs by
    kind; Liked Songs is named "@" by the home, not by the account."""
    shortcuts, shelves = home_shelves(HOME)

    assert [(c["name"], c["kind"]) for c in shortcuts] == [
        ("Titres likés", "liked"), ("Currents", "album"), ("FKA twigs", "artist"),
    ]
    assert SHORTCUTS_SECTION not in {shelf["id"] for shelf in shelves}


def test_a_card_the_browser_cannot_open_is_left_out_with_the_shelf_it_empties():
    """A show's episodes are nothing go-librespot lists: the account's
    podcasts shelf would be cards that open onto nothing."""
    _, shelves = home_shelves(HOME)
    assert SHOWS not in {shelf["id"] for shelf in shelves}


def test_a_track_whose_metadata_is_not_cached_yet_is_left_out():
    """go-librespot lists such an entry with `track: null` while its sweep runs."""
    assert normalize_track({"uri": "spotify:track:x", "track": None}) is None


def test_a_track_carries_each_artist_with_its_uri():
    """The artist line opens the artist's page: names and uris must stay paired,
    and an artist go-librespot gave no uri for has none rather than its
    neighbour's."""
    track = normalize_track({"uri": "spotify:track:t", "track": {
        "uri": "spotify:track:t", "name": "Seven", "artist_names": ["A", "B"],
        "artist_uris": ["spotify:artist:a"], "album_name": "Album", "album_uri": "spotify:album:al",
        "album_cover_url": "https://i.scdn.co/image/ab67616d0000b273abcdef", "duration": 269533,
    }})

    assert track["artists"] == [{"name": "A", "uri": "spotify:artist:a"}, {"name": "B", "uri": None}]
    assert track["album"] == {"name": "Album", "uri": "spotify:album:al"}
    assert track["duration_ms"] == 269533
    assert track["thumbnail"] == "https://i.scdn.co/image/ab67616d00004851abcdef"


def test_only_album_covers_are_resized():
    """The size code is an album cover's; any other image is served as given."""
    other = "https://pickasso.spotifycdn.com/image/ab67c0de0000deef/dt/v1/img/radio/x"
    assert thumbnail_url(other) == other
    assert thumbnail_url(None) is None


def album(cover_id):
    return {"track": {"album_cover_url": f"https://i.scdn.co/image/ab67616d0000b273{cover_id}"}}


def test_a_playlist_with_no_picture_is_drawn_from_its_first_four_albums():
    """The Spotify apps' mosaic: the first four distinct albums in playlist
    order, an album met twice taking one tile."""
    listing = [album("a1"), album("b2"), album("a1"), album("c3"), album("d4"), album("e5")]

    assert playlist_cover(leading_covers(listing)) == (
        "https://mosaic.scdn.co/300/"
        "ab67616d0000b273a1ab67616d0000b273b2ab67616d0000b273c3ab67616d0000b273d4"
    )


def test_fewer_than_four_albums_draw_the_first_cover():
    listing = [album("a1"), album("b2"), album("a1")]
    assert playlist_cover(leading_covers(listing)) == "https://i.scdn.co/image/ab67616d0000b273a1"
    assert playlist_cover(leading_covers([])) is None


def test_an_entry_not_described_yet_ends_the_mosaic_until_the_listing_is_complete():
    """go-librespot describes a listing while it is read: an album after a gap
    is not the playlist's second one yet. Once the listing is complete, the gap
    is a track Spotify no longer describes, and the albums around it are the
    mosaic."""
    listing = [album("a1"), {"uri": "spotify:track:x", "track": None}, album("b2"), album("c3"), album("d4")]
    assert leading_covers(listing) == ["https://i.scdn.co/image/ab67616d0000b273a1"]
    assert len(leading_covers(listing, complete=True)) == 4


def test_only_a_playlist_without_a_picture_is_pointed_at_the_mosaic():
    uploaded = normalize_playlist(playlist("2collabxyz", "Claléo", "someone", "https://i.scdn.co/image/y"))
    bare = normalize_playlist(playlist("1mineabcdefgh", "Chill appart", ACCOUNT))

    assert "/cover" not in uploaded["image"]
    assert bare["image"] == "/api/spotify/contexts/spotify%3Aplaylist%3A1mineabcdefgh/cover"


def test_a_track_not_described_yet_ends_the_listing_until_it_is_complete():
    """go-librespot describes a listing front to back: a track already cached
    past a gap is not shown yet, or the rows on screen would move when the gap
    fills. Once complete, the gap is a track Spotify no longer describes."""
    def entry(name):
        return {"uri": f"spotify:track:{name}", "track": {"uri": f"spotify:track:{name}", "name": name}}

    listing = [entry("a"), {"uri": "spotify:track:gap", "track": None}, entry("c")]

    assert [t["title"] for t in described_tracks(listing, complete=False)] == ["a"]
    assert [t["title"] for t in described_tracks(listing, complete=True)] == ["a", "c"]
