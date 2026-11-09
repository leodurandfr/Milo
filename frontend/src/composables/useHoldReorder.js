// frontend/src/composables/useHoldReorder.js
import { onScopeDispose, ref, toValue, watch } from 'vue';
import { useTimer } from '@/composables/useTimer';

const HOLD_MS = 400;
// A finger that travels this far before the hold fires is scrolling.
const SLOP_PX = 8;
const EDGE_PX = 48;
const EDGE_MAX_PX_PER_FRAME = 16;
// A release's click follows its pointerup by a frame or two, and a touch may
// send none at all: the guard must not outlive the gesture by much.
const CLICK_GUARD_MS = 400;
// A finger landing this soon after a scroll is catching a fling: Chromium sends
// that touch's moves uncancellable, so a lift could never be dragged.
const SCROLL_SETTLE_MS = 100;

/** The rows of the list, in order: every one, movable or not. */
export const HOLD_ROW_SELECTOR = '[data-hold-row]';

/**
 * Where a lifted row would land: gap k is the space just before row k, gap
 * `rects.length` the one after the last row. -1 when landing there would leave
 * the order as it is (either side of the row's own place).
 *
 * @param {Object} drag
 * @param {number} drag.y - Where the lifted row's middle is, in the client
 *   coordinates the rects were measured in
 * @param {number} [drag.scrollDelta] - How far the list has scrolled since
 * @param {Array<{top: number, bottom: number}>} drag.rects - The rows at lift
 * @param {number} drag.from - The lifted row
 * @param {number} [drag.minGap] - The first gap a row may land in: none at or
 *   above the track playing
 * @returns {number}
 */
export function dropGap({ y, scrollDelta = 0, rects, from, minGap = 0 }) {
  const at = y + scrollDelta;
  const above = rects.filter((rect) => (rect.top + rect.bottom) / 2 < at).length;
  const gap = Math.min(Math.max(above, minGap), rects.length);
  return gap === from || gap === from + 1 ? -1 : gap;
}

/** The index the lifted row ends at once it lands in `gap`. */
export function gapIndex(gap, from) {
  return gap > from ? gap - 1 : gap;
}

/** The middle of `gap`, between the two rows it separates. */
export function gapLineY(gap, rects) {
  if (gap <= 0) return rects[0].top;
  if (gap >= rects.length) return rects[rects.length - 1].bottom;
  return (rects[gap - 1].bottom + rects[gap].top) / 2;
}

/**
 * How far to scroll this frame with the finger at `y` in a scroller spanning
 * `top`..`bottom`: nothing outside the edge zones, faster the deeper into one.
 */
export function edgeSpeed(y, top, bottom) {
  let depth = 0;
  if (y < top + EDGE_PX) depth = -(top + EDGE_PX - y);
  else if (y > bottom - EDGE_PX) depth = y - (bottom - EDGE_PX);
  if (!depth) return 0;
  const speed = EDGE_MAX_PX_PER_FRAME * Math.min(1, Math.abs(depth) / EDGE_PX);
  return Math.sign(depth) * Math.max(1, Math.round(speed));
}

function scrollParent(element) {
  for (let node = element.parentElement; node; node = node.parentElement) {
    if (/(auto|scroll)/.test(getComputedStyle(node).overflowY)) return node;
  }
  return null;
}

/**
 * A list reordered by holding a row, then dragging it: the row lifts after
 * HOLD_MS still, follows the finger, and the other rows stay where they are
 * while a line marks the gap it would land in.
 *
 * A tap plays through (released before the hold fires), and so does a scroll
 * (moved before it fires). Once lifted, the gesture is the drag's alone: no
 * scroll, no context menu, and the click that follows the release is swallowed.
 *
 * Each row is an element matching HOLD_ROW_SELECTOR inside `list`, whose
 * pointerdown calls `onPointerDown(event, index)`.
 *
 * @param {Object} options
 * @param {import('vue').MaybeRefOrGetter<HTMLElement|null>} options.list
 * @param {(index: number) => boolean} [options.canMove] - Whether a row lifts
 * @param {import('vue').MaybeRefOrGetter<number>} [options.minGap]
 * @param {(from: number, to: number) => void} options.onDrop - Called once on
 *   a release that moves the row, with where it was and where it now is
 */
export function useHoldReorder({ list, canMove = () => true, minGap = 0, onDrop }) {
  const timer = useTimer();
  const lifted = ref(-1);
  const offset = ref(0);
  // Where the drop line sits within the list, null while there is none.
  const line = ref(null);

  // 'armed' waits for the hold; 'spent' is a lift cancelled under the finger,
  // whose release still owes a swallowed click.
  let phase = 'idle';
  let pointerId = null;
  let element = null;
  let from = -1;
  let gap = -1;
  let startX = 0;
  let startY = 0;
  let lastY = 0;
  let rects = [];
  let listBox = null;
  let scroller = null;
  let scrollerBox = null;
  let scrollStart = 0;
  let scrolledAt = -Infinity;
  let holdTimer = null;
  let guardTimer = null;
  let frame = 0;

  function onPointerDown(event, index) {
    if (phase !== 'idle' || !event.isPrimary) return;
    if (event.pointerType === 'mouse' && event.button !== 0) return;
    if (!canMove(index)) return;
    if (Date.now() - scrolledAt < SCROLL_SETTLE_MS) return;
    phase = 'armed';
    pointerId = event.pointerId;
    element = event.currentTarget;
    from = index;
    startX = event.clientX;
    startY = lastY = event.clientY;
    listenGesture(true);
    holdTimer = timer.setTimeout(lift, HOLD_MS);
  }

  function lift() {
    holdTimer = null;
    const listEl = toValue(list);
    const rows = listEl ? [...listEl.querySelectorAll(HOLD_ROW_SELECTOR)] : [];
    if (!rows[from]) {
      reset();
      return;
    }
    rects = rows.map((row) => row.getBoundingClientRect());
    listBox = listEl.getBoundingClientRect();
    scrollerBox = scroller?.getBoundingClientRect() ?? null;
    scrollStart = scroller?.scrollTop ?? 0;
    try {
      element?.setPointerCapture?.(pointerId);
    } catch {
      // The pointer is already gone: its pointerup or pointercancel ends this.
    }
    phase = 'lifted';
    lifted.value = from;
    offset.value = 0;
    if (scroller) frame = requestAnimationFrame(autoScroll);
  }

  // The render window mounts more rows as the list scrolls: measured as they
  // come, in the coordinates of the lift, or the drag could never reach them.
  function measureAppended(scrollDelta) {
    const rows = toValue(list)?.querySelectorAll(HOLD_ROW_SELECTOR) ?? [];
    for (let i = rects.length; i < rows.length; i++) {
      const rect = rows[i].getBoundingClientRect();
      rects.push({
        top: rect.top + scrollDelta, bottom: rect.bottom + scrollDelta, left: rect.left, width: rect.width,
      });
    }
  }

  function update() {
    const scrollDelta = scroller ? scroller.scrollTop - scrollStart : 0;
    measureAppended(scrollDelta);
    const dy = lastY - startY;
    const row = rects[from];
    offset.value = dy + scrollDelta;
    gap = dropGap({ y: (row.top + row.bottom) / 2 + dy, scrollDelta, rects, from, minGap: toValue(minGap) });
    line.value = gap === -1 ? null : {
      top: gapLineY(gap, rects) - listBox.top,
      left: rects[0].left - listBox.left,
      width: rects[0].width,
    };
  }

  function autoScroll() {
    const top = Math.max(scrollerBox.top, 0);
    const bottom = Math.min(scrollerBox.bottom, window.innerHeight);
    const speed = edgeSpeed(lastY, top, bottom);
    if (speed) {
      const before = scroller.scrollTop;
      scroller.scrollTop = before + speed;
      if (scroller.scrollTop !== before) update();
    }
    frame = requestAnimationFrame(autoScroll);
  }

  function onMove(event) {
    if (event.pointerId !== pointerId) return;
    if (phase === 'armed') {
      if (Math.hypot(event.clientX - startX, event.clientY - startY) > SLOP_PX) reset();
    } else if (phase === 'lifted') {
      lastY = event.clientY;
      update();
    }
  }

  function onUp(event) {
    if (event.pointerId !== pointerId) return;
    const wasLifted = phase === 'lifted';
    const owesClick = wasLifted || phase === 'spent';
    const landed = wasLifted && gap !== -1 ? [from, gapIndex(gap, from)] : null;
    reset();
    if (owesClick) guardClick();
    if (landed) onDrop(...landed);
  }

  function onCancel(event) {
    if (event.pointerId === pointerId) reset();
  }

  /** Drops a lift without moving anything, the finger still down. */
  function cancel() {
    if (phase === 'armed') {
      reset();
    } else if (phase === 'lifted') {
      stopVisuals();
      phase = 'spent';
    }
  }

  function stopVisuals() {
    if (frame) cancelAnimationFrame(frame);
    frame = 0;
    lifted.value = -1;
    offset.value = 0;
    line.value = null;
    gap = -1;
  }

  function reset() {
    if (holdTimer !== null) timer.clear(holdTimer);
    holdTimer = null;
    stopVisuals();
    listenGesture(false);
    phase = 'idle';
    pointerId = null;
    element = null;
    rects = [];
  }

  function listenGesture(on) {
    const method = on ? 'addEventListener' : 'removeEventListener';
    document[method]('pointermove', onMove);
    document[method]('pointerup', onUp);
    document[method]('pointercancel', onCancel);
  }

  function swallowClick(event) {
    event.preventDefault();
    event.stopPropagation();
    unguard();
  }

  function guardClick() {
    document.addEventListener('click', swallowClick, true);
    guardTimer = timer.setTimeout(unguard, CLICK_GUARD_MS);
  }

  function unguard() {
    document.removeEventListener('click', swallowClick, true);
    if (guardTimer !== null) timer.clear(guardTimer);
    guardTimer = null;
  }

  // On the list for as long as it is mounted, not from the lift: both engines
  // decide at touchstart whether a touch's moves can be cancelled at all, and a
  // listener added mid-touch would watch the browser scroll and cancel the drag.
  const preventWhileHeld = (event) => {
    if (phase !== 'idle' && event.cancelable) event.preventDefault();
  };
  const preventWhileLifted = (event) => {
    if ((phase === 'lifted' || phase === 'spent') && event.cancelable) event.preventDefault();
  };
  // A scroll under a held row is a fling the finger did not stop; under a
  // lifted one (a wheel, the auto-scroll) it moves the gap without a pointermove.
  function onScroll() {
    scrolledAt = Date.now();
    if (phase === 'armed') reset();
    else if (phase === 'lifted') update();
  }
  function listenList(el, on) {
    if (!el) return;
    const method = on ? 'addEventListener' : 'removeEventListener';
    el[method]('touchmove', preventWhileLifted, { passive: false });
    el[method]('contextmenu', preventWhileHeld);
    el[method]('dragstart', preventWhileHeld);
    scroller?.[method]('scroll', onScroll, { passive: true });
  }
  let listened = null;
  watch(() => toValue(list), (el) => {
    listenList(listened, false);
    listened = el ?? null;
    scroller = listened ? scrollParent(listened) : null;
    listenList(listened, true);
  }, { immediate: true, flush: 'post' });

  onScopeDispose(() => {
    reset();
    unguard();
    listenList(listened, false);
    listened = null;
    scroller = null;
  });

  function rowStyle(index) {
    return lifted.value === index ? { transform: `translateY(${offset.value}px)` } : null;
  }

  return { lifted, offset, line, rowStyle, onPointerDown, cancel };
}
