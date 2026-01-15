// frontend/src/stores/podcastStore.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import axios from 'axios'
import { useUnifiedAudioStore } from './unifiedAudioStore'

// Maximum progress entries to cache (prevents unbounded memory growth)
const MAX_PROGRESS_ENTRIES = 200

export const usePodcastStore = defineStore('podcast', () => {
  // Access unified audio store (source of truth for playback state)
  const unifiedStore = useUnifiedAudioStore()

  // === PLAYBACK STATE ===
  const currentEpisode = ref(null)
  const displayEpisode = ref(null) // Preserved during fade-out animation
  const currentPosition = ref(0)
  const currentDuration = ref(0)
  const playbackSpeed = ref(1.0)
  const pendingEpisodeUuid = ref(null) // Optimistic loading state before WebSocket confirms

  // Timeout for delayed metadata clearing (during fade-out animation)
  let delayedClearTimeout = null

  // === PROGRESS CACHE ===
  // Reactive cache of playback progress for all episodes
  // Key: episode_uuid, Value: { position, duration, lastPlayed }
  const progressCache = ref(new Map())

  // === SUBSCRIPTIONS CACHE ===
  // Cache subscriptions as Map for O(1) lookups by uuid
  const subscriptions = ref(new Map()) // Map<uuid, subscription>
  const latestSubscriptionEpisodes = ref([])

  // Computed array for iteration (sorted by name)
  const subscriptionsList = computed(() => {
    return Array.from(subscriptions.value.values())
      .sort((a, b) => (a.name || '').localeCompare(b.name || ''))
  })
  const subscriptionsListLoaded = ref(false) // True when subscriptions list is loaded (no Taddy call)
  const subscriptionsLoaded = ref(false) // True when latest episodes are also loaded (with Taddy call)

  // === SEARCH STATE ===
  // Persisted across navigation within Podcasts module
  const searchTerm = ref('')
  const lastSearchTerm = ref('')
  const searchFilters = ref({
    language: '',
    duration: '',
    genre: ''
  })
  const searchResults = ref({
    podcasts: [],
    episodes: []
  })
  const searchPagination = ref({
    podcasts: { total: 0, pages: 0 },
    episodes: { total: 0, pages: 0 }
  })
  const searchCurrentPage = ref({
    podcasts: 1,
    episodes: 1
  })
  const hasSearched = ref(false)
  const searchLoading = ref(false)
  const searchLoadingMore = ref({
    podcasts: false,
    episodes: false
  })

  // === SETTINGS ===
  // Note: Language/country are centralized in /var/lib/milo/settings.json (via settingsStore)
  const settings = ref({
    safeMode: false,
    playbackSpeed: 1.0
  })

  // === COMPUTED ===
  // Read playback state from unifiedStore (single source of truth)
  const isPlaying = computed(() => {
    if (unifiedStore.systemState.active_source !== 'podcast') return false
    return unifiedStore.systemState.metadata?.is_playing || false
  })

  const isBuffering = computed(() => {
    if (unifiedStore.systemState.active_source !== 'podcast') return false
    return unifiedStore.systemState.metadata?.is_buffering || false
  })

  const isPaused = computed(() => {
    if (unifiedStore.systemState.active_source !== 'podcast') return false
    // Paused = has episode but not playing and not buffering
    return hasCurrentEpisode.value && !isPlaying.value && !isBuffering.value
  })

  const hasCurrentEpisode = computed(() => currentEpisode.value !== null)

  const hasDisplayEpisode = computed(() => displayEpisode.value !== null)

  const progressPercentage = computed(() => {
    if (!currentDuration.value) return 0
    return (currentPosition.value / currentDuration.value) * 100
  })

  const hasSubscriptions = computed(() => subscriptions.value.size > 0)

  // === PLAYBACK ACTIONS ===

  async function play(episodeUuid) {
    // Set pending immediately for instant UI feedback (spinner)
    pendingEpisodeUuid.value = episodeUuid
    try {
      const response = await axios.post('/api/podcast/play', { episode_uuid: episodeUuid })
      if (!response.data.success) {
        pendingEpisodeUuid.value = null
        throw new Error('Failed to play episode')
      }
      // State will be updated via WebSocket broadcast from backend
      // pendingEpisodeUuid will be cleared in handleStateUpdate()
    } catch (error) {
      pendingEpisodeUuid.value = null
      console.error('Error playing episode:', error)
      throw error
    }
  }

  async function pause() {
    try {
      await axios.post('/api/podcast/pause')
      // State will be updated via WebSocket broadcast from backend
    } catch (error) {
      console.error('Error pausing:', error)
    }
  }

  async function resume() {
    try {
      await axios.post('/api/podcast/resume')
      // State will be updated via WebSocket broadcast from backend
    } catch (error) {
      console.error('Error resuming:', error)
    }
  }

  async function seek(position) {
    try {
      await axios.post('/api/podcast/seek', { position: Math.floor(position) })
      currentPosition.value = position
    } catch (error) {
      console.error('Error seeking:', error)
    }
  }

  async function stop() {
    try {
      await axios.post('/api/podcast/stop')
      // State will be cleared via WebSocket broadcast from backend
      currentEpisode.value = null
      displayEpisode.value = null
      currentPosition.value = 0
      currentDuration.value = 0

      // Clear any pending delayed clear
      if (delayedClearTimeout) {
        clearTimeout(delayedClearTimeout)
        delayedClearTimeout = null
      }
    } catch (error) {
      console.error('Error stopping:', error)
    }
  }

  async function setSpeed(speed) {
    try {
      const response = await axios.post('/api/podcast/speed', { speed })
      if (response.data.success) {
        playbackSpeed.value = response.data.speed
      }
    } catch (error) {
      console.error('Error setting speed:', error)
    }
  }

  // === SETTINGS ACTIONS ===

  async function loadSettings() {
    try {
      const response = await axios.get('/api/podcast/settings')
      if (response.data.settings) {
        settings.value = { ...settings.value, ...response.data.settings }
        playbackSpeed.value = response.data.settings.playbackSpeed || 1.0
      }
    } catch (error) {
      console.error('Error loading settings:', error)
    }
  }

  async function updateSettings(newSettings) {
    try {
      await axios.post('/api/podcast/settings', newSettings)
      settings.value = { ...settings.value, ...newSettings }
    } catch (error) {
      console.error('Error updating settings:', error)
    }
  }

  // === WEBSOCKET STATE HANDLER ===

  function handleStateUpdate(data) {
    // Update from WebSocket broadcast
    // Extract metadata from nested structure (data.metadata) or use data directly
    const metadata = data.metadata || data

    // Handle episode end FIRST (before updating any other state)
    if (metadata.episode_ended === true) {
      // Clear currentEpisode immediately (for state consistency)
      currentEpisode.value = null
      currentPosition.value = 0

      // DON'T clear displayEpisode yet - preserve metadata during fade-out animation
      // The parent component will call clearDisplayEpisode() after animation completes

      // RETURN EARLY - don't process any other updates from this event
      return
    }

    // Update episode metadata (only if NOT an episode_ended event)
    if (metadata.current_episode) {
      currentEpisode.value = metadata.current_episode
      displayEpisode.value = metadata.current_episode

      // Clear pending state - WebSocket has confirmed playback
      if (pendingEpisodeUuid.value === metadata.current_episode.uuid) {
        pendingEpisodeUuid.value = null
      }

      // Clear any pending delayed clear
      if (delayedClearTimeout) {
        clearTimeout(delayedClearTimeout)
        delayedClearTimeout = null
      }
    }
    if (metadata.position !== undefined) {
      currentPosition.value = metadata.position
    }
    if (metadata.duration !== undefined) {
      currentDuration.value = metadata.duration
    }
    if (metadata.playback_speed !== undefined) {
      playbackSpeed.value = metadata.playback_speed
    }

    // Update progress cache for reactive updates in EpisodeCard
    if (metadata.episode_uuid && metadata.position !== undefined && metadata.duration !== undefined) {
      progressCache.value.set(metadata.episode_uuid, {
        position: metadata.position,
        duration: metadata.duration,
        lastPlayed: Date.now()
      })
      enforceProgressCacheLimit()
    }

    // Note: is_playing and is_buffering are read from unifiedStore.systemState.metadata
    // They are updated by the unified audio state machine via WebSocket
  }

  function handlePluginEvent(event) {
    // Handle WebSocket plugin events for podcast
    if (event.source !== 'podcast') {
      return
    }

    if (event.type === 'state_changed') {
      handleStateUpdate(event.data || {})
    }
  }

  // === PENDING STATE HELPER ===
  function isEpisodePending(episodeUuid) {
    return pendingEpisodeUuid.value === episodeUuid
  }

  // === PROGRESS CACHE HELPERS ===
  function getEpisodeProgress(episodeUuid) {
    // Get progress from cache (reactive)
    return progressCache.value.get(episodeUuid) || null
  }

  /**
   * Enforce cache limit by evicting oldest entries (LRU based on lastPlayed)
   * Preserves the currently playing episode
   */
  function enforceProgressCacheLimit() {
    if (progressCache.value.size <= MAX_PROGRESS_ENTRIES) return

    const currentUuid = currentEpisode.value?.uuid
    const entries = Array.from(progressCache.value.entries())
      .filter(([uuid]) => uuid !== currentUuid)
      .sort((a, b) => (a[1].lastPlayed || 0) - (b[1].lastPlayed || 0))

    // Remove oldest entries until under limit
    const toRemove = progressCache.value.size - MAX_PROGRESS_ENTRIES
    for (let i = 0; i < toRemove && i < entries.length; i++) {
      progressCache.value.delete(entries[i][0])
    }
  }

  function setEpisodeProgress(episodeUuid, position, duration) {
    // Manually set progress (used when loading from API)
    progressCache.value.set(episodeUuid, {
      position,
      duration,
      lastPlayed: Date.now()
    })
    enforceProgressCacheLimit()
  }

  function enrichEpisodesWithProgress(episodes) {
    // Populate progress cache from API data (when loading episodes)
    // This initializes the reactive cache with existing progress
    if (!Array.isArray(episodes)) return episodes

    episodes.forEach(episode => {
      if (episode.playback_progress) {
        const progress = episode.playback_progress
        if (progress.position !== undefined && progress.duration !== undefined) {
          progressCache.value.set(episode.uuid, {
            position: progress.position,
            duration: progress.duration,
            lastPlayed: progress.lastPlayed || Date.now()
          })
        }
      }
    })

    enforceProgressCacheLimit()
    return episodes
  }

  // === SUBSCRIPTIONS ACTIONS ===

  // Helper to convert array to Map
  function arrayToSubscriptionsMap(arr) {
    const map = new Map()
    for (const sub of arr) {
      map.set(sub.uuid, sub)
    }
    return map
  }

  // Lightweight preload - only fetches subscriptions list (no Taddy API call)
  // Called at app startup to know if hasSubscriptions before opening Podcasts
  async function preloadSubscriptionsList() {
    if (subscriptionsListLoaded.value) return

    try {
      const response = await axios.get('/api/podcast/subscriptions')
      subscriptions.value = arrayToSubscriptionsMap(response.data.subscriptions || [])
      subscriptionsListLoaded.value = true
    } catch (error) {
      console.error('Error preloading subscriptions list:', error)
    }
  }

  // Full load - fetches subscriptions list + latest episodes (Taddy API call)
  // Called when HomeView opens
  async function loadSubscriptions(forceRefresh = false) {
    // Return cached data if fully loaded and not forcing refresh
    if (subscriptionsLoaded.value && !forceRefresh) {
      return { subscriptions: subscriptionsList.value, latestEpisodes: latestSubscriptionEpisodes.value }
    }

    // Reuse subscriptions list if already preloaded, otherwise fetch
    if (!subscriptionsListLoaded.value) {
      const response = await axios.get('/api/podcast/subscriptions')
      subscriptions.value = arrayToSubscriptionsMap(response.data.subscriptions || [])
      subscriptionsListLoaded.value = true
    }

    // Fetch latest episodes (Taddy API call) if user has subscriptions
    if (subscriptions.value.size > 0) {
      const response = await axios.get('/api/podcast/subscriptions/latest-episodes', { params: { limit: 20 } })
      latestSubscriptionEpisodes.value = enrichEpisodesWithProgress(response.data.results || [])
    } else {
      latestSubscriptionEpisodes.value = []
    }

    subscriptionsLoaded.value = true
    return { subscriptions: subscriptionsList.value, latestEpisodes: latestSubscriptionEpisodes.value }
  }

  function addSubscription(subscription) {
    // Add to subscriptions Map if not already present (O(1) lookup)
    if (!subscriptions.value.has(subscription.uuid)) {
      subscriptions.value.set(subscription.uuid, subscription)
    }
    // Mark as needing refresh to fetch latest episodes on next HomeView load
    subscriptionsLoaded.value = false
  }

  function removeSubscription(uuid) {
    subscriptions.value.delete(uuid) // O(1) removal
    // Also remove episodes from this podcast in latestSubscriptionEpisodes
    latestSubscriptionEpisodes.value = latestSubscriptionEpisodes.value.filter(
      ep => ep.podcast?.uuid !== uuid
    )
  }

  // O(1) subscription check
  function isSubscribed(uuid) {
    return subscriptions.value.has(uuid)
  }

  // Get subscription by uuid (O(1))
  function getSubscription(uuid) {
    return subscriptions.value.get(uuid)
  }

  // === SEARCH ACTIONS ===

  function setSearchResults(podcasts, episodes, pagination) {
    searchResults.value = {
      podcasts: podcasts || [],
      episodes: enrichEpisodesWithProgress(episodes || [])
    }
    searchPagination.value = pagination || {
      podcasts: { total: 0, pages: 0 },
      episodes: { total: 0, pages: 0 }
    }
    searchCurrentPage.value = { podcasts: 1, episodes: 1 }
    hasSearched.value = true
    lastSearchTerm.value = searchTerm.value
  }

  function appendSearchResults(type, items) {
    if (type === 'podcasts') {
      searchResults.value.podcasts = [...searchResults.value.podcasts, ...items]
      searchCurrentPage.value.podcasts++
    } else if (type === 'episodes') {
      const enrichedItems = enrichEpisodesWithProgress(items)
      searchResults.value.episodes = [...searchResults.value.episodes, ...enrichedItems]
      searchCurrentPage.value.episodes++
    }
  }

  function clearSearch() {
    searchTerm.value = ''
    lastSearchTerm.value = ''
    searchFilters.value = { language: '', duration: '', genre: '' }
    searchResults.value = { podcasts: [], episodes: [] }
    searchPagination.value = {
      podcasts: { total: 0, pages: 0 },
      episodes: { total: 0, pages: 0 }
    }
    searchCurrentPage.value = { podcasts: 1, episodes: 1 }
    hasSearched.value = false
    searchLoading.value = false
    searchLoadingMore.value = { podcasts: false, episodes: false }
  }

  // === CLEAR STATE ===
  function clearState() {
    // Clear all podcast state (called when switching away from podcast source)
    currentEpisode.value = null
    displayEpisode.value = null
    currentPosition.value = 0
    currentDuration.value = 0

    // Clear any pending delayed clear
    if (delayedClearTimeout) {
      clearTimeout(delayedClearTimeout)
      delayedClearTimeout = null
    }

    // Note: playback state comes from unifiedStore, no need to clear locally
    // Keep progress cache for displaying "X min restantes" on paused episodes
  }

  // Clear display episode after fade-out animation
  function clearDisplayEpisode() {
    displayEpisode.value = null

    // Clear any pending timeout
    if (delayedClearTimeout) {
      clearTimeout(delayedClearTimeout)
      delayedClearTimeout = null
    }
  }

  // === RETURN ===
  return {
    // State
    currentEpisode,
    displayEpisode,
    currentPosition,
    currentDuration,
    playbackSpeed,
    pendingEpisodeUuid,
    settings,
    progressCache,
    subscriptions: subscriptionsList, // Expose as array for iteration (backward compatible)
    latestSubscriptionEpisodes,
    subscriptionsLoaded,

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
    isPlaying,
    isPaused,
    isBuffering,
    hasCurrentEpisode,
    hasDisplayEpisode,
    progressPercentage,
    hasSubscriptions,

    // Actions
    play,
    pause,
    resume,
    seek,
    stop,
    setSpeed,
    loadSettings,
    updateSettings,
    handleStateUpdate,
    handlePluginEvent,
    clearState,
    clearDisplayEpisode,

    // Pending state helper
    isEpisodePending,

    // Progress cache helpers
    getEpisodeProgress,
    setEpisodeProgress,
    enrichEpisodesWithProgress,

    // Subscriptions
    preloadSubscriptionsList,
    loadSubscriptions,
    addSubscription,
    removeSubscription,
    isSubscribed,
    getSubscription,

    // Search
    setSearchResults,
    appendSearchResults,
    clearSearch
  }
})
