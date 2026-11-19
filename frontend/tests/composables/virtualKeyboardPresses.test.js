// frontend/tests/composables/virtualKeyboardPresses.test.js
/**
 * The kiosk's touchscreen is multi-touch, and the virtual keyboard is the only
 * way to type on it — a Wi-Fi key, a share's or the system's password, all
 * masked, so a wrong character is never seen.
 *
 * Each finger types its own key. If this fails, a second finger landing before
 * the first lifts makes the first type the second's key ("e" then "s" overlapped
 * gave "ss"), and a long-press timer overwritten by the second finger opens an
 * accent popup on its own later, swallowing the next key.
 *
 * Backspace's repeat stops whenever its press ends — released, canceled, the
 * window losing focus, or the keyboard closed from outside (its field unmounted
 * by a modal closing). The keyboard is mounted once for the app's life, so if
 * this fails a repeat nothing stops goes on erasing, ten characters a second,
 * every field opened after it until the kiosk is restarted.
 *
 * VirtualKeyboard is mounted as the host it is in App.vue and driven by pointer
 * events on its keys; what is asserted is the value the open field receives
 * through its onChange, never the DOM.
 */
import { describe, it, expect, vi, beforeAll, afterAll, beforeEach, afterEach } from 'vitest';
import { mount } from '@vue/test-utils';
import { nextTick } from 'vue';
import VirtualKeyboard from '@/components/ui/VirtualKeyboard.vue';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import { i18n } from '@/services/i18n';

describe('VirtualKeyboard — presses', () => {
  let url;
  let wrapper;
  let typed;

  beforeAll(() => {
    url = window.location.href;
    window.history.replaceState(null, '', '?virtualKeyboard=true');
  });
  afterAll(() => window.history.replaceState(null, '', url));

  beforeEach(() => {
    vi.useFakeTimers();
    wrapper = mount(VirtualKeyboard, {
      attachTo: document.body,
      global: { directives: { press: {} } }
    });
  });
  afterEach(() => {
    useVirtualKeyboard().close();
    wrapper.unmount();
    i18n.currentLanguage.value = 'english';
    vi.useRealTimers();
  });

  async function openField(value = '') {
    typed = value;
    useVirtualKeyboard().open({ value, onChange: (v) => { typed = v; } });
    await nextTick();
  }

  function key(char) {
    const found = wrapper.findAll('button').find((b) => b.text() === char);
    if (!found) throw new Error(`no key "${char}" on the keyboard`);
    return found.element;
  }

  const backspaceKey = () => wrapper.find('.key-backspace').element;

  function pointer(el, type, pointerId) {
    el.dispatchEvent(new PointerEvent(type, { bubbles: true, cancelable: true, pointerId }));
  }

  it('two overlapping fingers each type their own key', async () => {
    await openField();

    pointer(key('e'), 'pointerdown', 1);
    pointer(key('s'), 'pointerdown', 2);
    pointer(key('e'), 'pointerup', 1);
    pointer(key('s'), 'pointerup', 2);

    expect(typed).toBe('es');
  });

  it("a second finger's long press does not orphan the first's", async () => {
    i18n.currentLanguage.value = 'french'; // 'e' and 'a' both have accents
    await openField();

    pointer(key('e'), 'pointerdown', 1);
    pointer(key('a'), 'pointerdown', 2);
    vi.advanceTimersByTime(100);
    pointer(key('e'), 'pointerup', 1);
    vi.advanceTimersByTime(100);
    pointer(key('a'), 'pointerup', 2);
    // Long after both lifted: no popup may open and swallow the next key.
    vi.advanceTimersByTime(1000);
    pointer(key('r'), 'pointerdown', 3);
    pointer(key('r'), 'pointerup', 3);

    expect(typed).toBe('ear');
  });

  it('a finger typing while another holds the accent popup types its key', async () => {
    i18n.currentLanguage.value = 'french';
    await openField();

    pointer(key('e'), 'pointerdown', 1);
    vi.advanceTimersByTime(600); // the popup opens for finger 1
    pointer(key('r'), 'pointerdown', 2);
    pointer(key('r'), 'pointerup', 2);
    pointer(key('e'), 'pointerup', 1); // lifted without sliding: no accent

    expect(typed).toBe('r');
  });

  it('a canceled press types nothing, and its long press ends with it', async () => {
    i18n.currentLanguage.value = 'french';
    await openField();

    pointer(key('e'), 'pointerdown', 1);
    pointer(key('e'), 'pointercancel', 1);
    vi.advanceTimersByTime(1000);
    pointer(key('r'), 'pointerdown', 2);
    pointer(key('r'), 'pointerup', 2);

    expect(typed).toBe('r');
  });

  it('a finger down when the window loses focus types nothing when it lifts', async () => {
    await openField();

    pointer(key('e'), 'pointerdown', 1);
    window.dispatchEvent(new Event('blur'));
    pointer(key('e'), 'pointerup', 1);
    pointer(key('r'), 'pointerdown', 2);
    pointer(key('r'), 'pointerup', 2);

    expect(typed).toBe('r');
  });

  it("a second finger on backspace leaves no repeat behind the first's release", async () => {
    await openField('abcdefghijklmnop');

    pointer(backspaceKey(), 'pointerdown', 1);
    pointer(backspaceKey(), 'pointerdown', 2);
    pointer(backspaceKey(), 'pointerup', 2);
    pointer(backspaceKey(), 'pointerup', 1);
    const left = typed;
    vi.advanceTimersByTime(1000);

    expect(typed).toBe(left);
  });

  it('backspace held while its field closes stops erasing', async () => {
    await openField('abcdef');
    pointer(backspaceKey(), 'pointerdown', 1);

    // The field unmounts (its modal closed) and closes the keyboard directly;
    // the key under the finger is gone, so no release ever reaches it.
    useVirtualKeyboard().close();
    await nextTick();
    await openField('hello');
    vi.advanceTimersByTime(1000);

    expect(typed).toBe('hello');
  });

  it('backspace held stops on a cancel and when the window loses focus', async () => {
    await openField('abcdefghijklmnop');

    pointer(backspaceKey(), 'pointerdown', 1);
    pointer(backspaceKey(), 'pointercancel', 1);
    let left = typed;
    vi.advanceTimersByTime(1000);
    expect(typed).toBe(left);

    pointer(backspaceKey(), 'pointerdown', 1);
    window.dispatchEvent(new Event('blur'));
    left = typed;
    vi.advanceTimersByTime(1000);
    expect(typed).toBe(left);
  });
});
