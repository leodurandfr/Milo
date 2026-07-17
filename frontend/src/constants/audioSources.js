/**
 * Canonical list of audio source identifiers, matching backend
 * `AudioSource` enum in `backend/core/models/audio_state.py`.
 * Order matters for the default dock layout.
 */
export const ALL_AUDIO_SOURCES = ['spotify', 'bluetooth', 'radio', 'podcast', 'airplay', 'mac', 'cd', 'dlna', 'qobuz'];

/**
 * i18n label key per source. Not derivable from the id: some labels
 * diverge (mac → macOS, podcast → podcasts).
 */
export const AUDIO_SOURCE_LABEL_KEYS = {
  spotify: 'audioSources.spotify',
  bluetooth: 'audioSources.bluetooth',
  radio: 'audioSources.radio',
  podcast: 'audioSources.podcasts',
  airplay: 'audioSources.airplay',
  mac: 'audioSources.macOS',
  cd: 'audioSources.cd',
  dlna: 'audioSources.dlna',
  qobuz: 'audioSources.qobuz',
};
