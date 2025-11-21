<template>
  <div class="progress-bar" v-if="duration > 0">
    <span class="text-mono time">{{ formatTime(currentPosition) }}</span>
    <div class="progress-container" @click="onProgressClick">
      <div class="progress" :style="progressStyle"></div>
    </div>
    <span class="text-mono time">{{ formatTime(duration) }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  currentPosition: {
    type: Number,
    default: 0
  },
  duration: {
    type: Number,
    default: 0
  },
  progressPercentage: {
    type: Number,
    default: 0
  }
})

const emit = defineEmits(['seek'])

// Computed to guarantee a valid numeric value
const progressPercent = computed(() => {
  const val = parseFloat(props.progressPercentage)
  return isNaN(val) ? 0 : Math.min(100, Math.max(0, val))
})

// Computed for progress bar styles - same approach as VolumeBar.vue
const progressStyle = computed(() => {
  const percent = progressPercent.value
  return {
    width: '100%',
    transform: `translateX(${percent - 100}%)`
  }
})

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

function onProgressClick(event) {
  if (!props.duration) return

  const container = event.currentTarget
  const rect = container.getBoundingClientRect()
  const offsetX = event.clientX - rect.left
  const percentage = offsetX / rect.width

  const newPosition = Math.floor(props.duration * percentage)
  emit('seek', newPosition)
}
</script>

<style scoped>
.progress-bar {
  display: flex;
  align-items: center;
  width: 100%;
  gap: var(--space-03)
}

.progress-container {
  flex-grow: 1;
  height: 8px;
  background-color: var(--color-background-neutral-12);
  border-radius: 4px;
  cursor: pointer;
  position: relative;
  overflow: hidden;
}

.progress {
  height: 100%;
  background-color: var(--color-background-neutral);
  border-radius: 4px;
  position: absolute;
  transition: transform 0.2s ease;
}

.time {
  color: var(--color-text-contrast-50);
}
</style>
