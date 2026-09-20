# backend/tests/test_push_payloads.py
"""The two APNs payload shapes.

Pure functions, no mocks. What breaks when these fail is invisible from this
side: APNs accepts any well-formed JSON, so a payload iOS cannot decode is
answered 200 and dropped on the phone.

The dB ↔ 0..1 conversion used to be declared here and is tested next to its
owner now — `tests/test_core_volume.py::TestVolumeScale`.
"""
from datetime import datetime, timezone

import pytest

from backend.core.push.payloads import (
    NowPlayingDevice,
    build_attributes,
    now_playing_payload,
    widget_payload,
)

FIXED = datetime(2026, 9, 19, 12, 0, 0, tzinfo=timezone.utc)


class TestWidgetPayload:
    def test_it_carries_no_data(self):
        """The contract is one word: reload. The widget then makes its own LAN
        call. Anything else here would be state the widget must not trust,
        since these pushes are delivered opportunistically and out of order."""
        assert widget_payload() == {"aps": {"content-changed": True}}


class TestNowPlayingAttributes:
    """The mutable state, re-sent whole on every event."""

    @pytest.fixture
    def metadata(self):
        return {
            "title": "Un parmi des millions", "artist": "Koma, Roce, Kohndo",
            "album": "Le réveil", "album_art_url": "https://example/art.jpg",
            "duration": 334906, "position": 54654, "is_playing": True,
        }

    def test_milliseconds_become_seconds(self, metadata):
        """/api/audio/state serves milliseconds; the iOS Codable declares
        seconds. Sent unconverted, a 5-minute track reads as 93 hours and the
        scrubber is unusable."""
        attrs = build_attributes("sess", metadata, [], "spotify", now=FIXED)

        assert attrs["currentTrack"]["duration"] == 334.91
        assert attrs["elapsedTime"] == 54.65

    def test_a_missing_position_is_zero_not_null(self, metadata):
        """The iOS type declares elapsedTime non-optional, so a null fails the
        decode — and a failed decode is silent on the device."""
        attrs = build_attributes("sess", {**metadata, "position": None}, [], "radio", now=FIXED)

        assert attrs["elapsedTime"] == 0.0

    def test_no_metadata_at_all_still_builds(self):
        """A source can go active before its first metadata arrives. The push
        must still describe a session rather than raise inside the loop."""
        attrs = build_attributes("sess", None, [], "bluetooth", now=FIXED)

        assert attrs["id"] == "sess"
        assert attrs["isPlaying"] is False
        assert attrs["currentTrack"]["title"] is None

    def test_each_speaker_is_its_own_device(self, metadata):
        """One slider per room in Control Center. Collapsing them to a global
        level is what the per-client volume exists not to do."""
        devices = [
            NowPlayingDevice(id="2c:cf:67:b9:46:6f", name="Milō", volume=0.36),
            NowPlayingDevice(id="dc:a6:32:7e:d3:43", name="Canapé", volume=0.38),
        ]
        attrs = build_attributes("sess", metadata, devices, "spotify", now=FIXED)

        assert [d["name"] for d in attrs["devices"]] == ["Milō", "Canapé"]
        assert attrs["devices"][0] == {
            "id": "2c:cf:67:b9:46:6f", "name": "Milō", "type": "speaker", "volume": 0.36
        }

    def test_the_timestamp_is_iso_8601_zulu(self, metadata):
        attrs = build_attributes("sess", metadata, [], "spotify", now=FIXED)

        assert attrs["timestamp"] == "2026-09-19T12:00:00Z"


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
