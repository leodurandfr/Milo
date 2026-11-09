// frontend/tests/composables/useHeldOrder.test.js
/**
 * useHeldOrder keeps a dropped queue row where it was dropped until the state
 * confirms the move. If the release on a refused move fails, the music
 * library's queue view keeps drawing the order the drag predicted, and each
 * row carries the queue index of its predicted place: tapping one plays
 * another track (`play_index` carries no track id the backend could check).
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useHeldOrder } from '@/composables/useHeldOrder';

function mountHeld(shape) {
  let held;
  mount(defineComponent({
    setup() {
      held = useHeldOrder(() => shape.value);
      return () => h('div');
    },
  }));
  return held;
}

function deferred() {
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  return { promise, resolve };
}

describe('useHeldOrder', () => {
  it('releases an order its request refused', async () => {
    const { held, hold } = mountHeld(ref('1:a,b,c'));

    hold(['b', 'c'], Promise.resolve(false));
    expect(held.value).toEqual(['b', 'c']);
    await nextTick();

    expect(held.value).toBeNull();
  });

  it('keeps an accepted order until the state moves', async () => {
    const shape = ref('1:a,b,c');
    const { held, hold } = mountHeld(shape);

    hold(['c', 'b'], Promise.resolve(true));
    await nextTick();
    expect(held.value).toEqual(['c', 'b']);

    shape.value = '1:a,c,b';
    await nextTick();
    expect(held.value).toBeNull();
  });

  it('keeps a newer drop when an earlier one is refused', async () => {
    const { held, hold } = mountHeld(ref('1:a,b,c'));
    const first = deferred();

    hold(['c', 'b'], first.promise);
    hold(['b', 'c'], Promise.resolve(true));
    first.resolve(false);
    await first.promise;
    await nextTick();

    expect(held.value).toEqual(['b', 'c']);
  });
});
