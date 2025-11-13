<!-- frontend/src/components/snapcast/SnapclientItem.vue -->
<template>
  <div class="snapclient-item">
    <!-- CLIENT NAME -->
    <div class="client-name-wrapper">
      <!-- Skeleton shimmer -->
      <div
        class="client-name-skeleton heading-2"
        :class="{ 'visible': isLoading }"
      ></div>

      <!-- Real content -->
      <div
        class="client-name heading-2"
        :class="{
          'visible': !isLoading,
          'muted': client.muted
        }"
      >
        <span>{{ client.name }}</span>
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
          :model-value="!client.muted"
          variant="secondary"
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
  border-radius: var(--radius-06);
  gap: var(--space-04);
  padding: var(--space-04) var(--space-04) var(--space-04) var(--space-05);
  background: var(--color-background-neutral);
}

/* === CLIENT NAME WRAPPER === */
.client-name-wrapper {
  min-width: 144px;
  max-width: 144px;
  height: 28px;
  position: relative;
  display: flex;
  align-items: center;
}

/* Skeleton for name */
.client-name-skeleton {
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
  transition: opacity 300ms ease 0ms;
  pointer-events: none;
}

.client-name-skeleton.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms;
}

/* Real name content */
.client-name {
  position: absolute;
  inset: 0;
  color: var(--color-text);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  opacity: 0;
  transition: opacity 300ms ease 0ms, color 300ms ease;
}

.client-name.visible {
  opacity: 1;
  transition: opacity 300ms ease 0ms, color 300ms ease;
}

.client-name.muted {
  color: var(--color-text-light);
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
    var(--color-background-glass) 50%,
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
    var(--color-background-glass) 50%,
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
  .snapclient-item {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: var(--space-03);
    border-radius: var(--radius-05);
  }

  .client-name-wrapper {
    flex: 1;
    order: 1;
    min-width: 0;
    height: 24px;
  }

  .toggle-wrapper {
    order: 2;
    margin-left: auto;
    width: 56px;
    height: 32px;
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