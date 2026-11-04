/**
 * useVolumeHold drives the Dock's discrete volume buttons (the phone's portrait
 * dock): a tap is one step, a hold repeats. If this fails, a second finger laid
 * on the dock during a hold leaves a repeat running after both fingers lift, and
 * every later hold moves the room's level twice as fast — three times after a
 * second double press — until the tab reloads.
 *
 * `adjustVolume` is the boundary (the call into the volume store); the real
 * `useTimer` runs under fake timers, so its handles carry the proof. A host
 * component is mounted only to give the composable a lifecycle; nothing is
 * rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useVolumeHold } from '@/composables/useVolumeHold';

function mountHold() {
  const adjustVolume = vi.fn();
  let hold;
  mount(defineComponent({
    setup() {
      hold = useVolumeHold({
        adjustVolume,
        gestureHasMoved: ref(false),
        gestureStartPosition: ref({ x: 0, y: 0 }),
        getEventX: (e) => e.clientX,
        getEventY: (e) => e.clientY,
      });
      return () => h('div');
    },
  }));
  return { hold, adjustVolume };
}

const touch = (type, pointerId, isPrimary = pointerId === 1) => ({
  type, pointerId, isPrimary, pointerType: 'touch', clientX: 10, clientY: 10,
});

function holdFor(hold, delta, ms, pointerId = 1) {
  hold.onVolumeHoldStart(delta, touch('pointerdown', pointerId));
  vi.advanceTimersByTime(ms);
  hold.onVolumeHoldEnd(touch('pointerup', pointerId));
}

describe('useVolumeHold', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('steps once on a tap and repeats on a hold', () => {
    const { hold, adjustVolume } = mountHold();

    holdFor(hold, 1, 100);
    expect(adjustVolume).toHaveBeenCalledTimes(1);

    adjustVolume.mockClear();
    holdFor(hold, 1, 1000);
    expect(adjustVolume.mock.calls.length).toBeGreaterThan(1);
  });

  it('a second press during a hold leaves no repeat running after release', () => {
    const { hold, adjustVolume } = mountHold();
    holdFor(hold, 1, 1000);
    const freshHoldCalls = adjustVolume.mock.calls.length;
    expect(freshHoldCalls).toBeGreaterThan(1);

    hold.onVolumeHoldStart(1, touch('pointerdown', 1));
    hold.onVolumeHoldStart(-1, touch('pointerdown', 2));
    vi.advanceTimersByTime(500);
    hold.onVolumeHoldStart(1, touch('pointerdown', 3));
    vi.advanceTimersByTime(500);
    hold.onVolumeHoldEnd(touch('pointerup', 2));
    hold.onVolumeHoldEnd(touch('pointerup', 3));
    hold.onVolumeHoldEnd(touch('pointerup', 1));
    expect(vi.getTimerCount()).toBe(0);

    adjustVolume.mockClear();
    holdFor(hold, 1, 1000);
    expect(adjustVolume).toHaveBeenCalledTimes(freshHoldCalls);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('keeps one repeat when the same pointer presses twice without a release', () => {
    const { hold, adjustVolume } = mountHold();
    holdFor(hold, 1, 1000);
    const freshHoldCalls = adjustVolume.mock.calls.length;

    adjustVolume.mockClear();
    hold.onVolumeHoldStart(1, touch('pointerdown', 1));
    hold.onVolumeHoldStart(1, touch('pointerdown', 1));
    vi.advanceTimersByTime(1000);
    hold.onVolumeHoldEnd(touch('pointerup', 1));

    expect(adjustVolume).toHaveBeenCalledTimes(freshHoldCalls);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a second finger lifting does not end the first finger\'s hold', () => {
    const { hold, adjustVolume } = mountHold();
    holdFor(hold, 1, 1000);
    const freshHoldCalls = adjustVolume.mock.calls.length;

    adjustVolume.mockClear();
    hold.onVolumeHoldStart(1, touch('pointerdown', 1));
    vi.advanceTimersByTime(100);
    hold.onVolumeHoldStart(-1, touch('pointerdown', 2));
    hold.onVolumeHoldEnd(touch('pointerup', 2));
    vi.advanceTimersByTime(900);
    hold.onVolumeHoldEnd(touch('pointerup', 1));

    expect(adjustVolume).toHaveBeenCalledTimes(freshHoldCalls);
    expect(adjustVolume.mock.calls.every(([delta]) => delta === 1)).toBe(true);
  });

  it('answers a press made while another touch rests elsewhere on the screen', () => {
    const { hold, adjustVolume } = mountHold();
    holdFor(hold, 1, 1000);
    const freshHoldCalls = adjustVolume.mock.calls.length;

    adjustVolume.mockClear();
    holdFor(hold, -1, 100, 7);
    expect(adjustVolume).toHaveBeenCalledTimes(1);

    adjustVolume.mockClear();
    holdFor(hold, -1, 1000, 7);
    expect(adjustVolume).toHaveBeenCalledTimes(freshHoldCalls);
  });

  it('ends the press when the dock gesture abandons it without an event', () => {
    const { hold, adjustVolume } = mountHold();

    hold.onVolumeHoldStart(1, touch('pointerdown', 1));
    vi.advanceTimersByTime(600);
    hold.onVolumeHoldEnd();
    const callsAtEnd = adjustVolume.mock.calls.length;
    vi.advanceTimersByTime(1000);

    expect(adjustVolume).toHaveBeenCalledTimes(callsAtEnd);
    expect(vi.getTimerCount()).toBe(0);
  });
});
