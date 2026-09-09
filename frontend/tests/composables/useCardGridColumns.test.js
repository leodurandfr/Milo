// frontend/tests/composables/useCardGridColumns.test.js
/**
 * useCardGridColumns reads --card-grid-columns off the document so a view that
 * slices its list cuts on the number the CSS lays out.
 *
 * What breaks when this fails is silent: the composable falls back to its
 * unstyled default, HomeView keeps slicing the podcast chart at 8, and the last
 * row is left with orphans on every screen wider than the default band — no
 * error, and only visible on a resolution nobody develops at.
 *
 * A host component is mounted only to give the composable a lifecycle; nothing
 * is rendered or asserted on the DOM.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { defineComponent, h, nextTick } from 'vue';
import { mount } from '@vue/test-utils';
import { useCardGridColumns } from '@/composables/useCardGridColumns';

/** Mount a host exposing the composable. */
function mountColumns() {
  let api;
  const Host = defineComponent({
    setup() {
      api = useCardGridColumns();
      return () => h('div');
    },
  });
  const wrapper = mount(Host);
  return { api, wrapper };
}

afterEach(() => {
  document.documentElement.style.removeProperty('--card-grid-columns');
});

describe('useCardGridColumns', () => {
  it('reads the count the stylesheet declares', () => {
    document.documentElement.style.setProperty('--card-grid-columns', '7');

    const { api, wrapper } = mountColumns();

    // Not the unstyled default: a composable that never found the token would
    // answer 4 here and pass every other assertion below.
    expect(api.columns.value).toBe(7);
    wrapper.unmount();
  });

  it('re-reads when the viewport changes band', async () => {
    document.documentElement.style.setProperty('--card-grid-columns', '4');
    const { api, wrapper } = mountColumns();

    // What a media query in design-system.css does to :root when the window
    // crosses one of the ladder's widths.
    document.documentElement.style.setProperty('--card-grid-columns', '6');
    window.dispatchEvent(new Event('resize'));
    await nextTick();

    expect(api.columns.value).toBe(6);
    wrapper.unmount();
  });

  it('stops listening once the last consumer unmounts', async () => {
    document.documentElement.style.setProperty('--card-grid-columns', '5');
    const { api, wrapper } = mountColumns();
    wrapper.unmount();

    document.documentElement.style.setProperty('--card-grid-columns', '9');
    window.dispatchEvent(new Event('resize'));
    await nextTick();

    // The state is module-level and shared; a listener surviving its consumers
    // would keep updating it for every mount that follows.
    expect(api.columns.value).toBe(5);
  });
});
