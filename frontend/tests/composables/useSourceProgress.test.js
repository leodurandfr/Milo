// frontend/tests/composables/useSourceProgress.test.js
/**
 * useSourceProgress draws one source's playhead from the backend's position
 * anchor: `ms` at the instant `at`, moving at `rate` while the phase is
 * playing (docs: "le fil", §2). Three rules carry the weight:
 *
 *  - the formula is the only interpolation: a consumer mounting mid-track reads
 *    the same number as one open for an hour, with no staleness to compensate,
 *  - only the *selected* source's session is drawn (an instance created for
 *    another source would show someone else's playhead),
 *  - a seek shows its target at once, and the anchor it causes replaces it.
 *
 * The sub-second smoothing the bar used to do itself is gone: the backend moves
 * the anchor only past a 2 s discontinuity, so every anchor that arrives is one
 * to land on exactly (backend tests/test_wire_state.py).
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { positionAt, useSourceProgress } from '@/composables/useSourceProgress';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { resetApiCallMock, ok } from '../helpers/apiCallMock';
import { apiCall } from '@/services/apiCall';
import { makeSession, publishState } from '../helpers/audioState';

vi.mock('@/services/apiCall', () => import('../helpers/apiCallMock'));

// Epoch seconds the tests' anchors are taken at.
const T0 = 1_750_000_000;

/** Mount a host exposing the composable for `source`. */
function mountProgress(source) {
  let progress;
  const Host = defineComponent({
    setup() {
      progress = useSourceProgress(source);
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { progress, wrapper };
}

function anchor(ms, { at = T0, rate = 1 } = {}) {
  return { ms, at, rate };
}

describe('positionAt', () => {
  it('moves the anchor by wall time × rate while playing', () => {
    expect(positionAt(anchor(1000, { rate: 1.5 }), 'playing', null, (T0 + 2) * 1000)).toBe(4000);
  });

  it('holds the anchor in every other phase', () => {
    for (const phase of ['loading', 'paused', 'connected']) {
      expect(positionAt(anchor(1000), phase, null, (T0 + 60) * 1000)).toBe(1000);
    }
  });

  it('is bounded by the duration and by zero', () => {
    expect(positionAt(anchor(9000), 'playing', 10000, (T0 + 5) * 1000)).toBe(10000);
    // A sender whose clock runs ahead of ours stamps an anchor in our future.
    expect(positionAt(anchor(0), 'playing', 10000, (T0 - 1) * 1000)).toBe(0);
  });

  it('answers null without an anchor', () => {
    expect(positionAt(null, 'playing', 10000, T0 * 1000)).toBeNull();
  });
});

describe('useSourceProgress', () => {
  let store;

  function publish({ source = 'spotify', session = null, resume = null } = {}) {
    publishState(store, { source, service: 'running', session, resume, controls: ['pause', 'seek'] });
  }

  function playing(position, overrides = {}) {
    return makeSession({ duration_ms: 200000, position, ...overrides });
  }

  beforeEach(() => {
    resetApiCallMock();
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] });
    vi.setSystemTime(T0 * 1000);
    store = useUnifiedAudioStore();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  describe('reading the anchor', () => {
    it('is uninitialised until an anchor arrives', () => {
      publish({ session: playing(null) });
      const { progress } = mountProgress('spotify');

      expect(progress.isPositionInitialized.value).toBe(false);
      expect(progress.currentPosition.value).toBe(0);
    });

    it('adopts the anchor and the duration', () => {
      publish({ session: playing(anchor(42000)) });
      const { progress } = mountProgress('spotify');

      expect(progress.isPositionInitialized.value).toBe(true);
      expect(progress.currentPosition.value).toBe(42000);
      expect(progress.duration.value).toBe(200000);
      expect(progress.progressPercentage.value).toBe(21);
    });

    it('reads mid-track exactly where a long-open consumer does', () => {
      // The Lyrics view mounting 30 s after the anchor was taken.
      publish({ session: playing(anchor(10000)) });
      vi.setSystemTime((T0 + 30) * 1000);
      const { progress } = mountProgress('spotify');

      expect(progress.currentPosition.value).toBe(40000);
    });

    it('is 0 % for a stream with no duration, not NaN', () => {
      publish({ session: playing(anchor(5000), { duration_ms: null }) });
      const { progress } = mountProgress('spotify');

      expect(progress.progressPercentage.value).toBe(0);
    });

    it('shows the resume point when there is no session', () => {
      publish({
        source: 'podcast',
        resume: { title: 'Ep', artist: null, album: null, artwork: null, duration_ms: 60000, position_ms: 15000 },
      });
      const { progress } = mountProgress('podcast');

      expect(progress.currentPosition.value).toBe(15000);
      expect(progress.duration.value).toBe(60000);
    });

    it("never draws another source's playhead", () => {
      publish({ source: 'radio', session: playing(anchor(42000)) });
      const { progress } = mountProgress('spotify');

      expect(progress.isPositionInitialized.value).toBe(false);
    });

    it('lands on a `source/position` for its session', async () => {
      publish({ session: playing(anchor(42000)) });
      const { progress } = mountProgress('spotify');

      store.updatePosition({ source: 'spotify', session_id: 'session-1', position: anchor(90000) });
      await nextTick();

      expect(progress.currentPosition.value).toBe(90000);
    });
  });

  describe('moving the bar', () => {
    it('advances with the clock while playing', async () => {
      publish({ session: playing(anchor(1000)) });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(2000);
      await nextTick();

      expect(progress.currentPosition.value).toBe(3000);
    });

    it('scales with the rate', async () => {
      publish({ session: playing(anchor(1000, { rate: 1.5 })) });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(2000);
      await nextTick();

      expect(progress.currentPosition.value).toBe(4000);
    });

    it('holds still while paused or loading', async () => {
      for (const phase of ['paused', 'loading']) {
        publish({ session: playing(anchor(1000), { phase }) });
        const { progress, wrapper } = mountProgress('spotify');

        vi.advanceTimersByTime(5000);
        await nextTick();

        expect(progress.currentPosition.value).toBe(1000);
        wrapper.unmount();
      }
    });

    it('stops at the duration', async () => {
      publish({ session: playing(anchor(199000)) });
      const { progress } = mountProgress('spotify');

      vi.advanceTimersByTime(5000);
      await nextTick();

      expect(progress.currentPosition.value).toBe(200000);
    });

    it('stops ticking once another source is selected', async () => {
      publish({ session: playing(anchor(1000)) });
      const { progress } = mountProgress('spotify');

      publish({ source: 'radio', session: playing(anchor(0)) });
      await nextTick();

      expect(progress.isPositionInitialized.value).toBe(false);
      expect(vi.getTimerCount()).toBe(0);
    });

    it('stops the timer when the consumer unmounts', () => {
      publish({ session: playing(anchor(1000)) });
      const { wrapper } = mountProgress('spotify');
      expect(vi.getTimerCount()).toBe(1);

      wrapper.unmount();

      expect(vi.getTimerCount()).toBe(0);
    });
  });

  describe('seekTo', () => {
    it('moves the bar at once and sends the seek command', async () => {
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      const seeking = progress.seekTo(120000);
      expect(progress.currentPosition.value).toBe(120000);
      await seeking;

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/audio/control/spotify',
        { command: 'seek', data: { position_ms: 120000 } },
        expect.anything(),
      );
    });

    it('gives the bar to the anchor the seek caused', async () => {
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      await progress.seekTo(120000);
      store.updatePosition({ source: 'spotify', session_id: 'session-1', position: anchor(119500) });
      await nextTick();

      expect(progress.currentPosition.value).toBe(119500);
    });

    it('keeps the target through a state that did not move the anchor', async () => {
      // Every `source/state` replaces the whole state with fresh objects: a
      // phase flip or a favorite landing before the seek's own anchor must not
      // snap the bar back to where it was.
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      await progress.seekTo(120000);
      publish({ session: playing(anchor(1000), { phase: 'paused', title: 'Renamed' }) });
      await nextTick();

      expect(progress.currentPosition.value).toBe(120000);
    });

    it('lets the target go after the hold when no anchor comes back', async () => {
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      await progress.seekTo(120000);
      expect(progress.currentPosition.value).toBe(120000);
      vi.advanceTimersByTime(1000);
      await nextTick();

      expect(progress.currentPosition.value).toBe(1000);
    });
  });
});
