// frontend/src/stores/snapclient.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useSnapclientStore = defineStore('snapclient', () => {
  // État minimal
  const isActive = ref(false);
  const isConnected = ref(false);
  const deviceName = ref(null);
  const host = ref(null);
  const pluginState = ref('inactive');
  const discoveredServers = ref([]);
  const blacklistedServers = ref([]);
  const error = ref(null);
  const isLoading = ref(false);

  // Un seul getter essentiel
  const currentServer = computed(() => isConnected.value ? 
    { name: deviceName.value, host: host.value } : null);

  function updateConnectionState(connected, details = {}) {
    const wasConnected = isConnected.value;
    isConnected.value = connected;

    if (connected) {
      deviceName.value = details.device_name || details.deviceName || 'Serveur inconnu';
      host.value = details.host;
      pluginState.value = 'connected';
    } else if (wasConnected) {
      deviceName.value = null;
      host.value = null;
      pluginState.value = isActive.value ? 'ready' : 'inactive';
    }

    // Notification seulement si changement d'état
    if (wasConnected !== connected) {
      window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
        detail: { connected, source: details.source, reason: details.reason, timestamp: Date.now() }
      }));
    }
    return connected;
  }

  function updateFromStateEvent(data) {
    if (data.source !== 'snapclient') return false;

    pluginState.value = data.plugin_state || pluginState.value;
    isActive.value = true;

    if (data.plugin_state === 'connected' && data.connected === true) {
      updateConnectionState(true, data);
    }
    else if (data.plugin_state === 'ready' || data.plugin_state === 'inactive') {
      updateConnectionState(false, {
        source: 'state_event',
        reason: data.disconnection_reason || 'state_changed',
        ...data
      });
    }
    return true;
  }

  function updateFromWebSocketEvent(eventType, data) {
    // Gestion des déconnexions
    if (eventType === 'snapclient_monitor_disconnected' || eventType === 'snapclient_server_disappeared') {
      updateConnectionState(false, {
        source: eventType,
        reason: data.reason || 'server_unreachable',
        host: data.host,
        timestamp: data.timestamp
      });
      error.value = `Le serveur ${data.host} s'est déconnecté (${data.reason || 'raison inconnue'})`;
      return true;
    }

    // Gestion des connexions
    if (eventType === 'snapclient_monitor_connected' && host.value === data.host) {
      error.value = null;
      updateConnectionState(true, {
        source: eventType,
        host: data.host,
        timestamp: data.timestamp
      });
      return true;
    }
    
    // Gestion des découvertes de serveurs
    if (eventType === 'snapclient_server_discovered' && data.server) {
      const exists = discoveredServers.value.some(s => s.host === data.server.host);
      if (!exists) {
        discoveredServers.value = [...discoveredServers.value, data.server];
      }
      return true;
    }

    return false;
  }

  async function fetchStatus(force = false) {
    try {
      isLoading.value = true;
      const response = await axios.get(`/api/snapclient/status?_t=${Date.now()}`);
      const data = response.data;

      if (data.status === 'error') throw new Error(data.message);

      isActive.value = data.is_active === true;
      updateConnectionState(data.device_connected === true, {
        device_name: data.device_name,
        host: data.host,
        source: 'api_status'
      });

      if (data.discovered_servers) discoveredServers.value = [...data.discovered_servers];
      if (data.blacklisted_servers) blacklistedServers.value = data.blacklisted_servers;

      return data;
    } catch (err) {
      error.value = err.message || 'Erreur de communication';
      
      // Déconnexion en cas d'erreur réseau
      if (err.response?.status >= 500 || err.code === 'ECONNABORTED' || 
          err.message.includes('Network Error')) {
        updateConnectionState(false, { source: 'network_error', reason: 'api_unreachable' });
      }
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  async function connectToServer(serverHost) {
    if (!isActive.value) return { success: false, inactive: true };

    try {
      isLoading.value = true;
      error.value = null;
      const response = await axios.post(`/api/snapclient/connect/${serverHost}`);
      const data = response.data;

      if (data.status === 'error') throw new Error(data.message);
      
      await fetchStatus(true);
      return data;
    } catch (err) {
      error.value = err.message || `Erreur de connexion à ${serverHost}`;
      updateConnectionState(false, { source: 'connect_error', reason: err.message });
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  async function disconnectFromServer() {
    try {
      isLoading.value = true;
      error.value = null;
      const response = await axios.post('/api/snapclient/disconnect');
      
      // Mise à jour immédiate de l'UI
      updateConnectionState(false, { source: 'manual_disconnect', reason: 'user_requested' });
      return response.data;
    } catch (err) {
      error.value = err.message || 'Erreur de déconnexion';
      // Forcer déconnexion même en cas d'erreur
      updateConnectionState(false, { source: 'disconnect_error', reason: err.message });
      return { status: "forced_disconnect", message: "Déconnexion forcée" };
    } finally {
      isLoading.value = false;
    }
  }

  return {
    // État
    isActive, isConnected, deviceName, host, pluginState,
    discoveredServers, blacklistedServers, error, isLoading,
    
    // Getters
    currentServer,

    // Actions
    fetchStatus, connectToServer, disconnectFromServer,
    updateFromWebSocketEvent, updateFromStateEvent
  };
});