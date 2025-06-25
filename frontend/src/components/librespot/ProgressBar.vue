<template>
  <div class="progress-bar" v-if="duration > 0">
    <span class="text-mono time">{{ formatTime(currentPosition) }}</span>
    <div class="progress-container" @click="onProgressClick">
      <div class="progress" :style="{ width: progressPercent + '%' }"></div>
    </div>
    <span class="text-mono time">{{ formatTime(duration) }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue';

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
});

const emit = defineEmits(['seek']);

// Computed pour garantir une valeur numérique valide
const progressPercent = computed(() => {
  const val = parseFloat(props.progressPercentage);
  return isNaN(val) ? 0 : Math.min(100, Math.max(0, val));
});

function formatTime(ms) {
  if (!ms) return '0:00';
  const totalSeconds = Math.floor(ms / 1000);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  return `${minutes}:${seconds.toString().padStart(2, '0')}`;
}

function onProgressClick(event) {
  if (!props.duration) return;

  const container = event.currentTarget;
  const rect = container.getBoundingClientRect();
  const offsetX = event.clientX - rect.left;
  const percentage = offsetX / rect.width;

  // Calculer la nouvelle position en ms
  const newPosition = Math.floor(props.duration * percentage);

  // Émettre l'événement vers le parent
  emit('seek', newPosition);
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
  background-color: var(--color-background-strong);
  border-radius: 4px;
  cursor: pointer;
  position: relative;
}

.progress {
  height: 100%;
  background-color: var(--color-background-contrast);
  border-radius: 4px;
}

.time {
  color: var(--color-text-secondary)
}
</style>