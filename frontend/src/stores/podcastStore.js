// frontend/src/stores/podcastStore.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useUnifiedAudioStore } from './unifiedAudioStore'

export const usePodcastStore = defineStore('podcast', () => {
  // Access unified audio store (source of truth for playback state)
  const unifiedStore = useUnifiedAudioStore()

  // === PLAYBACK STATE ===
  const currentEpisode = ref(null)
  const currentPosition = ref(0)
  const currentDuration = ref(0)
  const playbackSpeed = ref(1.0)

  // === PROGRESS CACHE ===
  // Reactive cache of playback progress for all episodes
  // Key: episode_uuid, Value: { position, duration, lastPlayed }
  const progressCache = ref(new Map())

  // === SETTINGS ===
  const settings = ref({
    defaultCountry: 'FRANCE',
    defaultLanguage: 'FRENCH',
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

  const progressPercentage = computed(() => {
    if (!currentDuration.value) return 0
    return (currentPosition.value / currentDuration.value) * 100
  })

  // === PLAYBACK ACTIONS ===

  async function play(episodeUuid) {
    try {
      const response = await fetch('/api/podcast/play', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ episode_uuid: episodeUuid })
      })
      const data = await response.json()
      if (!data.success) {
        throw new Error('Failed to play episode')
      }
      // State will be updated via WebSocket broadcast from backend
    } catch (error) {
      console.error('Error playing episode:', error)
      throw error
    }
  }

  async function pause() {
    try {
      await fetch('/api/podcast/pause', { method: 'POST' })
      // State will be updated via WebSocket broadcast from backend
    } catch (error) {
      console.error('Error pausing:', error)
    }
  }

  async function resume() {
    try {
      await fetch('/api/podcast/resume', { method: 'POST' })
      // State will be updated via WebSocket broadcast from backend
    } catch (error) {
      console.error('Error resuming:', error)
    }
  }

  async function seek(position) {
    try {
      await fetch('/api/podcast/seek', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ position: Math.floor(position) })
      })
      currentPosition.value = position
    } catch (error) {
      console.error('Error seeking:', error)
    }
  }

  async function stop() {
    try {
      await fetch('/api/podcast/stop', { method: 'POST' })
      // State will be cleared via WebSocket broadcast from backend
      currentEpisode.value = null
      currentPosition.value = 0
      currentDuration.value = 0
    } catch (error) {
      console.error('Error stopping:', error)
    }
  }

  async function setSpeed(speed) {
    try {
      const response = await fetch('/api/podcast/speed', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed })
      })
      const data = await response.json()
      if (data.success) {
        playbackSpeed.value = data.speed
      }
    } catch (error) {
      console.error('Error setting speed:', error)
    }
  }

  // === SETTINGS ACTIONS ===

  async function loadSettings() {
    try {
      const response = await fetch('/api/podcast/settings')
      const data = await response.json()
      if (data.settings) {
        settings.value = { ...settings.value, ...data.settings }
        playbackSpeed.value = data.settings.playbackSpeed || 1.0
      }
    } catch (error) {
      console.error('Error loading settings:', error)
    }
  }

  async function updateSettings(newSettings) {
    try {
      await fetch('/api/podcast/settings', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(newSettings)
      })
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

    // Update episode metadata (not playback state - that comes from unifiedStore)
    if (metadata.current_episode) {
      currentEpisode.value = metadata.current_episode
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
    if (metadata.episode_ended) {
      currentEpisode.value = null
      currentPosition.value = 0
    }

    // Update progress cache for reactive updates in EpisodeCard
    if (metadata.episode_uuid && metadata.position !== undefined && metadata.duration !== undefined) {
      progressCache.value.set(metadata.episode_uuid, {
        position: metadata.position,
        duration: metadata.duration,
        lastPlayed: Date.now()
      })
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

  // === PROGRESS CACHE HELPERS ===
  function getEpisodeProgress(episodeUuid) {
    // Get progress from cache (reactive)
    return progressCache.value.get(episodeUuid) || null
  }

  function setEpisodeProgress(episodeUuid, position, duration) {
    // Manually set progress (used when loading from API)
    progressCache.value.set(episodeUuid, {
      position,
      duration,
      lastPlayed: Date.now()
    })
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

    return episodes
  }

  // === CLEAR STATE ===
  function clearState() {
    // Clear all podcast state (called when switching away from podcast source)
    currentEpisode.value = null
    currentPosition.value = 0
    currentDuration.value = 0
    // Note: playback state comes from unifiedStore, no need to clear locally
    // Keep progress cache for displaying "X min restantes" on paused episodes
  }

  // === RETURN ===
  return {
    // State
    currentEpisode,
    currentPosition,
    currentDuration,
    playbackSpeed,
    settings,
    progressCache,

    // Computed
    isPlaying,
    isPaused,
    isBuffering,
    hasCurrentEpisode,
    progressPercentage,

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

    // Progress cache helpers
    getEpisodeProgress,
    setEpisodeProgress,
    enrichEpisodesWithProgress
  }
})
