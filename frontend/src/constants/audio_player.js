// frontend/src/constants/audio_player.js
// Shared constants for audio player behavior across sources

/**
 * Delay in milliseconds before hiding the AudioPlayer after playback stops.
 * Used by Radio (stop) and Podcast (pause) to show the player briefly
 * before hiding it, allowing users to quickly resume if needed.
 */
export const PLAYER_HIDE_DELAY_MS = 5000
