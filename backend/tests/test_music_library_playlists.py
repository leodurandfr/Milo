# backend/tests/test_music_library_playlists.py
"""Tests for GET /music-library/playlists/containing, the membership query.

The "add to playlist" picker used to ask one request per playlist from the
browser to fill in its checkmarks. This route answers the same question once,
and the properties that make it worth having are the ones asserted here: the
storage scope is resolved a single time for the whole fan-out, only in-scope
playlists are fetched at all, and the playability filter is deliberately NOT
applied — a track on an unplugged key is still in the playlist, and answering
"absent" would hand the user a button that silently re-adds it.

If these fail, the picker either shows wrong checkmarks (a second add
duplicates the track) or the route has quietly grown back the per-playlist cost
it exists to remove.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.sources.music_library.routes import router, setup_music_library_routes

URL = "/api/music-library/playlists/containing"


def _playlist(playlist_id, *song_ids, album="alb-mounted"):
    return {
        "id": playlist_id,
        "name": playlist_id,
        "entry": [{"id": s, "albumId": album, "duration": 10} for s in song_ids],
    }


class TestPlaylistsContaining:
    @pytest.fixture
    def harness(self):
        """A source whose scope is honoured and whose entries are canned.

        ``playlists_in_scope`` stands for the real storage filter: the test drives
        what it returns, so "only in-scope playlists are fetched" is observable.
        """
        client = MagicMock()
        source = MagicMock()
        source.get_navidrome_client = AsyncMock(return_value=client)
        source.mounted_album_ids = AsyncMock(return_value=set())

        app = FastAPI()
        setup_music_library_routes(lambda: source)
        app.include_router(router, prefix="/api")
        return TestClient(app), source, client

    @staticmethod
    def _serve(client, source, details, in_scope=None):
        """Wire the two Navidrome calls plus the scope filter."""
        listed = [{"id": pid, "name": pid} for pid in details]
        client.get_playlists = AsyncMock(return_value=listed)
        source.playlists_in_scope = AsyncMock(
            return_value=listed if in_scope is None
            else [p for p in listed if p["id"] in in_scope]
        )
        client.get_playlist = AsyncMock(side_effect=lambda pid: details[pid])

    def test_only_playlists_holding_every_song_are_returned(self, harness):
        """Partial membership is not membership: the picker's checkmark means
        "all of these songs are already here", so adding is a no-op."""
        api, source, client = harness
        self._serve(client, source, {
            "both": _playlist("both", "s1", "s2", "s9"),
            "one": _playlist("one", "s1"),
            "none": _playlist("none", "s7"),
        })

        r = api.get(URL, params={"song_id": ["s1", "s2"]})

        assert r.status_code == 200
        assert r.json() == {"playlist_ids": ["both"]}

    def test_the_storage_scope_is_resolved_once_for_the_whole_fan_out(self, harness):
        """The reason this route exists. Per-playlist requests from the browser
        re-read the five storage JSON files each; here the scope is read once,
        whatever the playlist count."""
        api, source, client = harness
        self._serve(client, source, {
            f"pl{i}": _playlist(f"pl{i}", "s1") for i in range(6)
        })

        r = api.get(URL, params={"song_id": "s1"})

        assert r.status_code == 200
        assert len(r.json()["playlist_ids"]) == 6
        source.playlists_in_scope.assert_awaited_once()
        client.get_playlists.assert_awaited_once()
        assert client.get_playlist.await_count == 6

    def test_an_out_of_scope_playlist_is_never_fetched(self, harness):
        """The fan-out follows the scope filter, not the raw catalog — a playlist
        belonging to another storage space costs nothing and cannot be answered
        for, since the picker does not render it."""
        api, source, client = harness
        self._serve(client, source, {
            "mine": _playlist("mine", "s1"),
            "other-space": _playlist("other-space", "s1"),
        }, in_scope={"mine"})

        r = api.get(URL, params={"song_id": "s1"})

        assert r.json() == {"playlist_ids": ["mine"]}
        assert [c.args[0] for c in client.get_playlist.await_args_list] == ["mine"]

    def test_entries_are_read_raw_so_an_unmounted_track_still_counts(self, harness):
        """`_keep_playable` is deliberately not applied here. It drops entries no
        mounted space can serve, which for this question means answering "absent"
        for a track that is present — and a second add would duplicate it."""
        api, source, client = harness
        self._serve(client, source, {
            "on-unplugged-key": _playlist("on-unplugged-key", "s1", album="alb-absent"),
        })

        r = api.get(URL, params={"song_id": "s1"})

        assert r.json() == {"playlist_ids": ["on-unplugged-key"]}
        source.mounted_album_ids.assert_not_awaited()

    def test_a_playlist_that_vanished_mid_request_is_skipped(self, harness):
        """getPlaylist answers None for an id deleted between the list and the
        fan-out. One gone playlist must not fail the other checkmarks."""
        api, source, client = harness
        self._serve(client, source, {
            "gone": None,
            "here": _playlist("here", "s1"),
        })

        r = api.get(URL, params={"song_id": "s1"})

        assert r.status_code == 200
        assert r.json() == {"playlist_ids": ["here"]}
