# backend/tests/test_push_payloads.py
"""The two APNs payload shapes.

Pure functions, no mocks. What breaks when these fail is invisible from this
side: APNs accepts any well-formed JSON, so a payload iOS cannot decode is
answered 200 and dropped on the phone.

The dB ↔ 0..1 conversion used to be declared here and is tested next to its
owner now — `tests/test_core_volume.py::TestVolumeScale`.
"""
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.core.push.payloads import (
    MILO_CARD,
    SOURCE_CARDS,
    NowPlayingDevice,
    build_attributes,
    now_playing_payload,
    widget_payload,
)

FIXED = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)
NOW = FIXED.timestamp()


class TestWidgetPayload:
    def test_it_carries_no_data(self):
        """The contract is one word: reload. The widget then makes its own LAN
        call. Anything else here would be state the widget must not trust,
        since these pushes are delivered opportunistically and out of order."""
        assert widget_payload() == {"aps": {"content-changed": True}}


class TestNowPlayingAttributes:
    """The mutable state, re-sent whole on every event, built from the audio
    state the way Milo-iOS builds its own card."""

    @pytest.fixture
    def state(self):
        return {
            "source": "spotify",
            "session": {
                "id": "s-1", "phase": "playing",
                "title": "Un parmi des millions", "artist": "Koma, Roce, Kohndo",
                "album": "Le réveil", "artwork": "https://i.scdn.co/image/art",
                "senders": [], "duration_ms": 334906,
                "position": {"ms": 54654, "at": FIXED.timestamp() + 0.123, "rate": 1.0},
            },
            "resume": None,
        }

    def test_milliseconds_become_seconds(self, state):
        """The state carries milliseconds; the iOS Codable declares seconds.
        Sent unconverted, a 5-minute track reads as 93 hours and the scrubber
        is unusable."""
        attrs = build_attributes("sess", state, [], now=NOW)

        assert attrs["currentTrack"]["duration"] == 334.91
        assert attrs["elapsedTime"] == 54.65

    def test_the_playhead_is_the_anchor_not_the_build_time(self, state):
        """iOS extrapolates the playhead from `elapsedTime` at `timestamp`: the
        pair must be the anchor's own, or the lock screen runs ahead of the
        sound by however long the push waited to be built."""
        attrs = build_attributes("sess", state, [], now=NOW + 42)

        assert attrs["elapsedTime"] == 54.65
        assert attrs["timestamp"] == "2026-09-19T12:00:00.123Z"

    def test_without_an_anchor_the_playhead_is_zero_at_build_time(self, state):
        """The iOS type declares elapsedTime non-optional, so a null fails the
        decode — and a failed decode is silent on the device."""
        state["session"]["position"] = None

        attrs = build_attributes("sess", state, [], now=NOW)

        assert attrs["elapsedTime"] == 0.0
        assert attrs["timestamp"] == "2026-09-19T12:00:00.000Z"

    @pytest.mark.parametrize("phase,playing", [
        ("playing", True), ("paused", False), ("loading", False), ("connected", False),
    ])
    def test_is_playing_is_the_session_phase(self, state, phase, playing):
        """The lock screen's play/pause glyph: a loading or connected session
        is not playing, and showing it as playing extrapolates a playhead
        that does not move."""
        state["session"]["phase"] = phase

        assert build_attributes("sess", state, [], now=NOW)["isPlaying"] is playing

    def test_no_session_and_no_resume_still_builds(self):
        """A source can be selected before anything plays. The push must still
        describe a session rather than raise inside the loop."""
        attrs = build_attributes("sess", {"source": "bluetooth", "session": None,
                                          "resume": None}, [], now=NOW)

        assert attrs["id"] == "sess"
        assert attrs["isPlaying"] is False
        assert attrs["currentTrack"]["title"] == "Bluetooth"

    def test_the_resume_point_is_shown_when_the_session_names_nothing(self, state):
        """The card's own rule (MiloAudioState.shown): a session with no title
        yet shows what "play" would bring back, so the lock screen and the
        app agree on what is on the card."""
        state["session"]["title"] = None
        state["resume"] = {"title": "Episode 12", "artist": "Host", "album": None,
                           "artwork": None, "duration_ms": 1800000, "position_ms": 60000}

        track = build_attributes("sess", state, [], now=NOW)["currentTrack"]

        assert (track["title"], track["artist"], track["duration"]) == ("Episode 12", "Host", 1800.0)

    @pytest.mark.parametrize("record", ["session", "resume"])
    def test_an_untitled_cd_track_carries_its_number_not_a_label(self, record):
        """A burned disc, or one read offline, names no track. Milō does not
        know the phone's language, so the card carries the number and Milo-iOS
        names it; a label written here was French on every phone. The card is
        still the track's, never the « Lecteur CD » card, and its id changes
        with every track so the system re-reads the content."""
        def track_for(number):
            untitled = {"title": None, "artist": None, "album": None, "artwork": None,
                        "duration_ms": 245000}
            state = {
                "source": "cd", "controls": [], "session": None, "resume": None,
                "details": {"kind": "cd", "disc": None, "current_track": number,
                            "artwork_pending": False, "shuffle": False, "repeat": "off"},
            }
            if record == "session":
                state["session"] = {**untitled, "id": "c-1", "phase": "playing",
                                    "senders": [], "position": None}
            else:
                state["resume"] = {**untitled, "position_ms": 0}
            return build_attributes("sess", state, [], now=NOW)["currentTrack"]

        third, fourth = track_for(3), track_for(4)

        assert third["title"] is None
        assert third["trackNumber"] == 3
        assert third["duration"] == 245.0
        assert third["artworkURL"] is None
        assert third["id"] != fourth["id"]
        assert third["id"].startswith("cd:")

    def test_a_titled_track_carries_no_number(self, state):
        """The number is only a CD's stand-in for a title: a track that has
        one is named by it, on every source."""
        track = build_attributes("sess", state, [], now=NOW)["currentTrack"]

        assert track["title"] == "Un parmi des millions"
        assert track["trackNumber"] is None

    def test_a_titled_session_wins_over_the_resume_point(self, state):
        state["resume"] = {"title": "Older", "artist": None, "album": None,
                           "artwork": None, "duration_ms": None, "position_ms": None}

        track = build_attributes("sess", state, [], now=NOW)["currentTrack"]

        assert track["title"] == "Un parmi des millions"
        assert track["artworkURL"] == "https://i.scdn.co/image/art"

    def test_the_track_id_moves_with_what_is_shown(self, state):
        """Without a new id the system keeps the previous title and artwork."""
        before = build_attributes("sess", state, [], now=NOW)["currentTrack"]["id"]
        state["session"]["title"] = "Another"
        after = build_attributes("sess", state, [], now=NOW)["currentTrack"]["id"]

        assert before != after

    def test_it_carries_the_commands_the_source_takes(self, state):
        """The extension enables a lock-screen button only for a listed
        command: Qobuz and a Mac take none, Tidal no seek, and a button that
        does nothing was a 400 on the network (measured 2026-09-25)."""
        state["controls"] = ["pause", "next", "prev"]

        assert build_attributes("sess", state, [], now=NOW)["controls"] == ["pause", "next", "prev"]

    def test_an_idle_card_offers_only_to_resume(self):
        """A stopped CD keeps its steps, its seek and its eject; the Lock Screen
        card with nothing playing offers only the play press that brings back
        what it names (owner's call, 2026-09-25)."""
        state = {"source": "cd", "session": None,
                 "resume": {"title": "Track 3", "artist": None, "album": "Album", "artwork": None,
                            "duration_ms": 200000, "position_ms": 12000},
                 "controls": ["resume", "seek", "skip", "next", "prev", "play_track", "eject"]}

        assert build_attributes("sess", state, [], now=NOW)["controls"] == ["resume"]

    def test_a_source_card_offers_nothing(self):
        state = {"source": "cd", "session": None, "resume": None,
                 "controls": ["eject"]}

        assert build_attributes("sess", state, [], now=NOW)["controls"] == []

    def test_a_mac_names_its_senders_under_the_macos_icon(self):
        """A Mac sends a stream with no track: the card names the source and
        who is sending, under the dock's macOS icon, and draws no bar."""
        state = {
            "source": "mac", "controls": [], "resume": None,
            "session": {
                "id": "m-1", "phase": "connected", "title": None, "artist": None,
                "album": None, "artwork": None, "senders": ["Mac mini", "MacBook Air"],
                "duration_ms": None, "position": None,
            },
        }

        track = build_attributes("sess", state, [], now=NOW)["currentTrack"]

        assert (track["title"], track["artist"], track["artworkURL"], track["duration"]) == (
            "Récepteur macOS", "Mac mini, MacBook Air", "/now-playing/macos.jpg", 0.0,
        )

    def test_a_connected_session_naming_a_track_keeps_its_artist(self):
        """A sender Milō cannot call playing or paused that names a title, an
        artist and a cover (an AirPlay stream naming every track, a Bluetooth
        phone whose cover was found): CONNECTED, and Milō's full player draws
        it with its artist line (D14). The Lock Screen says the same, and
        Milo-iOS' `buildAttributes` is held to it by
        `test_the_app_writes_the_artist_line_as_the_push_does`."""
        state = {
            "source": "bluetooth", "controls": [], "resume": None,
            "session": {
                "id": "b-1", "phase": "connected", "title": "Says", "artist": "Nils Frahm",
                "album": None, "artwork": "https://example.com/says.jpg",
                "senders": ["iPhone de Léo"], "duration_ms": None, "position": None,
            },
        }

        assert build_attributes("sess", state, [], now=NOW)["currentTrack"]["artist"] == "Nils Frahm"

    def test_a_half_named_connected_session_shows_the_source_card(self):
        """A Bluetooth phone names its track while its stream is stopped and
        its cover is still being looked up: Milō's screen draws "Connected to
        iPhone de Léo" (D14), so the Lock Screen draws the source's card, not
        a title the kiosk does not show. Which states name a track is
        `connected_naming_cases.json`'s; this pins the card it falls back to."""
        state = {
            "source": "bluetooth", "controls": ["disconnect"], "resume": None,
            "session": {
                "id": "b-2", "phase": "connected", "title": "Says", "artist": "Nils Frahm",
                "album": None, "artwork": None, "senders": ["iPhone de Léo"],
                "duration_ms": None, "position": None,
            },
            "details": {"kind": "bluetooth", "artwork_pending": True},
        }

        track = build_attributes("sess", state, [], now=NOW)["currentTrack"]

        assert (track["title"], track["artist"], track["artworkURL"]) == (
            "Bluetooth", "iPhone de Léo", "/now-playing/bluetooth.jpg",
        )

    def test_a_source_with_nothing_playing_shows_its_own_card(self):
        """Selected, idle, nothing to resume: the source's name over its icon,
        rather than a media card with every field null."""
        attrs = build_attributes("sess", {"source": "spotify", "session": None,
                                          "resume": None}, [], now=NOW)

        track = attrs["currentTrack"]
        assert (track["title"], track["artist"], track["artworkURL"]) == (
            "Spotify", None, "/now-playing/spotify.jpg",
        )
        assert track["id"] == "spotify:Spotify"

    @pytest.mark.parametrize("source", ["none", None, "a-source-from-the-future"])
    def test_no_source_shows_milos_card(self, source):
        """What the Lock Screen holds for the grace once the source is left."""
        track = build_attributes("sess", {"source": source, "session": None,
                                          "resume": None}, [], now=NOW)["currentTrack"]

        assert (track["title"], track["artworkURL"]) == ("Milō", "/now-playing/milo.jpg")

    def test_every_card_has_its_icon_on_disk(self):
        """nginx serves these from dist/, copied from frontend/public/. A name
        with no file is a 404 the extension turns into a grey square."""
        public = Path(__file__).resolve().parents[2] / "frontend" / "public"
        for _, icon in [*SOURCE_CARDS.values(), MILO_CARD]:
            assert (public / "now-playing" / f"{icon}.jpg").is_file(), icon

    def test_each_speaker_is_its_own_device(self, state):
        """One slider per room in Control Center. Collapsing them to a global
        level is what the per-client volume exists not to do."""
        devices = [
            NowPlayingDevice(id="2c:cf:67:b9:46:6f", name="Milō", volume=0.36),
            NowPlayingDevice(id="dc:a6:32:7e:d3:43", name="Canapé", volume=0.38),
        ]
        attrs = build_attributes("sess", state, devices, now=NOW)

        assert [d["name"] for d in attrs["devices"]] == ["Milō", "Canapé"]
        assert attrs["devices"][0] == {
            "id": "2c:cf:67:b9:46:6f", "name": "Milō", "type": "speaker", "volume": 0.36
        }


class TestNowPlayingEnvelope:
    """`aps` is the envelope; everything mutable sits in `attributes`."""

    def test_an_update_carries_the_whole_state_in_attributes(self):
        """NowPlaying declares no ContentState — `update(_ attributes:)` takes
        one type where ActivityKit's `update(using contentState:)` takes two.
        A payload split into `content-state` would be a different protocol's
        envelope, accepted by APNs and ignored by the device."""
        payload = now_playing_payload("update", "sess", {"id": "sess", "isPlaying": True})

        assert set(payload["aps"]) == {"event", "timestamp", "attributes"}
        assert payload["aps"]["attributes"]["isPlaying"] is True
        assert "content-state" not in payload["aps"]
        assert "attributes-type" not in payload["aps"]

    def test_an_end_carries_only_the_id(self):
        """There is no state left to describe, and a full snapshot alongside
        the teardown races it on the device."""
        payload = now_playing_payload("end", "sess", {"id": "sess", "isPlaying": True})

        assert payload["aps"]["attributes"] == {"id": "sess"}

    def test_a_start_without_attributes_is_refused(self):
        """Silent on the wire otherwise: APNs would answer 200 to a session
        that describes nothing."""
        with pytest.raises(ValueError):
            now_playing_payload("start", "sess")

