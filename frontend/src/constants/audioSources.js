/**
 * Canonical list of audio source identifiers, matching backend
 * `AudioSource` enum in `backend/core/models/audio_state.py`.
 * Order matters for the default dock layout.
 */
export const ALL_AUDIO_SOURCES = ['spotify', 'bluetooth', 'airplay', 'music_library', 'radio', 'cd', 'qobuz', 'tidal', 'podcast', 'mac'];

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
  tidal: 'audioSources.tidal',
  podcast: 'audioSources.podcasts',
  mac: 'audioSources.macOS',
};

/**
 * The sources played from Milō's own browser (AudioSourceLayout + AudioPlayer):
 * their view is where the first thing to play is chosen, so it shows with
 * nothing playing too.
 */
export const BROWSER_SOURCES = ['radio', 'podcast', 'music_library', 'spotify'];

/**
 * Browser sources whose player is a track player: album and artist pages to
 * open from it, and the shuffle / transport / heart row.
 */
export const TRACK_LAYOUT_SOURCES = ['music_library', 'spotify'];
