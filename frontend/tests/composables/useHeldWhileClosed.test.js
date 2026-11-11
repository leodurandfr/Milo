// frontend/tests/composables/useHeldWhileClosed.test.js
/**
 * useHeldWhileClosed is what a collapsing section draws while it folds. If the
 * hold breaks, the slider of a toggle section (a client's trim and delay,
 * Spotify's crossfade) jumps to the "off" value as the section closes, the
 * hardware dropdowns blank, and the WiFi card flips to "Not connected" mid-fade.
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { useHeldWhileClosed } from '@/composables/useHeldWhileClosed';

function mountHeld(open, live) {
  let shown;
  mount(defineComponent({
    setup() {
      shown = useHeldWhileClosed(open, live);
      return () => h('div');
    },
  }));
  return shown;
}

describe('useHeldWhileClosed', () => {
  it('follows the live value while open', async () => {
    const open = ref(true);
    const value = ref(6);
    const shown = mountHeld(open, value);
    value.value = 9;
    await nextTick();
    expect(shown.value).toBe(9);
  });

  it('keeps the last open value when closing and the value move in one tick, in either order', async () => {
    const open = ref(true);
    const value = ref(-6);
    const shown = mountHeld(open, value);

    value.value = 0;
    open.value = false;
    await nextTick();
    expect(shown.value).toBe(-6);

    open.value = true;
    value.value = -3;
    await nextTick();
    open.value = false;
    value.value = 0;
    await nextTick();
    expect(shown.value).toBe(-3);
  });

  it('holds when the closing is derived from the value itself', async () => {
    const seconds = ref(6);
    const shown = mountHeld(() => seconds.value !== 0, seconds);
    seconds.value = 0;
    await nextTick();
    expect(shown.value).toBe(6);
  });

  it('ignores changes while closed and shows the live value again on opening', async () => {
    const open = ref(true);
    const value = ref({ badge: 'connected' });
    const shown = mountHeld(open, () => value.value);
    open.value = false;
    await nextTick();
    value.value = { badge: 'disconnected' };
    await nextTick();
    expect(shown.value).toEqual({ badge: 'connected' });

    open.value = true;
    await nextTick();
    expect(shown.value).toEqual({ badge: 'disconnected' });
  });
});
