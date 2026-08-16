// frontend/tests/composables/useVolumeThrottle.test.js
/**
 * useVolumeThrottle rate-limits a volume gesture into at most one call per
 * throttle window, plus one trailing call carrying the value the gesture ended
 * on. `flush()` exists so a slider release sends that final value immediately
 * instead of waiting out the trailing timer.
 *
 * The invariant these cases pin: **the value a gesture ends on is emitted
 * exactly once**. It is not a preference — the zone slider's consumer
 * (MultiroomControl.handleVolumeChange) reads a zone change as a DELTA against
 * the average captured when the drag began, and clears that capture only after
 * `applyZoneVolumeDelta` has awaited. A second synchronous emit therefore reads
 * the same `startAvg` and applies the same delta again: a zone dragged from
 * -30 dB to -20 dB lands at -10 dB, audibly.
 *
 * That second emit came from `lastArgs` surviving the immediate branch — the
 * trailing timer and `flush()` were its only clears. So it fired whenever the
 * last `pointermove` before release took the rising edge (guaranteed on a small
 * single-gesture adjustment, since `flush()` does not refresh `lastCallTime`),
 * and not on the tap or long-pause paths one would try first.
 *
 * Pure logic: no mocks beyond the clock, no DOM assertions. A host component is
 * mounted only to give `onUnmounted` a lifecycle to hang on.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { useVolumeThrottle, useVolumeThrottleMap } from '@/composables/useVolumeThrottle';

/** MEDIUM preset — the zone slider's. Restated here only to drive the clock. */
const MEDIUM_THROTTLE_MS = 80;
const MEDIUM_FINAL_MS = 300;

/** Mount a host exposing the composable, so its onUnmounted has an instance. */
function mountThrottle(factory) {
  let api;
  const Host = defineComponent({
    setup() {
      api = factory();
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { ...api, wrapper };
}

describe('useVolumeThrottle', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    // lastCallTime seeds at 0, so the clock must sit far past the throttle
    // window for the first call to take the rising edge — as it does in life.
    vi.setSystemTime(new Date('2026-08-16T12:00:00Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('emits once when the last drag move took the rising edge and release flushes', () => {
    // The audible bug: RangeSlider emits `input` from handleDrag and `change`
    // from stopDrag in the same tick, and MultiroomItem answers `change` with
    // flushZoneVolume(). Both must not reach the callback.
    const callback = vi.fn();
    const { throttledFn, flush } = mountThrottle(() => useVolumeThrottle(callback, 'MEDIUM'));

    throttledFn(-20); // rising edge: fires immediately
    expect(callback).toHaveBeenCalledTimes(1);

    flush(); // release, same tick, nothing new to send
    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenLastCalledWith(-20);
  });

  it('does not re-send the immediate value when the trailing timer fires', () => {
    const callback = vi.fn();
    const { throttledFn } = mountThrottle(() => useVolumeThrottle(callback, 'MEDIUM'));

    throttledFn(-20);
    vi.advanceTimersByTime(MEDIUM_FINAL_MS + 1);

    expect(callback).toHaveBeenCalledTimes(1);
  });

  it('still flushes a value the throttle window swallowed', () => {
    // The reason flush() exists: the release value must survive even when the
    // move that carried it landed inside the window.
    const callback = vi.fn();
    const { throttledFn, flush } = mountThrottle(() => useVolumeThrottle(callback, 'MEDIUM'));

    throttledFn(-20); // rising edge
    vi.advanceTimersByTime(MEDIUM_THROTTLE_MS - 10);
    throttledFn(-18); // swallowed by the window
    flush();

    expect(callback).toHaveBeenCalledTimes(2);
    expect(callback).toHaveBeenLastCalledWith(-18);
  });

  it('still sends a swallowed value through the trailing timer when nothing flushes', () => {
    const callback = vi.fn();
    const { throttledFn } = mountThrottle(() => useVolumeThrottle(callback, 'MEDIUM'));

    throttledFn(-20);
    vi.advanceTimersByTime(MEDIUM_THROTTLE_MS - 10);
    throttledFn(-18);
    vi.advanceTimersByTime(MEDIUM_FINAL_MS + 1);

    expect(callback).toHaveBeenCalledTimes(2);
    expect(callback).toHaveBeenLastCalledWith(-18);
  });
});

describe('useVolumeThrottleMap', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-08-16T12:00:00Z'));
  });
  afterEach(() => vi.useRealTimers());

  it('does not re-send the immediate value when the trailing timer fires', () => {
    // Same shape as the zone slider's immediate branch; this one has no flush,
    // so the duplicate lands one `final` later instead of in the same tick.
    const callback = vi.fn();
    const { getThrottledFn } = mountThrottle(() =>
      useVolumeThrottleMap((key) => (value) => callback(key, value), 'FAST')
    );

    getThrottledFn('dc:a6:32:7e:d3:43')(-20);
    vi.advanceTimersByTime(1000);

    expect(callback).toHaveBeenCalledTimes(1);
    expect(callback).toHaveBeenCalledWith('dc:a6:32:7e:d3:43', -20);
  });

  it('keeps one throttle state per key', () => {
    const callback = vi.fn();
    const { getThrottledFn } = mountThrottle(() =>
      useVolumeThrottleMap((key) => (value) => callback(key, value), 'FAST')
    );

    getThrottledFn('aa:aa:aa:aa:aa:aa')(-20);
    getThrottledFn('bb:bb:bb:bb:bb:bb')(-30);
    vi.advanceTimersByTime(1000);

    expect(callback).toHaveBeenCalledTimes(2);
    expect(callback).toHaveBeenCalledWith('aa:aa:aa:aa:aa:aa', -20);
    expect(callback).toHaveBeenCalledWith('bb:bb:bb:bb:bb:bb', -30);
  });
});
