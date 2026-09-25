// frontend/src/utils/deviceName.js
// Shared device name formatting for Bluetooth and ROC (Mac) source displays.

/**
 * Strip ".local" suffix and normalize hyphens to spaces.
 *
 * @param {string|null|undefined} name - Raw device hostname or display name
 * @returns {string} Cleaned name, or empty string if falsy
 */
function cleanDeviceName(name) {
  if (!name) return '';
  return name.replace(/\.local$/, '').replace(/-/g, ' ');
}

/**
 * Format a session's senders for display, joined with newlines (several Macs
 * streaming over ROC) for use with `white-space: pre-line` in CSS.
 *
 * @param {string[]|null|undefined} senders - The wire's `session.senders`
 * @returns {string} Formatted string, or empty string if absent/empty
 */
export function formatDeviceNames(senders) {
  if (!senders?.length) return '';
  return senders.map(n => cleanDeviceName(n)).join('\n');
}
