<template>
  <div class="podcast-player">
    <div class="episode-art-background">
      <img :src="episodeImage" alt="" class="background-episode-image" />
    </div>

    <div class="player-content">
      <img :src="episodeImage" :alt="episodeName" class="episode-image" />

      <div class="episode-info">
        <p class="episode-name text-body">{{ episodeName }}</p>
        <p class="podcast-name text-mono" @click="$emit('select-podcast', podcastStore.currentEpisode?.podcast?.uuid)">
          {{ podcastName }}
        </p>
      </div>

      <!-- Progress bar -->
      <ProgressBar :currentPosition="podcastStore.currentPosition" :duration="podcastStore.currentDuration"
        :progressPercentage="progressPercentage" @seek="handleSeek" />

      <!-- Controls -->
      <div class="controls">
        <!-- Speed selector -->
        <div class="speed-selector">
          <Dropdown v-model="selectedSpeed" :options="speedOptions" variant="minimal" size="small"
            @change="handleSpeedChange" />
        </div>

        <!-- Playback controls -->
        <div class="playback-controls">
          <IconButton icon="rewind15" type="dark" size="small" @click="seekBackward" />

          <!-- Loading spinner during buffering -->
          <div v-if="podcastStore.isBuffering" class="play-button-wrapper">
            <LoadingSpinner :size="56" />
          </div>

          <!-- Play/Pause button when not buffering -->
          <IconButton v-else :icon="podcastStore.isPlaying ? 'pause' : 'play'" type="dark" size="large"
            @click="togglePlayPause" />

          <IconButton icon="forward30" type="dark" size="small" @click="seekForward" />
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { computed, ref } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import IconButton from '@/components/ui/IconButton.vue'
import Dropdown from '@/components/ui/Dropdown.vue'
import LoadingSpinner from '@/components/ui/LoadingSpinner.vue'
import ProgressBar from './ProgressBar.vue'

defineEmits(['select-podcast', 'select-episode'])

const podcastStore = usePodcastStore()
const speeds = [0.5, 0.75, 1, 1.25, 1.5, 2]

// Format speed options for Dropdown component
const speedOptions = computed(() =>
  speeds.map(speed => ({
    label: `${speed}x`,
    value: String(speed)
  }))
)

// Selected speed as string for v-model
const selectedSpeed = computed({
  get: () => String(podcastStore.playbackSpeed || 1),
  set: () => { } // Handled by @change event
})

const episodeImage = computed(() => {
  return podcastStore.currentEpisode?.image_url || '/default-episode.png'
})

const episodeName = computed(() => {
  return podcastStore.currentEpisode?.name || 'Aucun épisode'
})

const podcastName = computed(() => {
  return podcastStore.currentEpisode?.podcast?.name || ''
})

const currentSpeed = computed(() => {
  return podcastStore.playbackSpeed || 1
})

const progressPercentage = computed(() => {
  if (!podcastStore.currentDuration || podcastStore.currentDuration === 0) {
    return 0
  }
  return (podcastStore.currentPosition / podcastStore.currentDuration) * 100
})

async function togglePlayPause() {
  if (podcastStore.isPlaying) {
    await podcastStore.pause()
  } else {
    await podcastStore.resume()
  }
}

async function seekBackward() {
  const newPosition = Math.max(0, podcastStore.currentPosition - 15)
  await podcastStore.seek(newPosition)
}

async function seekForward() {
  const newPosition = Math.min(
    podcastStore.currentDuration,
    podcastStore.currentPosition + 30
  )
  await podcastStore.seek(newPosition)
}

async function handleSeek(position) {
  await podcastStore.seek(position)
}

async function handleSpeedChange(speedValue) {
  const speed = parseFloat(speedValue)
  await podcastStore.setSpeed(speed)
}
</script>

<style scoped>
.podcast-player {
  display: flex;
  width: 310px;
  margin-top: var(--space-07);  max-height: 540px;
  flex-direction: column;
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-05) var(--space-04);
  background: var(--color-background-contrast);
  border-radius: var(--radius-06);
  backdrop-filter: blur(16px);
  position: relative;
  overflow: hidden;
}

.podcast-player::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  opacity: 0.8;
  background: var(--stroke-glass);
  border-radius: var(--radius-06);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: 1;
  pointer-events: none;
}

.episode-art-background {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 100%;
  height: 100%;
  z-index: 0;
  pointer-events: none;
  overflow: hidden;
}

.background-episode-image {
  filter: blur(96px) saturate(1.6) contrast(1) brightness(0.6);
  transform: scale(2);
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.player-content {
  position: relative;
  z-index: 2;
  display: flex;
  flex-direction: column;
  gap: var(--space-04);
  overflow-y: auto;
}

.episode-image {
  width: 100%;
  aspect-ratio: 1;
  border-radius: var(--radius-04);
  object-fit: cover;
}

.episode-info {
  display: flex;
  height: 100%;
  flex-direction: column;
  gap: var(--space-02);
}

.episode-name {
  color: var(--color-text-contrast);
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  margin: 0;
}

.podcast-name {
  color: var(--color-text-contrast-50);
  margin: 0;
  cursor: pointer;
}

.podcast-name:hover {
  color: var(--color-accent);
}

.controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
  position: relative;
}

.playback-controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
}

.speed-selector {
  display: flex;
  align-items: center;
  position: absolute;
  left: 0;
}

.speed-selector :deep(.dropdown) {
  width: auto;
  flex: none;
}

.speed-selector :deep(.dropdown-trigger--minimal) {
  font-size: var(--font-size-h1);
  min-width: 48px;
  text-align: center;
}

.speed-selector :deep(.dropdown-menu) {
  min-width: 100px;
}

.speed-selector :deep(.dropdown-label) {
  text-align: center;
  min-width: 48px;
}

.speed-selector :deep(.dropdown-trigger) {
  padding: var(--space-02) 0;
}

.play-button-wrapper {
  width: 56px;
  height: 56px;
  display: flex;
  align-items: center;
  justify-content: center;
  background: var(--color-background-contrast-12);
  border-radius: 50%;
}

/* Mobile responsive layout */
@media (max-aspect-ratio: 4/3) {
  .podcast-player {
    position: fixed;
    bottom: var(--space-08);
    left: 50%;
    transform: translateX(-50%);
    width: calc(100% - var(--space-02) * 2);
    height: auto;
    max-height: none;
    flex-direction: row;
    align-items: center;
    padding: var(--space-03) var(--space-04);
    border-radius: var(--radius-06);
    box-shadow: 0 var(--space-04) var(--space-07) rgba(0, 0, 0, 0.2);
  }



  .player-content {
    flex-direction: row;
    align-items: center;
    overflow-y: visible;
    gap: var(--space-03);
  }

  .episode-image {
    width: 48px;
    height: 48px;
    min-width: 48px;
    border-radius: var(--radius-03);
    box-shadow: 0 var(--space-02) var(--space-04) rgba(0, 0, 0, 0.3);
  }

  .episode-info {
    flex: 1;
    text-align: left;
    min-width: 0;
  }

  .episode-name {
    font-size: var(--font-size-h1);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    -webkit-line-clamp: unset;
    -webkit-box-orient: unset;
    display: block;
  }

  .podcast-name {
    display: none;
  }

  .player-content :deep(.progress-bar),
  .speed-selector {
    display: none;
  }

  .controls {
    gap: var(--space-02);
    justify-content: center;
  }
}
</style>
