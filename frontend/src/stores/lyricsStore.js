// frontend/src/stores/lyricsStore.js
// Lyrics for the now-playing track, fetched on demand when the Lyrics app modal
// opens (and refetched when the track changes while it's open). Source-agnostic:
// keys off the unified store's metadata, so it works for any rich-metadata source.
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { useUnifiedAudioStore } from './unifiedAudioStore';
import { apiCall } from '@/services/apiCall';

export const useLyricsStore = defineStore('lyrics', () => {
  const loading = ref(false);
  const found = ref(false);
  const synced = ref(null); // [{ t: <ms>, line: <str> }] | null
  const plain = ref(null); // string | null

  // The track the current lyrics belong to (for the modal's empty-state copy).
  const trackArtist = ref('');
  const trackTitle = ref('');

  let abortController = null;

  // Per-track result cache (artist|||title → {found, synced, plain}). Reopening
  // the view for a track already looked up this session resolves instantly, with
  // no loader — lyrics never change, so a memory cache is enough for the appliance.
  const cache = new Map();

  async function loadLyrics() {
    const unifiedStore = useUnifiedAudioStore();
    const meta = unifiedStore.systemState.metadata || {};
    const artist = (meta.artist || '').trim();
    const title = (meta.title || '').trim();

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
    });

    // A newer loadLyrics() aborted this request → leave its state untouched.
    if (signal.aborted) return;

    loading.value = false;

    // On a real failure the backend already fails open (found:false); leaving
    // found=false here surfaces the same clean "no lyrics" empty state.
    if (result.ok && result.data?.status === 'success') {
      found.value = !!result.data.found;
      synced.value = result.data.synced || null;
      plain.value = result.data.plain || null;
      // Cache the resolved lookup (found or not) so reopening this track is instant.
      cache.set(key, { found: found.value, synced: synced.value, plain: plain.value });
    }
  }

  return { loading, found, synced, plain, trackArtist, trackTitle, loadLyrics };
});
