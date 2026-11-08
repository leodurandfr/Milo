# backend/tests/test_radio_shazam.py
"""`ShazamRecognitionService`'s decisions: what a Shazam answer means for the
title on screen, and when Shazam is asked again.

The rules moved twice on 2026-11-08, from a week of journal (399 tries, see the
module docstring of `sources/radio/shazam.py`). Every miss used to be retried
twice at once and a non-200 read as a no-match, which kept the unit throttled
(911 s before the first title in one session) and cleared good titles on soft
refusals. The first fix also asked less while a title held and wanted two
agreeing matches per title, which left the screen a song or two behind, minutes
after the change. Now: one call per try, a refusal is no verdict and backs off,
a no-match right after a refusal is none either, a match is shown at once, a
clear takes two no-matches, every answer is followed by the same
`RECOGNITION_INTERVAL_S`, and one budget is shared by every run.

The pure pieces are tested as functions; the loop is driven through
`tests/shazam_world.py` — shazamio, Shazam's HTTP answers, ffmpeg and the clock
scripted — never by replacing the service's own methods. Consumer:
`RadioSource` → the player's now-playing line and the lock screen.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

import backend.sources.radio.shazam as shazam_mod
from backend.sources.radio.shazam import (
    BACKOFF_BASE_S, BACKOFF_CAP_S, BACKOFF_JITTER, CAPTURE_GIVE_UP_S, CAPTURE_RETRIES,
    CAPTURE_RETRY_S, INITIAL_DELAY_SECONDS, QUOTA_REQUESTS, QUOTA_WINDOW_S,
    RECOGNITION_INTERVAL_S, STALE_AFTER_S, DisplayedTitle, RecognitionQuota,
    ShazamRecognitionService, backoff_delay, next_delay,
)
from backend.tests.shazam_world import Hang, Reply, ShazamWorld, match, no_match


@pytest.fixture
def service():
    settings = Mock()
    settings.get_setting = AsyncMock(return_value={"shazam_enabled": True})
    return ShazamRecognitionService(settings_service=settings, quota=RecognitionQuota())


@pytest.fixture
def world(monkeypatch):
    w = ShazamWorld(monkeypatch)
    yield w
    w.close()


def _track(title, artist="Artist"):
    return {"title": title, "artist": artist, "artwork": None}


def _gaps(times):
    return [round(b - a, 6) for a, b in zip(times, times[1:])]


class TestConfidenceGate:
    """Zero-match results are low-confidence and must be dropped (WI-2)."""

    def test_zero_matches_dropped(self, service):
        result = {
            "track": {"title": "Guess", "subtitle": "Someone"},
            "matches": [],
        }
        assert service._parse_result(result) is None

    def test_missing_matches_key_dropped(self, service):
        result = {"track": {"title": "Guess", "subtitle": "Someone"}}
        assert service._parse_result(result) is None

    def test_one_match_accepted(self, service):
        result = {
            "track": {"title": "So What", "subtitle": "Miles Davis"},
            "matches": [{"id": "abc"}],
        }
        track = service._parse_result(result)
        assert track == {"title": "So What", "artist": "Miles Davis", "artwork": None}

    def test_no_track_block(self, service):
        assert service._parse_result({"matches": [{"id": "x"}]}) is None
        assert service._parse_result({}) is None


class TestArtwork:
    def test_artwork_upgraded_resolution(self, service):
        result = {
            "track": {
                "title": "T",
                "subtitle": "A",
                "images": {"coverart": "https://x/image/400x400cc.jpg"},
            },
            "matches": [{"id": "1"}],
        }
        track = service._parse_result(result)
        assert track["artwork"] == "https://x/image/1280x1280cc.jpg"


class TestRunningFlag:
    def test_is_running_default_false(self, service):
        assert service.is_running is False


class TestDisplayedTitle:
    """`DisplayedTitle`: a song change is shown at the first match naming it;
    a clear waits for a second no-match, so one noisy capture does not drop a
    song still playing."""

    def hear_all(self, verdicts):
        title = DisplayedTitle()
        shown = []
        for i, verdict in enumerate(verdicts):
            if title.hear(verdict, float(i)):
                shown.append(title.track["title"] if title.track else None)
        return title, shown

    def test_a_first_match_is_shown_at_once(self):
        title, shown = self.hear_all([_track("A")])
        assert shown == ["A"]

    def test_a_different_match_replaces_at_once(self):
        _, shown = self.hear_all([_track("A"), _track("A"), _track("B")])
        assert shown == ["A", "B"]

    def test_a_single_no_match_never_clears(self):
        title, shown = self.hear_all([_track("A"), None, _track("A")])
        assert shown == ["A"]
        assert title.track["title"] == "A"

    def test_two_no_matches_in_a_row_clear(self):
        _, shown = self.hear_all([_track("A"), None, None])
        assert shown == ["A", None]

    def test_a_match_between_two_no_matches_breaks_the_clear(self):
        _, shown = self.hear_all([_track("A"), None, _track("A"), None])
        assert shown == ["A"]

    def test_a_new_song_after_a_no_match_is_shown(self):
        """The gap between two songs reads as a no-match; the next song must
        not wait for it to be confirmed."""
        _, shown = self.hear_all([_track("A"), None, _track("B")])
        assert shown == ["A", "B"]

    def test_tracks_compare_on_title_and_artist_whatever_the_case_and_spacing(self):
        _, shown = self.hear_all([_track("So  What", "Miles Davis"), _track("so what", "MILES DAVIS ")])
        assert shown == ["So  What"]

    def test_a_shown_title_goes_stale_from_its_last_verdict(self):
        title = DisplayedTitle()
        assert title.stale_at() is None
        title.hear(_track("A"), 10)
        title.hear(_track("A"), 30)
        title.hear(None, 50)          # a verdict, even a contradicting one
        assert title.stale_at() == 50 + STALE_AFTER_S


class TestCadence:
    """`next_delay` — the wait after a try."""

    def test_every_answer_is_followed_by_the_same_wait(self):
        assert next_delay() == RECOGNITION_INTERVAL_S

    def test_a_failed_capture_is_tried_again_soon_a_few_times_then_slows(self):
        delays = [next_delay(n) for n in range(1, CAPTURE_RETRIES + 3)]
        assert delays == [CAPTURE_RETRY_S] * CAPTURE_RETRIES + [CAPTURE_GIVE_UP_S] * 2


class TestBackoff:
    def test_it_doubles_from_the_base(self):
        assert [backoff_delay(n, 1.0) for n in range(4)] == [
            BACKOFF_BASE_S, 2 * BACKOFF_BASE_S, 4 * BACKOFF_BASE_S, 8 * BACKOFF_BASE_S,
        ]

    def test_it_never_exceeds_the_cap_even_jittered_up(self):
        assert backoff_delay(50, 1 + BACKOFF_JITTER) == BACKOFF_CAP_S
        assert backoff_delay(10_000, 1.0) == BACKOFF_CAP_S

    def test_the_jitter_scales_it(self):
        assert backoff_delay(0, 1 - BACKOFF_JITTER) == pytest.approx(BACKOFF_BASE_S * (1 - BACKOFF_JITTER))
        assert backoff_delay(0, 1 + BACKOFF_JITTER) == pytest.approx(BACKOFF_BASE_S * (1 + BACKOFF_JITTER))


class TestQuota:
    """`RecognitionQuota` — the window, the deadline and the refusal count."""

    @pytest.fixture
    def clock(self, monkeypatch):
        clock = SimpleNamespace(now=1000.0, draws=[])
        monkeypatch.setattr(shazam_mod, "time", SimpleNamespace(monotonic=lambda: clock.now))

        def uniform(low, high):
            clock.draws.append((low, high))
            return 1.0

        monkeypatch.setattr(shazam_mod, "random", SimpleNamespace(uniform=uniform))
        return clock

    def test_the_window_lets_its_requests_through_then_waits_for_the_oldest(self, clock):
        quota = RecognitionQuota()
        for _ in range(QUOTA_REQUESTS):
            assert quota.ready_at() <= clock.now
            quota.spend()
            clock.now += 1
        assert quota.ready_at() == 1000.0 + QUOTA_WINDOW_S

        clock.now = 1000.0 + QUOTA_WINDOW_S
        assert quota.ready_at() <= clock.now

    def test_a_later_deadline_stands(self, clock):
        quota = RecognitionQuota()
        quota.defer(600)
        quota.defer(10)
        assert quota.ready_at() == clock.now + 600

    def test_the_backoff_grows_and_starts_over_on_an_answer(self, clock):
        quota = RecognitionQuota()
        assert not quota.backing_off
        assert [quota.back_off() for _ in range(3)] == [backoff_delay(n, 1.0) for n in range(3)]
        assert quota.backing_off
        assert quota.ready_at() == clock.now + backoff_delay(2, 1.0)

        quota.answered()
        assert not quota.backing_off
        assert quota.back_off() == backoff_delay(0, 1.0)

    def test_a_retry_after_longer_than_the_backoff_wins(self, clock):
        quota = RecognitionQuota()
        assert quota.back_off(at_least=1234) == 1234
        assert quota.ready_at() == clock.now + 1234

    def test_the_jitter_is_drawn_within_its_bounds(self, clock):
        RecognitionQuota().back_off()
        assert clock.draws == [(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)]


class TestTheLoopShowsATitle:
    async def test_the_first_title_appears_at_the_first_match(self, world):
        world.replies = [Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=600)

        assert world.requests[0] == INITIAL_DELAY_SECONDS
        assert world.shown[0] == (world.requests[0], {"title": "A", "artist": "Artist", "artwork": None})
        assert ("INFO", world.requests[0], "Shazam: Artist – A") in world.lines

    async def test_a_song_change_is_shown_at_the_next_try(self, world):
        """A held title is asked about as often as anything: the radio moves
        on to the next song whenever it likes."""
        world.replies = [Reply(match("A"))] * 5 + [Reply(match("B"))]
        svc = world.service()

        await world.run(svc, until=10_000)

        assert set(_gaps(world.requests[:6])) == {RECOGNITION_INTERVAL_S, QUOTA_WINDOW_S - RECOGNITION_INTERVAL_S}
        assert world.titles() == ["A", "B", None]  # published once each, not per round; stop() clears
        assert world.shown[1][0] == world.requests[5]

    async def test_a_no_match_under_a_title_is_confirmed_before_it_clears(self, world):
        world.replies = [Reply(b) for b in [match("A"), no_match(), no_match(), no_match()]]
        svc = world.service()

        await world.run(svc, until=10_000)

        assert world.titles() == ["A", None]
        assert world.shown[1][0] == world.requests[2]

    async def test_a_no_match_right_after_a_refusal_is_no_verdict(self, world, monkeypatch):
        """Throttling ends on a "no match" that says nothing of the audio. (The
        backoff carries this run past STALE_AFTER_S, which is not what it is
        about: that expiry has its own test below.)"""
        monkeypatch.setattr(shazam_mod, "STALE_AFTER_S", 10_000)
        world.replies = [Reply(b) for b in [match("A"), no_match()]] + [Reply(status=429)] + [
            Reply(no_match()), Reply(match("A"))
        ]
        svc = world.service()

        await world.run(svc, until=10_000)

        assert world.replies == []                # every answer was heard
        assert world.titles() == ["A", None]      # never cleared: stop() did

    async def test_the_window_holds_back_a_third_request_within_a_minute(self, world):
        """Tries every 20 s would make three calls in a minute, which is where
        the 429s began."""
        world.replies = [Reply(match(t)) for t in "ABCD"]
        svc = world.service()

        await world.run(svc, until=10_000)

        assert len(world.requests) >= 3
        for i in range(len(world.requests) - QUOTA_REQUESTS):
            assert world.requests[i + QUOTA_REQUESTS] - world.requests[i] >= QUOTA_WINDOW_S


class TestRefusals:
    async def test_a_429_backs_off_doubling_without_an_immediate_retry(self, world):
        world.replies = [Reply(status=429)] * 5
        svc = world.service()

        await world.run(svc, until=100_000)

        assert _gaps(world.requests[:5]) == [backoff_delay(n, 1.0) for n in range(4)]

    async def test_the_backoff_is_capped(self, world):
        world.replies = [Reply(status=429)] * 12
        svc = world.service()

        await world.run(svc, until=100_000)

        assert max(_gaps(world.requests)) == BACKOFF_CAP_S

    async def test_the_jitter_moves_it_within_its_bounds(self, world):
        world.jitter = 1 + BACKOFF_JITTER
        world.replies = [Reply(status=429)] * 2
        svc = world.service()

        await world.run(svc, until=100_000)

        assert _gaps(world.requests[:2]) == [pytest.approx(BACKOFF_BASE_S * (1 + BACKOFF_JITTER))]
        assert set(world.jitter_bounds) == {(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER)}

    async def test_a_retry_after_is_honored(self, world):
        world.replies = [Reply(status=429, headers={"Retry-After": "700"}), Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=100_000)

        assert _gaps(world.requests[:2]) == [700]

    async def test_a_retryms_in_a_200_is_honored(self, world):
        """The next try comes RECOGNITION_INTERVAL_S later — unless Shazam
        asked for longer in the body of its answer."""
        world.replies = [Reply(match("A", retryms=400_000)), Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=100_000)

        assert _gaps(world.requests[:2]) == [400]

    async def test_an_answer_starts_the_backoff_over(self, world):
        world.replies = [Reply(status=429), Reply(status=429), Reply(no_match()), Reply(status=429), Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=100_000)

        gaps = _gaps(world.requests)
        assert gaps[0] == backoff_delay(0, 1.0)
        assert gaps[1] == backoff_delay(1, 1.0)
        assert gaps[3] == backoff_delay(0, 1.0)

    @pytest.mark.parametrize("reply", [
        Reply(status=429),
        Reply({"matches": []}, status=403),
        Reply({"matches": []}, status=500),
        Reply("<html>captive portal</html>"),
        Reply('{"matches": ['),
    ], ids=["429", "403-json", "500-json", "200-html", "200-broken-json"])
    async def test_a_refusal_is_no_verdict_and_never_an_error(self, world, reply):
        """A non-200 JSON body used to reach the parser as an answer with no
        track — a no-match — and clear the title. And a refusal logged at
        error lands in errors.log and on the UI banner for a condition the
        next try clears."""
        world.replies = [Reply(match("A")), reply, reply]
        svc = world.service()

        await world.run(svc, until=STALE_AFTER_S, stop=False)
        assert world.replies == []                # both refusals were heard
        assert svc.current_track["title"] == "A"  # and held through, before stop() clears
        assert world.titles() == ["A"]
        await svc.stop()

        assert world.logged("ERROR") == []
        refusals = [line for line in world.lines if "next try in" in line[2] and line[0] != "DEBUG"]
        assert [level for level, _, _ in refusals] == ["INFO"]   # once per backoff

    async def test_a_refusal_keeps_a_pending_clear_standing(self, world, monkeypatch):
        """A refusal is no verdict: the no-match heard before it and the next
        real one are still two in a row (the soft no-match between them counts
        for neither). STALE_AFTER_S is moved out of the way: the backoff would
        otherwise carry the run to it, and that expiry has its own test."""
        monkeypatch.setattr(shazam_mod, "STALE_AFTER_S", 10_000)
        world.replies = [Reply(b) for b in [match("A"), no_match()]] + [
            Reply(status=429), Reply(no_match()), Reply(no_match())
        ]
        svc = world.service()

        await world.run(svc, until=100_000)

        assert world.titles() == ["A", None]
        assert world.shown[1][0] == world.requests[4]

    async def test_a_timeout_and_a_transport_error_back_off_too(self, world, monkeypatch):
        monkeypatch.setattr(shazam_mod, "RECOGNITION_TIMEOUT_SECONDS", 0.05)
        world.replies = [Hang(), aiohttp.ClientConnectionError("reset"), Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=10_000)

        assert _gaps(world.requests[:3]) == [backoff_delay(0, 1.0), backoff_delay(1, 1.0)]
        messages = [m for level, _, m in world.lines if "next try in" in m]
        assert "timed out" in messages[0] and "unreachable" in messages[1]
        assert world.logged("ERROR") == []

    async def test_a_title_with_no_verdict_for_too_long_is_cleared(self, world):
        world.replies = [Reply(match("A"))] + [Reply(status=429)] * 10
        svc = world.service()

        await world.run(svc, until=100_000)

        assert world.titles() == ["A", None]
        assert world.shown[1][0] == world.requests[0] + STALE_AFTER_S


class TestOneBudget:
    """The quota outlives a run: zapping and a source switch must not reset it.

    Each scenario has its control: the same steps with a fresh quota send the
    request at once, so it is the shared quota that holds it back."""

    async def test_a_station_change_keeps_the_backoff(self, world):
        svc = world.service()
        world.replies = [Reply(status=429), Reply(match("A"))]
        await world.run(svc, until=30, stop=False)
        await world.run(svc, until=10_000, url="http://stream.example/nova.aac")

        assert world.requests[:2] == [INITIAL_DELAY_SECONDS, INITIAL_DELAY_SECONDS + backoff_delay(0, 1.0)]

    @pytest.mark.parametrize("shared", [True, False], ids=["shared-quota", "control"])
    async def test_a_source_restart_keeps_the_backoff(self, world, shared):
        quota = RecognitionQuota()
        first = world.service(quota=quota)
        world.replies = [Reply(status=429), Reply(match("A"))]
        await world.run(first, until=30, stop=False)
        await first.close()                      # RadioSource._cleanup

        second = world.service(quota=quota if shared else RecognitionQuota())
        await world.run(second, until=10_000)

        backoff_ends = INITIAL_DELAY_SECONDS + backoff_delay(0, 1.0)
        if shared:
            assert world.requests[:2] == [INITIAL_DELAY_SECONDS, backoff_ends]
        else:
            assert world.requests[:2] == [INITIAL_DELAY_SECONDS, 30 + INITIAL_DELAY_SECONDS]


class TestTheLoopSurvives:
    async def test_an_unexpected_error_is_reported_once_and_the_loop_goes_on(self, world):
        world.replies = [RuntimeError("boom"), RuntimeError("boom again"), Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=100_000, stop=False)
        assert svc.is_running
        await svc.stop()

        assert len(world.logged("ERROR")) == 1
        assert world.titles() == ["A", None]      # shown, then cleared by stop()

    async def test_capture_failures_are_tried_again_soon_and_send_nothing(self, world):
        world.captures_failing = CAPTURE_RETRIES + 2
        world.replies = [Reply(match("A"))]
        svc = world.service()

        await world.run(svc, until=10_000)

        expected = [next_delay(n) for n in range(1, CAPTURE_RETRIES + 3)]
        assert _gaps(world.captures[:CAPTURE_RETRIES + 3]) == expected
        assert world.requests[0] == world.captures[CAPTURE_RETRIES + 2]

    async def test_the_toggle_turned_off_mid_stream_clears_the_title_and_captures_nothing(self, world):
        world.replies = [Reply(match("A"))]
        svc = world.service()
        await svc.start("http://stream.example/fip.mp3")
        await world.advance_to(INITIAL_DELAY_SECONDS + 1)
        assert world.titles() == ["A"]
        captured = len(world.captures)

        world.enabled = False
        await world.advance_to(2_000)

        # Cleared by the loop at its first turn after the switch, well before
        # the title could go stale on its own and before stop() could clear it.
        assert svc.current_track is None
        assert world.titles() == ["A", None]
        assert world.shown[1][0] == world.requests[0] + RECOGNITION_INTERVAL_S
        assert world.shown[1][0] < world.requests[0] + STALE_AFTER_S
        assert len(world.captures) == captured
        await svc.stop()
        assert len(world.shown) == 2
