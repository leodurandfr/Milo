/**
 * Canonical list of audio source identifiers, matching backend
 * `AudioSource` enum in `backend/core/models/audio_state.py`.
 * Order matters for the default dock layout.
 */
export const ALL_AUDIO_SOURCES = ['spotify', 'bluetooth', 'airplay', 'music_library', 'radio', 'cd', 'qobuz', 'podcast', 'dlna', 'mac'];

/**
 * i18n label key per source. Not derivable from the id: some labels
 * diverge (mac → macOS, podcast → podcasts).
 */
export const AUDIO_SOURCE_LABEL_KEYS = {
  spotify: 'audioSources.spotify',
  bluetooth: 'audioSources.bluetooth',
  airplay: 'audioSources.airplay',
  music_library: 'audioSources.musicLibrary',
  radio: 'audioSources.radio',
  cd: 'audioSources.cd',
  qobuz: 'audioSources.qobuz',
  podcast: 'audioSources.podcasts',
  dlna: 'audioSources.dlna',
  mac: 'audioSources.macOS',
};
