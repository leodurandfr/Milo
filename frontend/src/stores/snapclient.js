// frontend/src/stores/snapclient.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useSnapclientStore = defineStore('snapclient', () => {
  // État spécifique à Snapclient
  const deviceName = ref(null);
  const host = ref(null);
  const discoveredServers = ref([]);
  const blacklistedServers = ref([]);
  const error = ref(null);
  const isLoading = ref(false);

  // Un seul getter essentiel
  const currentServer = computed(() => deviceName.value ?
    { name: deviceName.value, host: host.value } : null);

  async function fetchStatus(force = false) {
    try {
      isLoading.value = true;
      // Utiliser la bonne route sans le préfixe /api
      const response = await axios.get(`/snapclient/status?_t=${Date.now()}`);
      const data = response.data;

      if (data.status === 'error') throw new Error(data.message);

      deviceName.value = data.device_name;
      host.value = data.host;
      
      if (data.discovered_servers) discoveredServers.value = [...data.discovered_servers];
      if (data.blacklisted_servers) blacklistedServers.value = data.blacklisted_servers;

      return data;
    } catch (err) {
      error.value = err.message || 'Erreur de communication';
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  function handleWebSocketEvent(eventType, data) {
    // Gérer les événements spécifiques à snapclient
    if (eventType === 'plugin_state_changed' && data.source === 'snapclient') {
      // Mettre à jour en fonction des métadonnées
      if (data.metadata) {
        if (data.metadata.device_name) {
          deviceName.value = data.metadata.device_name;
        }
        if (data.metadata.host) {
          host.value = data.metadata.host;
        }
        if (data.metadata.server_discovered) {
          // Ajouter le serveur découvert
          const server = data.metadata.server;
          const exists = discoveredServers.value.some(s => s.host === server.host);
          if (!exists) {
            discoveredServers.value = [...discoveredServers.value, server];
          }
        }
      }
    }
  }

  async function connectToServer(serverHost) {
    try {
      isLoading.value = true;
      error.value = null;
      // Utiliser la bonne route sans le préfixe /api
      const response = await axios.post(`/snapclient/connect/${serverHost}`);
      const data = response.data;

      if (data.status === 'error') throw new Error(data.message);

      await fetchStatus(true);
      return data;
    } catch (err) {
      error.value = err.message || `Erreur de connexion à ${serverHost}`;
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  async function disconnectFromServer() {
    try {
      isLoading.value = true;
      error.value = null;
      // Utiliser la bonne route sans le préfixe /api
      const response = await axios.post('/snapclient/disconnect');

      deviceName.value = null;
      host.value = null;
      return response.data;
    } catch (err) {
      error.value = err.message || 'Erreur de déconnexion';
      return { status: "forced_disconnect", message: "Déconnexion forcée" };
    } finally {
      isLoading.value = false;
    }
  }

  return {
    // État
    deviceName, host,
    discoveredServers, blacklistedServers, error, isLoading,

    // Getters
    currentServer,

    // Actions
    fetchStatus, connectToServer, disconnectFromServer,
    handleWebSocketEvent
  };
});