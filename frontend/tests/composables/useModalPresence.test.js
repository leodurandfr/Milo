// frontend/tests/composables/useModalPresence.test.js
/**
 * The dock must never sit under a modal, and it now appears on its own at every
 * page load and backend restart — so it hides whenever *any* Modal opens, the
 * Music Library's playlist modals included, not only the three App.vue owns.
 * `anyModalOpen` is what it watches. If this miscounts, the dock either lingers
 * under a modal or hides with none open.
 *
 * Host components are mounted only to give each "modal" a lifecycle; nothing is
 * rendered or asserted on the DOM.
 */
import { describe, it, expect } from 'vitest';
import { defineComponent, h } from 'vue';
import { mount } from '@vue/test-utils';
import { useModalPresence, anyModalOpen } from '@/composables/useModalPresence';

function mountModal() {
  let presence;
  const wrapper = mount(defineComponent({
    setup() {
      presence = useModalPresence();
      return () => h('div');
    },
  }));
  return { presence, wrapper };
}

describe('useModalPresence', () => {
  it('stays open while one of two open modals remains', () => {
    const a = mountModal();
    const b = mountModal();
    a.presence.setOpen(true);
    b.presence.setOpen(true);

    a.presence.setOpen(false);
    expect(anyModalOpen.value).toBe(true);

    b.presence.setOpen(false);
    expect(anyModalOpen.value).toBe(false);
    a.wrapper.unmount();
    b.wrapper.unmount();
  });

  it('counts a modal reported open twice once', () => {
    const a = mountModal();
    a.presence.setOpen(true);
    a.presence.setOpen(true);
    a.presence.setOpen(false);

    expect(anyModalOpen.value).toBe(false);
    a.wrapper.unmount();
  });

  it('forgets a modal unmounted while open', () => {
    const a = mountModal();
    a.presence.setOpen(true);

    a.wrapper.unmount();

    expect(anyModalOpen.value).toBe(false);
  });
});
