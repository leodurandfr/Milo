// frontend/tests/composables/useTheme.test.js
/**
 * The theme change on a tab nobody is looking at.
 *
 * `auto` flips the theme at sunset on every browser showing Milō, phones in a
 * pocket included. A hidden document refuses a view transition by rejecting its
 * `ready` promise, and left unhandled that rejection reached errors.log as an
 * ERROR from the frontend, ~40 a week from Safari alone — a banner-grade line
 * for a fade nobody could have seen. What must hold: the theme still lands on
 * the document, and nothing is left unhandled.
 *
 * Mounts a bare host for `useTimer`'s unmount hook and asserts nothing about
 * the DOM beyond `data-theme`, which is the composable's output.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { defineComponent, h, nextTick, ref } from 'vue';
import { mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { useSettingsStore } from '@/stores/settingsStore';
import { applyTheme, mountTheme } from '@/composables/useTheme';

let host;
let settings;
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

async function flushRejections() {
  await new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  setActivePinia(createPinia());
  settings = useSettingsStore();
  settings.screenTheme = { theme: 'light' };
  applyTheme('light');
  unhandled = [];
  process.on('unhandledRejection', onUnhandled);
  host = mount(defineComponent({
    setup: () => mountTheme({ animate: ref(true) }),
    render: () => h('div'),
  }));
});

afterEach(() => {
  host.unmount();
  process.off('unhandledRejection', onUnhandled);
  delete document.startViewTransition;
  setVisibility('visible');
});

describe('mountTheme on a hidden or hiding tab', () => {
  it('applies the theme without a transition while the tab is hidden', async () => {
    refusingTransitions();
    setVisibility('hidden');

    settings.screenTheme = { theme: 'dark' };
    await nextTick();

    expect(document.startViewTransition).not.toHaveBeenCalled();
    expect(document.documentElement.dataset.theme).toBe('dark');
  });

  it('leaves no unhandled rejection when a transition is aborted mid-fade', async () => {
    refusingTransitions();
    setVisibility('visible');

    settings.screenTheme = { theme: 'dark' };
    await nextTick();
    await flushRejections();

    expect(document.startViewTransition).toHaveBeenCalledOnce();
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(unhandled).toEqual([]);
  });
});
