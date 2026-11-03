/**
 * i18n key per speaker adoption error code.
 *
 * `POST /api/discovery/adopt-speaker` answers a failure with
 * `detail: {code, message}`, the codes of `_ADOPTION_CLIENT_ERROR_CODES` in
 * `backend/api/discovery.py` plus `internal_error`. The code is what the user
 * reads, in their language; the message is the technical detail, already in
 * the backend log and in the one `apiCall` writes.
 *
 * Anything else — an unknown code, a 422's list, no response at all — falls
 * back to the generic string, so the error is never empty and never an
 * object. `tests/architecture/adoptionErrors.test.js` keeps the table in step
 * with the backend.
 */
export const ADOPTION_ERROR_KEYS = {
  invalid_ssid: 'multiroom.adopt.errors.invalidSsid',
  invalid_target_wifi: 'multiroom.adopt.errors.invalidTargetWifi',
  unsupported_target_wifi: 'multiroom.adopt.errors.unsupportedTargetWifi',
  already_configured: 'multiroom.adopt.errors.alreadyConfigured',
  server_in_hotspot_mode: 'multiroom.adopt.errors.serverInHotspotMode',
  push_rejected: 'multiroom.adopt.errors.pushRejected',
  push_failed: 'multiroom.adopt.errors.pushFailed',
  no_gateway: 'multiroom.adopt.errors.noGateway',
  hotspot_connect_failed: 'multiroom.adopt.errors.hotspotConnectFailed',
  internal_error: 'multiroom.adopt.errors.internalError',
};

export const ADOPTION_ERROR_FALLBACK_KEY = 'multiroom.adopt.errorPush';

export function adoptionErrorKey(detail) {
  const code = detail?.code;
  return Object.hasOwn(ADOPTION_ERROR_KEYS, code) ? ADOPTION_ERROR_KEYS[code] : ADOPTION_ERROR_FALLBACK_KEY;
}
