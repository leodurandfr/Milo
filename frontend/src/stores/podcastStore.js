// frontend/src/stores/podcastStore.js
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
import { apiCall } from '@/services/apiCall';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { positionAt } from '@/composables/useSourceProgress';

// Maximum progress entries to cache (prevents unbounded memory growth)
const MAX_PROGRESS_ENTRIES = 200;

export const usePodcastStore = defineStore('podcast', () => {
  const unifiedStore = useUnifiedAudioStore();

  // === PLAYBACK STATE ===
  // The podcast's content, while podcast is the selected source: the episode
  // (live, or the one kept to resume) and the speed.
  const podcastDetails = computed(() => {
    const state = unifiedStore.systemState;
    if (state.source !== 'podcast' || state.details?.kind !== 'podcast') return null;
    return state.details;
  });
  const currentEpisode = computed(() => podcastDetails.value?.episode ?? null);
  // Canonical list fetched from backend (GET /api/podcast/playback-speeds).
  // Safe fallback used until the first successful fetch.
  const playbackSpeeds = ref([1.0]);
  const pendingEpisodeUuid = ref(null); // Optimistic loading state before WebSocket confirms

  // The current episode's playhead, as the state describes it: the live
  // session's anchor, or the resume point when no session runs.
  const playhead = computed(() => {
    const uuid = currentEpisode.value?.uuid;
    if (!uuid) return null;
    const { session, resume } = unifiedStore.systemState;
    if (session) {
      return {
        uuid, sessionId: session.id, anchor: session.position,
        phase: session.phase, durationMs: session.duration_ms,
      };
    }
    if (resume) {
      return { uuid, sessionId: null, positionMs: resume.position_ms, durationMs: resume.duration_ms };
    }
    return null;
  });

  /** Where `view` puts the playhead now (ms), or null when it has none. */
  function playheadMs(view) {
    return view.sessionId !== null
      ? positionAt(view.anchor, view.phase, view.durationMs, Date.now())
      : view.positionMs;
  }

  // The current episode's position and duration (ms), for its EpisodeCard.
  // Read once per state: the card shows it only while the playhead stands
  // still (loading, paused, stopped) — a playing episode reads "now playing".
  const currentEpisodeProgress = computed(() => {
    const view = playhead.value;
    return view ? { positionMs: playheadMs(view), durationMs: view.durationMs } : null;
  });

  // === PROGRESS CACHE ===
  // Reactive cache of playback progress for all episodes
  // Key: episode_uuid, Value: { position, duration, last_played }
  const progressCache = ref(new Map());

  // === SUBSCRIPTIONS CACHE ===
  // Cache subscriptions as Map for O(1) lookups by uuid
  const subscriptions = ref(new Map()); // Map<uuid, subscription>
  const latestSubscriptionEpisodes = ref([]);

  // Sorted array for rendering (the Map above stays the source of truth)
  const subscriptionsList = computed(() => {
    return Array.from(subscriptions.value.values()).sort((a, b) =>
      (a.name || '').localeCompare(b.name || '')
    );
  });
  const subscriptionsListLoaded = ref(false); // True when subscriptions list is loaded (no discovery API call)
  const subscriptionsLoaded = ref(false); // True when latest episodes are also loaded (with discovery API call)
  const subscriptionsFullLoading = ref(false); // Guard against concurrent loadSubscriptions calls

  // === SEARCH STATE ===
  // Persisted across navigation within Podcasts module
  const searchTerm = ref('');
  const lastSearchTerm = ref('');
  const searchResults = ref({
    podcasts: []
  });
  const searchPagination = ref({
    podcasts: { total: 0, pages: 0 }
  });
  const searchCurrentPage = ref({
    podcasts: 1
  });
  const hasSearched = ref(false);
  const searchLoading = ref(false);
  const searchLoadingMore = ref({
    podcasts: false
  });

  // === CATALOGUE STATE ===
  // The `api_error` key a discovery route returns: Apple did not
  // answer. Not a claim about the link — subscriptions are local and an
  // episode still plays from its own host while this is set.
  const apiError = ref(false);

  // === SETTINGS ===
  // Note: Language/country are centralized in /var/lib/milo/settings.json (via settingsStore)
  const settings = ref({
    playback_speed: 1.0
  });

  // The speed the state publishes beside an episode; the saved setting when
  // there is none.
  const playbackSpeed = computed(() => podcastDetails.value?.speed ?? settings.value.playback_speed);

  // === COMPUTED ===
  const hasSubscriptions = computed(() => subscriptions.value.size > 0);

  // === PLAYBACK ACTIONS ===

  async function play(episodeUuid) {
    // Set pending immediately for instant UI feedback (spinner)
    pendingEpisodeUuid.value = episodeUuid;
    const played = await unifiedStore.sendCommand('podcast', 'play_episode', {
      episode_uuid: episodeUuid,
    });
    if (!played) {
      pendingEpisodeUuid.value = null;
      throw new Error('Failed to play episode');
    }
    // Cleared by the first state that carries this episode (watch below).
  }

  async function pause() {
    await unifiedStore.sendCommand('podcast', 'pause');
  }

  async function resume() {
    await unifiedStore.sendCommand('podcast', 'resume');
  }

  async function setSpeed(speed) {
    // The applied speed comes back in the state (`details.speed`), which also
    // snaps an off-grid request to the nearest valid value.
    await unifiedStore.sendCommand('podcast', 'set_speed', { speed });
  }

  async function loadPlaybackSpeeds() {
    const result = await apiCall.get('/api/podcast/playback-speeds', {
      category: 'store',
      message: 'Error loading playback speeds',
      checkStatus: true,
    });
    if (result.ok && Array.isArray(result.data.speeds)) {
      playbackSpeeds.value = result.data.speeds;
    }
  }

  // === SETTINGS ACTIONS ===

  async function loadSettings() {
    const result = await apiCall.get('/api/podcast/settings', {
      category: 'store',
      message: 'Error loading settings',
    });
    if (result.ok && result.data.settings) {
      settings.value = { ...settings.value, ...result.data.settings };
    }
  }

  // === WEBSOCKET STATE HANDLERS ===

  // The optimistic spinner ends with the first state naming the episode asked
  // for — including when it was already the resume episode, so this watches
  // every state rather than a change of episode.
  watch(() => unifiedStore.systemState, () => {
    if (pendingEpisodeUuid.value && currentEpisode.value?.uuid === pendingEpisodeUuid.value) {
      pendingEpisodeUuid.value = null;
    }
  }, { flush: 'sync' });

  // The session that ended at the end of its file: its last playhead would
  // overwrite the "listened" mark handleSessionEnded has just written.
  let endedSessionId = null;

  /** Record `view`'s playhead, as of now, in the progress cache (seconds). */
  function recordProgress(view) {
    if (!view || (view.sessionId !== null && view.sessionId === endedSessionId)) return;
    const positionMs = playheadMs(view);
    if (positionMs == null || view.durationMs == null) return;
    progressCache.value.set(view.uuid, {
      position: Math.floor(positionMs / 1000),
      duration: Math.floor(view.durationMs / 1000),
      last_played: Date.now()
    });
    enforceProgressCacheLimit();
  }

  // Keeps EpisodeCard's "X min left" right for an episode once it is no longer
  // the current one. The anchor is republished only on a discontinuity, so the
  // episode being left is recorded as of the moment it is left: the state that
  // replaces it is the last word on where it stopped.
  watch(playhead, (current, previous) => {
    recordProgress(previous);
    recordProgress(current);
  }, { flush: 'sync' });

  /**
   * `source/session_ended`, before the state that follows it. An episode
   * played to its end (reason 'eof') is marked listened in the cache, so its
   * EpisodeCard shows the badge without a refetch.
   */
  function handleSessionEnded({ source, session_id, reason }) {
    if (source !== 'podcast' || reason !== 'eof') return;
    const view = playhead.value;
    if (!view || view.sessionId !== session_id) return;
    endedSessionId = session_id;
    // Merged, so the card keeps the duration it already knew.
    progressCache.value.set(view.uuid, {
      ...(progressCache.value.get(view.uuid) || {}),
      completed: true,
      last_played: Date.now()
    });
  }

  // === PENDING STATE HELPER ===
  function isEpisodePending(episodeUuid) {
    return pendingEpisodeUuid.value === episodeUuid;
  }

  // === PROGRESS CACHE HELPERS ===

  /**
   * Enforce cache limit by evicting oldest entries (LRU based on last_played)
   * Preserves the currently playing episode
   */
  function enforceProgressCacheLimit() {
    if (progressCache.value.size <= MAX_PROGRESS_ENTRIES) return;

    const currentUuid = currentEpisode.value?.uuid;
    const entries = Array.from(progressCache.value.entries())
      .filter(([uuid]) => uuid !== currentUuid)
      .sort((a, b) => (a[1].last_played || 0) - (b[1].last_played || 0));

    // Remove oldest entries until under limit
    const toRemove = progressCache.value.size - MAX_PROGRESS_ENTRIES;
    for (let i = 0; i < toRemove && i < entries.length; i++) {
      progressCache.value.delete(entries[i][0]);
    }
  }

  function getEpisodeProgress(episodeUuid) {
    return progressCache.value.get(episodeUuid) || null;
  }

  function enrichEpisodesWithProgress(episodes) {
    // Populate progress cache from API data (when loading episodes)
    // This initializes the reactive cache with existing progress
    if (!Array.isArray(episodes)) return episodes;

    episodes.forEach((episode) => {
      if (episode.playback_progress) {
        const progress = episode.playback_progress;
        if (
          progress.position !== undefined &&
          progress.duration !== undefined
        ) {
          progressCache.value.set(episode.uuid, {
            position: progress.position,
            duration: progress.duration,
            completed: progress.completed === true,
            last_played: progress.last_played || Date.now()
          });
        }
      }
    });

    enforceProgressCacheLimit();
    return episodes;
  }

  // === SUBSCRIPTIONS ACTIONS ===

  function arrayToSubscriptionsMap(arr) {
    const map = new Map();
    for (const sub of arr) {
      map.set(sub.uuid, sub);
    }
    return map;
  }

  // Preload subscriptions list only (no discovery API call)
  // Called at app startup for instant hasSubscriptions check
  //
  // force=true refetches even when already loaded (reconnect/tab-visible
  // resync — favorite_* WS deltas may have been missed) and invalidates the
  // latest-episodes cache so the next HomeView open refetches with fresh
  // subscription and progress state (lazy: no discovery API call during the resync).
  async function preloadSubscriptionsList({ force = false } = {}) {
    if (subscriptionsListLoaded.value && !force) return;
    const result = await apiCall.get('/api/podcast/subscriptions', {
      category: 'store',
      message: 'Error preloading subscriptions list',
    });
    if (result.ok) {
      subscriptions.value = arrayToSubscriptionsMap(
        result.data.subscriptions || []
      );
      subscriptionsListLoaded.value = true;
      if (force) {
        subscriptionsLoaded.value = false;
      }
    }
  }

  // Full load - fetches subscriptions list + latest episodes (discovery API call)
  // Called when HomeView opens
  async function loadSubscriptions(forceRefresh = false) {
    // Return cached data if fully loaded and not forcing refresh
    if (subscriptionsLoaded.value && !forceRefresh) {
      return {
        subscriptions: subscriptionsList.value,
        latestEpisodes: latestSubscriptionEpisodes.value
      };
    }

    // Prevent concurrent calls (HomeView + SubscriptionsView mounting simultaneously)
    if (subscriptionsFullLoading.value) return;
    subscriptionsFullLoading.value = true;

    try {
      // Reuse subscriptions list if already preloaded, otherwise fetch
      if (!subscriptionsListLoaded.value) {
        const subsResult = await apiCall.get('/api/podcast/subscriptions', {
          category: 'store',
          message: 'Error loading subscriptions',
        });
        if (!subsResult.ok) return false;
        subscriptions.value = arrayToSubscriptionsMap(
          subsResult.data.subscriptions || []
        );
        subscriptionsListLoaded.value = true;
      }

      // Fetch latest episodes (discovery API call) if user has subscriptions
      if (subscriptions.value.size > 0) {
        const epResult = await apiCall.get(
          '/api/podcast/subscriptions/latest-episodes',
          {
            category: 'store',
            message: 'Error loading latest subscription episodes',
            params: { limit: 20 },
          },
        );
        if (epResult.ok) {
          // Hide episodes already listened to from "new episodes"
          latestSubscriptionEpisodes.value = enrichEpisodesWithProgress(
            epResult.data.results || []
          ).filter((ep) => !ep.playback_progress?.completed);
        }
      } else {
        latestSubscriptionEpisodes.value = [];
      }

      subscriptionsLoaded.value = true;
      return {
        subscriptions: subscriptionsList.value,
        latestEpisodes: latestSubscriptionEpisodes.value,
      };
    } finally {
      subscriptionsFullLoading.value = false;
    }
  }

  // Called after a local subscribe AND from the favorite_added WS handler
  // (cross-device sync) — upsert so a re-subscribe refreshes metadata
  function addSubscription(subscription) {
    subscriptions.value.set(subscription.uuid, subscription);
    // Mark as needing refresh to fetch latest episodes on next HomeView load
    subscriptionsLoaded.value = false;
  }

  function removeSubscription(uuid) {
    subscriptions.value.delete(uuid);
    // Also remove episodes from this podcast in latestSubscriptionEpisodes
    latestSubscriptionEpisodes.value = latestSubscriptionEpisodes.value.filter(
      (ep) => ep.podcast?.uuid !== uuid
    );
  }

  // === SEARCH ACTIONS ===

  const SEARCH_PAGE_SIZE = '25';

  function searchParams(page) {
    return new URLSearchParams({
      term: searchTerm.value,
      limit: SEARCH_PAGE_SIZE,
      page: String(page)
    });
  }

  // Cancels the request a previous keystroke left in flight. Two uncached
  // iTunes queries can invert, and the response that hurts is a failed one:
  // its `api_error` replaces a fresh result list with the "catalogue
  // unavailable" panel until the next search succeeds.
  let searchAbortController = null;

  async function search() {
    if (searchAbortController) searchAbortController.abort();

    // Held locally as well: after the await the store's controller may already
    // belong to a newer search, and only identity tells the two apart.
    const ctrl = new AbortController();
    searchAbortController = ctrl;

    searchLoading.value = true;
    const result = await apiCall.get('/api/podcast/search', {
      category: 'podcast',
      message: 'Error searching podcasts',
      params: searchParams(1),
      signal: ctrl.signal
    });

    // Superseded: the newer search owns the spinner and the controller, so
    // clearing them here would end its loading state early.
    if (searchAbortController !== ctrl) return;
    searchAbortController = null;

    if (result.ok) {
      const data = result.data;
      if (data.api_error) {
        apiError.value = true;
      } else {
        apiError.value = false;
        setSearchResults(data.podcasts, data.pagination);
      }
    }
    searchLoading.value = false;
  }

  async function loadMoreSearchResults() {
    if (searchLoadingMore.value.podcasts ||
        searchCurrentPage.value.podcasts >= searchPagination.value.podcasts.pages) return;

    searchLoadingMore.value.podcasts = true;
    const result = await apiCall.get('/api/podcast/search', {
      category: 'podcast',
      message: 'Error loading more podcasts',
      params: searchParams(searchCurrentPage.value.podcasts + 1)
    });
    if (result.ok) {
      appendSearchResults(result.data.podcasts || []);
    }
    searchLoadingMore.value.podcasts = false;
  }

  function setSearchResults(podcasts, pagination) {
    searchResults.value = {
      podcasts: podcasts || []
    };
    searchPagination.value = pagination || {
      podcasts: { total: 0, pages: 0 }
    };
    searchCurrentPage.value = { podcasts: 1 };
    hasSearched.value = true;
    lastSearchTerm.value = searchTerm.value;
  }

  function appendSearchResults(items) {
    searchResults.value.podcasts = [
      ...searchResults.value.podcasts,
      ...items
    ];
    searchCurrentPage.value.podcasts++;
  }

  function clearSearch() {
    // Dropping the controller is what neutralises an in-flight search: the
    // identity check then rejects its response instead of repopulating the
    // state this call just wiped.
    if (searchAbortController) {
      searchAbortController.abort();
      searchAbortController = null;
    }
    searchTerm.value = '';
    lastSearchTerm.value = '';
    searchResults.value = { podcasts: [] };
    searchPagination.value = {
      podcasts: { total: 0, pages: 0 }
    };
    searchCurrentPage.value = { podcasts: 1 };
    hasSearched.value = false;
    searchLoading.value = false;
    searchLoadingMore.value = { podcasts: false };
    apiError.value = false;
  }

  // === RETURN ===
  // The now-playing slice is a view of unifiedStore.systemState, which App.vue
  // heals first: only the subscriptions list has deltas of its own to refetch.
  async function resync() {
    await preloadSubscriptionsList({ force: true });
  }

  return {
    resync,
    // State
    currentEpisode,
    currentEpisodeProgress,
    playbackSpeed,
    playbackSpeeds,
    pendingEpisodeUuid,
    progressCache,
    subscriptions: subscriptionsList, // exposed as array for iteration
    latestSubscriptionEpisodes,
    subscriptionsLoaded,

    // Catalogue state
    apiError,

    // Search state
    searchTerm,
    lastSearchTerm,
    searchResults,
    searchPagination,
    searchCurrentPage,
    hasSearched,
    searchLoading,
    searchLoadingMore,

    // Computed
    hasSubscriptions,

    // Actions
    play,
    pause,
    resume,
    setSpeed,
    loadPlaybackSpeeds,
    loadSettings,
    handleSessionEnded,

    // Pending state helper
    isEpisodePending,

    // Progress cache helpers
    getEpisodeProgress,
    enrichEpisodesWithProgress,

    // Subscriptions
    preloadSubscriptionsList,
    loadSubscriptions,
    addSubscription,
    removeSubscription,

    // Search
    search,
    loadMoreSearchResults,
    setSearchResults,
    appendSearchResults,
    clearSearch
  };
});
