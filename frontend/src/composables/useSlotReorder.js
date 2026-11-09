// frontend/src/composables/useSlotReorder.js
import { computed, onScopeDispose, ref, toValue } from 'vue';

/**
 * A list reordered by dragging its items between the slots they occupy, in
 * whatever layout the container gives them — a grid or a column alike.
 *
 * Unlike useDragReorder (one row-height step at a time, a column only), the
 * slots are measured at the press: the dragged item follows the pointer, takes
 * the slot its center is nearest to, and the items in between slide one slot
 * along to make room. Nothing reaches the caller's list before the release, so
 * the DOM keeps its order for the whole drag and only transforms move.
 *
 * The items are the container's direct children of class `drag-item`, in the
 * order of the list; the container must be positioned (their offsetParent).
 *
 * @param {import('vue').Ref<HTMLElement|null>} container - The list's element
 * @param {Object} options
 * @param {(from: number, to: number) => void} options.onDrop - Called once on
 *   release, only when the item ended elsewhere than it started
 */
export function useSlotReorder(container, { onDrop }) {
  // Container-relative slot boxes in CSS pixels, and the scale between those
  // and client pixels (the kiosk scales #app), measured at the press.
  let slots = [];
  let scale = 1;
  let grab = { x: 0, y: 0 };
  let landingFrame = 0;

  const from = ref(-1);
  const to = ref(-1);
  const pointer = ref({ x: 0, y: 0 });
  // The frame after a drop: the list has its new order, the dropped item
  // still sits under the pointer and nothing else may animate yet.
  const landing = ref(null);

  const dragging = computed(() => from.value !== -1);

  function items() {
    const el = toValue(container);
    return el ? [...el.children].filter(child => child.classList.contains('drag-item')) : [];
  }

  function local(event) {
    const box = toValue(container).getBoundingClientRect();
    return { x: (event.clientX - box.left) / scale, y: (event.clientY - box.top) / scale };
  }

  function nearestSlot() {
    const own = slots[from.value];
    const cx = pointer.value.x - grab.x + own.width / 2;
    const cy = pointer.value.y - grab.y + own.height / 2;
    let best = from.value;
    let bestDistance = Infinity;
    slots.forEach((slot, index) => {
      const distance = (slot.left + slot.width / 2 - cx) ** 2 + (slot.top + slot.height / 2 - cy) ** 2;
      if (distance < bestDistance) {
        bestDistance = distance;
        best = index;
      }
    });
    return best;
  }

  // Where an item that is not dragged sits while the dragged one is over `to`.
  function displacedSlot(index) {
    if (from.value < to.value && index > from.value && index <= to.value) return index - 1;
    if (from.value > to.value && index >= to.value && index < from.value) return index + 1;
    return index;
  }

  function itemStyle(index) {
    if (landing.value) {
      if (index !== landing.value.index) return { transition: 'none' };
      return { transform: `translate(${landing.value.dx}px, ${landing.value.dy}px)`, transition: 'none', zIndex: 10 };
    }
    if (!dragging.value) return {};
    if (index === from.value) {
      const origin = slots[index];
      return {
        transform: `translate(${pointer.value.x - grab.x - origin.left}px, ${pointer.value.y - grab.y - origin.top}px) scale(1.02)`,
        transition: 'none',
        zIndex: 10,
      };
    }
    const target = slots[displacedSlot(index)];
    return { transform: `translate(${target.left - slots[index].left}px, ${target.top - slots[index].top}px)` };
  }

  function onPointerDown(event, index) {
    const el = toValue(container);
    if (!el || dragging.value || event.button !== 0) return;
    event.preventDefault();
    cancelAnimationFrame(landingFrame);
    landing.value = null;

    scale = el.offsetWidth ? el.getBoundingClientRect().width / el.offsetWidth : 1;
    // Layout boxes, not client rects: an item still settling from the last
    // drop is mid-transform, and its slot is where layout put it.
    slots = items().map(item => ({
      left: item.offsetLeft,
      top: item.offsetTop,
      width: item.offsetWidth,
      height: item.offsetHeight,
    }));
    if (!slots[index]) return;

    pointer.value = local(event);
    grab = { x: pointer.value.x - slots[index].left, y: pointer.value.y - slots[index].top };
    from.value = index;
    to.value = index;
    listen(true);
  }

  function onPointerMove(event) {
    if (!dragging.value) return;
    pointer.value = local(event);
    to.value = nearestSlot();
  }

  function onPointerUp() {
    if (!dragging.value) return;
    listen(false);
    const start = from.value;
    const end = to.value;
    const target = slots[end];
    const dx = pointer.value.x - grab.x - target.left;
    const dy = pointer.value.y - grab.y - target.top;
    from.value = -1;
    to.value = -1;
    // The new order and the dropped item's offset from its new slot land in
    // the same render, so neither the others nor it jump; the frame after,
    // the offset is released and the item's own transition settles it.
    landing.value = { index: end, dx, dy };
    if (end !== start) onDrop(start, end);
    landingFrame = requestAnimationFrame(() => {
      landingFrame = requestAnimationFrame(() => { landing.value = null; });
    });
  }

  function listen(on) {
    const method = on ? 'addEventListener' : 'removeEventListener';
    document[method]('pointermove', onPointerMove);
    document[method]('pointerup', onPointerUp);
    document[method]('pointercancel', onPointerUp);
  }

  onScopeDispose(() => {
    listen(false);
    cancelAnimationFrame(landingFrame);
  });

  return { dragging, draggedIndex: from, itemStyle, onPointerDown };
}
