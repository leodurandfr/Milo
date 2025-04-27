// frontend/src/stores/snapclient.js
import { defineStore } from 'pinia';
import { computed, ref } from 'vue';
import axios from 'axios';
import { useAudioStore } from './audioStore';

export const useSnapclientStore = defineStore('snapclient', () => {
  const audioStore = useAudioStore();
  const isLoading = ref(false);
  
  // Getters dérivés du store principal
  const deviceName = computed(() => {
    if (audioStore.currentSource === 'snapclient') {
      return audioStore.metadata?.device_name || null;
    }
    return null;
  });
  
  const host = computed(() => {
    if (audioStore.currentSource === 'snapclient') {
      return audioStore.metadata?.host || null;
    }
    return null;
  });
  
  const discoveredServers = computed(() => {
    if (audioStore.currentSource === 'snapclient') {
      return audioStore.metadata?.discovered_servers || [];
    }
    return [];
  });
  
  const currentServer = computed(() => {
    if (deviceName.value) {
      return { name: deviceName.value, host: host.value };
    }
    return null;
  });
  
  // Actions spécifiques à snapclient
  async function connectToServer(serverHost) {
    try {
      isLoading.value = true;
      const response = await axios.post(`/snapclient/connect/${serverHost}`);
      return response.data.status === 'success';
    } catch (err) {
      throw err;
    } finally {
      isLoading.value = false;
    }
  }
  
  async function disconnectFromServer() {
    try {
      isLoading.value = true;
      const response = await axios.post('/snapclient/disconnect');
      return response.data.status === 'success';
    } catch (err) {
      return false;
    } finally {
      isLoading.value = false;
    }
  }
  
  return {
    // État
    isLoading,
    
    // Getters
    deviceName,
    host,
    discoveredServers,
    currentServer,
    
    // Actions
    connectToServer,
    disconnectFromServer
  };
});