"""shairport-sync's D-Bus properties, decoded into what the AirPlay source reads.

The inputs are the properties as shairport-sync 5.6 published them on the unit
(2026-10-16, `busctl get-property` and a `busctl monitor` of PropertiesChanged:
iPhone Music, iPhone Spotify, a Mac), wrapped in the Variants dbus-next hands
the handler. A key renamed upstream, or a type that changed (the Timestamp is a
string, not a date), leaves the source with an item it cannot read — no title,
no playhead — with no error anywhere.
"""
from datetime import datetime, timezone

from dbus_next import Variant

from backend.sources.airplay.remote import (
    ADVANCED_IFACE, CLIENT_IFACE, REMOTE_IFACE, NowPlaying, RemoteUpdate, _update,
)

# iPhone Music, as `NowPlayingInformation` carried it (a subset of its 30 keys).
IPHONE_ITEM = Variant("a{sv}", {
    "kMRMediaRemoteNowPlayingInfoTitle": Variant("s", "Steam"),
    "kMRMediaRemoteNowPlayingInfoArtist": Variant("s", "Ledeunff"),
    "kMRMediaRemoteNowPlayingInfoAlbum": Variant("s", "My Storm - EP"),
    "kMRMediaRemoteNowPlayingInfoDuration": Variant("d", 223.34666666666666),
    "kMRMediaRemoteNowPlayingInfoElapsedTime": Variant("d", 14.92398572),
    "kMRMediaRemoteNowPlayingInfoTimestamp": Variant("s", "2026-10-16T09:40:42.896440Z"),
    "kMRMediaRemoteNowPlayingInfoPlaybackRate": Variant("d", 1.0),
    "kMRMediaRemoteNowPlayingInfoUniqueIdentifier": Variant("t", 9811617133902594685),
    "kMRMediaRemoteNowPlayingInfoArtworkIdentifier": Variant(
        "s", "Music/v4/bb/20/a2/bb20a229-75a2-b012-933d-6f347adf2745/3700551755493_cover.jpg"),
    "kMRMediaRemoteNowPlayingInfoArtworkDataWidth": Variant("t", 600),
    "kMRMediaRemoteNowPlayingInfoCollectionInfo": Variant("a{sv}", {
        "kMRMediaRemoteNowPlayingCollectionInfoKeyTitle": Variant("s", "My Storm - EP"),
    }),
    "kShairportSyncNowPlayingInfoArtworkFilePath": Variant(
        "s", "file:///tmp/shairport-sync/.cache/coverart/cover-88579ebbabd30a12c3cc8df58d0f75f7.jpg"),
})

STAMP = datetime(2026, 10, 16, 9, 40, 42, 896440, tzinfo=timezone.utc).timestamp()


def _item() -> NowPlaying:
    update = _update(CLIENT_IFACE, "NowPlayingInformation", IPHONE_ITEM)
    assert update.kind == "now_playing" and update.value is not None, "the item decoded to nothing"
    return update.value


def test_the_item_carries_the_track():
    item = _item()
    assert (item.title, item.artist, item.album) == ("Steam", "Ledeunff", "My Storm - EP")
    assert item.duration_ms == 223_346
    assert item.track_id == 9811617133902594685


def test_an_item_without_a_unique_identifier_is_named_by_its_content_item():
    """Measured 2026-10-23 (a Mac, shairport-sync 5.6.1): some items carry no
    UniqueIdentifier, only a ContentItemIdentifier. Without an identity every
    track read as the same one, and the first one never got a playhead."""
    keys = {k: v for k, v in IPHONE_ITEM.value.items() if k != "kMRMediaRemoteNowPlayingInfoUniqueIdentifier"}
    keys["kMRMediaRemoteNowPlayingInfoContentItemIdentifier"] = Variant(
        "s", "76F86BD5-2542-44CE-8AD5-AD4481AF5ECA")
    item = _update(CLIENT_IFACE, "NowPlayingInformation", Variant("a{sv}", keys)).value
    assert item.track_id == "76F86BD5-2542-44CE-8AD5-AD4481AF5ECA"


def test_a_unique_identifier_of_zero_is_still_the_identity():
    keys = dict(IPHONE_ITEM.value)
    keys["kMRMediaRemoteNowPlayingInfoUniqueIdentifier"] = Variant("t", 0)
    keys["kMRMediaRemoteNowPlayingInfoContentItemIdentifier"] = Variant("s", "176::180")
    item = _update(CLIENT_IFACE, "NowPlayingInformation", Variant("a{sv}", keys)).value
    assert item.track_id == 0


def test_the_playhead_is_the_elapsed_time_at_its_stamp():
    """ElapsedTime is where the sender was at Timestamp — an ISO string on
    D-Bus — and it moves at PlaybackRate from there."""
    item = _item()
    assert item.elapsed_ms == 14_923
    assert abs(item.at - STAMP) < 1e-6
    assert item.position_ms(STAMP + 10) == 24_923


def test_a_paused_item_stands_still():
    item = NowPlaying(**{**_item().__dict__, "rate": 0.0})
    assert item.position_ms(STAMP + 600) == 14_923


def test_the_picture_is_a_local_path():
    item = _item()
    assert item.artwork_path == "/tmp/shairport-sync/.cache/coverart/cover-88579ebbabd30a12c3cc8df58d0f75f7.jpg"
    assert item.artwork_id.endswith("_cover.jpg")


def test_the_item_re_sent_without_its_picture_has_no_path():
    bare = Variant("a{sv}", {
        k: v for k, v in IPHONE_ITEM.value.items() if k != "kShairportSyncNowPlayingInfoArtworkFilePath"
    })
    item = _update(CLIENT_IFACE, "NowPlayingInformation", bare).value
    assert item.artwork_path is None and item.artwork_id is not None


def test_the_empty_item_of_a_sender_arriving_is_no_item():
    assert _update(CLIENT_IFACE, "NowPlayingInformation", Variant("a{sv}", {})) == \
        RemoteUpdate("now_playing", None)


def test_the_commands_are_the_enabled_numbers():
    """CommandInformation as the iPhone listed it: 17/18 present but disabled."""
    def entry(number, enabled):
        return Variant("a{sv}", {
            "kCommandInfoCommandKey": Variant("t", number),
            "kCommandInfoEnabledKey": Variant("b", enabled),
        })
    commands = Variant("av", [entry(0, True), entry(1, True), entry(4, True), entry(17, False),
                              entry(26, True)])

    assert _update(CLIENT_IFACE, "CommandInformation", commands) == \
        RemoteUpdate("commands", frozenset({0, 1, 4, 26}))


def test_the_modes_are_milos_vocabulary():
    assert _update(ADVANCED_IFACE, "Shuffle", Variant("s", "On")) == RemoteUpdate("shuffle", True)
    assert _update(ADVANCED_IFACE, "Shuffle", Variant("s", "Off")) == RemoteUpdate("shuffle", False)
    assert [_update(ADVANCED_IFACE, "LoopStatus", Variant("s", loop)).value
            for loop in ("Off", "All", "One")] == ["off", "context", "track"]


def test_the_senders_slider_is_its_airplay_volume():
    assert _update(REMOTE_IFACE, "AirplayVolume", Variant("d", -22.7386)) == RemoteUpdate("volume", -22.7386)


def test_what_milo_does_not_read_is_ignored():
    assert _update(REMOTE_IFACE, "PlayerState", Variant("s", "Playing")) is None
    assert _update(ADVANCED_IFACE, "Volume", Variant("i", 33)) is None
