"""The Spotify browser's pure shaping (sources/spotify/catalog.py).

The fixture has the shapes measured on the owner's library (2026-10-03), not
its names: Spotify's own playlists are named in the session's language, so the
home sections must hold whatever the names say.
"""
from backend.sources.spotify.catalog import classify_home, normalize_track, thumbnail_url

ACCOUNT = "owner"
RADIO_IMAGE = "https://pickasso.spotifycdn.com/image/ab67c0de0000deef/dt/v1/img/radio/track/73Ik/en"


def playlist(uri_id, name, owner, image=None, can_edit=False, collaborative=False, length=50):
    return {
        "uri": f"spotify:playlist:{uri_id}", "name": name, "description": "", "owner_username": owner,
        "length": length, "image_url": image, "can_edit": can_edit, "collaborative": collaborative,
        "folder": [],
    }


LIBRARY = [
    playlist("37i9dQZF1E8PE5vUgEe9T7", "Radio Leaves", "spotify", RADIO_IMAGE),
    playlist("37i9dQZF1DX0abcdefgh", "Songs to Test Speakers With", "spotify", "https://i.scdn.co/image/x"),
    playlist("1mineabcdefgh", "Chill appart", ACCOUNT),
    playlist("37i9dQZF1EYkqdzj48dyYq", "", "spotify"),                       # an expired mix: no name, no image
    playlist("37i9dQZF1E4mixabcdef", "Mix Jazz", "spotify", "https://seed-mix-image.spotifycdn.com/v6/img/desc/x"),
    playlist("37i9dQZEVXbReleaseRad", "Radar des sorties", "spotify", length=200),   # lists 66
    playlist("37i9dQZF1F0wrapped", "Votre Top Titres 2023", "spotify", "https://wrapped-images.spotifycdn.com/x"),
    playlist("37i9dQZF1E3blendabcd", "Cla + Léo", "spotify", "https://blend-playlist-covers.spotifycdn.com/x"),
    playlist("2collabxyz", "Claléo", "someone", "https://i.scdn.co/image/y", can_edit=True, collaborative=True),
    playlist("3followedxyz", "Funk à l'ancienne!", "furkan_93", "https://i.scdn.co/image/z"),
]


def names(section):
    return [p["name"] for p in section]


def test_home_sections_never_read_a_name():
    """Radios by their cover path, editorial by the id prefix, the rest of
    Spotify's own as made for you; owned or editable as mine. A name in any
    language lands in the same section."""
    sections = classify_home(LIBRARY, ACCOUNT)

    assert names(sections["radios"]) == ["Radio Leaves"]
    assert names(sections["made_for_you"]) == [
        None, "Mix Jazz", "Radar des sorties", "Votre Top Titres 2023", "Cla + Léo",
    ]
    assert names(sections["mine"]) == ["Chill appart", "Claléo"]
    assert names(sections["followed"]) == ["Songs to Test Speakers With", "Funk à l'ancienne!"]


def test_every_playlist_lands_in_exactly_one_section_and_shortcuts_follow_the_library():
    sections = classify_home(LIBRARY, ACCOUNT)
    placed = [p["uri"] for key in ("made_for_you", "radios", "mine", "followed") for p in sections[key]]

    assert sorted(placed) == sorted(item["uri"] for item in LIBRARY)
    assert [p["uri"] for p in sections["shortcuts"]] == [item["uri"] for item in LIBRARY[:7]]


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
