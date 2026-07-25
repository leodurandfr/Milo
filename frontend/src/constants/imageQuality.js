/**
 * Minimum image dimension (px) below which we treat the image as
 * a placeholder / broken source (covers, screensaver, lazy images).
 */
export const MIN_IMAGE_SIZE = 8;

/**
 * Artwork from an untrusted external sender (AirPlay, DLNA) below this
 * resolution is treated as untrustworthy: browser audio without MediaSession
 * cover art ends up as a tiny favicon / app icon, whereas real senders (Apple
 * Music, Spotify desktop, a DLNA controller) push ≥600px covers. We can't
 * recover the quality (it's never sent), so we decline the rich display.
 * Shared by the main now-playing view and the screensaver. Qobuz doesn't use
 * this gate — it's a trusted CDN source that always sends full-size covers.
 */
export const UNTRUSTED_SENDER_MIN_ARTWORK_PX = 300;
