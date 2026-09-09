// frontend/src/stores/lyricsStore.js
// Lyrics for the now-playing track, fetched on demand when the Lyrics app modal
// opens (and refetched when the track changes while it's open). Keys off the
// unified store's metadata, so it works for any rich-metadata source — except
// Radio, which carries its Shazam/in-band recognized track under track_title/
// track_artist rather than the canonical title/artist (see radioStore.trackInfo;
// the station itself is a continuous stream, not a track).
import { defineStore } from 'pinia';
import { computed, ref, watch } from 'vue';
import { useUnifiedAudioStore } from './unifiedAudioStore';
import { useSnapcastStore } from './snapcastStore';
import { apiCall } from '@/services/apiCall';

// Mac carries no title/artist at all and a podcast is spoken word, not a song.
// Bluetooth is the deliberate one: AVRCP does give a title and artist, but the
// playhead behind them is a notification the sender may push coarsely or not at
// all, and synced lyrics against a playhead that freezes is worse than none.
const LYRICS_INCOMPATIBLE_SOURCES = new Set(['bluetooth', 'mac', 'podcast']);

export function isLyricsCompatible(activeSource) {
  return !!activeSource && activeSource !== 'none' && !LYRICS_INCOMPATIBLE_SOURCES.has(activeSource);
}

export function getTrackIdentity(activeSource, metadata) {
  const meta = metadata || {};
  if (activeSource === 'radio') {
    return { artist: meta.track_artist || '', title: meta.track_title || '' };
  }
  return { artist: meta.artist || '', title: meta.title || '' };
}

export const useLyricsStore = defineStore('lyrics', () => {
  const isOpen = ref(false);
  const loading = ref(false);
  const found = ref(false);
  const synced = ref(null); // [{ t: <ms>, line: <str> }] | null
  const plain = ref(null); // string | null

  // The track the current lyrics belong to (for the modal's empty-state copy).
  const trackArtist = ref('');
  const trackTitle = ref('');

  // Snapserver's stream buffer, in ms — the last value read from the server
  // config, kept for the session. See syncOffsetMs.
  const multiroomBufferMs = ref(null);

  // The "Title · Artist" line the "no lyrics found for" empty state shows below
  // its message — i.e. the exact track loadLyrics() searched with. The loading
  // screen deliberately shows no track, just what it's doing.
  const trackLine = computed(() => `${trackTitle.value} · ${trackArtist.value}`);

  // What synced lyrics must add to the source position to line up with what the
  // room actually hears.
  //
  // Snapserver schedules every chunk `buffer_ms` into the future so all speakers
  // play it at the same instant, so in multiroom the sound heard at any moment is
  // what the source wrote buffer_ms ago — while metadata.position reports where
  // the source *is*. The highlight is therefore held BACK by the buffer (the
  // offset is negative); it used to be pushed forward by a fixed 500 ms, which
  // doubled the error instead of cancelling it. Direct mode has no such stage,
  // and the DAC-side latency it does have is the same in both modes, so it is
  // not part of the difference and is not modelled.
  //
  // The value is read, never guessed: buffer_ms is a user-facing setting (presets
  // 180 / 700 / 1500 ms, free between 150 and 3000), so a constant could only
  // ever be right for one of them. Per-client delay_ms is deliberately left out —
  // it shifts one speaker, and nothing tells us which room the reader is in.
  const syncOffsetMs = computed(() => {
    const unifiedStore = useUnifiedAudioStore();
    if (!unifiedStore.systemState.multiroom_enabled) return 0;
    return -(multiroomBufferMs.value ?? 0);
  });

  // A failed read keeps the last known value (snapserver restarting is exactly
  // when this can fail) — a one-preset-stale offset beats none at all.
  async function loadSyncOffset() {
    const unifiedStore = useUnifiedAudioStore();
    if (!unifiedStore.systemState.multiroom_enabled) return;
    const config = await useSnapcastStore().fetchServerConfig();
    if (config?.buffer_ms) multiroomBufferMs.value = config.buffer_ms;
  }

  // Driven by state, not by open(): multiroom can be switched on from anywhere
  // (a second browser, Milo-Mac) while the view is already up, and with no value
  // read the offset silently degrades to 0 — the largest error there is, and the
  // one this whole computation exists to remove. Every (open × multiroom) edge
  // asks again, which is also what retries a read that failed with nothing to
  // keep. Not watched any finer: the buffer itself only moves from the multiroom
  // settings page, a deliberate act that ends in a snapserver restart.
  watch(
    () => {
      const unifiedStore = useUnifiedAudioStore();
      return isOpen.value && !!unifiedStore.systemState.multiroom_enabled;
    },
    (shouldRead) => { if (shouldRead) loadSyncOffset(); }
  );

  let abortController = null;

  // Per-track result cache (artist|||title → {found, synced, plain}). Reopening
  // the view for a track already looked up this session resolves instantly, with
  // no loader — lyrics never change, so a memory cache is enough for the appliance.
  const cache = new Map();

  const scrollPositions = new Map();
  function getScrollPosition(key) {
    return scrollPositions.get(key) ?? 0;
  }
  function saveScrollPosition(key, top) {
    scrollPositions.set(key, top);
  }

  async function loadLyrics() {
    const unifiedStore = useUnifiedAudioStore();
    const activeSource = unifiedStore.systemState.active_source;
    const meta = unifiedStore.systemState.metadata || {};
    const identity = getTrackIdentity(activeSource, meta);
    const artist = identity.artist.trim();
    const title = identity.title.trim();

    if (abortController) {
      abortController.abort();
      abortController = null;
    }

    trackArtist.value = artist;
    trackTitle.value = title;

    // Nothing playing, or a mute receiver with no metadata → empty state, no request.
    if (!artist || !title) {
      found.value = false;
      synced.value = null;
      plain.value = null;
      loading.value = false;
      return;
    }

    // Cache hit → populate straight from memory, no request and no loader flash.
    const key = `${artist}|||${title}`;
    const cached = cache.get(key);
    if (cached) {
      found.value = cached.found;
      synced.value = cached.synced;
      plain.value = cached.plain;
      loading.value = false;
      return;
    }

    // Miss → reset before the request resolves so the previous track's lyrics
    // never flash on a new one (Option A: refetch per track change).
    found.value = false;
    synced.value = null;
    plain.value = null;

    abortController = new AbortController();
    const { signal } = abortController;
    loading.value = true;

    const result = await apiCall.get('/api/lyrics', {
      category: 'lyrics',
      message: 'Failed to fetch lyrics',
      params: {
        artist,
        title,
        album: meta.album || '',
        duration: meta.duration || 0,
      },
      signal,
      // An unreachable LRCLIB comes back as a 200 with status=error; checkStatus
      // turns it into ok:false so it's logged and, crucially, not cached below
      // as a genuine "no lyrics" for this track. A warning, not an error: the
      // upstream service being briefly down is not an appliance fault.
      checkStatus: true,
      logLevel: 'warn',
    });

    // A newer loadLyrics() aborted this request → leave its state untouched.
    if (signal.aborted) return;

    loading.value = false;

    // On a failure (LRCLIB down, backend unreachable) found stays false, so the
    // UI shows the same clean "no lyrics" empty state — but nothing is cached,
    // so reopening the view retries instead of freezing the outage for the session.
    if (result.ok && result.data?.status === 'success') {
      found.value = !!result.data.found;
      synced.value = result.data.synced || null;
      plain.value = result.data.plain || null;
      // Cache the resolved lookup (found or not) so reopening this track is instant.
      cache.set(key, { found: found.value, synced: synced.value, plain: plain.value });
    }
  }

  // Opened from the dock, over whichever source view is currently on screen
  // (AudioSourceView swaps its content-container slot to Lyrics — see there).
  function open() {
    isOpen.value = true;
    loadLyrics();
  }
  function close() {
    isOpen.value = false;
  }

  return {
    isOpen, open, close,
    loading, found, synced, plain, trackArtist, trackTitle, trackLine, loadLyrics,
    syncOffsetMs, loadSyncOffset,
    getScrollPosition, saveScrollPosition
  };
});
