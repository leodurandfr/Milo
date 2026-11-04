// frontend/tests/composables/useVirtualKeyboard.test.js
/**
 * The kiosk's virtual keyboard writes into a field through `updateValue`, never
 * through the field's own `<input>`, so the input's `maxlength` attribute bounds
 * nothing there. The field hands its bound to `open()`; if this fails, a name
 * typed on the kiosk can run past what the backend accepts and be refused with
 * nothing on screen (a zone name: 422 on create and rename).
 *
 * No component is mounted: the state is module-level and the only outside
 * world is the field's callback.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { useVirtualKeyboard } from '@/composables/useVirtualKeyboard';

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
