<!-- frontend/src/components/ui/VolumeBar.vue -->
<template>
  <div class="volume-overlay" :class="{ visible: isVisible }">
    <div class="volume-bar">
      <div class="volume-slider">
        <div class="volume-fill" :style="{ width: `${currentVolume}%` }"></div>
      </div>
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
  }, 2500);
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
.volume-overlay {
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
  z-index: 1000;
  display: flex;
  justify-content: center;
  align-items: flex-start;
  padding-top: 24px;
  opacity: 0;
  transform: translateY(-20px);
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.volume-overlay.visible {
  opacity: 1;
  transform: translateY(0);
}

.volume-bar {
  width: 280px;
  padding: 16px;
  border-radius: 16px;
  background: rgba(195, 195, 195, 0.24);
  backdrop-filter: blur(12px);
}

.volume-slider {
  position: relative;
  width: 100%;
  height: 12px;
  background: rgba(255, 255, 255, 0.3);
  border-radius: 6px;
  overflow: hidden;
}

.volume-fill {
  position: absolute;
  height: 100%;
  background: #333;
  border-radius: 6px;
  left: 0;
  top: 0;
  transition: width 0.2s ease;
}

@media (max-aspect-ratio: 4/3) {
  .volume-bar {
    width: 256px;
  }
}
</style>