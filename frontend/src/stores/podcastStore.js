// frontend/src/stores/podcastStore.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import { apiCall } from '@/services/apiCall';

// Maximum progress entries to cache (prevents unbounded memory growth)
const MAX_PROGRESS_ENTRIES = 200;

export const usePodcastStore = defineStore('podcast', () => {
  // === PLAYBACK STATE ===
  const currentEpisode = ref(null);
  const displayEpisode = ref(null); // Preserved during fade-out animation
  const playbackSpeed = ref(1.0);
  // Canonical list fetched from backend (GET /api/podcast/playback-speeds).
  // Safe fallback used until the first successful fetch.
  const playbackSpeeds = ref([1.0]);
  const pendingEpisodeUuid = ref(null); // Optimistic loading state before WebSocket confirms

  // === PROGRESS CACHE ===
  // Reactive cache of playback progress for all episodes
  // Key: episode_uuid, Value: { position, duration, last_played }
  const progressCache = ref(new Map());

  // === SUBSCRIPTIONS CACHE ===
  // Cache subscriptions as Map for O(1) lookups by uuid
  const subscriptions = ref(new Map()); // Map<uuid, subscription>
  const latestSubscriptionEpisodes = ref([]);

  // Computed array for iteration (sorted by name)
  const subscriptionsList = computed(() => {
    return Array.from(subscriptions.value.values()).sort((a, b) =>
      (a.name || '').localeCompare(b.name || '')
    );
  });
  const subscriptionsListLoaded = ref(false); // True when subscriptions list is loaded (no Taddy call)
  const subscriptionsLoaded = ref(false); // True when latest episodes are also loaded (with Taddy call)
  const subscriptionsFullLoading = ref(false); // Guard against concurrent loadSubscriptions calls

  // === SEARCH STATE ===
  // Persisted across navigation within Podcasts module
  const searchTerm = ref('');
  const lastSearchTerm = ref('');
  const searchFilters = ref({
    language: '',
    duration: '',
    genre: ''
  });
  const searchResults = ref({
    podcasts: [],
    episodes: []
  });
  const searchPagination = ref({
    podcasts: { total: 0, pages: 0 },
    episodes: { total: 0, pages: 0 }
  });
  const searchCurrentPage = ref({
    podcasts: 1,
    episodes: 1
  });
  const hasSearched = ref(false);
  const searchLoading = ref(false);
  const searchLoadingMore = ref({
    podcasts: false,
    episodes: false
  });

  // === NETWORK STATE ===
  const networkError = ref(false);

  // === SETTINGS ===
  // Note: Language/country are centralized in /var/lib/milo/settings.json (via settingsStore)
  const settings = ref({
    playback_speed: 1.0
  });

  // === COMPUTED ===
  const hasSubscriptions = computed(() => subscriptions.value.size > 0);

  // === PLAYBACK ACTIONS ===

  async function play(episodeUuid) {
    // Set pending immediately for instant UI feedback (spinner)
    pendingEpisodeUuid.value = episodeUuid;
    const result = await apiCall.post('/api/podcast/play', {
      episode_uuid: episodeUuid,
    }, {
      category: 'store',
      message: 'Error playing episode',
    });
    if (!result.ok || !result.data.success) {
      pendingEpisodeUuid.value = null;
      throw new Error(result.error?.detail || 'Failed to play episode');
    }
    // State will be updated via WebSocket broadcast from backend
    // pendingEpisodeUuid will be cleared in _applyMetadata()
  }

  async function pause() {
    await apiCall.post('/api/podcast/pause', null, {
      category: 'store',
      message: 'Error pausing',
    });
  }

  async function resume() {
    await apiCall.post('/api/podcast/resume', null, {
      category: 'store',
      message: 'Error resuming',
    });
  }

  async function setSpeed(speed) {
    const result = await apiCall.post('/api/podcast/speed', { speed }, {
      category: 'store',
      message: 'Error setting speed',
    });
    if (result.ok && result.data.success) {
      playbackSpeed.value = result.data.speed;
    }
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
      playbackSpeed.value = result.data.settings.playback_speed || 1.0;
    }
  }

  // === WEBSOCKET STATE HANDLER ===

  // Applies an already-flat metadata object to the podcast store state.
  // Callers are responsible for extracting metadata from whichever envelope
  // they receive (initial_state payload vs source.state_changed event).
  function _applyMetadata(metadata) {
    // Handle episode end FIRST (before updating any other state)
    if (metadata.episode_ended === true) {
      // Flip the just-finished episode to "already listened" in the reactive
      // cache so its EpisodeCard shows the badge without a re-fetch. Capture the
      // uuid before nulling currentEpisode below; merge to preserve position/duration.
      const finishedUuid = metadata.episode_uuid;
      if (finishedUuid && metadata.completed === true) {
        progressCache.value.set(finishedUuid, {
          ...(progressCache.value.get(finishedUuid) || {}),
          completed: true,
          last_played: Date.now()
        });
      }

      // Clear currentEpisode immediately (for state consistency)
      currentEpisode.value = null;

      // DON'T clear displayEpisode yet - preserve metadata during fade-out animation
      // The parent component will call clearDisplayEpisode() after animation completes

      // RETURN EARLY - don't process any other updates from this event
      return;
    }

    // Update episode metadata (only if NOT an episode_ended event)
    if (metadata.current_episode) {
      currentEpisode.value = metadata.current_episode;
      displayEpisode.value = metadata.current_episode;

      // Clear pending state - WebSocket has confirmed playback
      if (pendingEpisodeUuid.value === metadata.current_episode.uuid) {
        pendingEpisodeUuid.value = null;
      }
    }
    // Backend emits position/duration in milliseconds (wire convention shared
    // with all other audio sources). Live position for the playing episode is
    // read directly from unifiedStore.systemState.metadata; here we only derive
    // seconds for the per-episode progress cache (EpisodeCard "X min left").
    const positionSeconds = metadata.position !== undefined
      ? Math.floor(metadata.position / 1000)
      : undefined;
    const durationSeconds = metadata.duration !== undefined
      ? Math.floor(metadata.duration / 1000)
      : undefined;

    if (metadata.playback_speed !== undefined) {
      playbackSpeed.value = metadata.playback_speed;
    }

    // Update progress cache for reactive updates in EpisodeCard
    if (
      metadata.episode_uuid &&
      positionSeconds !== undefined &&
      durationSeconds !== undefined
    ) {
      progressCache.value.set(metadata.episode_uuid, {
        position: positionSeconds,
        duration: durationSeconds,
        last_played: Date.now()
      });
      enforceProgressCacheLimit();
    }

    // Note: is_playing and is_buffering are read from unifiedAudioStore.systemState.metadata
    // They are updated by the unified audio state machine via WebSocket
  }

  // Called from App.vue on system.initial_state / system.state_changed when
  // full_state.active_source === 'podcast' and metadata is already flat.
  function handleInitialMetadata(metadata) {
    _applyMetadata(metadata);
  }

  // Called from App.vue on source.state_changed; metadata is nested under
  // event.data.metadata (the event also carries old_state/new_state).
  function handleSourceEvent(event) {
    if (event.origin !== 'podcast') return;
    if (event.type === 'state_changed') {
      _applyMetadata(event.data?.metadata || {});
    }
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

  // Helper to convert array to Map
  function arrayToSubscriptionsMap(arr) {
    const map = new Map();
    for (const sub of arr) {
      map.set(sub.uuid, sub);
    }
    return map;
  }

  // Preload subscriptions list only (no Taddy API call)
  // Called at app startup for instant hasSubscriptions check
  //
  // force=true refetches even when already loaded (reconnect/tab-visible
  // resync — favorite_* WS deltas may have been missed) and invalidates the
  // latest-episodes cache so the next HomeView open refetches with fresh
  // subscription and progress state (lazy: no Taddy call during the resync).
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

  // Full load - fetches subscriptions list + latest episodes (Taddy API call)
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

      // Fetch latest episodes (Taddy API call) if user has subscriptions
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
    subscriptions.value.delete(uuid); // O(1) removal
    // Also remove episodes from this podcast in latestSubscriptionEpisodes
    latestSubscriptionEpisodes.value = latestSubscriptionEpisodes.value.filter(
      (ep) => ep.podcast?.uuid !== uuid
    );
  }

  // === SEARCH ACTIONS ===

  function setSearchResults(podcasts, episodes, pagination) {
    searchResults.value = {
      podcasts: podcasts || [],
      episodes: enrichEpisodesWithProgress(episodes || [])
    };
    searchPagination.value = pagination || {
      podcasts: { total: 0, pages: 0 },
      episodes: { total: 0, pages: 0 }
    };
    searchCurrentPage.value = { podcasts: 1, episodes: 1 };
    hasSearched.value = true;
    lastSearchTerm.value = searchTerm.value;
  }

  function appendSearchResults(type, items) {
    if (type === 'podcasts') {
      searchResults.value.podcasts = [
        ...searchResults.value.podcasts,
        ...items
      ];
      searchCurrentPage.value.podcasts++;
    } else if (type === 'episodes') {
      const enrichedItems = enrichEpisodesWithProgress(items);
      searchResults.value.episodes = [
        ...searchResults.value.episodes,
        ...enrichedItems
      ];
      searchCurrentPage.value.episodes++;
    }
  }

  function clearSearch() {
    searchTerm.value = '';
    lastSearchTerm.value = '';
    searchFilters.value = { language: '', duration: '', genre: '' };
    searchResults.value = { podcasts: [], episodes: [] };
    searchPagination.value = {
      podcasts: { total: 0, pages: 0 },
      episodes: { total: 0, pages: 0 }
    };
    searchCurrentPage.value = { podcasts: 1, episodes: 1 };
    hasSearched.value = false;
    searchLoading.value = false;
    searchLoadingMore.value = { podcasts: false, episodes: false };
  }

  // Clear display metadata after fade-out animation completes
  function clearDisplayEpisode() {
    displayEpisode.value = null;
  }

  // === RETURN ===
  async function resync() {
    return preloadSubscriptionsList({ force: true });
  }

  return {
    resync,
    // State
    currentEpisode,
    displayEpisode,
    playbackSpeed,
    playbackSpeeds,
    pendingEpisodeUuid,
    progressCache,
    subscriptions: subscriptionsList, // exposed as array for iteration
    latestSubscriptionEpisodes,
    subscriptionsLoaded,

    // Network state
    networkError,

    // Search state
    searchTerm,
    lastSearchTerm,
    searchFilters,
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
    handleInitialMetadata,
    handleSourceEvent,
    clearDisplayEpisode,

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
    setSearchResults,
    appendSearchResults,
    clearSearch
  };
});
