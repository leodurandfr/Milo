<!-- frontend/src/components/ui/VolumeBar.vue -->
<template>
  <div class="volume-bar" :class="{ visible: isVisible }">
    <div class="volume-slider">
      <div class="volume-fill" :style="volumeFillStyle"></div>
      <div class="text-mono">{{ unifiedStore.currentVolume }} %</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';

const unifiedStore = useUnifiedAudioStore();

const isVisible = ref(false);
let hideTimer = null;

// Computed pour les styles du volume-fill
const volumeFillStyle = computed(() => {
  const volume = unifiedStore.currentVolume;
  const isCircleMode = volume < 10;
  
  return {
    width: isCircleMode ? '32px' : `${volume}%`,
    left: isCircleMode ? `${(volume * 3.2) - 32}px` : '-0.3px'
  };
});

function showVolume() {
  if (hideTimer) clearTimeout(hideTimer);
  isVisible.value = true;
  hideTimer = setTimeout(() => isVisible.value = false, 3000);
}

function hideVolume() {
  if (hideTimer) clearTimeout(hideTimer);
  isVisible.value = false;
}

defineExpose({ showVolume, hideVolume });

onMounted(() => {
  unifiedStore.setVolumeBarRef({ showVolume, hideVolume });
});
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
  background: var(--color-background-glass);
  backdrop-filter: blur(12px);
  transition: all var(--transition-spring);
  z-index: 120;
}

.volume-bar::before {
  content: '';
  position: absolute;
  inset: 0;
  padding: 2px;
  background: var(--stroke-glass);
  border-radius: var(--radius-06);
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
  background: var(--color-background-neutral);
  border-radius: var(--radius-full);
  overflow: hidden;
}

.volume-slider .text-mono {
  height: 100%;
  align-content: center;
  color: var(--color-text-secondary);
  margin-left: var(--space-04);
  position: absolute;
}

.volume-fill {
  position: absolute;
  height: 100%;
  background: var(--color-background-contrast);
  border-radius: var(--radius-full);
  transition: width 0.2s ease, left 0.2s ease;
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