// frontend/tests/composables/useHoldReorder.test.js
/**
 * useHoldReorder moves a row of the music library's queue by holding it, then
 * dragging it. What breaks, silently:
 *
 * - the gap: a drop line above the track playing is a move the backend
 *   refuses, and a line on either side of the row's own place promises a move
 *   that changes nothing;
 * - the gesture: a tap that lifts plays nothing, a scroll that lifts moves a
 *   row nobody meant to move, and a drop whose click is let through plays the
 *   track that was just moved;
 * - the release: a drag reported more than once sends one `move_in_queue` per
 *   report, each against a queue the previous one already changed;
 * - the scroll: rows the render window mounts during a drag must be reachable,
 *   a scroll with no pointer move must still move the gap, and a finger that
 *   catches a fling must not lift (its moves cannot be cancelled).
 *
 * A host component is mounted for the lifecycle and for elements to dispatch
 * pointer events on; only the composable's state and callbacks are asserted.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import {
  dropGap, edgeSpeed, gapIndex, gapLineY, useHoldReorder, HOLD_ROW_SELECTOR,
} from '@/composables/useHoldReorder';

const ROW = 50;
const rectsOf = (n) => Array.from({ length: n }, (_, i) =>
  ({ top: i * ROW, bottom: (i + 1) * ROW, left: 0, width: 300 }));
// The middle of row i.
const mid = (i) => i * ROW + ROW / 2;

describe('dropGap', () => {
  const rects = rectsOf(5);

  it('lands the row in the gap its middle has crossed into', () => {
    expect(dropGap({ y: mid(3) + 1, rects, from: 1 })).toBe(4);
    expect(dropGap({ y: mid(1) - 1, rects, from: 3 })).toBe(1);
  });

  it('draws no line on either side of the row\'s own place', () => {
    expect(dropGap({ y: mid(2) - 1, rects, from: 2 })).toBe(-1);
    expect(dropGap({ y: mid(2) + 1, rects, from: 2 })).toBe(-1);
  });

  it('offers the gap after the last row', () => {
    expect(dropGap({ y: 1000, rects, from: 1 })).toBe(5);
  });

  it('never lands at or above the track playing', () => {
    // Row 0 plays: dragged to the very top, row 3 stops just below it.
    expect(dropGap({ y: -200, rects, from: 3, minGap: 1 })).toBe(1);
    // Row 1 can go no higher than where it is: no move at all.
    expect(dropGap({ y: -200, rects, from: 1, minGap: 1 })).toBe(-1);
  });

  it('counts the scroll since the lift', () => {
    // The row has barely moved, but the list scrolled two rows up under it.
    expect(dropGap({ y: mid(1) - 10, scrollDelta: 2 * ROW, rects, from: 1 })).toBe(3);
    expect(dropGap({ y: mid(1) - 10, rects, from: 1 })).toBe(-1);
  });
});

describe('gapIndex', () => {
  it('names the index the row ends at', () => {
    expect(gapIndex(4, 1)).toBe(3);
    expect(gapIndex(5, 1)).toBe(4);
    expect(gapIndex(1, 3)).toBe(1);
  });
});

describe('gapLineY', () => {
  it('sits between the two rows, or on the outer edge', () => {
    const rects = [{ top: 0, bottom: 50 }, { top: 54, bottom: 100 }];
    expect(gapLineY(1, rects)).toBe(52);
    expect(gapLineY(0, rects)).toBe(0);
    expect(gapLineY(2, rects)).toBe(100);
  });
});

describe('edgeSpeed', () => {
  it('scrolls only near an edge, faster the deeper', () => {
    expect(edgeSpeed(400, 0, 800)).toBe(0);
    expect(edgeSpeed(10, 0, 800)).toBeLessThan(0);
    expect(edgeSpeed(790, 0, 800)).toBeGreaterThan(edgeSpeed(760, 0, 800));
    expect(edgeSpeed(900, 0, 800)).toBe(edgeSpeed(800, 0, 800));
  });
});

describe('useHoldReorder', () => {
  const N = 5;
  let wrapper;
  let hold;
  let onDrop;
  let played;
  let rows;
  let count;
  let scroller;

  // The rows' client rects, wherever the scroller has taken them.
  function measureRows() {
    rows = [...wrapper.element.querySelectorAll(HOLD_ROW_SELECTOR)];
    rows.forEach((row, i) => {
      row.getBoundingClientRect = () => {
        const { top, bottom, left, width } = rectsOf(i + 1)[i];
        return { top: top - scroller.scrollTop, bottom: bottom - scroller.scrollTop, left, width };
      };
    });
  }

  async function mountList({ minGap = 1 } = {}) {
    onDrop = vi.fn();
    played = vi.fn();
    count = ref(N);
    const listRef = ref(null);
    wrapper = mount(defineComponent({
      setup() {
        hold = useHoldReorder({
          list: listRef,
          canMove: (i) => i !== 0,
          minGap,
          onDrop,
        });
        return () => h('div', { style: 'overflow-y: auto' }, [
          h('div', { ref: listRef }, Array.from({ length: count.value }, (_, i) => h('div', {
            'data-hold-row': '',
            onPointerdown: (e) => hold.onPointerDown(e, i),
            onClick: () => played(i),
          }))),
        ]);
      },
    }), { attachTo: document.body });
    scroller = wrapper.element;
    Object.defineProperty(scroller, 'scrollTop', { value: 0, writable: true });
    // Tall enough that no test's pointer reaches an auto-scroll edge.
    scroller.getBoundingClientRect = () => ({ top: -1000, bottom: 10000 });
    const list = scroller.firstElementChild;
    list.getBoundingClientRect = () => ({ top: -scroller.scrollTop, left: 0, width: 300 });
    measureRows();
    // The list's listeners go on once it is rendered.
    await nextTick();
  }

  function scrollTo(top) {
    scroller.scrollTop = top;
    scroller.dispatchEvent(new Event('scroll'));
  }

  const pointer = (type, row, y, x = 10) => rows[row].dispatchEvent(new PointerEvent(type, {
    bubbles: true, cancelable: true, pointerId: 1, isPrimary: true, pointerType: 'touch',
    button: 0, clientX: x, clientY: y,
  }));
  const click = (row) => rows[row].dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    wrapper?.unmount();
    vi.useRealTimers();
  });

  it('lets a release before the hold through as a tap', async () => {
    await mountList();
    pointer('pointerdown', 2, mid(2));
    vi.advanceTimersByTime(399);
    pointer('pointerup', 2, mid(2));
    click(2);
    vi.advanceTimersByTime(1000);

    expect(hold.lifted.value).toBe(-1);
    expect(onDrop).not.toHaveBeenCalled();
    expect(played).toHaveBeenCalledWith(2);
  });

  it('takes a move before the hold for a scroll', async () => {
    await mountList();
    pointer('pointerdown', 2, mid(2));
    pointer('pointermove', 2, mid(2) + 9);
    vi.advanceTimersByTime(500);

    expect(hold.lifted.value).toBe(-1);
    pointer('pointermove', 2, mid(4));
    pointer('pointerup', 2, mid(4));
    expect(onDrop).not.toHaveBeenCalled();
  });

  it('never lifts the track playing', async () => {
    await mountList();
    pointer('pointerdown', 0, mid(0));
    vi.advanceTimersByTime(500);
    expect(hold.lifted.value).toBe(-1);
  });

  it('reports a hold and drag once, where the row landed, and eats its click', async () => {
    await mountList();
    pointer('pointerdown', 1, mid(1));
    vi.advanceTimersByTime(400);
    expect(hold.lifted.value).toBe(1);

    for (const y of [mid(1) + 30, mid(2) + 10, mid(3) + 10]) pointer('pointermove', 1, y);
    expect(hold.offset.value).toBe(mid(3) + 10 - mid(1));
    expect(hold.line.value).toEqual({ top: 4 * ROW, left: 0, width: 300 });
    expect(onDrop).not.toHaveBeenCalled();

    pointer('pointerup', 1, mid(3) + 10);
    click(1);

    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onDrop).toHaveBeenCalledWith(1, 3);
    expect(played).not.toHaveBeenCalled();
    expect(hold.lifted.value).toBe(-1);
    expect(hold.line.value).toBeNull();

    // The guard is for that click alone.
    click(1);
    expect(played).toHaveBeenCalledWith(1);
  });

  it('drops nothing on a release back in place, and still eats the click', async () => {
    await mountList();
    pointer('pointerdown', 2, mid(2));
    vi.advanceTimersByTime(400);
    pointer('pointermove', 2, mid(2) + 10);
    expect(hold.line.value).toBeNull();
    pointer('pointerup', 2, mid(2) + 10);
    click(2);

    expect(onDrop).not.toHaveBeenCalled();
    expect(played).not.toHaveBeenCalled();
  });

  it('drops nothing when the browser takes the pointer mid-drag', async () => {
    await mountList();
    pointer('pointerdown', 1, mid(1));
    vi.advanceTimersByTime(400);
    pointer('pointermove', 1, mid(3));
    pointer('pointercancel', 1, mid(3));
    pointer('pointermove', 1, mid(4));
    pointer('pointerup', 1, mid(4));

    expect(onDrop).not.toHaveBeenCalled();
    expect(hold.lifted.value).toBe(-1);
  });

  it('drops nothing once cancelled under the finger, and still eats the click', async () => {
    await mountList();
    pointer('pointerdown', 1, mid(1));
    vi.advanceTimersByTime(400);
    pointer('pointermove', 1, mid(3));
    hold.cancel();
    expect(hold.lifted.value).toBe(-1);
    expect(hold.line.value).toBeNull();

    pointer('pointermove', 1, mid(4));
    pointer('pointerup', 1, mid(4));
    click(1);
    expect(onDrop).not.toHaveBeenCalled();
    expect(played).not.toHaveBeenCalled();
  });

  it('blocks the scroll and the context menu only while a row is held', async () => {
    await mountList();
    const touchmove = () => {
      const event = new Event('touchmove', { bubbles: true, cancelable: true });
      rows[2].dispatchEvent(event);
      return event.defaultPrevented;
    };
    const contextmenu = () => {
      const event = new MouseEvent('contextmenu', { bubbles: true, cancelable: true });
      rows[2].dispatchEvent(event);
      return event.defaultPrevented;
    };

    expect(touchmove()).toBe(false);
    expect(contextmenu()).toBe(false);
    pointer('pointerdown', 2, mid(2));
    expect(contextmenu()).toBe(true);
    vi.advanceTimersByTime(400);
    expect(touchmove()).toBe(true);
    pointer('pointerup', 2, mid(2));
    expect(touchmove()).toBe(false);
    expect(contextmenu()).toBe(false);
  });

  it('reaches the rows mounted after the lift', async () => {
    await mountList();
    pointer('pointerdown', 1, mid(1));
    vi.advanceTimersByTime(400);

    // The auto-scroll takes the list three rows up, the render window mounts five more.
    scrollTo(3 * ROW);
    count.value = 10;
    await nextTick();
    measureRows();
    // The row's middle over row 8's lower half, counted from the lift.
    const y = mid(1) + (mid(8) + 10 - mid(1) - 3 * ROW);
    pointer('pointermove', 1, y);
    expect(hold.line.value).toEqual({ top: 9 * ROW, left: 0, width: 300 });
    pointer('pointerup', 1, y);

    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onDrop).toHaveBeenCalledWith(1, 8);
  });

  it('moves the gap with a scroll the pointer did not make', async () => {
    await mountList();
    pointer('pointerdown', 1, mid(1));
    vi.advanceTimersByTime(400);
    pointer('pointermove', 1, mid(1) + 10);
    expect(hold.line.value).toBeNull();

    // A wheel under a still mouse: the row stays under it, the list moves on.
    scrollTo(2 * ROW);
    expect(hold.offset.value).toBe(10 + 2 * ROW);
    pointer('pointerup', 1, mid(1) + 10);

    expect(onDrop).toHaveBeenCalledWith(1, 3);
  });

  it('never lifts under a finger that caught a fling', async () => {
    await mountList();
    scrollTo(10);
    pointer('pointerdown', 2, mid(2));
    vi.advanceTimersByTime(500);
    expect(hold.lifted.value).toBe(-1);
    pointer('pointerup', 2, mid(2));

    // Still scrolling once held.
    vi.advanceTimersByTime(200);
    pointer('pointerdown', 2, mid(2));
    vi.advanceTimersByTime(100);
    scrollTo(20);
    vi.advanceTimersByTime(400);
    expect(hold.lifted.value).toBe(-1);
    pointer('pointerup', 2, mid(2));

    // A list at rest lifts again.
    vi.advanceTimersByTime(200);
    pointer('pointerdown', 2, mid(2));
    vi.advanceTimersByTime(400);
    expect(hold.lifted.value).toBe(2);
  });

  it('lets go of the document when unmounted mid-drag', async () => {
    await mountList();
    const removed = vi.spyOn(document, 'removeEventListener');
    pointer('pointerdown', 1, mid(1));
    vi.advanceTimersByTime(400);
    pointer('pointermove', 1, mid(3));
    const leftRows = rows;
    wrapper.unmount();
    wrapper = null;

    const types = removed.mock.calls.map(([type]) => type);
    expect(types).toEqual(expect.arrayContaining(['pointermove', 'pointerup', 'pointercancel']));
    leftRows[1].dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true }));
    document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1, isPrimary: true }));
    expect(onDrop).not.toHaveBeenCalled();
    removed.mockRestore();
  });
});
