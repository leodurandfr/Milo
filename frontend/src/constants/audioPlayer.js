// frontend/src/constants/audioPlayer.js
// Shared constants for audio player behavior across sources

/**
 * Delay in milliseconds before hiding the AudioPlayer after radio stops.
 * Short delay since radio stop is typically intentional.
 */
export const RADIO_PLAYER_HIDE_DELAY_MS = 5000

/**
 * Delay in milliseconds before hiding the AudioPlayer after podcast pause.
 * Longer delay since users often pause podcasts temporarily.
 */
export const PODCAST_PLAYER_HIDE_DELAY_MS = 5000 // 10 * 60 * 1000=10 minutes
