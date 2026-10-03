/**
 * Whether a handshake comes from a backend that restarted rather than from a
 * reconnect to the same one — identical from the socket. Every
 * `system/initial_state` names the backend process it came from
 * (`server_instance`); a restart is that name changing.
 *
 * @param {string|null} known - the instance the app last heard from (null at boot)
 * @param {string|undefined} current - the instance this handshake names
 */
export function isBackendRestart(known, current) {
  return Boolean(known) && Boolean(current) && current !== known;
}
