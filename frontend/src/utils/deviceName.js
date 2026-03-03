// frontend/src/utils/deviceName.js
// Shared device name formatting for Bluetooth and ROC (Mac) source displays.

/**
 * Strip ".local" suffix and normalize hyphens to spaces.
 *
 * @param {string|null|undefined} name - Raw device hostname or display name
 * @returns {string} Cleaned name, or empty string if falsy
 */
export function cleanDeviceName(name) {
  if (!name) return '';
  return name.replace(/\.local$/, '').replace(/-/g, ' ');
}

/**
 * Format a device name or list of names for display.
 * Arrays (ROC multi-client) are joined with newlines for use with
 * `white-space: pre-line` in CSS.
 *
 * @param {string|string[]|null|undefined} deviceName
 * @returns {string} Formatted string, or empty string if falsy/empty
 */
export function formatDeviceNames(deviceName) {
  if (!deviceName) return '';
  if (Array.isArray(deviceName)) {
    if (deviceName.length === 0) return '';
    return deviceName.map(n => cleanDeviceName(n)).join('\n');
  }
  return cleanDeviceName(deviceName);
}
