// frontend/tests/pure/errorDetail.test.js
/**
 * What breaks when these fail: a refused settings write (a 422) shows
 * `[object Object]` where the server named the field it refused — FastAPI
 * answers a body it cannot validate with a list of errors, not a sentence.
 */
import { describe, it, expect } from 'vitest';
import { describeDetail } from '@/utils/errorDetail';

describe('describeDetail', () => {
  it('reads a validation list as its messages, one per line', () => {
    const detail = [
      { type: 'value_error', loc: ['body', 'rotary_encoder'], msg: 'Value error, All GPIO pins must be different' },
      { type: 'less_than_equal', loc: ['body', 'ir_remote', 'gpio_pin'], msg: 'Input should be less than or equal to 27' },
    ];

    expect(describeDetail(detail)).toBe('All GPIO pins must be different\nInput should be less than or equal to 27');
  });

  it('leaves a sentence, and a structured detail its caller reads, as they are', () => {
    const coded = { code: 'push_rejected' };

    expect(describeDetail('Failed to save setting')).toBe('Failed to save setting');
    expect(describeDetail(coded)).toBe(coded);
  });
});
