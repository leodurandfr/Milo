/**
 * Minimum image dimension (px) below which we treat the image as
 * a placeholder / broken source (covers, screensaver, lazy images).
 */
export const MIN_IMAGE_SIZE = 8;

/**
 * AirPlay artwork below this resolution is treated as untrustworthy: browser
 * audio without MediaSession cover art ends up as a tiny favicon / app icon,
 * whereas real senders (Apple Music, Spotify desktop) push ≥600px covers. We
 * can't recover the quality (it's never sent), so we decline the rich display.
 * Shared by the main now-playing view and the screensaver.
 */
export const AIRPLAY_MIN_ARTWORK_PX = 300;
