// frontend/tests/composables/useSourceProgress.test.js
/**
 * useSourceProgress interpolates the playback position locally between the
 * backend's periodic broadcasts. Three rules carry the weight:
 *
 *  - the timer only runs for the *active* source (an instance created for
 *    another source would interpolate someone else's position),
 *  - interpolation is scaled by mpv's playback_speed, so a 1.5× podcast bar
 *    doesn't drift,
 *  - a consumer mounted mid-song can compensate for how stale the last
 *    broadcast is (AirPlay only emits every 30 s).
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { useSourceProgress } from '@/composables/useSourceProgress';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { apiCall } from '@/services/apiCall';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

/** Mount a host exposing the composable for `source`. */
function mountProgress(source, options = {}) {
  let progress;
  const Host = defineComponent({
    setup() {
      progress = useSourceProgress(source, options);
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { progress, wrapper };
}

function broadcast(store, { source = 'spotify', metadata = {} } = {}) {
  store.updateState({
    data: {
      full_state: {
        active_source: source,
        source_state: 'active',
        transitioning: false,
        multiroom_enabled: false,
        equalizer_effects_enabled: false,
        metadata,
      },
    },
  });
}

describe('useSourceProgress', () => {
  let store;

  beforeEach(() => {
    resetApiCallMock();
    // `performance` must be faked too: staleness compensation measures the age
    // of the last broadcast with performance.now(), so leaving it on the real
    // clock would make every seed look perfectly fresh.
    vi.useFakeTimers({
      toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date', 'performance'],
    });
    // Move off zero: the store stamps positionTimestamp with performance.now(),
    // and the composable treats a falsy stamp as "never received".
    vi.advanceTimersByTime(1000);
    store = useUnifiedAudioStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('seeding from the store', () => {
    it('starts uninitialised until a position arrives', () => {
      const { progress } = mountProgress('spotify');

      expect(progress.isPositionInitialized.value).toBe(false);
      expect(progress.currentPosition.value).toBe(0);
    });

    it('adopts the broadcast position and duration', () => {
      broadcast(store, { metadata: { position: 5000, duration: 200000 } });
      const { progress } = mountProgress('spotify');

      expect(progress.isPositionInitialized.value).toBe(true);
      expect(progress.currentPosition.value).toBe(5000);
      expect(progress.duration.value).toBe(200000);
    });

    it('lands on a restart that answers the position the store already holds', async () => {
      // Previous on a track already anchored at 0 — the state a track start
      // leaves behind for the 30 s until the source's next periodic sync. The
      // restart re-sends 0, so nothing about the value says the playhead moved
      // and the bar used to carry on from our own clock. Reported on the music
      // library's expanded mobile player, where a second Previous is one tap.
      broadcast(store, {
        source: 'music_library',
        metadata: { position: 0, duration: 244000, is_playing: true },
      });
      const { progress } = mountProgress('music_library');
      vi.advanceTimersByTime(4000);
      expect(progress.currentPosition.value).toBe(4000);

      broadcast(store, {
        source: 'music_library',
        metadata: { position: 0, duration: 244000, is_playing: true },
      });
      await nextTick();

      expect(progress.currentPosition.value).toBe(0);
    });

    it('resets on a track change even when the position stays at 0', () => {
      broadcast(store, { metadata: { position: 0, duration: 200000 } });
      const { progress } = mountProgress('spotify');

      broadcast(store, { metadata: { position: 0, duration: 150000 } });

      expect(progress.duration.value).toBe(150000);
    });
  });

  describe('progressPercentage', () => {
    it('is the position over the duration', () => {
      broadcast(store, { metadata: { position: 50000, duration: 200000 } });
      const { progress } = mountProgress('spotify');

      expect(progress.progressPercentage.value).toBe(25);
    });

    it('is 0 for a stream with no duration, not NaN', () => {
      // Web radio has no duration; a NaN would blow up the bar's width style.
      broadcast(store, { metadata: { position: 50000, duration: 0 } });
      const { progress } = mountProgress('radio');

      expect(progress.progressPercentage.value).toBe(0);
    });
  });

  describe('local interpolation', () => {
    it('advances by real time while playing', () => {
      broadcast(store, { metadata: { position: 0, duration: 200000, is_playing: true } });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(1000);

      expect(progress.currentPosition.value).toBe(1000);
    });

    it('does not advance while paused', () => {
      broadcast(store, { metadata: { position: 4000, duration: 200000, is_playing: false } });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(2000);

      expect(progress.currentPosition.value).toBe(4000);
    });

    it('freezes while buffering', () => {
      broadcast(store, { metadata: { position: 4000, duration: 200000, is_playing: true, is_buffering: true } });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(2000);

      expect(progress.currentPosition.value).toBe(4000);
    });

    it('scales with mpv playback_speed', () => {
      // A 1.5× podcast advances 1.5 s of media per second of wall clock.
      broadcast(store, {
        source: 'podcast',
        metadata: { position: 0, duration: 200000, is_playing: true, playback_speed: 1.5 },
      });
      const { progress } = mountProgress('podcast');

      vi.advanceTimersByTime(1000);

      expect(progress.currentPosition.value).toBe(1500);
    });

    it('picks up a speed change on the next tick', () => {
      broadcast(store, {
        source: 'podcast',
        metadata: { position: 0, duration: 200000, is_playing: true, playback_speed: 1 },
      });
      const { progress } = mountProgress('podcast');
      vi.advanceTimersByTime(1000);

      store.systemState.metadata.playback_speed = 2;
      vi.advanceTimersByTime(1000);

      expect(progress.currentPosition.value).toBe(3000);
    });

    it('advances by wall clock, not by tick count, when the browser throttles', () => {
      // A backgrounded tab has its 100 ms interval throttled to ~1 Hz. Counting
      // ticks made the bar advance ten times too slowly, and nothing corrects it
      // before the next broadcast — never, on Spotify, between two events.
      broadcast(store, { metadata: { position: 0, duration: 200000, is_playing: true } });
      const { progress } = mountProgress('spotify');

      // One firing for a second of wall clock: the browser skipped nine.
      const tickClock = performance.now.bind(performance);
      vi.spyOn(performance, 'now').mockImplementation(() => tickClock() + 900);
      vi.advanceTimersByTime(100);

      expect(progress.currentPosition.value).toBe(1000);
      performance.now.mockRestore();
    });

    it('does not credit time spent buffering once playback resumes', () => {
      // The mirror image of the throttling fix: reading a clock must not hand
      // back the seconds the bar deliberately stood still for.
      broadcast(store, {
        metadata: { position: 0, duration: 200000, is_playing: true, is_buffering: true },
      });
      const { progress } = mountProgress('spotify');
      vi.advanceTimersByTime(2000);

      store.systemState.metadata.is_buffering = false;
      vi.advanceTimersByTime(1000);

      expect(progress.currentPosition.value).toBe(1000);
    });

    it('stops at the duration', () => {
      broadcast(store, { metadata: { position: 9900, duration: 10000, is_playing: true } });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(5000);

      expect(progress.currentPosition.value).toBe(10000);
    });

    it('never ticks for a source that is not the active one', () => {
      // The screensaver keeps a podcast tracker alive while Spotify plays.
      broadcast(store, {
        source: 'spotify',
        metadata: { position: 1000, duration: 200000, is_playing: true },
      });
      const { progress } = mountProgress('podcast');

      vi.advanceTimersByTime(3000);

      expect(progress.currentPosition.value).toBe(1000);
    });

    it('stops interpolating once the source is taken over', () => {
      broadcast(store, { metadata: { position: 0, duration: 200000, is_playing: true } });
      const { progress } = mountProgress('spotify');
      vi.advanceTimersByTime(1000);

      broadcast(store, {
        source: 'radio',
        metadata: { position: 0, duration: 0, is_playing: true },
      });
      const positionAtSwitch = progress.currentPosition.value;
      vi.advanceTimersByTime(3000);

      expect(progress.currentPosition.value).toBe(positionAtSwitch);
    });

    it('stops the timer when the consumer unmounts', () => {
      broadcast(store, { metadata: { position: 0, duration: 200000, is_playing: true } });
      const { progress, wrapper } = mountProgress('spotify');
      vi.advanceTimersByTime(500);

      wrapper.unmount();
      const positionAtUnmount = progress.currentPosition.value;
      vi.advanceTimersByTime(3000);

      expect(progress.currentPosition.value).toBe(positionAtUnmount);
    });
  });

  describe('staleness compensation', () => {
    it('is off by default — the seed is the broadcast value', () => {
      broadcast(store, { metadata: { position: 10000, duration: 200000, is_playing: true } });
      const { progress } = mountProgress('spotify');

      expect(progress.currentPosition.value).toBe(10000);
    });

    it('advances the seed by the age of the last broadcast when enabled', () => {
      // The Lyrics modal mounts mid-song and needs line-level sync: seeding at
      // the raw broadcast value would leave it a whole interval behind.
      broadcast(store, { metadata: { position: 10000, duration: 200000, is_playing: true } });
      vi.advanceTimersByTime(4000);

      const { progress } = mountProgress('spotify', { compensateStaleness: true });

      expect(progress.currentPosition.value).toBeGreaterThanOrEqual(13900);
      expect(progress.currentPosition.value).toBeLessThanOrEqual(14100);
    });

    it('does not compensate while paused', () => {
      broadcast(store, { metadata: { position: 10000, duration: 200000, is_playing: false } });
      vi.advanceTimersByTime(4000);

      const { progress } = mountProgress('spotify', { compensateStaleness: true });

      expect(progress.currentPosition.value).toBe(10000);
    });

    it('never seeds past the end of the track', () => {
      broadcast(store, { metadata: { position: 9000, duration: 10000, is_playing: true } });
      vi.advanceTimersByTime(30000);

      const { progress } = mountProgress('spotify', { compensateStaleness: true });

      expect(progress.currentPosition.value).toBe(10000);
    });
  });

  describe('seekTo', () => {
    it('moves the bar immediately and sends the seek command', async () => {
      broadcast(store, { metadata: { position: 1000, duration: 200000, is_playing: true } });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      const seeking = progress.seekTo(120000);
      expect(progress.currentPosition.value).toBe(120000);
      // seekTo holds the guard open for 50 ms after the command resolves.
      await vi.advanceTimersByTimeAsync(100);
      await seeking;

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/audio/control/spotify',
        { command: 'seek', data: { position_ms: 120000 } },
        expect.anything(),
      );
    });

    it('ignores the echo of the old position while the seek settles', async () => {
      broadcast(store, { metadata: { position: 1000, duration: 200000, is_playing: true } });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      const seeking = progress.seekTo(120000);
      // A stale broadcast arriving mid-seek must not rewind the bar.
      broadcast(store, { metadata: { position: 1100, duration: 200000, is_playing: true } });

      expect(progress.currentPosition.value).toBe(120000);
      await vi.advanceTimersByTimeAsync(100);
      await seeking;
    });
  });

  describe('sub-second corrections', () => {
    it('does not drag the bar back for a source whose clock lags ours', async () => {
      // Measured on Bluetooth over the WebSocket: BlueZ's playhead advances
      // slower than real time for the first seconds of a track (815 ms, then
      // 915 ms with 780 ms of wall clock between them). Adopting each correction
      // pulled the display back across the second boundary, and the digit
      // flickered 0:00/0:01 on every skip.
      broadcast(store, { source: 'bluetooth', metadata: { position: 815, duration: 286230, is_playing: true } });
      const { progress } = mountProgress('bluetooth');

      vi.advanceTimersByTime(800);
      expect(progress.currentPosition.value).toBe(1615);

      broadcast(store, { source: 'bluetooth', metadata: { position: 915, duration: 286230, is_playing: true } });
      await nextTick();

      expect(progress.currentPosition.value).toBe(1615);
    });

    it('still lands exactly on anything that really moved the playhead', async () => {
      broadcast(store, { source: 'bluetooth', metadata: { position: 45000, duration: 286230, is_playing: true } });
      const { progress } = mountProgress('bluetooth');
      vi.advanceTimersByTime(500);

      // A Previous restarting the track.
      broadcast(store, { source: 'bluetooth', metadata: { position: 0, duration: 286230, is_playing: true } });
      await nextTick();
      expect(progress.currentPosition.value).toBe(0);

      // And a track change lands even when the two positions are close, because
      // the duration moved with it.
      vi.advanceTimersByTime(500);
      broadcast(store, { source: 'bluetooth', metadata: { position: 148, duration: 241506, is_playing: true } });
      await nextTick();
      expect(progress.currentPosition.value).toBe(148);
    });

    it('lands exactly while paused, where no local clock is running', async () => {
      // A pause is where a source re-anchors, and the value it settles on is the
      // one the user can check against the screen.
      broadcast(store, { source: 'bluetooth', metadata: { position: 60000, duration: 286230, is_playing: true } });
      const { progress } = mountProgress('bluetooth');
      vi.advanceTimersByTime(500);

      broadcast(store, { source: 'bluetooth', metadata: { position: 60200, duration: 286230, is_playing: false } });
      await nextTick();

      expect(progress.currentPosition.value).toBe(60200);
    });
  });
});
