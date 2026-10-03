// frontend/tests/pure/serverInstance.test.js
/**
 * App.vue reveals the dock when the backend restarted, and must not when the
 * socket merely reconnected to the same backend — a phone back from the
 * background reconnects too, and the dock popping up on every return is what
 * 6547247b removed. The two look identical from the socket; only the
 * `server_instance` the handshake carries tells them apart.
 */
import { describe, it, expect } from 'vitest';
import { isBackendRestart } from '@/utils/serverInstance';

describe('isBackendRestart', () => {
  it('is false for the first backend the app meets (the boot reveals the dock)', () => {
    expect(isBackendRestart(null, 'a1')).toBe(false);
  });

  it('is false for a reconnect to the same backend', () => {
    expect(isBackendRestart('a1', 'a1')).toBe(false);
  });

  it('is true for a backend that came back as a new process', () => {
    expect(isBackendRestart('a1', 'b2')).toBe(true);
  });

  it('is false for a handshake that names no backend', () => {
    expect(isBackendRestart('a1', undefined)).toBe(false);
  });
});
