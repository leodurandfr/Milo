// frontend/tests/pure/qobuzAccount.test.js
/**
 * The Qobuz settings screen crosses two answers: the route's, which reads the
 * cached token only, and the running sidecar's, published as
 * `availability.qobuz`. If the screen trusts the cache alone, a token the
 * sidecar refused shows "connected" with no Connect button, while the source
 * card says the account is missing and sends the user to that very screen.
 */
import { describe, it, expect } from 'vitest';
import { accountView } from '@/utils/qobuzAccount';

const cached = { authenticated: true, email: 'listener@example.com' };
const none = { authenticated: false, email: null };

const running = (availability) => ({ source: 'qobuz', service: 'running', availability });

describe('accountView', () => {
  it('a cached token the running speaker refuses offers Connect', () => {
    expect(accountView(cached, running({ qobuz: 'no_account' }))).toBe('refused');
  });

  it('a cached token with the sidecar stopped reads as connected', () => {
    const refusal = { qobuz: 'no_account' };
    expect(accountView(cached, { source: 'radio', service: 'running', availability: refusal }))
      .toBe('connected');
    expect(accountView(cached, running({}))).toBe('connected');
  });

  it('no cached token reads as not connected', () => {
    expect(accountView(none, running({ qobuz: 'no_account' }))).toBe('disconnected');
    expect(accountView(none, running({}))).toBe('disconnected');
    expect(accountView(none, { source: 'radio', service: 'running', availability: {} }))
      .toBe('disconnected');
  });

  it('a starting sidecar does not read as a refusal', () => {
    const starting = { source: 'qobuz', service: 'starting', availability: { qobuz: 'no_account' } };
    expect(accountView(cached, starting)).toBe('connected');
  });
});
