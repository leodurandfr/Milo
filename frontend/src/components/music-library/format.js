// Formatting helpers for the Music Library views. Durations from Navidrome are
// in seconds; the wire/now-playing convention elsewhere is milliseconds, but
// catalog payloads (album/song/playlist `duration`) are seconds — these helpers
// only ever see seconds.

/** "m:ss" or "h:mm:ss" for a track/album duration in seconds. */
export function formatDuration(totalSeconds) {
  const s = Math.max(0, Math.floor(totalSeconds || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h > 0) {
    return `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
  }
  return `${m}:${String(sec).padStart(2, '0')}`;
}

/** Whole minutes (rounded) for an album/playlist meta line ("61 min"). */
export function totalMinutes(totalSeconds) {
  return Math.round((totalSeconds || 0) / 60);
}
