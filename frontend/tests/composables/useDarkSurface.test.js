// frontend/tests/composables/useDarkSurface.test.js
/**
 * `useDarkSurface` is the only thing telling VolumeBar what it is drawn on: the
 * bar is fixed above every view, App.vue mounts it once, and nothing can read a
 * colour back out of the backdrop. So a wrong answer here is a near-black fill
 * painted onto the dark theme or the Lyrics view, or a light one onto the app.
 *
 * Two inputs make the answer: the theme, and the surfaces that declare
 * themselves dark (Lyrics, dark in both themes). The theme is set through
 * `applyTheme`, the same entry the gallery canvas uses. The counting is covered
 * because that is where a rewrite would go wrong: two Lyrics views overlap while
 * one fades out under the next, and a flag would go light again on the first of
 * the two to unmount.
 *
 * Mounts a bare host and asserts nothing about the DOM: the shared state is what
 * is under test, not a rendering.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { markDarkSurface, useDarkSurface } from '@/composables/useDarkSurface';
import { applyTheme } from '@/composables/useTheme';

/** A component that declares itself dark for its whole mounted life. */
function darkSurface() {
  return mount(defineComponent({
    setup: () => markDarkSurface(),
    render: () => h('div'),
  }));
}

const { isDarkSurface } = useDarkSurface();

describe('useDarkSurface', () => {
  afterEach(() => applyTheme('light'));

  it('answers light in the light theme with nothing on screen', () => {
    applyTheme('light');
    expect(isDarkSurface.value).toBe(false);
  });

  it('answers dark in the dark theme alone', () => {
    applyTheme('dark');
    expect(isDarkSurface.value).toBe(true);
  });

  it('goes dark for the lyrics in the light theme, for their whole mounted life', async () => {
    applyTheme('light');
    const lyrics = darkSurface();
    expect(isDarkSurface.value).toBe(true);

    lyrics.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(false);
  });

  it('stays dark with the lyrics open in the dark theme, and after they close', async () => {
    applyTheme('dark');
    const lyrics = darkSurface();
    expect(isDarkSurface.value).toBe(true);

    lyrics.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(true);
  });

  it('stays dark until the last of two overlapping Lyrics views leaves', async () => {
    const leaving = darkSurface();
    const opening = darkSurface();
    expect(isDarkSurface.value).toBe(true);

    leaving.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(true);

    opening.unmount();
    await nextTick();
    expect(isDarkSurface.value).toBe(false);
  });
});
