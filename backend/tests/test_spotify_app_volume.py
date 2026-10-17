"""The Spotify app's slider and Milō's volume, driven through the go-librespot
of tests/spotify_world.py (0.10.5's volume: kept across sessions, settable with
no session, reported 100 ms after a change).

With `spotify.allow_app_volume` on, the two are one level, as an AirPlay
sender's slider is: what is asserted is what Milō asked of each side — the
slider positions it posted, the levels it set on VolumeService — after one of
them moved. Off, neither moves the other. Either way go-librespot leaves the
samples at unity: CamillaDSP is the only attenuation (invariant 7).
"""
from typing import Callable, List
from unittest.mock import Mock

import pytest

from backend.core.models.volume import VolumeConfig
from backend.sources.spotify.source import AppVolumeSetting
from backend.tests.spotify_world import PARAPLUIE, SpotifyWorld


class _Volume:
    """VolumeService as the source uses it: the level in dB over the real
    VolumeConfig, a state listener called after each broadcast, as the real one."""

    def __init__(self, volume_db: float = -50.0) -> None:
        self.volume_config = VolumeConfig()
        self.volume_db, self.muted = volume_db, False
        self.listeners: List[Callable[[float, bool], None]] = []
        self.sets: List[float] = []
        # Where the house lands off the level asked: a room held at its limit
        # moves the average elsewhere (multiroom), by this many dB.
        self.lands_off_db = 0.0

    def add_state_listener(self, listener) -> None:
        self.listeners.append(listener)

    async def get_volume_state(self):
        return Mock(global_volume_db=self.volume_db, global_mute=self.muted)

    async def set_volume_db(self, volume_db: float, show_bar: bool = True) -> bool:
        self.sets.append(volume_db)
        self.volume_db = volume_db + self.lands_off_db
        self._broadcast()
        return True

    def moved_in_milo(self, volume_db: float, muted: bool = False) -> None:
        """The knob, the slider, a remote: anything but the phone."""
        self.volume_db, self.muted = volume_db, muted
        self._broadcast()

    def level(self) -> float:
        return self.volume_config.normalize(self.volume_db)

    def _broadcast(self) -> None:
        for listener in self.listeners:
            listener(self.level(), self.muted)


def _slider_posts(world: SpotifyWorld) -> List[int]:
    """Every position Milō put the app's slider at, in go-librespot's steps."""
    return [body["volume"] for command, body in world.daemon.posted if command == "volume"]


async def _world(monkeypatch, tmp_path, allowed: bool, volume: _Volume) -> SpotifyWorld:
    world = SpotifyWorld(monkeypatch, tmp_path, settings={"spotify.allow_app_volume": allowed})
    world.source.set_volume_service(volume)
    await world.select()
    return world


@pytest.fixture
def volume():
    return _Volume(volume_db=-50.0)


@pytest.fixture
async def shared(monkeypatch, tmp_path, volume):
    world = await _world(monkeypatch, tmp_path, True, volume)
    yield world
    await world.source.shutdown()


@pytest.fixture
async def separate(monkeypatch, tmp_path, volume):
    world = await _world(monkeypatch, tmp_path, False, volume)
    yield world
    await world.source.shutdown()


def _steps(volume: _Volume) -> int:
    return round(volume.level() * 100)


class TestShared:
    async def test_the_daemon_starts_at_milos_level(self, shared, volume):
        """The daemon keeps its own last volume (state.json): left there, the
        phone would show a level the room is not at, and its first press
        would move the house from it."""
        assert _slider_posts(shared) == [_steps(volume)]
        assert volume.sets == []

    async def test_a_session_opening_moves_nothing(self, shared, volume):
        """go-librespot 0.10.5 starts a session from the volume it was last
        given, which is Milō's: the first position a session reports is not a
        move. Before 0.10.5 it reported 100 %, and following it would have put
        the house at full volume on every cast."""
        await shared.phone_plays(PARAPLUIE)
        assert volume.sets == []

    async def test_the_phones_slider_moves_milos_volume(self, shared, volume):
        await shared.phone_plays(PARAPLUIE)
        posts = len(_slider_posts(shared))
        await shared.advance(2)
        await shared.phone_moves_volume(80)
        assert volume.sets == [pytest.approx(volume.volume_config.denormalize(0.8))]
        # Milō now stands where the phone put it: nothing is sent back.
        assert len(_slider_posts(shared)) == posts

    async def test_milos_volume_moves_the_phones_slider(self, shared, volume):
        await shared.phone_plays(PARAPLUIE)
        volume.moved_in_milo(-30.0)
        await shared.advance(0)
        assert _slider_posts(shared)[-1] == _steps(volume)
        assert volume.sets == []

    async def test_milos_move_written_back_is_not_a_move(self, shared, volume):
        """go-librespot reports the position it was put at. Taken as the
        phone's, it would set Milō's volume to itself, rounded to a step."""
        await shared.phone_plays(PARAPLUIE)
        volume.moved_in_milo(-30.0)
        await shared.advance(2)
        await shared._says(shared.daemon.volume_event())
        assert volume.sets == []

    async def test_a_report_older_than_milos_last_move_is_ignored(self, shared, volume):
        """A knob turn: go-librespot reports the position of 100 ms ago while
        Milō has moved past it. Followed, the house would jump back."""
        await shared.phone_plays(PARAPLUIE)
        volume.moved_in_milo(-40.0)
        await shared.advance(0)
        older = _slider_posts(shared)[-1]
        volume.moved_in_milo(-30.0)
        await shared.advance(0)
        await shared.phone_moves_volume(older)
        assert volume.sets == []

    async def test_a_phone_move_inside_the_echo_window_is_followed_after_it(self, shared, volume):
        """The phone's slider moved right after a knob turn: go-librespot
        reports it once and says nothing more. Dropped, the two sliders
        would disagree until the next move."""
        await shared.phone_plays(PARAPLUIE)
        volume.moved_in_milo(-30.0)
        await shared.advance(0)
        await shared.phone_moves_volume(60)
        assert volume.sets == []
        await shared.advance(1.5)
        assert volume.sets == [pytest.approx(volume.volume_config.denormalize(0.6))]

    async def test_the_level_the_house_landed_on_is_not_sent_back(self, shared, volume):
        """A room held at its limit puts the house's average off the phone's
        step. Sent back, it would snap the slider under the finger, and open
        an echo window that drops the rest of the drag."""
        await shared.phone_plays(PARAPLUIE)
        posts = len(_slider_posts(shared))
        volume.lands_off_db = -2.0
        await shared.advance(2)
        await shared.phone_moves_volume(80)
        await shared.advance(0)
        assert len(volume.sets) == 1
        assert len(_slider_posts(shared)) == posts

    async def test_a_burst_of_milo_moves_sends_the_last(self, shared, volume):
        await shared.phone_plays(PARAPLUIE)
        posts = len(_slider_posts(shared))
        for db in (-45.0, -40.0, -35.0):
            volume.moved_in_milo(db)
        await shared.advance(0)
        assert _slider_posts(shared)[posts:] == [_steps(volume)]

    async def test_milos_move_reaches_the_slider_with_no_session(self, shared, volume):
        """0.10.5 takes a volume with no session and starts the next session
        from it, so a phone casting later sees the room's level at once."""
        assert not shared.daemon.session
        volume.moved_in_milo(-30.0)
        await shared.advance(0)
        assert shared.daemon.volume == _steps(volume)
        # The session the phone opens later reports it: Milō's own level,
        # not a move to set it to again, rounded to a step.
        await shared.advance(5)
        await shared.phone_plays(PARAPLUIE)
        assert volume.sets == []

    async def test_the_next_session_reports_milos_level_back_without_a_move(self, shared, volume):
        """Between two casts Milō's volume moved, and go-librespot took it
        with no session. The next session reports that position: a change
        from the last session's, but Milō's own, not the phone's."""
        await shared.phone_plays(PARAPLUIE)
        await shared.phone_leaves()
        volume.moved_in_milo(-30.0)
        await shared.advance(5)
        await shared.phone_plays(PARAPLUIE)
        assert volume.sets == []

    async def test_a_daemon_that_kept_its_own_level_moves_nothing(self, monkeypatch, tmp_path, volume):
        """A daemon that refused Milō's level with no session (0.10.4 and
        before) opens the session at its own, 100 % after a takeover. That
        first position is not a move: followed, it would put the house at
        full volume on a cast."""
        world = SpotifyWorld(monkeypatch, tmp_path, settings={"spotify.allow_app_volume": True})
        world.daemon.volume_without_session = False
        world.daemon.volume = 100
        world.source.set_volume_service(volume)
        try:
            await world.select()
            await world.advance(5)
            await world.phone_plays(PARAPLUIE)
            assert volume.sets == []
        finally:
            await world.source.shutdown()

    async def test_a_mute_leaves_the_slider_where_it_is(self, shared, volume):
        """The slider has no mute. Put at 0, the phone's next press would
        start the house from silence."""
        posts = list(_slider_posts(shared))
        volume.moved_in_milo(volume.volume_db, muted=True)
        await shared.advance(0)
        assert _slider_posts(shared) == posts


class TestSeparate:
    async def test_the_phones_slider_moves_nothing(self, separate, volume):
        await separate.phone_plays(PARAPLUIE)
        await separate.advance(2)
        await separate.phone_moves_volume(80)
        assert volume.sets == []

    async def test_milos_volume_does_not_move_the_slider(self, separate, volume):
        await separate.phone_plays(PARAPLUIE)
        volume.moved_in_milo(-30.0)
        await separate.advance(0)
        assert _slider_posts(separate) == []

    async def test_turning_it_on_applies_without_a_restart(self, separate, volume):
        """The setting is Milō's to follow, not the daemon's: a restart would
        drop the Connect session on the phone for a switch on a settings page."""
        await separate.phone_plays(PARAPLUIE)
        separate.source._settings_service.get_setting.side_effect = (
            lambda key, *a, **k: {"spotify.allow_app_volume": True}.get(key)
        )
        assert await separate.source.on_spotify_settings_changed(apply_now=False) is True
        await separate.advance(0)
        assert _slider_posts(separate) == [_steps(volume)]
        assert separate.restarts == []
        await separate.advance(2)
        await separate.phone_moves_volume(80)
        assert volume.sets == [pytest.approx(volume.volume_config.denormalize(0.8))]

    async def test_turning_it_on_twice_in_one_burst_still_moves_the_slider(self, separate, volume):
        """Two settings writes before the source reads its mail (the switch,
        then the crossfade): the second must not cancel the first's sync, or
        the phone keeps a stale level its next press moves the house from."""
        await separate.phone_plays(PARAPLUIE)
        # What on_spotify_settings_changed posts, twice before the mail is read.
        separate.source._post_feed(AppVolumeSetting(True))
        separate.source._post_feed(AppVolumeSetting(True))
        await separate.advance(0)
        assert _slider_posts(separate) == [_steps(volume)]
