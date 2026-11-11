/**
 * The one condition that puts the app in its mobile layout.
 *
 * Narrower than 4:3 *and* under 1024 CSS px: a phone or a tablet held upright
 * is mobile, while a large near-square screen (or a near-square browser window
 * on a monitor) keeps the desktop layout it has room for.
 *
 * CSS cannot read a JS constant, so every `@media` rule spells this string out
 * literally; `tests/architecture/mobileBreakpoint.test.js` holds each of them
 * equal to it, because one rule left on another condition gives a screen that
 * is half desktop, half mobile.
 */
export const MOBILE_QUERY = '(max-aspect-ratio: 4/3) and (max-width: 1023px)';

/**
 * A spinner standing alone as a whole view's wait, in CSS px — the Lyrics view's
 * loader and MessageContent's `loading` variant, which must read as one thing.
 */
export const STANDALONE_SPINNER_SIZE = { desktop: 96, mobile: 64 };
