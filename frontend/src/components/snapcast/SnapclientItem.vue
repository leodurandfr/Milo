<!-- frontend/src/components/snapcast/SnapclientItem.vue - Skeleton via CSS uniquement (corrigé) -->
<template>
  <div class="snapclient-item">
    <!-- Name -->
    <div class="client-name heading-2" :class="{ 'skeleton-shimmer': isLoading }">
      <span>{{ isLoading ? '' : client.name }}</span>
    </div>
    
    <!-- Volume -->
    <div class="volume-control" :class="{ 'skeleton-shimmer': isLoading }">
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
    
    <!-- Controls -->
    <div class="controls-wrapper">
      <div class="control-button" :class="{ 'skeleton-shimmer': isLoading }">
        <IconButton 
          icon="threeDots" 
          @click="handleShowDetails" 
          title="Voir les détails du client"
          :style="{ opacity: isLoading ? 0 : 1 }"
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
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted, watch } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import IconButton from '@/components/ui/IconButton.vue';

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

const emit = defineEmits(['volume-change', 'mute-toggle', 'show-details']);

// 🔍 DEBUG: Watcher pour suivre les changements de isLoading
watch(() => props.isLoading, (newVal, oldVal) => {
  console.log(`🎭 SnapclientItem [${props.client.id}] isLoading changed: ${oldVal} → ${newVal}`);
  if (newVal === false && oldVal === true) {
    console.log('✨ Transition skeleton → content devrait démarrer maintenant (400ms)');
  }
}, { immediate: true });

watch(() => props.client, (newVal, oldVal) => {
  if (oldVal?.id !== newVal?.id) {
    console.log(`🔄 SnapclientItem ID changed: ${oldVal?.id} → ${newVal?.id}`);
  }
  if (oldVal?.name !== newVal?.name) {
    console.log(`📝 SnapclientItem name changed: ${oldVal?.name} → ${newVal?.name}`);
  }
}, { deep: true });

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

function handleShowDetails() {
  if (!props.isLoading) {
    emit('show-details', props.client);
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

/* Client name */
.client-name {
  min-width: 112px;
  max-width: 112px;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  height: 28px;
  position: relative;
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

/* Volume control */
.volume-control {
  flex: 1;
  /* height: 40px; */
  display: flex;
  align-items: center;
  position: relative;
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

/* Controls wrapper */
.controls-wrapper {
  display: flex;
  align-items: center;
  gap: var(--space-03);
}

.control-button {
  /* width: 40px;
  height: 40px; */
  border-radius: var(--radius-full);
  display: flex;
  align-items: center;
  justify-content: center;
  position: relative;
}

.control-button::before {
  content: '';
  position: absolute;
  inset: 0;
  border-radius: inherit;
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

.control-button.skeleton-shimmer::before {
  opacity: 1;
}

.control-button > * {
  opacity: 1;
  transition: opacity 400ms ease;
  position: relative;
  z-index: 1;
}

.control-button.skeleton-shimmer > * {
  opacity: 0;
}

.control-toggle {
  /* width: 70px;
  height: 40px; */
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

/* Responsive Mobile */
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

  .controls-wrapper {
    order: 2;
    margin-left: auto;
    flex-shrink: 0;
  }

  .volume-control {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }
}
</style>