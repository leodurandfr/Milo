<!-- frontend/src/components/snapcast/SnapclientItem.vue -->
<template>
  <div class="snapclient-item">
    <div 
      class="client-name heading-2" 
      :class="{ 
        'skeleton-shimmer': isLoading,
        'muted': client.muted
      }"
    >
      <span>{{ isLoading ? '' : client.name }}</span>
    </div>
    
    <div 
      class="volume-control" 
      :class="{ 
        'skeleton-shimmer': isLoading,
        'muted': client.muted
      }"
    >
      <RangeSlider 
        :model-value="displayVolume" 
        :min="0" 
        :max="100" 
        :step="1"
        :disabled="client.muted || isLoading"
        show-value
        value-unit="%"
        @input="handleVolumeInput" 
        @change="handleVolumeChange" 
      />
    </div>
    
    <div class="control-toggle" :class="{ 'skeleton-shimmer': isLoading }">
      <Toggle 
        :model-value="!client.muted" 
        variant="secondary" 
        @change="handleMuteToggle"
        :style="{ opacity: isLoading ? 0 : 1 }"
      />
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';

const props = defineProps({
  client: {
    type: Object,
    default: () => ({})
  },
  isLoading: {
    type: Boolean,
    default: false
  }
});

const emit = defineEmits(['volume-change', 'mute-toggle']);

const localDisplayVolume = ref(null);
let throttleTimeout = null;
let finalTimeout = null;

const displayVolume = computed(() => {
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }
  const volume = props.client.volume || 0;
  return Math.max(0, Math.min(100, Math.round(volume)));
});

function handleVolumeInput(newDisplayVolume) {
  localDisplayVolume.value = newDisplayVolume;

  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  throttleTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 25);

  finalTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 500);
}

function handleVolumeChange(newDisplayVolume) {
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  localDisplayVolume.value = null;
  sendVolumeUpdate(newDisplayVolume);
}

function sendVolumeUpdate(displayVolume) {
  if (!props.isLoading) {
    emit('volume-change', props.client.id, displayVolume);
  }
}

function handleMuteToggle(enabled) {
  if (!props.isLoading) {
    const newMuted = !enabled;
    emit('mute-toggle', props.client.id, newMuted);
  }
}

onUnmounted(() => {
  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);
});
</script>

<style scoped>
.snapclient-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: var(--radius-04);
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  background: var(--color-background-neutral);
}

.client-name {
  min-width: 112px;
  max-width: 112px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  height: 28px;
  position: relative;
  transition: color 300ms ease;
}

.client-name.muted {
  color: var(--color-text-light);
}

.client-name::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  height: 28px;
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-glass) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 400ms ease;
  pointer-events: none;
}

.client-name.skeleton-shimmer::before {
  opacity: 1;
}

.client-name span {
  opacity: 1;
  transition: opacity 400ms ease;
  position: relative;
  z-index: 1;
}

.client-name.skeleton-shimmer span {
  opacity: 0;
}

.volume-control {
  flex: 1;
  display: flex;
  align-items: center;
  position: relative;
}

.volume-control.muted :deep(.slider-value) {
  color: var(--color-text-light);
}

.volume-control::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-glass) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 400ms ease;
  pointer-events: none;
}

.volume-control.skeleton-shimmer::before {
  opacity: 1;
}

.volume-control > * {
  opacity: 1;
  transition: opacity 400ms ease;
  position: relative;
  z-index: 1;
}

.volume-control.skeleton-shimmer > * {
  opacity: 0;
}

.control-toggle {
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.control-toggle::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-glass) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 400ms ease;
  pointer-events: none;
}

.control-toggle.skeleton-shimmer::before {
  opacity: 1;
}

.control-toggle > * {
  opacity: 1;
  transition: opacity 400ms ease;
  position: relative;
  z-index: 1;
}

.control-toggle.skeleton-shimmer > * {
  opacity: 0;
}

@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

@media (max-aspect-ratio: 4/3) {
  .snapclient-item {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-03);
  }

  .client-name {
    flex: 1;
    order: 1;
    min-width: 0;
  }

  .control-toggle {
    order: 2;
    margin-left: auto;
  }

  .volume-control {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }
}
</style>