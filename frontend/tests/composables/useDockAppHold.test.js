/**
 * useDockAppHold closes the active source when its Dock icon is held. If this
 * fails, a hold on the phone or the kiosk is cut short by any other finger
 * lifting from the screen, and the source the user is holding never closes —
 * or a second press arms a timer that closes it after the first one let go.
 *
 * `onCloseActive` is the boundary (the call that changes the source); the real
 * `useTimer` runs under fake timers. A host component is mounted only to give
 * the composable a lifecycle; nothing is rendered or asserted on the DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useDockAppHold } from '@/composables/useDockAppHold';

const ACTIVE = 'spotify';

function mountHold() {
  const onCloseActive = vi.fn();
  const gestureStartPosition = ref({ x: 0, y: 0 });
  let hold;
  mount(defineComponent({
    setup() {
      hold = useDockAppHold({
        isActiveSource: (id) => id === ACTIVE,
        onCloseActive,
        gestureHasMoved: ref(false),
        gestureStartPosition,
        getEventX: (e) => e.clientX,
        getEventY: (e) => e.clientY,
      });
      return () => h('div');
    },
  }));
  return { hold, onCloseActive, gestureStartPosition };
}

const touch = (type, pointerId, isPrimary = pointerId === 1) => ({
  type, pointerId, isPrimary, pointerType: 'touch', clientX: 10, clientY: 10,
});

describe('useDockAppHold', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('closes the active source on a hold, never on a tap', () => {
    const { hold, onCloseActive } = mountHold();

    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 1));
    vi.advanceTimersByTime(100);
    hold.onAppHoldEnd(touch('pointerup', 1));
    vi.advanceTimersByTime(1000);
    expect(onCloseActive).not.toHaveBeenCalled();

    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 2));
    vi.advanceTimersByTime(600);
    expect(onCloseActive).toHaveBeenCalledTimes(1);
    expect(hold.consumeHoldFired()).toBe(true);
  });

  it('a second finger lifting does not end the first finger\'s hold', () => {
    const { hold, onCloseActive } = mountHold();

    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 1));
    vi.advanceTimersByTime(100);
    hold.onAppHoldStart('radio', touch('pointerdown', 2));
    hold.onAppHoldEnd(touch('pointerup', 2));
    vi.advanceTimersByTime(500);

    expect(onCloseActive).toHaveBeenCalledTimes(1);
  });

  it('a second press arms no timer that cuts a later hold short', () => {
    const { hold, onCloseActive } = mountHold();

    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 1));
    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 2));
    vi.advanceTimersByTime(100);
    hold.onAppHoldEnd(touch('pointerup', 1));
    vi.advanceTimersByTime(100);
    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 3));
    vi.advanceTimersByTime(400);
    expect(onCloseActive).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);
    expect(onCloseActive).toHaveBeenCalledTimes(1);
  });

  it('a touch whose release never arrived does not lock out the next one', () => {
    const { hold, onCloseActive } = mountHold();

    hold.onAppHoldStart('radio', touch('pointerdown', 1));
    vi.advanceTimersByTime(100);
    hold.onAppHoldStart(ACTIVE, touch('pointerdown', 2, true));
    vi.advanceTimersByTime(600);

    expect(onCloseActive).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('a pointer that holds nothing leaves the gesture state it shares alone', () => {
    const { hold, gestureStartPosition } = mountHold();
    gestureStartPosition.value = { x: 5, y: 5 };

    hold.onAppHoldEnd(touch('pointerup', 9));

    expect(gestureStartPosition.value).toEqual({ x: 5, y: 5 });
  });
});
