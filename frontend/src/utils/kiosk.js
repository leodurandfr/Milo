// frontend/src/utils/kiosk.js
/**
 * Whether this browser is the Pi's own touchscreen (the "kiosk").
 *
 * The kiosk always loads the app from http://localhost (see
 * system/milo-kiosk.service); remote devices reach the unit via milo.local / IP,
 * so their hostname is never 'localhost'. Single source of truth for the
 * "Pi-screen-only" features — ui_scale, warm color filter, screensaver, and
 * screen-activity wake reporting — so a remote Mac/iPhone viewing the UI never
 * drives the physical display.
 *
 * Dev note: the dev server at localhost:5173 also counts as kiosk. The backend
 * enforces the same distinction server-side on /screen-activity via nginx's
 * X-Real-IP (loopback == kiosk), which remote clients cannot spoof.
 */
export function isKiosk() {
  return window.location.hostname === 'localhost';
}
