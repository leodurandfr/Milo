<!-- frontend/src/components/multiroom/MultiroomItem.vue -->
<template>
  <div class="multiroom-item">
    <!-- CLIENT NAME -->
    <div class="client-name-wrapper" :class="{ 'is-zone': client.isZone || zoneClients }">
      <!-- Skeleton wrapper -->
      <div
        class="client-name-skeletons"
        :class="{ 'visible': isLoading }"
      >
        <div class="item-name-skeleton"></div>
        <div
          v-if="client.isZone || zoneClients"
          class="zone-clients-skeleton"
        ></div>
      </div>

      <!-- Real content -->
      <div
        class="client-name heading-2"
        :class="{
          'visible': !isLoading,
          'muted': client.dspMuted
        }"
      >
        <span class="item-name">{{ client.name }}</span>
        <span v-if="zoneClients" class="zone-clients text-mono">{{ zoneClients }}</span>
      </div>
    </div>

    <!-- VOLUME CONTROL -->
    <div class="volume-wrapper">
      <!-- Skeleton shimmer -->
      <div
        class="volume-skeleton"
        :class="{ 'visible': isLoading }"
      ></div>

      <!-- Real content -->
      <div
        class="volume-control"
        :class="{
          'visible': !isLoading,
          'muted': client.dspMuted
        }"
      >
        <RangeSlider
          :model-value="displayVolume"
          :min="sliderMin"
          :max="sliderMax"
          :step="1"
          :disabled="client.dspMuted || isLoading"
          show-value
          value-unit=" dB"
          @input="handleVolumeInput"
          @change="handleVolumeChange"
        />
      </div>
    </div>

    <!-- TOGGLE CONTROL -->
    <div class="toggle-wrapper">
      <!-- Skeleton shimmer -->
      <div
        class="toggle-skeleton"
        :class="{ 'visible': isLoading }"
      ></div>

      <!-- Real content -->
      <div
        class="control-toggle"
        :class="{ 'visible': !isLoading }"
      >
        <Toggle
          :model-value="!client.dspMuted"
          type="background-strong"
          @change="handleMuteToggle"
        />
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onUnmounted } from 'vue';
import RangeSlider from '@/components/ui/RangeSlider.vue';
import Toggle from '@/components/ui/Toggle.vue';
import { useSettingsStore } from '@/stores/settingsStore';

const settingsStore = useSettingsStore();

const props = defineProps({
  client: {
    type: Object,
    default: () => ({})
  },
  isLoading: {
    type: Boolean,
    default: false
  },
  // Client names to show when item represents a zone
  zoneClients: {
    type: String,
    default: ''
  }
});

const emit = defineEmits(['volume-change', 'mute-toggle']);

const localDisplayVolume = ref(null);
let throttleTimeout = null;
let finalTimeout = null;

// Slider configuration - always in dB, respecting volume limits
const sliderMin = computed(() => settingsStore.volumeLimits.min_db);
const sliderMax = computed(() => settingsStore.volumeLimits.max_db);

// Volume is always in dB
const displayVolume = computed(() => {
  if (localDisplayVolume.value !== null) {
    return localDisplayVolume.value;
  }

  // Use dspVolume from client (populated by parent), clamp to limits
  const volume = props.client.dspVolume ?? -30;
  return Math.max(sliderMin.value, Math.min(sliderMax.value, Math.round(volume)));
});

function handleVolumeInput(newDisplayVolume) {
  localDisplayVolume.value = newDisplayVolume;

  if (throttleTimeout) clearTimeout(throttleTimeout);
  if (finalTimeout) clearTimeout(finalTimeout);

  throttleTimeout = setTimeout(() => {
    sendVolumeUpdate(newDisplayVolume);
  }, 50);

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

function sendVolumeUpdate(volumeDb) {
  if (!props.isLoading) {
    emit('volume-change', props.client.id, volumeDb);
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
.multiroom-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  border-radius: var(--radius-06);
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  background: var(--color-background-neutral);
  max-height: 72px;
}

/* === CLIENT NAME WRAPPER === */
.client-name-wrapper {
  min-width: 190px;
  max-width: 190px;
  min-height: 24px;
  position: relative;
  display: flex;
  align-items: center;
}

.client-name-wrapper.is-zone {
  min-height: 42px;
}

/* Skeleton wrapper for name */
.client-name-skeletons {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  justify-content: flex-start;
  gap: var(--space-01);
  opacity: 0;
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.client-name-skeletons.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

.item-name-skeleton {
  height: 20px;
  width: 64%;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
}

.zone-clients-skeleton {
  height: 14px;
  width: 80%;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  animation-delay: 0.2s;
}

/* Real name content */
.client-name {
  color: var(--color-text);
  overflow: hidden;
  opacity: 0;
  transition: opacity 300ms ease 0ms, color 300ms ease;
  width: 100%;
}

.client-name.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms, color 300ms ease;
}

.client-name.muted {
  color: var(--color-text-light);
}

.client-name {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 0;
}

.item-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  width: 100%;
}

.zone-clients {
  color: var(--color-text-light);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  width: 100%;
}

/* === VOLUME WRAPPER === */
.volume-wrapper {
  flex: 1;
  height: 40px;
  position: relative;
  display: flex;
  align-items: center;
}

/* Skeleton for volume */
.volume-skeleton {
  position: absolute;
  inset: 0;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.volume-skeleton.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* Real volume content */
.volume-control {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
}

.volume-control.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

.volume-control.muted :deep(.slider-value) {
  color: var(--color-text-light);
}

/* === TOGGLE WRAPPER === */
.toggle-wrapper {
  width: 70px;
  height: 40px;
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
}

/* Skeleton for toggle */
.toggle-skeleton {
  position: absolute;
  width: 70px;
  height: 40px;
  border-radius: var(--radius-full);
  background: linear-gradient(
    90deg,
    var(--color-background-strong) 0%,
    var(--color-background-medium-16) 50%,
    var(--color-background-strong) 100%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s infinite;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.toggle-skeleton.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* Real toggle content */
.control-toggle {
  position: absolute;
  display: flex;
  align-items: center;
  justify-content: center;
  opacity: 0;
  transition: opacity 300ms ease 0ms;
}

.control-toggle.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}

@media (max-aspect-ratio: 4/3) {
  .multiroom-item {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-03);
    border-radius: var(--radius-05);
    max-height: none;

  }

  .client-name-wrapper {
    flex: 1;
    order: 1;
    min-width: 0;
  }

  .toggle-wrapper {
    order: 2;
    margin-left: auto;
    width: 56px;
    height: 32px;
    align-self: flex-start;
  }

  .toggle-skeleton {
    width: 56px;
    height: 32px;
  }

  .volume-wrapper {
    order: 3;
    width: 100%;
    flex-basis: 100%;
  }
}
</style>
