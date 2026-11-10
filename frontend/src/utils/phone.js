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
 *
 * Milo-Android is a web view of this page whose user agent is its own and
 * nothing else (`Milo-Android-App/1.0`, MainActivity.kt), so it carries no
 * `Mobi` and is named here: it is a phone's browser, by the app's purpose.
 */
export function isPhone(userAgent = navigator.userAgent) {
  return userAgent.startsWith('Milo-Android-App/')
    || (/Mobi/.test(userAgent) && !/iPad/.test(userAgent));
}
