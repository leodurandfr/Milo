// frontend/tests/composables/useVirtualKeyboard.test.js
/**
 * The kiosk's virtual keyboard writes into a field through `updateValue`, never
 * through the field's own `<input>`, so the input's `maxlength` attribute bounds
 * nothing there. The field hands its bound to `open()`; if this fails, a name
 * typed on the kiosk can run past what the backend accepts and be refused with
 * nothing on screen (a zone name: 422 on create and rename).
 *
 * A password field hands `secret` the same way: the keyboard echoes what it
 * types in a display of its own, so if this fails the kiosk shows the `milo`
 * password (SSH and sudo), a Wi-Fi key or a share's password in clear on the
 * living-room screen while the field itself stays masked.
 *
 * The bound is driven through the composable alone: its state is module-level
 * and the only outside world is the field's callback. The secret is read from
 * the real producer, an InputText mounted as a host; nothing about the DOM is
 * asserted.
 */
import { describe, it, expect, afterEach, beforeAll, afterAll } from 'vitest';
import { mount } from '@vue/test-utils';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';
import InputText from '@/components/ui/InputText.vue';

describe('useVirtualKeyboard', () => {
  afterEach(() => useVirtualKeyboard().close());

  it('a maxlength given to open() bounds what the keys type', () => {
    const keyboard = useVirtualKeyboard();
    const received = [];
    keyboard.open({ value: '', maxlength: 15, onChange: (v) => received.push(v) });

    keyboard.updateValue('a'.repeat(16));

    expect(Array.from(keyboard.currentValue.value)).toHaveLength(15);
    expect(received).toHaveLength(1);
    expect(Array.from(received[0])).toHaveLength(15);

    keyboard.close();
    keyboard.open({});
    keyboard.updateValue('a'.repeat(16));

    expect(Array.from(keyboard.currentValue.value)).toHaveLength(16);
  });

  it('a field without a bound opened over a bounded one inherits no bound', () => {
    const keyboard = useVirtualKeyboard();
    keyboard.open({ value: '', maxlength: 15 });
    keyboard.open({ value: '' });

    keyboard.updateValue('a'.repeat(16));

    expect(Array.from(keyboard.currentValue.value)).toHaveLength(16);
  });

  it('the bound counts code points, so a surrogate pair is never split', () => {
    const keyboard = useVirtualKeyboard();
    keyboard.open({ value: '', maxlength: 3 });

    keyboard.updateValue('ab\u{1F3B5}\u{1F3B5}');

    expect(Array.from(keyboard.currentValue.value)).toHaveLength(3);
    expect(keyboard.currentValue.value.endsWith('\u{1F3B5}')).toBe(true);
  });

  it('a value already past the bound when the field opened keeps its end on the next key', () => {
    const keyboard = useVirtualKeyboard();
    keyboard.open({ value: 'a'.repeat(20), maxlength: 15 });

    keyboard.updateValue(`${'a'.repeat(19)}bc`);
    expect(Array.from(keyboard.currentValue.value)).toHaveLength(20);
    expect(keyboard.currentValue.value.endsWith('b')).toBe(true);

    keyboard.updateValue('a'.repeat(18));
    expect(Array.from(keyboard.currentValue.value)).toHaveLength(18);
  });
});

describe('useVirtualKeyboard — secret fields', () => {
  let url;
  const wrappers = [];

  beforeAll(() => {
    url = window.location.href;
    window.history.replaceState(null, '', '?virtualKeyboard=true');
  });
  afterAll(() => window.history.replaceState(null, '', url));
  afterEach(() => {
    useVirtualKeyboard().close();
    wrappers.splice(0).forEach((w) => w.unmount());
  });

  async function tapField(type) {
    const wrapper = mount(InputText, {
      props: { type },
      global: { directives: { press: {} } }
    });
    wrappers.push(wrapper);
    await wrapper.find('input').trigger('focus');
  }

  it('a password field opens the keyboard secret, a text field does not', async () => {
    const keyboard = useVirtualKeyboard();

    await tapField('password');
    expect(keyboard.isVisible.value).toBe(true);
    expect(keyboard.secret.value).toBe(true);

    await tapField('text');
    expect(keyboard.isVisible.value).toBe(true);
    expect(keyboard.secret.value).toBe(false);
  });

  it('close() and submit() clear the secret', async () => {
    const keyboard = useVirtualKeyboard();

    await tapField('password');
    expect(keyboard.secret.value).toBe(true);
    keyboard.close();
    expect(keyboard.secret.value).toBe(false);

    await tapField('password');
    expect(keyboard.secret.value).toBe(true);
    keyboard.submit();
    expect(keyboard.secret.value).toBe(false);
  });
});
