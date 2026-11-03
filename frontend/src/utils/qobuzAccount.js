/**
 * What the Qobuz settings screen shows of the account. GET /api/qobuz/account
 * reads the cached token only, so it still answers while the sidecar is down;
 * whether that token still works is the running sidecar's to say, through the
 * source's availability. qobuz-proxy keeps the cache of a token it refused, so
 * the cache alone would show "connected" over a speaker the Qobuz app cannot see.
 */

// The sidecar runs and reports no account: the only state in which its OAuth
// page is served and a login is what is missing. The selection alone is set
// before the source starts and survives a failed start, and a starting source
// falls back on the cache, so neither reads as a refusal.
export function sidecarWantsLogin({ source, service, availability }) {
  return source === 'qobuz' && service === 'running' && availability.qobuz === 'no_account';
}

export function accountView(account, systemState) {
  if (!account.authenticated) return 'disconnected';
  return sidecarWantsLogin(systemState) ? 'refused' : 'connected';
}
