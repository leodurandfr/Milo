<!-- frontend/src/components/ui/VolumeBar.vue -->
<template>
  <div class="volume-bar" :class="{ visible: isVisible }">
    <div class="volume-slider">
      <div class="volume-fill" :style="{ width: `${currentVolume}%` }"></div>
      <div class="text-mono">{{ currentVolume }} %</div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed, onMounted } from 'vue';
import { storeToRefs } from 'pinia';
import { useVolumeStore } from '@/stores/volumeStore';

const volumeStore = useVolumeStore();
const { currentVolume } = storeToRefs(volumeStore);

const isVisible = ref(false);
let hideTimer = null;

function showVolume() {
  if (hideTimer) {
    clearTimeout(hideTimer);
  }

  isVisible.value = true;

  hideTimer = setTimeout(() => {
    isVisible.value = false;
  }, 3000);
}

function hideVolume() {
  if (hideTimer) {
    clearTimeout(hideTimer);
  }
  isVisible.value = false;
}

// Exposer les méthodes pour le parent
defineExpose({
  showVolume,
  hideVolume
});

// Enregistrer la référence dans le store
onMounted(() => {
  volumeStore.setVolumeBarRef({ showVolume, hideVolume });
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
  left: -0.3px;
  /* trick to hide bad supperposition */
  transition: width 0.2s ease;
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