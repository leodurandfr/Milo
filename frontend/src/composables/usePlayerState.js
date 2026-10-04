// frontend/src/composables/usePlayerState.js
// One reading of a player's state per player. The shell (AudioPlayer, the
// playing bar of the navigation; AudioPlayerFull, the full player) reads its
// source's controls (usePlayerControls) and metadata (usePlayerMetadata) once
// and hands that reading down to the body and the transport it draws
// (PlayerBody, PlayerTransport), which take it instead of reading their own:
// three copies of one state can disagree for a tick — a held title, a settled
// transport — where one copy cannot. A part drawn on its own (the gallery
// mounts the body and the transport alone) is its own player and makes the
// reading itself, for itself and whatever it draws.
import { inject, provide } from 'vue';
import { usePlayerControls } from './usePlayerControls';
import { usePlayerMetadata } from './usePlayerMetadata';

const PLAYER_STATE = Symbol('player-state');

/**
 * The reading of `source`'s player this component belongs to: the one its
 * shell made, else a new one, provided to everything it draws.
 *
 * @param {string} source
 * @returns {{ source: string, controls: ReturnType<typeof usePlayerControls>, metadata: ReturnType<typeof usePlayerMetadata> }}
 */
export function usePlayerState(source) {
  const outer = inject(PLAYER_STATE, null);
  if (outer?.source === source) return outer;
  const state = { source, controls: usePlayerControls(source), metadata: usePlayerMetadata(source) };
  provide(PLAYER_STATE, state);
  return state;
}
