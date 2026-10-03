// frontend/src/utils/transport.js
// What the play/pause button shows is what a press does, never whether sound
// is flowing — the split AVPlayer makes between `rate` and `timeControlStatus`,
// and Media3 between `playWhenReady` and buffering. A loading session is on its
// way to playing (the phase table only reaches `loading` with playback asked
// for), so it shows pause and a press pauses it. Reading the glyph from
// `playing` alone flipped it to play for the length of every track change.

/** @param {string|null|undefined} phase - the session's phase */
export function pausesOnPress(phase) {
  return phase === 'playing' || phase === 'loading';
}
