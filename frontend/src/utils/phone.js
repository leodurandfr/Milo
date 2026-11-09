// frontend/src/utils/phone.js
/**
 * Whether this browser runs on a phone, read from its user agent: the screen's
 * shape cannot say, since a phone turned sideways gets the wide layout and a
 * computer's narrow window the mobile one.
 *
 * `Mobi` is what every phone browser puts in its user agent (Safari, Chrome,
 * Firefox — `Mobile` on Android, `Mobile/15E148` on iOS); Android tablets omit
 * it, and iPads that still send it are taken out by name. iPadOS Safari
 * presents itself as a Mac, so an iPad counts as a computer either way.
 */
export function isPhone(userAgent = navigator.userAgent) {
  return /Mobi/.test(userAgent) && !/iPad/.test(userAgent);
}
