// frontend/tests/composables/useTheme.test.js
/**
 * The cross-fade of a theme change: a view transition, or in WebKit — whose
 * view transitions drop every backdrop-filter — a copy of the screen in the
 * old theme, faded out over the live page.
 *
 * `auto` flips the theme at sunset on every browser showing Milō, phones in a
 * pocket included. A hidden document refuses a view transition by rejecting its
 * `ready` promise, and left unhandled that rejection reached errors.log as an
 * ERROR from the frontend, ~40 a week from Safari alone.
 *
 * What breaks if the copy tests fail: a copy left behind covers the whole screen (it is
 * on top of everything and ignores the pointer, so the app looks frozen in the
 * old theme); a copy made on a hidden tab or under reduced motion is a fade
 * nobody asked for; a copy not held in the old theme fades the new one into
 * itself, which is no fade at all.
 *
 * Mounts a bare host for `useTimer`'s unmount hook. The copy and `data-theme`
 * are the composable's output, so they are what is asserted.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { useSettingsStore } from '@/stores/settingsStore';
import { applyTheme, mountTheme } from '@/composables/useTheme';

let host;
let settings;
let reducedMotion;
let webkit;
let unhandled;
const onUnhandled = (reason) => unhandled.push(reason);

function setVisibility(state) {
  Object.defineProperty(document, 'visibilityState', { value: state, configurable: true });
}

/** A browser that refuses the transition the way Safari and Chromium do on a hidden document. */
function refusingTransitions() {
  document.startViewTransition = vi.fn((update) => {
    update();
    return {
      ready: Promise.reject(new DOMException('Transition was aborted because of invalid state. Document hidden', 'InvalidStateError')),
      finished: Promise.resolve(),
      updateCallbackDone: Promise.resolve(),
    };
  });
}

const ghosts = () => document.querySelectorAll('.theme-ghost');

async function switchTo(theme) {
  settings.screenTheme = { theme };
  await nextTick();
}

beforeEach(() => {
  vi.useFakeTimers();
  reducedMotion = false;
  webkit = false;
  vi.stubGlobal('CSS', { supports: (prop) => webkit && prop === '-webkit-backdrop-filter' });
  unhandled = [];
  process.on('unhandledRejection', onUnhandled);
  window.matchMedia = vi.fn(() => ({ matches: reducedMotion }));
  document.body.innerHTML = '<div id="app"><div class="scroller"></div></div>';
  setActivePinia(createPinia());
  settings = useSettingsStore();
  settings.screenTheme = { theme: 'light' };
  applyTheme('light');
  host = mount(defineComponent({
    setup: () => mountTheme({ animate: ref(true) }),
    render: () => h('div'),
  }));
});

afterEach(() => {
  host.unmount();
  ghosts().forEach((el) => el.remove());
  delete window.matchMedia;
  delete document.startViewTransition;
  process.off('unhandledRejection', onUnhandled);
  vi.unstubAllGlobals();
  setVisibility('visible');
  vi.useRealTimers();
});

describe('mountTheme view transition', () => {
  it('applies the theme without a transition while the tab is hidden', async () => {
    refusingTransitions();
    setVisibility('hidden');
    await switchTo('dark');

    expect(document.startViewTransition).not.toHaveBeenCalled();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('leaves no unhandled rejection when a transition is aborted mid-fade', async () => {
    vi.useRealTimers();
    refusingTransitions();
    await switchTo('dark');
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(document.startViewTransition).toHaveBeenCalledOnce();
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(unhandled).toEqual([]);
  });

  it('lays no copy outside WebKit', async () => {
    refusingTransitions();
    await switchTo('dark');

    expect(ghosts()).toHaveLength(0);
  });
});

describe('mountTheme copy fade, WebKit', () => {
  beforeEach(() => {
    webkit = true;
    refusingTransitions();
  });

  it('fades a copy instead of starting a view transition', async () => {
    await switchTo('dark');

    expect(document.startViewTransition).not.toHaveBeenCalled();
    expect(ghosts()).toHaveLength(1);
  });

  it('lays one copy held in the old theme while the document takes the new one', async () => {
    await switchTo('dark');

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(ghosts()).toHaveLength(1);
    expect(ghosts()[0].style.colorScheme).toBe('light');
    expect(ghosts()[0].querySelector('#app .scroller')).not.toBeNull();
  });

  it('scrolls the copy once it is in the document, where it has a box to scroll', async () => {
    const live = document.querySelector('.scroller');
    Object.defineProperty(live, 'scrollTop', { value: 480, configurable: true });
    const scrolled = [];
    Element.prototype.scrollTo = function scrollTo(left, top) { scrolled.push({ top, attached: this.isConnected }); };

    await switchTo('dark');
    delete Element.prototype.scrollTo;

    expect(scrolled).toEqual([{ top: 480, attached: true }]);
  });

  it('copies no iframe, whose document would load again', async () => {
    document.querySelector('#app').insertAdjacentHTML('afterbegin', '<iframe class="stage" src="about:blank"></iframe>');

    await switchTo('dark');

    expect(ghosts()[0].querySelector('iframe')).toBeNull();
    expect(ghosts()[0].querySelector('.stage')).not.toBeNull();
  });

  it('keeps a single copy when the theme changes again mid-fade', async () => {
    await switchTo('dark');
    await switchTo('light');

    expect(ghosts()).toHaveLength(1);
    expect(ghosts()[0].style.colorScheme).toBe('dark');
  });

  it('removes the copy at its own animationend, not at a descendant\'s', async () => {
    await switchTo('dark');
    const layer = ghosts()[0];

    layer.querySelector('.scroller').dispatchEvent(new Event('animationend', { bubbles: true }));
    expect(ghosts()).toHaveLength(1);

    layer.dispatchEvent(new Event('animationend'));
    expect(ghosts()).toHaveLength(0);
  });

  it('removes a copy whose animation never ran', async () => {
    await switchTo('dark');
    vi.advanceTimersByTime(1000);

    expect(ghosts()).toHaveLength(0);
  });

  it('applies the theme without a copy while the tab is hidden', async () => {
    setVisibility('hidden');
    await switchTo('dark');

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(ghosts()).toHaveLength(0);
  });

  it('applies the theme without a copy under reduced motion', async () => {
    reducedMotion = true;
    await switchTo('dark');

    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(ghosts()).toHaveLength(0);
  });
});
