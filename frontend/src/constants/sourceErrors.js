/**
 * i18n key per `source/error` reason code.
 *
 * Mirrors `SourceErrorReason` in `backend/core/models/ws_events.py`: the
 * backend sends a code because the banner is read by a user who picked one of
 * eight languages, and the sentence therefore belongs here. The technical
 * detail stays on the backend, in the log line each producer writes next to
 * its broadcast.
 *
 * A code with no entry falls back to the generic string, so the banner is
 * never empty; `tests/architecture/sourceErrors.test.js` keeps the two lists
 * in step.
 */
export const SOURCE_ERROR_KEYS = {
  stream_disconnected: 'audioSources.errors.streamDisconnected',
  stream_load_failed: 'audioSources.errors.streamLoadFailed',
  track_load_failed: 'audioSources.errors.trackLoadFailed',
  playback_failed: 'audioSources.errors.playbackFailed',
  service_unreachable: 'audioSources.errors.serviceUnreachable',
};

export const SOURCE_ERROR_FALLBACK_KEY = 'audioSources.errors.generic';
