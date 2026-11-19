// frontend/tests/composables/modalLifecycle.test.js
/**
 * Two things every Modal owes the screen it covers.
 *
 * It opens when it is mounted already open. The playlist pickers (Spotify,
 * Music Library) are driven by a store that outlives the source view, and the
 * view is unmounted and mounted again by any switch, outage or multiroom toggle:
 * if this fails, the picker comes back invisible and every "Add to playlist"
 * after it does nothing, with no message, until the kiosk is restarted.
 *
 * Its 120 s auto-close counts typing as activity, wherever it lands: on the
 * virtual keyboard (mounted beside the modals, outside their veil), in a
 * teleported menu, or from a phone's own keyboard (no pointer at all). If this
 * fails, Settings closes under a Wi-Fi key or a password being typed, and a
 * field that saves on blur (a speaker's or a zone's name) saves what was typed
 * so far.
 *
 * Modal is mounted as a host only; what is asserted is what it tells its
 * caller (`close`) and the app (the shared modal count, the page's scroll
 * lock) — nothing about its DOM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import Modal from '@/components/ui/Modal.vue';
import { anyModalOpen } from '@/composables/useModalPresence';

const IDLE_CLOSE_MS = 120000;
const SECOND = 1000;

describe('Modal', () => {
  const wrappers = [];

  function mountModal(isOpen) {
    const wrapper = mount(Modal, {
      props: { isOpen },
      attachTo: document.body,
      global: { directives: { press: {} } }
    });
    wrappers.push(wrapper);
    return wrapper;
  }

  // The open animation runs to its end (under a second) before the idle
  // clock starts.
  async function settleOpening() {
    await nextTick();
    await nextTick();
    vi.advanceTimersByTime(SECOND);
  }

  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    wrappers.splice(0).forEach((w) => w.unmount());
    vi.useRealTimers();
  });

  it('opens when it is mounted already open', async () => {
    const modal = mountModal(true);
    await settleOpening();

    expect(anyModalOpen.value).toBe(true);
    expect(document.body.style.overflow).toBe('hidden');
    // Its idle clock runs only once it has opened.
    vi.advanceTimersByTime(IDLE_CLOSE_MS);
    expect(modal.emitted('close')).toHaveLength(1);
  });

  it('mounted closed, it leaves an open modal its scroll lock and its count', async () => {
    mountModal(true);
    await settleOpening();

    mountModal(false);
    await settleOpening();

    expect(document.body.style.overflow).toBe('hidden');
    expect(anyModalOpen.value).toBe(true);
  });

  it('unmounted closed, it leaves an open modal its scroll lock', async () => {
    mountModal(true);
    await settleOpening();
    const closed = mountModal(false);
    await settleOpening();

    closed.unmount();
    wrappers.splice(wrappers.indexOf(closed), 1);

    expect(document.body.style.overflow).toBe('hidden');
  });

  it('closes after 120 s with nothing done', async () => {
    const modal = mountModal(false);
    await modal.setProps({ isOpen: true });
    await settleOpening();

    vi.advanceTimersByTime(IDLE_CLOSE_MS - 5 * SECOND);
    expect(modal.emitted('close')).toBeUndefined();
    vi.advanceTimersByTime(5 * SECOND);
    expect(modal.emitted('close')).toHaveLength(1);
  });

  // Each kind of typing that never touches the veil, dispatched where it lands.
  const typing = {
    'a key of a physical or phone keyboard': () =>
      document.activeElement.dispatchEvent(new KeyboardEvent('keydown', { key: 'a', bubbles: true })),
    'a character a phone keyboard inputs': () =>
      document.body.dispatchEvent(new Event('input', { bubbles: true })),
    'a tap on the virtual keyboard, outside the veil': () => {
      const key = document.createElement('button');
      document.body.appendChild(key);
      key.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1 }));
      key.remove();
    },
  };

  for (const [what, type] of Object.entries(typing)) {
    it(`${what} postpones the auto-close`, async () => {
      const modal = mountModal(false);
      await modal.setProps({ isOpen: true });
      await settleOpening();

      vi.advanceTimersByTime(IDLE_CLOSE_MS - 10 * SECOND);
      type();
      vi.advanceTimersByTime(IDLE_CLOSE_MS - 10 * SECOND);
      expect(modal.emitted('close')).toBeUndefined();

      vi.advanceTimersByTime(10 * SECOND);
      expect(modal.emitted('close')).toHaveLength(1);
    });
  }
});
