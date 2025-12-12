<template>
  <div class="progress-bar" v-if="duration > 0 && isReady">
    <span class="text-mono time">{{ formatTime(currentPosition) }}</span>
    <div ref="progressContainer" class="progress-container" @click="onProgressClick">
      <div class="progress" :style="progressStyle"></div>
    </div>
    <span class="text-mono time">{{ formatTime(duration) }}</span>
  </div>
</template>

<script setup>
import { computed, ref, onMounted } from 'vue';

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
  },
  isReady: {
    type: Boolean,
    default: true
  }
});

const emit = defineEmits(['seek']);

const progressContainer = ref(null);
const containerWidth = ref(400);

// Computed to guarantee a valid numeric value
const progressPercent = computed(() => {
  const val = parseFloat(props.progressPercentage);
  return isNaN(val) ? 0 : Math.min(100, Math.max(0, val));
});

// Computed for progress bar styles
const progressStyle = computed(() => {
  const percent = progressPercent.value;
  const height = 8;
  
  // Actual width the bar should have at this percentage
  const actualWidth = (percent / 100) * containerWidth.value;
  
  if (actualWidth <= height) {
    // Circle mode: movement from -8px to 0px
    return {
      width: `${height}px`,
      left: `${actualWidth - height}px`
    };
  } else {
    // Normal bar mode
    return {
      width: `${percent}%`,
      left: '0px'
    };
  }
});

onMounted(() => {
  if (progressContainer.value) {
    containerWidth.value = progressContainer.value.offsetWidth;
  }
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

  const newPosition = Math.floor(props.duration * percentage);
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
  overflow: hidden;
}

.progress {
  height: 100%;
  background-color: var(--color-background-contrast);
  border-radius: 4px;
  position: absolute;
}

.time {
  color: var(--color-text-light)
}
</style>