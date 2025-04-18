// frontend/src/stores/snapclient.js
/**
 * Store Pinia pour la gestion de l'état de Snapclient - Version simplifiée
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useSnapclientStore = defineStore('snapclient', () => {
  // État essentiel
  const isActive = ref(false);
  const isConnected = ref(false);
  const deviceName = ref(null);
  const host = ref(null);
  const pluginState = ref('inactive');
  const discoveredServers = ref([]);
  const error = ref(null);
  const isLoading = ref(false);
  const blacklistedServers = ref([]);

  // Getters
  const currentServer = computed(() => {
    if (!isConnected.value) return null;
    return { name: deviceName.value, host: host.value };
  });

  /**
   * Mise à jour centralisée de l'état de connexion
   */
  function updateConnectionState(connected, details = {}) {
    const wasConnected = isConnected.value;
    isConnected.value = connected;

    if (connected) {
      deviceName.value = details.device_name || details.deviceName || 'Serveur inconnu';
      host.value = details.host;
      pluginState.value = 'connected';
    } else {
      if (wasConnected) {
        deviceName.value = null;
        host.value = null;
        pluginState.value = isActive.value ? 'ready' : 'inactive';
      }
    }

    // Notifier seulement si l'état a changé
    if (wasConnected !== connected) {
      window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
        detail: {
          connected,
          source: details.source,
          reason: details.reason,
          timestamp: Date.now()
        }
      }));
    }

    return connected;
  }

  /**
   * Mise à jour depuis un événement d'état
   */
  function updateFromStateEvent(data) {
    if (data.source !== 'snapclient') return false;

    // Mise à jour de l'état
    pluginState.value = data.plugin_state || pluginState.value;
    isActive.value = true; // Si on reçoit des mises à jour, le plugin est actif

    // Mise à jour de la connexion basée sur l'état
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

  /**
   * Mise à jour depuis un événement WebSocket
   */
  function updateFromWebSocketEvent(eventType, data) {
    // Gestion prioritaire des déconnexions
    if (eventType === 'snapclient_monitor_disconnected' ||
      eventType === 'snapclient_server_disappeared') {
      
      updateConnectionState(false, {
        source: eventType,
        reason: data.reason || 'server_unreachable',
        host: data.host,
        timestamp: data.timestamp
      });

      error.value = `Le serveur ${data.host} s'est déconnecté (${data.reason || 'raison inconnue'})`;
      return true;
    }

    // Gestion des événements de connexion
    if (eventType === 'snapclient_monitor_connected') {
      error.value = null;

      // Vérifier si c'est le serveur auquel on est censé être connecté
      if (host.value === data.host) {
        updateConnectionState(true, {
          source: eventType,
          host: data.host,
          timestamp: data.timestamp
        });
      }
      return true;
    }
    
    // Gestion des nouveaux serveurs découverts via Zeroconf
    if (eventType === 'snapclient_server_discovered') {
      // Ajouter le serveur à la liste des serveurs découverts si pas déjà présent
      if (data.server && !discoveredServers.value.some(s => s.host === data.server.host)) {
        discoveredServers.value = [...discoveredServers.value, data.server];
      }
      
      return true;
    }

    return false;
  }

  /**
   * Récupère le statut depuis l'API
   */
  async function fetchStatus(force = false) {
    try {
      isLoading.value = true;

      // Ajouter un paramètre unique pour éviter le cache
      const response = await axios.get(`/api/snapclient/status?_t=${Date.now()}`);
      const data = response.data;

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      // Mise à jour de l'état
      isActive.value = data.is_active === true;

      // Mise à jour de l'état de connexion
      updateConnectionState(data.device_connected === true, {
        device_name: data.device_name,
        host: data.host,
        source: 'api_status'
      });

      // Mise à jour des serveurs découverts et blacklist
      if (data.discovered_servers) {
        discoveredServers.value = [...data.discovered_servers];
      }

      if (data.blacklisted_servers) {
        blacklistedServers.value = data.blacklisted_servers;
      }

      return data;
    } catch (err) {
      error.value = err.message || 'Erreur de communication avec le serveur';

      // En cas d'erreur réseau, considérer comme déconnecté
      if (err.response?.status >= 500 || err.code === 'ECONNABORTED' ||
        err.message.includes('Network Error')) {
        updateConnectionState(false, {
          source: 'network_error',
          reason: 'api_unreachable'
        });
      }

      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Se connecte à un serveur
   */
  async function connectToServer(serverHost) {
    if (!isActive.value) {
      return { success: false, inactive: true };
    }

    try {
      isLoading.value = true;
      error.value = null;

      const response = await axios.post(`/api/snapclient/connect/${serverHost}`);
      const data = response.data;

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      // Mise à jour après succès
      await fetchStatus(true);
      return data;
    } catch (err) {
      error.value = err.message || `Erreur lors de la connexion au serveur ${serverHost}`;
      updateConnectionState(false, {
        source: 'connect_error',
        reason: err.message
      });
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Se déconnecte du serveur actuel
   */
  async function disconnectFromServer() {
    try {
      isLoading.value = true;
      error.value = null;

      const response = await axios.post('/api/snapclient/disconnect');

      // Mise à jour immédiate de l'UI
      updateConnectionState(false, {
        source: 'manual_disconnect',
        reason: 'user_requested'
      });

      return response.data;
    } catch (err) {
      error.value = err.message || 'Erreur lors de la déconnexion du serveur';

      // Forcer la déconnexion même en cas d'erreur
      updateConnectionState(false, {
        source: 'disconnect_error',
        reason: err.message
      });

      return { status: "forced_disconnect", message: "Déconnexion forcée" };
    } finally {
      isLoading.value = false;
    }
  }

  return {
    // État
    isActive,
    isConnected,
    deviceName,
    host,
    pluginState,
    discoveredServers,
    blacklistedServers,
    error,
    isLoading,

    // Getters
    currentServer,

    // Actions
    fetchStatus,
    connectToServer,
    disconnectFromServer,
    updateFromWebSocketEvent,
    updateFromStateEvent
  };
});