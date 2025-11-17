// frontend/src/stores/podcastStore.js
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

export const usePodcastStore = defineStore('podcast', () => {
  // === PLAYBACK STATE ===
  const currentEpisode = ref(null)
  const playbackState = ref('stopped') // 'playing', 'paused', 'stopped', 'buffering'
  const currentPosition = ref(0)
  const currentDuration = ref(0)
  const playbackSpeed = ref(1.0)

  // === SETTINGS ===
  const settings = ref({
    defaultCountry: 'FRANCE',
    defaultLanguage: 'FRENCH',
    safeMode: false,
    playbackSpeed: 1.0
  })

  // === COMPUTED ===
  const isPlaying = computed(() => playbackState.value === 'playing')
  const isPaused = computed(() => playbackState.value === 'paused')
  const isBuffering = computed(() => playbackState.value === 'buffering')
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
      playbackState.value = 'buffering'
    } catch (error) {
      console.error('Error playing episode:', error)
      throw error
    }
  }

  async function pause() {
    try {
      await fetch('/api/podcast/pause', { method: 'POST' })
      playbackState.value = 'paused'
    } catch (error) {
      console.error('Error pausing:', error)
    }
  }

  async function resume() {
    try {
      await fetch('/api/podcast/resume', { method: 'POST' })
      playbackState.value = 'playing'
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
      currentEpisode.value = null
      playbackState.value = 'stopped'
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

    if (metadata.current_episode) {
      currentEpisode.value = metadata.current_episode
    }
    if (metadata.is_playing !== undefined) {
      playbackState.value = metadata.is_playing ? 'playing' : 'paused'
    }
    if (metadata.is_buffering !== undefined && metadata.is_buffering) {
      playbackState.value = 'buffering'
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
      playbackState.value = 'stopped'
      currentPosition.value = 0
    }
  }

  function handlePluginEvent(event) {
    // Handle WebSocket plugin events for podcast
    if (event.source !== 'podcast') return

    if (event.type === 'state_changed') {
      handleStateUpdate(event.data || {})
    }
  }

  // === RETURN ===
  return {
    // State
    currentEpisode,
    playbackState,
    currentPosition,
    currentDuration,
    playbackSpeed,
    settings,

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
    handlePluginEvent
  }
})
