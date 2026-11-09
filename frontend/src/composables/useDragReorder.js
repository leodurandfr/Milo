// frontend/src/composables/useDragReorder.js
import { computed, onScopeDispose, ref, toValue } from 'vue';

const IDLE = { index: -1, startY: 0, currentY: 0, itemHeight: 0 };

/**
 * A list reordered by dragging a row's grip, one row-height step at a time.
 *
 * While a drag is in flight the rows are a local copy taken at the press, so a
 * list that changes underneath (a reload landing mid-drag) neither moves the
 * row under the finger nor is moved by it. The copy is dropped on release and
 * the rows are the caller's list again — whatever `onDrop` made of it.
 *
 * Each row sits in an element of class `drag-item`, whose height is the step.
 *
 * @param {import('vue').MaybeRefOrGetter<Array>} items - The rows on screen
 * @param {Object} options
 * @param {(from: number, to: number, item: *) => void} options.onDrop - Called
 *   once on release, with where the row started and where it ended, and only
 *   when those differ
 */
export function useDragReorder(items, { onDrop }) {
  const dragState = ref({ ...IDLE });
  const draft = ref(null);
  let from = -1;

  const dragging = computed(() => dragState.value.index !== -1);
  const rows = computed(() => draft.value ?? toValue(items));

  function getDragItemStyle(index) {
    if (dragState.value.index !== index) return {};
    const offsetY = dragState.value.currentY - dragState.value.startY;
    return {
      transform: `translateY(${offsetY}px) scale(1.02)`,
      zIndex: 10,
      position: 'relative',
      transition: 'none',
    };
  }

  function onGripDown(event, index) {
    const wrapper = event.target?.closest?.('.drag-item');
    draft.value = [...toValue(items)];
    from = index;
    dragState.value = {
      index,
      startY: event.clientY,
      currentY: event.clientY,
      itemHeight: wrapper ? wrapper.offsetHeight : 0,
    };
    listen(true);
  }

  function step(to) {
    const arr = draft.value;
    const dragged = dragState.value.index;
    [arr[dragged], arr[to]] = [arr[to], arr[dragged]];
    dragState.value.index = to;
    dragState.value.startY += (to - dragged) * dragState.value.itemHeight;
  }

  function onDragMove(event) {
    if (!dragging.value) return;
    dragState.value.currentY = event.clientY;
    const deltaY = event.clientY - dragState.value.startY;
    const threshold = dragState.value.itemHeight * 0.5;
    const dragged = dragState.value.index;

    if (deltaY > threshold && dragged < draft.value.length - 1) {
      step(dragged + 1);
    } else if (deltaY < -threshold && dragged > 0) {
      step(dragged - 1);
    }
  }

  function onDragEnd() {
    if (!dragging.value) return;
    const to = dragState.value.index;
    const item = draft.value[to];
    dragState.value = { ...IDLE };
    listen(false);
    // Before the copy goes: a caller that reorders its own list here hands
    // the rows straight back in the new order, with no frame of the old one.
    if (to !== from) onDrop(from, to, item);
    draft.value = null;
  }

  function listen(on) {
    const method = on ? 'addEventListener' : 'removeEventListener';
    document[method]('pointermove', onDragMove);
    document[method]('pointerup', onDragEnd);
    document[method]('pointercancel', onDragEnd);
  }

  onScopeDispose(() => listen(false));

  return { rows, dragState, dragging, getDragItemStyle, onGripDown };
}
