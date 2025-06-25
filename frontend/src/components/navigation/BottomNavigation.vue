<!-- frontend/src/components/navigation/BottomNavigation.vue - Version OPTIM sans navigation -->
<template>
  <nav class="bottom-nav">
    <button @click="modalStore.openSnapcast()" :class="['nav-item', { active: modalStore.isSnapcastOpen }]">
      Multiroom
    </button>
    <button @click="modalStore.openEqualizer()" :class="['nav-item', { active: modalStore.isEqualizerOpen }]">
      Equalizer
    </button>
    <button @click="changeSource('librespot')" :disabled="unifiedStore.isTransitioning"
      :class="['nav-item', { active: unifiedStore.currentSource === 'librespot' }]">
      Spotify
    </button>
    <button @click="changeSource('bluetooth')" :disabled="unifiedStore.isTransitioning"
      :class="['nav-item', { active: unifiedStore.currentSource === 'bluetooth' }]">
      Bluetooth
    </button>
    <button @click="changeSource('roc')" :disabled="unifiedStore.isTransitioning"
      :class="['nav-item', { active: unifiedStore.currentSource === 'roc' }]">
      ROC for Mac
    </button>
    <button @click="decreaseVolume" class="nav-item volume-btn" :disabled="volumeStore.isAdjusting">
      Vol-
    </button>
    <button @click="increaseVolume" class="nav-item volume-btn" :disabled="volumeStore.isAdjusting">
      Vol+
    </button>
  </nav>
</template>

<script setup>
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useVolumeStore } from '@/stores/volumeStore';
import { useModalStore } from '@/stores/modalStore';

const unifiedStore = useUnifiedAudioStore();
const volumeStore = useVolumeStore();
const modalStore = useModalStore();

async function changeSource(source) {
  // 1. Fermer toutes les modales
  modalStore.closeAll();
  
  // 2. Changer la source (l'affichage suit automatiquement)
  await unifiedStore.changeSource(source);
}

async function increaseVolume() {
  await volumeStore.increaseVolume();
}

async function decreaseVolume() {
  await volumeStore.decreaseVolume();
}
</script>

<style scoped>
.bottom-nav {
  position: fixed;
  bottom: 0;
  left: 50%;
  transform: translateX(-50%);
  width: 460px;
  display: flex;
  background: white;
  border: 1px solid #ddd;
}

.nav-item {
  flex: 1;
  padding: 12px 8px;
  text-decoration: none;
  color: #666;
  text-align: center;
  border: none;
  background: none;
  cursor: pointer;
  font-size: 12px;
  font-weight: 500;
}

.nav-item:hover:not(:disabled) {
  background: #f0f0f0;
}

.nav-item.active {
  background: #2196F3;
  color: white;
}

.nav-item:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.volume-btn {
  background: #f5f5f5;
  font-weight: 600;
}

.volume-btn:hover:not(:disabled) {
  background: #e0e0e0;
}
</style>