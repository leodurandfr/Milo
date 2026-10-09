"""The Spotify browser's pure shaping (sources/spotify/catalog.py).

The fixtures have the shapes measured on the owner's account: its library
(2026-10-03), Spotify's home for it (spclient homeview, 2026-10-04) and an
artist's page with its discography (spclient artistview, 2026-10-05).
"""
from backend.sources.spotify.catalog import (
    ARTIST_POPULAR_RELEASES_SECTION, ARTIST_TOP_TRACKS_SECTION, SHORTCUTS_SECTION, artist_page, artist_portrait,
    artist_shelf_seeds, described_tracks, head_artist_shelves, release_type, home_shelves, leading_covers, library_sections,
    normalize_playlist, normalize_track, playlist_cover, spotify_gid, thumbnail_url,
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


def card(section, uri, title, subtitle=None, image=None):
    text = {"title": title, **({"subtitle": subtitle} if subtitle else {})}
    return {"id": f"{section}_card", "component": {"id": "glue2:card", "category": "card"}, "text": text,
            "images": {"main": {"uri": image or f"https://i.scdn.co/image/{title}", "placeholder": "playlist"}},
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


def card_listing(section, uri, title, subtitle, label=None):
    entry = card(section, uri, title, subtitle)
    if label:
        entry["metadata"]["label"] = label
    return entry


def test_a_shelf_whose_covers_carry_their_names_writes_one_line_in_no_language_it_cannot_trust():
    """The cover writes "Mix Tia Gordon", "Radio Tia Gordon", "This Is Tia
    Gordon": a card there draws no name. A mix keeps its artists; a station
    names its artists from its own list, since its subtitle is a sentence in
    the account's language ("Avec …" under any locale, measured); Best of
    artists drops "This is Tia Gordon. The essential tracks…", which says
    nothing the cover does not. Any other shelf keeps Spotify's subtitle."""
    mixes, stations, best_of = (f"spotify:section:0JQ5DAnM3wGh0gz1MXnu{s}" for s in ("89", "3R", "3n"))
    _, shelves = home_shelves({"body": [
        header(mixes, "Vos mix préférés"),
        card_listing(mixes, "spotify:playlist:37i9dQZF1EIWPBxYi0Zmaa", "Mix Tia Gordon", "BETTY BROWN et Nia Smith"),
        header(stations, "Radios recommandées"),
        card_listing(stations, "spotify:playlist:37i9dQZF1E4o1TR8JkP4f2", "Radio Tia Gordon",
                     "Avec Jamilah Barry et bien d'autres artistes", label="Nia Smith, Tia Gordon, Jamilah Barry"),
        header(best_of, "Best-of des artistes"),
        card_listing(best_of, "spotify:playlist:37i9dQZF1DZ06evO3cSwMP", "This Is Tia Gordon", "This is Tia…"),
        header(MADE_FOR, "Conçu pour Léo"),
        card_listing(MADE_FOR, "spotify:playlist:37i9dQZF1E35HBJ2wuuMhp", "Daily Mix 1", "Kery James et plus", label="x"),
    ]})

    assert [[(c["subtitle"], c["name_in_cover"]) for c in shelf["items"]] for shelf in shelves] == [
        [("BETTY BROWN et Nia Smith", True)], [("Nia Smith, Tia Gordon, Jamilah Barry", True)], [(None, True)],
        [("Kery James et plus", False)],
    ]


FKA_TWIGS = "6nB0iY1cjSY1KyhYyuIIKH"
FKA_TWIGS_PHOTO = "https://i.scdn.co/image/ab6761610000f1783f6b8973be0344896d8680ab"


def artist_shelf(title, cover):
    section = f"spotify:section:{title}"
    return home_shelves({"body": [
        header(section, title),
        card(section, "spotify:playlist:37i9dQZF1DZ06evO3LyCc0", "This Is FKA twigs", image=cover),
        card(section, "spotify:playlist:37i9dQZF1DX873GaRGUmPl", "Alternative 10s"),
    ]})[1]


THIS_IS = f"https://pickasso.spotifycdn.com/image/ab67c0de0000deef/dt/v1/img/thisisv3/{FKA_TWIGS}/fr"
RADIO = f"https://pickasso.spotifycdn.com/image/ab67c0de0000deef/dt/v1/img/radio/artist/{FKA_TWIGS}/fr"
ARTISTS = {FKA_TWIGS: {"name": "FKA twigs", "image": FKA_TWIGS_PHOTO}}


def headed(shelves, artists):
    return head_artist_shelves(shelves, artist_shelf_seeds(shelves), artists)


def test_a_shelf_about_an_artist_is_headed_with_it_and_what_its_title_says_around_it():
    """The apps head "Pour les fans de FKA twigs" with her photo, "Pour les
    fans de" over her name; the home gives only the title, and her id in the
    This Is or station cover. The name may open the title (Hindi)."""
    for title, cover, overline in [
        ("Pour les fans de FKA twigs", THIS_IS, "Pour les fans de"),
        ("FKA twigs के प्रशंसकों के लिए", RADIO, "के प्रशंसकों के लिए"),
    ]:
        [shelf] = headed(artist_shelf(title, cover), ARTISTS)
        assert (shelf["artist"], shelf["overline"]) == ({"name": "FKA twigs", "image": FKA_TWIGS_PHOTO}, overline)


def test_a_shelf_whose_title_does_not_name_the_artist_as_a_word_keeps_a_plain_title():
    """A shelf holding someone else's station; Chinese, which puts the name
    mid-sentence; a name that only ends a word of the title; an artist whose
    metadata failed."""
    for title, artists in [
        ("Réécoutez vos anciens favoris", ARTISTS), ("与 FKA twigs 相似的更多艺人", ARTISTS),
        ("Pour les fans de FKA twigs", {FKA_TWIGS: {"name": "wigs", "image": None}}),
        ("Pour les fans de FKA twigs", {}),
    ]:
        [shelf] = headed(artist_shelf(title, RADIO), artists)
        assert "artist" not in shelf and "overline" not in shelf


def test_a_named_cover_shelf_is_about_nobody_and_asks_for_no_artist():
    """Recommended Stations is a row of stations, each an artist's: its title
    never names one, and asking would cost a request per home for nothing."""
    stations = "spotify:section:0JQ5DAnM3wGh0gz1MXnu3R"
    _, shelves = home_shelves({"body": [
        header(stations, "Radios recommandées"),
        card(stations, "spotify:playlist:37i9dQZF1E4AT7kuvrVxp6", "Radio FKA twigs", image=RADIO),
    ]})
    assert artist_shelf_seeds(shelves) == {}


def test_an_artists_metadata_is_addressed_by_its_hex_gid_and_read_for_its_small_photo():
    """Measured 2026-10-09: FKA twigs answers at this gid, her portraits in
    three sizes; the 160 px one heads a shelf."""
    assert spotify_gid(FKA_TWIGS) == "d1a571ee170d41d48b99d934acff78bf"
    assert artist_portrait({"portrait_group": {"image": [
        {"file_id": "ab676161000051743f6b8973be0344896d8680ab", "size": "DEFAULT"},
        {"file_id": "ab6761610000f1783f6b8973be0344896d8680ab", "size": "SMALL"},
    ]}}) == FKA_TWIGS_PHOTO
    assert artist_portrait({}) is None


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


# === Artist page ===

def view_header(section, title, category="header"):
    # The discography files its headers as rows (glue2:sectionHeader).
    return {"id": section, "component": {"id": "glue:sectionHeader", "category": category},
            "text": {"title": title}}


def view_row(section, uri, title, subtitle=None):
    """A row as artistview draws one: its target is the click, which is the
    only place an Artist Pick names it."""
    return {"id": f"{section}_row", "component": {"id": "glue2:imageRow", "category": "row"},
            "text": {"title": title, **({"subtitle": subtitle} if subtitle else {})},
            "images": {"main": {"uri": f"https://i.scdn.co/image/{title}"}},
            "events": {"click": {"name": "navigate", "data": {"uri": uri}}}}


def view_carousel(section, title, cards):
    return {"id": section, "component": {"id": "glue:carousel", "category": "carousel"},
            "text": {"title": title}, "children": [view_row(section, uri, name) for uri, name in cards]}


ARTIST_VIEW = {
    "header": {"text": {"title": "FKA twigs", "accessory": "3 M auditeurs et auditrices par mois"},
               "images": {"main": {"uri": "https://i.scdn.co/image/ab67616100005174photo"}}},
    "body": [
        view_header(ARTIST_TOP_TRACKS_SECTION, "Populaires"),
        {"id": "top_row0", "component": {"id": "glue:entityRow", "category": "row"}, "text": {"title": "HARD"},
         "events": {"click": {"name": "playFromContext", "data": {"uri": "spotify:track:5AzO8bswSqsYtJIfVA2BqX"}}}},
        view_header("pinned_item_header", "Artist Pick"),
        view_row("pinned_item", "spotify:playlist:0aIwfSIOwb0fK5FtAay7SE", "On Your Mind", "Playlist"),
        view_header(ARTIST_POPULAR_RELEASES_SECTION, "Sorties populaires"),
        view_row(ARTIST_POPULAR_RELEASES_SECTION, "spotify:album:3G77BQuJy3jahjdkKQNNNM", "CAPRISONGS", "2022"),
        view_row(ARTIST_POPULAR_RELEASES_SECTION, "spotify:artist:6nB0iY1cjSY1KyhYyuIIKH:releases",
                 "Voir la discographie"),
        view_carousel("artist-entity-view-you-might-also-like", "Avec FKA twigs", [
            ("spotify:user:spotify:playlist:37i9dQZF1DZ06evO3LyCc0", "This Is FKA twigs"),
            ("spotify:user:spotify:playlist:37i9dQZF1E4AT7kuvrVxp6", "Radio FKA twigs"),
        ]),
        view_header("concerts_header", "Live Events"),
        view_row("concerts", "spotify:concert:5R2FBDPp2vtPnlVUNKDOL8", "Paris"),
        view_header("merchandise_header", "Merch"),
        view_row("merchandise", "https://shop.spotify.com/en/artist/6nB0iY1cjSY1KyhYyuIIKH/product/lp", "LP1"),
        view_carousel("artist-entity-view-related", "Les fans aiment aussi", [
            ("spotify:artist:4Ge8xMJNwt6EEXOzVXju9a", "Caroline Polachek"),
        ]),
    ],
}

RELEASES = {"body": [
    view_header("artist-entity-view-latest-release", "Dernière sortie", category="row"),
    view_row("latest", "spotify:album:2TZNyzwG89VKnROkBaK7w3", "On Your Mind", "2026"),
    view_header("artist-entity-view-albums-source", "Albums", category="row"),
    view_row("albums", "spotify:album:0v1sQbOCM2xDdIYA0XYapM", "EUSEXUA Afterglow", "2025"),
    view_row("albums", "spotify:album:25PQxi9SR1OODB5XG6m48J", "LP1", "2014"),
    view_header("artist-entity-view-artist-singles-source", "Singles", category="row"),
    view_row("singles", "spotify:album:44keDDETPLFK48WCykPKit", "EP1", "2012"),
]}


def sections_of(page):
    return [(s["title"], [c["name"] for c in s["items"]]) if "items" in s
            else ("discography", [(g["title"], [c["name"] for c in g["items"]]) for g in s["groups"]])
            for s in page["sections"]]


def test_the_artist_page_is_spotifys_sections_with_the_discography_in_place_of_its_popular_releases():
    """The discography stands where Spotify draws its four popular releases:
    those four first, then one list per kind, each in Spotify's newest-first
    order. The popular tracks are left to the artist's listing, and what no
    listing opens (concerts, merch, the "see discography" link) leaves with
    its section."""
    page = artist_page(ARTIST_VIEW, RELEASES)

    assert sections_of(page) == [
        ("Artist Pick", ["On Your Mind"]),
        ("discography", [
            ("Sorties populaires", ["On Your Mind", "CAPRISONGS"]),
            ("Albums", ["EUSEXUA Afterglow", "LP1"]),
            ("Singles", ["EP1"]),
        ]),
        ("Avec FKA twigs", ["This Is FKA twigs", "Radio FKA twigs"]),
        ("Les fans aiment aussi", ["Caroline Polachek"]),
    ]
    assert page["popular_title"] == "Populaires"


def discography(page):
    return {g["id"]: g["items"] for g in next(s for s in page["sections"] if "groups" in s)["groups"]}


def test_each_release_says_its_year_and_what_it_is():
    """Spotify's rows give the year only; what a release is comes from the
    list Spotify files it in — a popular release included, found in its own."""
    groups = discography(artist_page(ARTIST_VIEW, RELEASES))

    assert [(r["year"], r["release_type"]) for r in groups["albums"]] == [("2025", "album"), ("2014", "album")]
    assert groups["singles"][0]["release_type"] == "single"
    assert groups["popular"][0]["release_type"] is None   # CAPRISONGS is in no list here


def test_the_latest_release_goes_back_at_the_head_of_its_own_list_once_its_kind_is_known():
    """Spotify takes the latest release out of its list into a section of its
    own: a new album would be missing from the albums. Until its kind is
    known it goes in no kind's list — a wrong one would mislabel it — but
    leads the popular releases rather than vanish from the discography."""
    known = discography(artist_page(ARTIST_VIEW, RELEASES, latest_type="album"))
    unknown = discography(artist_page(ARTIST_VIEW, RELEASES))

    assert [(r["name"], r["latest"]) for r in known["albums"]] == [
        ("On Your Mind", True), ("EUSEXUA Afterglow", False), ("LP1", False),
    ]
    assert [r["name"] for r in known["singles"]] == ["EP1"]
    assert [(r["name"], r["release_type"]) for r in unknown["popular"]] == [
        ("On Your Mind", None), ("CAPRISONGS", None),
    ]
    assert "On Your Mind" not in {r["name"] for r in unknown["albums"] + unknown["singles"]}


def test_spotify_calls_a_release_an_album_from_seven_tracks_or_thirty_minutes():
    """Spotify's rule, which files an EP with the singles: under seven tracks
    only the whole running time decides, so a release not wholly described
    is not decided yet."""
    minute = 60_000
    assert release_type(7, None) == "album"
    assert release_type(5, [6 * minute] * 5) == "album"
    assert release_type(5, [3 * minute] * 5) == "single"
    assert release_type(1, [3 * minute]) == "single"
    assert release_type(5, [3 * minute] * 4) is None
    assert release_type(0, []) is None


def test_without_its_discography_the_artist_page_keeps_its_popular_releases():
    """The discography is a second request: its failure costs the full
    lists, not the releases the page already names."""
    page = artist_page(ARTIST_VIEW, None)
    assert ("discography", [("Sorties populaires", ["CAPRISONGS"])]) in sections_of(page)


def test_an_artist_page_card_opens_what_its_click_navigates_to():
    """Spotify still names its own playlists spotify:user:spotify:playlist:…
    here: the browser compares uris with the one playing, which go-librespot
    names spotify:playlist:…"""
    page = artist_page(ARTIST_VIEW, RELEASES)
    sections = {s["title"]: s["items"] for s in page["sections"] if "items" in s}

    assert [(c["uri"], c["kind"]) for c in sections["Avec FKA twigs"]] == [
        ("spotify:playlist:37i9dQZF1DZ06evO3LyCc0", "playlist"),
        ("spotify:playlist:37i9dQZF1E4AT7kuvrVxp6", "playlist"),
    ]
    assert sections["Artist Pick"][0]["uri"] == "spotify:playlist:0aIwfSIOwb0fK5FtAay7SE"
    assert discography(page)["albums"][0]["uri"] == "spotify:album:0v1sQbOCM2xDdIYA0XYapM"
    assert sections["Les fans aiment aussi"][0]["kind"] == "artist"


def test_the_artist_header_is_the_artists_photo_not_a_cover():
    """Opened from the player's artist line, the page had only its first
    track's album cover to show; the header is the artist's own photo, at the
    640 px size i.scdn.co serves for every one."""
    page = artist_page(ARTIST_VIEW, RELEASES)

    assert page["name"] == "FKA twigs"
    assert page["image"] == "https://i.scdn.co/image/ab6761610000e5ebphoto"
    assert page["listeners"] == "3 M auditeurs et auditrices par mois"
