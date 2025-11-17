<template>
  <div class="podcast-player" :class="{ open: isOpen }">
    <div class="player-toggle" @click="togglePanel">
      <Icon :name="isOpen ? 'caretRight' : 'caretLeft'" :size="20" />
    </div>

    <div class="player-content">
      <img :src="episodeImage" :alt="episodeName" class="episode-image" />

      <div class="episode-info">
        <h3 class="episode-name">{{ episodeName }}</h3>
        <p
          class="podcast-name"
          @click="$emit('select-podcast', podcastStore.currentEpisode?.podcast?.uuid)"
        >
          {{ podcastName }}
        </p>
      </div>

      <!-- Progress bar -->
      <div class="progress-section">
        <RangeSlider
          :min="0"
          :max="podcastStore.currentDuration || 100"
          :modelValue="podcastStore.currentPosition"
          @update:modelValue="handleSeek"
        />
        <div class="time-display">
          <span>{{ formatTime(podcastStore.currentPosition) }}</span>
          <span>{{ formatTime(podcastStore.currentDuration) }}</span>
        </div>
      </div>

      <!-- Controls -->
      <div class="controls">
        <Button variant="secondary" @click="seekBackward">
          <Icon name="rewind" :size="24" />
          <span class="control-label">-15s</span>
        </Button>

        <Button variant="toggle" size="large" @click="togglePlayPause">
          <Icon :name="podcastStore.isPlaying ? 'pause' : 'play'" :size="32" />
        </Button>

        <Button variant="secondary" @click="seekForward">
          <Icon name="fastForward" :size="24" />
          <span class="control-label">+30s</span>
        </Button>
      </div>

      <!-- Speed control -->
      <div class="speed-control">
        <label>Vitesse</label>
        <div class="speed-buttons">
          <Button
            v-for="speed in speeds"
            :key="speed"
            :variant="currentSpeed === speed ? 'toggle' : 'secondary'"
            size="small"
            @click="setSpeed(speed)"
          >
            {{ speed }}x
          </Button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { usePodcastStore } from '@/stores/podcastStore'
import Button from '@/components/ui/Button.vue'
import Icon from '@/components/ui/Icon.vue'
import RangeSlider from '@/components/ui/RangeSlider.vue'

defineEmits(['select-podcast', 'select-episode'])

const podcastStore = usePodcastStore()
const isOpen = ref(true)
const speeds = [0.5, 0.75, 1, 1.25, 1.5, 2]

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

function togglePanel() {
  isOpen.value = !isOpen.value
}

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

async function setSpeed(speed) {
  await podcastStore.setSpeed(speed)
}

function formatTime(seconds) {
  if (!seconds) return '0:00'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) {
    return `${h}:${m.toString().padStart(2, '0')}:${s.toString().padStart(2, '0')}`
  }
  return `${m}:${s.toString().padStart(2, '0')}`
}
</script>

<style scoped>
.podcast-player {
  position: fixed;
  right: 0;
  top: 0;
  bottom: 0;
  width: 320px;
  background: var(--color-background-contrast);
  transform: translateX(280px);
  transition: transform 0.3s ease;
  z-index: 100;
  display: flex;
}

.podcast-player.open {
  transform: translateX(0);
}

.player-toggle {
  width: 40px;
  display: flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  background: var(--color-background-subtle);
  border-radius: var(--radius-04) 0 0 var(--radius-04);
}

.player-content {
  flex: 1;
  padding: var(--space-04);
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
  text-align: center;
}

.episode-name {
  font-size: var(--font-size-md);
  font-weight: var(--font-weight-semibold);
  color: var(--color-text-contrast);
  margin: 0;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.podcast-name {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  margin: var(--space-01) 0 0;
  cursor: pointer;
}

.podcast-name:hover {
  color: var(--color-accent);
}

.progress-section {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.time-display {
  display: flex;
  justify-content: space-between;
  font-size: var(--font-size-xs);
  color: var(--color-text-muted);
}

.controls {
  display: flex;
  justify-content: center;
  align-items: center;
  gap: var(--space-04);
}

.control-label {
  font-size: var(--font-size-xs);
  margin-top: var(--space-01);
}

.speed-control {
  display: flex;
  flex-direction: column;
  gap: var(--space-02);
}

.speed-control label {
  font-size: var(--font-size-sm);
  color: var(--color-text-muted);
  text-align: center;
}

.speed-buttons {
  display: flex;
  justify-content: center;
  gap: var(--space-01);
  flex-wrap: wrap;
}
</style>
