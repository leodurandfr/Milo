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

const MODULE_SCRIPT = /<script\b(?=[^>]*\btype=["']module["'])[^>]*\bsrc=["']([^"']+)["']/i;

/**
 * Whether the index a restarted backend serves names another entry script than
 * the one this tab runs — an in-app Milō update swapped `dist/`, and this tab's
 * lazy chunks are gone. False when either side names none (an error page, a
 * proxy hiccup): only a proven mismatch reloads.
 *
 * @param {string|null} runningEntrySrc - the `src` attribute of this document's module script
 * @param {string} servedHtml - the index the server answers now
 */
export function isStaleBuild(runningEntrySrc, servedHtml) {
  const servedEntrySrc = MODULE_SCRIPT.exec(servedHtml ?? '')?.[1];
  return Boolean(runningEntrySrc) && Boolean(servedEntrySrc) && servedEntrySrc !== runningEntrySrc;
}
