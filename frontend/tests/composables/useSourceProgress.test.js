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
 *  - a seek or a skip shows its target at once, and only an anchor that agrees
 *    with it (or the end of the hold) gives the bar back: in a burst of
 *    presses, the anchor the first one caused arrives after the second one
 *    moved the target on (measured from Milo-iOS, 2026-09-25).
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
import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { SEEK_AGREEMENT_MS, positionAt, useSourceProgress } from '@/composables/useSourceProgress';
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
      // phase flip, a favorite or a late cover landing before the seek's own anchor must not
      // snap the bar back to where it was.
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      await progress.seekTo(120000);
      publish({ session: playing(anchor(1000), { phase: 'paused', artwork: 'https://img.example/late-cover.jpg' }) });
      await nextTick();

      expect(progress.currentPosition.value).toBe(120000);
    });

    it('lets the target go after the hold when no anchor comes back', async () => {
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce(ok({ status: 'success' }));

      await progress.seekTo(120000);
      vi.advanceTimersByTime(2900);
      await nextTick();
      expect(progress.currentPosition.value).toBe(120000);
      vi.advanceTimersByTime(100);
      await nextTick();

      expect(progress.currentPosition.value).toBe(1000);
    });

    it('lets the target go at once when the source refused the seek', async () => {
      publish({ session: playing(anchor(1000), { phase: 'paused' }) });
      const { progress } = mountProgress('spotify');
      apiCall.post.mockResolvedValueOnce({ ok: false, data: null, error: 'refused' });

      await progress.seekTo(120000);

      expect(progress.currentPosition.value).toBe(1000);
    });
  });

  describe('skip', () => {
    function podcast(position, overrides = {}) {
      publishState(store, {
        source: 'podcast', service: 'running', resume: null,
        session: makeSession({ duration_ms: 200000, position, ...overrides }),
        controls: ['pause', 'seek', 'skip'],
      });
    }

    function anchorArrives(ms, at = Date.now() / 1000) {
      store.updatePosition({ source: 'podcast', session_id: 'session-1', position: anchor(ms, { at }) });
      return nextTick();
    }

    beforeEach(() => {
      apiCall.post.mockResolvedValue(ok({ status: 'success' }));
    });

    it('sends the relative move, not a position, and shows where it lands', async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      const skipping = progress.skip(30);
      expect(progress.currentPosition.value).toBe(90000);
      await skipping;

      expect(apiCall.post).toHaveBeenCalledWith(
        '/api/audio/control/podcast',
        { command: 'skip', data: { seconds: 30 } },
        expect.anything(),
      );
    });

    it('adds up presses made before any anchor comes back', async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      await progress.skip(30);
      await progress.skip(30);
      await progress.skip(-15);

      expect(progress.currentPosition.value).toBe(105000);
      expect(apiCall.post.mock.calls.map(([, body]) => body.data.seconds)).toEqual([30, 30, -15]);
    });

    it("keeps the target through the first press's anchor, arriving after the second", async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      await progress.skip(30);
      await progress.skip(30);
      await anchorArrives(90000);

      expect(progress.currentPosition.value).toBe(120000);

      await anchorArrives(120400);
      expect(progress.currentPosition.value).toBe(120400);
    });

    it('moves the target with the clock while playing', async () => {
      podcast(anchor(60000));
      const { progress } = mountProgress('podcast');

      await progress.skip(30);
      vi.advanceTimersByTime(1000);
      await nextTick();

      expect(progress.currentPosition.value).toBe(91000);
      // The anchor the skip caused, stamped at the press: it agrees.
      await anchorArrives(90000, T0);
      expect(progress.currentPosition.value).toBe(91000);
      vi.advanceTimersByTime(3000);
      await nextTick();
      expect(progress.currentPosition.value).toBe(94000);
    });

    it('holds for three seconds after the last press, not the first', async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      await progress.skip(30);
      vi.advanceTimersByTime(2000);
      await progress.skip(30);
      vi.advanceTimersByTime(2000);
      await nextTick();
      expect(progress.currentPosition.value).toBe(120000);

      vi.advanceTimersByTime(1000);
      await nextTick();
      expect(progress.currentPosition.value).toBe(60000);
    });

    it('stops at zero and at the duration', async () => {
      podcast(anchor(10000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      await progress.skip(-15);
      expect(progress.currentPosition.value).toBe(0);

      await anchorArrives(0);
      await anchorArrives(190000);
      await progress.skip(30);
      expect(progress.currentPosition.value).toBe(200000);
    });

    it('holds the target still through a loading stretch, as the backend does', async () => {
      // The backend re-stamps its anchor on every phase change; a target that
      // kept its press-time stamp ran ahead by the whole load on resuming.
      podcast(anchor(60000));
      const { progress } = mountProgress('podcast');
      await progress.skip(30);
      await progress.skip(30);

      podcast(anchor(90000), { phase: 'loading' });
      await nextTick();
      vi.advanceTimersByTime(2000);
      podcast(anchor(90000, { at: T0 + 2 }));
      await nextTick();

      expect(progress.currentPosition.value).toBe(120000);
    });

    it('drops the target when the track changes', async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      await progress.skip(30);
      podcast(anchor(0), { phase: 'paused', title: 'Next track' });
      await nextTick();

      expect(progress.currentPosition.value).toBe(0);
    });

    it('keeps a later press when an earlier one is refused', async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');
      let refuseFirst;
      apiCall.post
        .mockImplementationOnce(() => new Promise((done) => { refuseFirst = done; }))
        .mockResolvedValueOnce(ok({ status: 'success' }));

      const first = progress.skip(30);
      await progress.skip(30);
      refuseFirst({ ok: false, data: null, error: 'refused' });
      await first;

      expect(progress.currentPosition.value).toBe(120000);
    });

    it("agrees with an anchor within the backend's own tolerance", () => {
      // An anchor the backend would not move is one the bar lands on.
      const here = dirname(fileURLToPath(import.meta.url));
      const base = readFileSync(resolve(here, '../../../backend/core/audio_source.py'), 'utf8');
      const declared = base.match(/^POSITION_TOLERANCE_MS = (\d+)$/m);
      expect(declared).not.toBeNull();
      expect(SEEK_AGREEMENT_MS).toBe(Number(declared[1]));
    });

    it('drops the target when another session takes over', async () => {
      podcast(anchor(60000), { phase: 'paused' });
      const { progress } = mountProgress('podcast');

      await progress.skip(30);
      podcast(anchor(5000), { phase: 'paused', id: 'session-2' });
      await nextTick();

      expect(progress.currentPosition.value).toBe(5000);
    });
  });
});
