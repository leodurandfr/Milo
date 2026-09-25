// frontend/tests/helpers/audioState.js
/**
 * Builds a complete AudioState — the object the backend publishes in
 * `source/state` — for tests that drive the real stores through their handlers.
 *
 * Complete because the store's schema is strict: a state missing one key is
 * refused whole, so a partial fixture would test the refusal, not the rule.
 */
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

export function makeSession(overrides = {}) {
  return {
    id: 'session-1',
    phase: 'playing',
    title: null,
    artist: null,
    album: null,
    artwork: null,
    senders: [],
    duration_ms: null,
    position: null,
    ...overrides,
  };
}

export function makeAudioState(overrides = {}) {
  const { availability, ...rest } = overrides;
  return {
    source: 'none',
    switching: false,
    service: 'stopped',
    service_error: null,
    availability: {
      ...Object.fromEntries(ALL_AUDIO_SOURCES.map((source) => [source, null])),
      ...availability,
    },
    session: null,
    controls: [],
    resume: null,
    details: null,
    multiroom_enabled: false,
    equalizer_effects_enabled: false,
    ...rest,
  };
}

/** Publish `state` to the unified store exactly as App.vue's `source/state` handler does. */
export function publishState(unifiedStore, overrides = {}) {
  const state = makeAudioState(overrides);
  unifiedStore.updateState({ category: 'source', type: 'state', data: state });
  return state;
}
