<template>
  <div v-if="duration > 0" class="progress-bar">
    <span class="text-mono time">{{ formatDuration(currentPosition) }}</span>
    <div class="progress-container" @click="onProgressClick">
      <div class="progress" :style="progressStyle"></div>
    </div>
    <span class="text-mono time">{{ formatDuration(duration) }}</span>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { formatDuration } from './format.js';

const props = defineProps({
  // Both in seconds.
  currentPosition: {
    type: Number,
    default: 0,
  },
  duration: {
    type: Number,
    default: 0,
  },
  progressPercentage: {
    type: Number,
    default: 0,
  },
});

const emit = defineEmits(['seek']);

const progressPercent = computed(() => {
  const val = parseFloat(props.progressPercentage);
  return isNaN(val) ? 0 : Math.min(100, Math.max(0, val));
});

const progressStyle = computed(() => ({
  width: '100%',
  transform: `translateX(${progressPercent.value - 100}%)`,
}));

function onProgressClick(event) {
  if (!props.duration) return;
  const rect = event.currentTarget.getBoundingClientRect();
  const percentage = (event.clientX - rect.left) / rect.width;
  emit('seek', Math.floor(props.duration * percentage));
}
</script>

<style scoped>
.progress-bar {
  display: flex;
  align-items: center;
  width: 100%;
  gap: var(--space-03);
}

.progress-container {
  flex-grow: 1;
  height: 8px;
  background-color: var(--color-background-neutral-12);
  border-radius: var(--radius-01);
  cursor: pointer;
  position: relative;
  overflow: hidden;
}

.progress {
  height: 100%;
  background-color: var(--color-background-neutral);
  border-radius: var(--radius-01);
  position: absolute;
  transition: transform var(--transition-fast);
}

.time {
  color: var(--color-text-contrast-50);
}
</style>
