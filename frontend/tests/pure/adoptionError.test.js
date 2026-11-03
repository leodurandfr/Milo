// frontend/tests/pure/adoptionError.test.js
/**
 * `adoptionErrorKey` turns the `detail` of a failed speaker adoption into the
 * i18n key ConfigureSystem renders. It must always return a key: the detail
 * is the `{code, message}` object the route sends, and assigning it to the
 * error text is what showed "[object Object]" under "Configuration failed".
 */
import { describe, it, expect } from 'vitest';
import { adoptionErrorKey, ADOPTION_ERROR_FALLBACK_KEY } from '@/utils/adoptionError';
import { backendAdoptionCodes } from '../helpers/adoptionCodes';

describe('adoptionErrorKey', () => {
  it('resolves every code the route sends to its own wording, never an object', () => {
    const codes = backendAdoptionCodes();
    expect(codes.length).toBeGreaterThan(5);

    for (const code of codes) {
      const key = adoptionErrorKey({ code, message: 'x' });
      expect(typeof key, code).toBe('string');
      expect(key, code).not.toBe(ADOPTION_ERROR_FALLBACK_KEY);
    }
  });

  it.each([
    ['an unknown code', { code: 'nope', message: 'x' }],
    ['a Pydantic 422 list', [{ loc: ['body', 'ssid'], msg: 'field required', type: 'missing' }]],
    ['a plain string', 'Internal Server Error'],
    ['no detail at all', undefined],
  ])('falls back to the generic wording for %s', (_label, detail) => {
    expect(adoptionErrorKey(detail)).toBe(ADOPTION_ERROR_FALLBACK_KEY);
  });
});
