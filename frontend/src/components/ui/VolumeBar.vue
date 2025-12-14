<!-- frontend/src/components/ui/VolumeBar.vue -->
<template>
  <div class="volume-bar" :class="{ visible: unifiedStore.showVolumeBar }">
    <div class="volume-slider">
      <div class="volume-fill" :style="volumeFillStyle"></div>
      <div class="text-mono">{{ volumeDisplay }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useSettingsStore } from '@/stores/settingsStore';

const unifiedStore = useUnifiedAudioStore();
const settingsStore = useSettingsStore();

// Volume in dB
const volumeDb = computed(() => unifiedStore.volumeState.volume_db);

// Volume limits from settings
const limitMin = computed(() => settingsStore.volumeLimits.min_db);
const limitMax = computed(() => settingsStore.volumeLimits.max_db);

// Display volume in dB
const volumeDisplay = computed(() => `${Math.round(volumeDb.value)} dB`);

// Fill percentage interpolated on volume limits (limit_min = 0%, limit_max = 100%)
const fillPercent = computed(() => {
  const range = limitMax.value - limitMin.value;
  if (range <= 0) return 0;
  return ((volumeDb.value - limitMin.value) / range) * 100;
});

const volumeFillStyle = computed(() => ({
  width: '100%',
  transform: `translateX(${fillPercent.value - 100}%)`
}));
</script>

<style scoped>
.volume-bar {
  top: var(--space-05);
  position: fixed;
  left: 50%;
  transform: translate(-50%, -80px);
  opacity: 0;
  width: 472px;
  padding: var(--space-04);
  border-radius: var(--radius-full);
  background: var(--color-background-medium-16);
  backdrop-filter: blur(12px);
  transition: all var(--transition-spring);
  z-index: 8000;
}

.volume-bar::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  background: var(--stroke-glass);
  border-radius: var(--radius-07);
  -webkit-mask:
    linear-gradient(#000 0 0) content-box,
    linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask-composite: exclude;
  z-index: -1;
  pointer-events: none;
}

.volume-bar.visible {
  opacity: 1;
  transform: translate(-50%, 0);
  left: 50%;
}

.volume-slider {
  position: relative;
  width: 100%;
  height: 32px;
  border-radius: var(--radius-full);
  overflow: hidden;
}

.volume-slider::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0.5px;
  right: 0.5px;
  height: 100%;
  background: var(--color-background-medium-32);
  border-radius: var(--radius-full);
  z-index: 0;
}

.volume-slider .text-mono {
  height: 100%;
  align-content: center;
  color: var(--color-text-light);
  margin-left: var(--space-04);
  position: absolute;
  z-index: 2;
}

.volume-fill {
  position: absolute;
  height: 100%;
  background: var(--color-background-contrast);
  border-radius: var(--radius-full);
  transition: transform 0.2s ease;
  z-index: 1;
}

@media (max-aspect-ratio: 4/3) {
  .volume-bar {
    width: calc(100% - 2*(var(--space-04)));
  }
}

.ios-app .volume-bar {
  top: var(--space-08);
}
</style>
