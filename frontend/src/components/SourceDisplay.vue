<template>
  <div class="source-display">
    <!-- Affiche le composant approprié en fonction de la source active -->
    <LibrespotPlayer v-if="currentSource === 'librespot'" />
    <DeviceConnectionInfo 
      v-else-if="['bluetooth', 'macos'].includes(currentSource) && isDeviceConnected"
      :source="currentSource"
      :deviceInfo="deviceInfo"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useAudioStore } from '@/stores/audio';
import LibrespotPlayer from './sources/librespot/LibrespotPlayer.vue';
import DeviceConnectionInfo from './sources/DeviceConnectionInfo.vue';

const audioStore = useAudioStore();

const currentSource = computed(() => audioStore.currentState);

const isDeviceConnected = computed(() => {
  // Pour bluetooth et macos, vérifier si un appareil est connecté
  return audioStore.metadata && audioStore.metadata.deviceConnected;
});

const deviceInfo = computed(() => {
  return audioStore.metadata || {};
});
</script>