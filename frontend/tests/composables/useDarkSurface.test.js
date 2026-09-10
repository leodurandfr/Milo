// frontend/tests/composables/useDarkSurface.test.js
/**
 * `useDarkSurface` is the only thing telling VolumeBar what it is drawn on: the
 * bar is fixed above every view, App.vue mounts it once, and nothing can read a
 * colour back out of the backdrop. So a wrong answer here is a near-black fill
 * painted onto the screensaver, or a white one onto the app.
 *
 * What is covered is the counting, which is where a rewrite would go wrong: the
 * two dark surfaces overlap (the screensaver rises over an open Lyrics view),
 * and a flag would go light again on the first of the two to close. Unmount is
 * covered for the same reason in the other direction — a surface that leaves
 * still holding its claim never lets the app go light again.
 *
 * Mounts a bare host and asserts nothing about the DOM: the shared state is what
 * is under test, not a rendering.
 */
import { describe, it, expect } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { markDarkSurface, useDarkSurface } from '@/composables/useDarkSurface';

/** A component that declares itself dark for as long as `active` says so. */
function darkSurface(active) {
  return mount(defineComponent({
    setup: () => markDarkSurface(active),
    render: () => h('div'),
  }));
}

const { isDarkSurface } = useDarkSurface();

describe('useDarkSurface', () => {
  it('answers light with nothing on screen', () => {
    expect(isDarkSurface.value).toBe(false);
  });

  it('goes dark for a surface’s whole mounted life', async () => {
    const surface = darkSurface();
    expect(isDarkSurface.value).toBe(true);

    surface.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(false);
  });

  it('follows a surface that stays mounted while hidden', async () => {
    const visible = ref(false);
    const surface = darkSurface(() => visible.value);
    expect(isDarkSurface.value).toBe(false);

    visible.value = true;
    await nextTick();
    expect(isDarkSurface.value).toBe(true);

    visible.value = false;
    await nextTick();
    expect(isDarkSurface.value).toBe(false);

    surface.unmount();
  });

  it('stays dark until the last of two overlapping surfaces leaves', async () => {
    const lyrics = darkSurface();
    const screensaver = darkSurface();
    expect(isDarkSurface.value).toBe(true);

    screensaver.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(true);

    lyrics.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(false);
  });

  it('releases whatever a surface still holds when it unmounts', async () => {
    const visible = ref(true);
    const surface = darkSurface(visible);
    expect(isDarkSurface.value).toBe(true);

    surface.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(false);
  });
});
