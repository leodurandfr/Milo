<template>
    <div class="device-connection-info">
      <div class="connection-card">
        <div class="source-icon">
          <span v-if="source === 'bluetooth'">📱</span>
          <span v-else-if="source === 'macos'">💻</span>
        </div>
        
        <div class="device-info">
          <h3>Appareil connecté</h3>
          <p class="device-name">{{ deviceInfo.deviceName || 'Périphérique inconnu' }}</p>
          
          <div class="actions" v-if="source === 'bluetooth'">
            <button @click="disconnectDevice" class="disconnect-button">
              Déconnecter
            </button>
          </div>
        </div>
      </div>
    </div>
  </template>
  
  <script setup>
import { useAudioStore } from '@/stores/index';
  
  const props = defineProps({
    source: {
      type: String,
      required: true
    },
    deviceInfo: {
      type: Object,
      default: () => ({})
    }
  });
  
  const audioStore = useAudioStore();
  
  function disconnectDevice() {
    // Uniquement pour Bluetooth
    if (props.source === 'bluetooth') {
      audioStore.controlSource('bluetooth', 'disconnect');
    }
  }
  </script>
  
  <style scoped>
  .device-connection-info {
    margin-top: 1.5rem;
    width: 100%;
  }
  
  .connection-card {
    background-color: #1E1E1E;
    border-radius: 10px;
    padding: 1.5rem;
    color: white;
    display: flex;
    align-items: center;
  }
  
  .source-icon {
    font-size: 3rem;
    margin-right: 1.5rem;
  }
  
  .device-info {
    flex-grow: 1;
  }
  
  .device-name {
    font-size: 1.2rem;
    opacity: 0.8;
    margin-top: 0.5rem;
  }
  
  .actions {
    margin-top: 1rem;
  }
  
  .disconnect-button {
    background-color: #e74c3c;
    color: white;
    border: none;
    padding: 0.5rem 1rem;
    border-radius: 4px;
    cursor: pointer;
    transition: background-color 0.2s;
  }
  
  .disconnect-button:hover {
    background-color: #c0392b;
  }
  </style>