// frontend/tests/composables/useDragReorder.test.js
/**
 * useDragReorder moves a row of a playlist by its grip. What breaks, silently:
 * a drag across several rows reports each step, or the first one, and the
 * playlist persists a half-move — or one save per row crossed, each against an
 * order the previous one already changed.
 *
 * The pointer is driven through `document`, where the composable listens;
 * nothing is asserted on the DOM.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { effectScope, ref } from 'vue';
import { useDragReorder } from '@/composables/useDragReorder';

const ROW = 50;
const grip = (clientY) => ({ clientY, target: { closest: () => ({ offsetHeight: ROW }) } });
const pointer = (type, clientY) =>
  document.dispatchEvent(Object.assign(new Event(type), { clientY }));

let scope;
afterEach(() => scope?.stop());

function setup(list, options) {
  scope = effectScope();
  return scope.run(() => useDragReorder(list, options));
}

describe('useDragReorder', () => {
  it('reports a drag over several rows once, with where the row ended', () => {
    const onDrop = vi.fn();
    const drag = setup(ref(['a', 'b', 'c', 'd', 'e']), { onDrop });

    drag.onGripDown(grip(100), 1);
    for (const y of [130, 160, 190, 220, 250]) pointer('pointermove', y);
    expect(onDrop).not.toHaveBeenCalled();
    pointer('pointerup', 250);

    expect(onDrop).toHaveBeenCalledTimes(1);
    expect(onDrop).toHaveBeenCalledWith(1, 4, 'b');
  });
});
