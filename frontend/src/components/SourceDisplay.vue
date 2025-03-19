<template>
  <div class="source-display">
    <!-- Affiche le composant approprié en fonction de la source active -->
    <LibrespotPlayer v-if="currentSource === 'librespot'" />
    
    <!-- Pour les sources connectées -->
    <DeviceConnectionInfo 
      v-else-if="['bluetooth', 'macos'].includes(currentSource) && isDeviceConnected"
      :source="currentSource"
      :deviceInfo="deviceInfo"
    />
    
    <!-- Pour les sources en attente de connexion -->
    <WaitingConnection 
      v-else-if="['bluetooth', 'macos'].includes(currentSource) && !isDeviceConnected" 
      :sourceType="currentSource"
    />
    
    <!-- Pour Web Radio (à implémenter plus tard) -->
    <WaitingConnection
      v-else-if="currentSource === 'webradio'"
      sourceType="webradio"
    />
  </div>
</template>

<script setup>
import { computed } from 'vue';
import { useAudioStore } from '@/stores/audio';
import LibrespotPlayer from './sources/librespot/LibrespotPlayer.vue';
import DeviceConnectionInfo from './sources/DeviceConnectionInfo.vue';
import WaitingConnection from './sources/WaitingConnection.vue';

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