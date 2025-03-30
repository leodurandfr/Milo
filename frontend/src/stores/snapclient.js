// frontend/src/stores/snapclient.js

/**
 * Store Pinia pour la gestion de l'état de Snapclient.
 */
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useSnapclientStore = defineStore('snapclient', () => {
  // État interne
  const isActive = ref(false);
  const isConnected = ref(false);
  const deviceName = ref(null);
  const host = ref(null);
  const pluginState = ref('inactive');
  const discoveredServers = ref([]);
  const error = ref(null);
  const lastAction = ref(null);
  const isLoading = ref(false);
  const blacklistedServers = ref([]);

  // Getters
  const hasServers = computed(() => discoveredServers.value.length > 0);
  const currentServer = computed(() => {
    if (!isConnected.value) return null;
    return {
      name: deviceName.value,
      host: host.value
    };
  });

  function updateFromStateEvent(data) {
    console.log("🔄 Mise à jour directe depuis événement d'état:", data.plugin_state);

    // Vérifier que les données viennent bien de snapclient
    if (data.source !== 'snapclient') return false;

    // Mise à jour directe de l'état sans appel réseau
    pluginState.value = data.plugin_state;

    if (data.plugin_state === 'connected' && data.connected === true) {
      isConnected.value = true;
      deviceName.value = data.device_name || 'Serveur inconnu';
      host.value = data.host;
      error.value = null;
      console.log("✅ État mis à jour: connecté à", deviceName.value);
    }
    else if (data.plugin_state === 'ready_to_connect') {
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      console.log("✅ État mis à jour: prêt à connecter");
    }

    return true;
  }

  /**
   * Met à jour l'état du store en fonction d'un événement WebSocket.
   */
  function updateFromWebSocketEvent(eventType, data) {
    console.log(`⚡ Mise à jour directe du store depuis: ${eventType}`);

    if (eventType === 'snapclient_monitor_disconnected' || eventType === 'snapclient_server_disappeared') {
      console.log("🔴 Déconnexion détectée via WebSocket, mise à jour instantanée de l'UI");
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';
      error.value = `Le serveur ${data.host} s'est déconnecté`;

      return true;
    }

    if (eventType === 'snapclient_monitor_connected') {
      console.log(`⚡ Moniteur connecté à ${data.host}`);
      error.value = null;
      return true;
    }

    return false;
  }

  /**
   * Récupère le statut actuel du plugin Snapclient.
   */
  async function fetchStatus() {
    try {
      isLoading.value = true;
      error.value = null;

      const response = await axios.get('/api/snapclient/status');
      const data = response.data;

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      // Mise à jour de l'état
      isActive.value = data.is_active === true;
      isConnected.value = data.device_connected === true;
      deviceName.value = data.device_name;
      host.value = data.host;
      discoveredServers.value = data.discovered_servers || [];

      if (data.blacklisted_servers) {
        blacklistedServers.value = data.blacklisted_servers;
      }

      // Déduction de l'état du plugin
      if (!isActive.value) {
        pluginState.value = 'inactive';
      } else if (isConnected.value) {
        pluginState.value = 'connected';
      } else {
        pluginState.value = 'ready_to_connect';
      }

      return data;
    } catch (err) {
      console.error('Erreur lors de la récupération du statut Snapclient:', err);
      error.value = err.message || 'Erreur lors de la récupération du statut';

      // Réinitialiser l'état en cas d'erreur
      isActive.value = false;
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'inactive';

      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Déclenche une découverte des serveurs Snapcast sur le réseau.
   */
  async function discoverServers() {
    if (!isActive.value) {
      return { success: false, inactive: true };
    }

    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'discover';

      const response = await axios.post('/api/snapclient/discover');
      const data = response.data;

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      // Mettre à jour la liste des serveurs découverts
      if (data.servers) {
        discoveredServers.value = data.servers;
      }

      return data;
    } catch (err) {
      console.error('Erreur lors de la découverte des serveurs:', err);
      error.value = err.message || 'Erreur lors de la découverte des serveurs';
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Se connecte à un serveur Snapcast spécifique.
   */
  async function connectToServer(serverHost) {
    if (!isActive.value) {
      return { success: false, inactive: true };
    }

    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'connect';

      // Vérifier la blacklist
      if (blacklistedServers.value.includes(serverHost)) {
        error.value = `Le serveur ${serverHost} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.`;
        throw new Error(error.value);
      }

      const response = await axios.post(`/api/snapclient/connect/${serverHost}`);
      const data = response.data;

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      if (data.blacklisted === true) {
        error.value = `Le serveur ${serverHost} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.`;
        throw new Error(error.value);
      }

      // Mise à jour après connexion
      await fetchStatus();
      return data;
    } catch (err) {
      console.error(`Erreur lors de la connexion au serveur ${serverHost}:`, err);
      error.value = error.value || err.message || `Erreur lors de la connexion au serveur ${serverHost}`;
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Se déconnecte du serveur Snapcast actuel.
   */
  async function disconnectFromServer() {
    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'disconnect';

      const response = await axios.post('/api/snapclient/disconnect');
      const data = response.data;

      // Mise à jour de la blacklist
      if (data.blacklisted) {
        blacklistedServers.value = data.blacklisted;
      }

      // Forcer l'état local à déconnecté
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';

      // Mise à jour après déconnexion
      try {
        await fetchStatus();
      } catch (statusErr) {
        console.warn('Erreur lors de la mise à jour du statut après déconnexion:', statusErr);
      }

      return data;
    } catch (err) {
      console.error('Erreur lors de la déconnexion du serveur:', err);
      error.value = err.message || 'Erreur lors de la déconnexion du serveur';

      // Même en cas d'erreur, forcer l'état à déconnecté
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';

      return { status: "forced_disconnect", message: "Déconnexion forcée après erreur" };
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Gère les mises à jour WebSocket pour Snapclient.
   */
  function handleWebSocketUpdate(eventType, data) {
    if (!data) return;

    // Événements spécifiques à snapclient
    if (data.source === 'snapclient') {
      if (eventType === 'audio_status_updated') {
        if (data.plugin_state) {
          pluginState.value = data.plugin_state;
        }

        if (data.plugin_state === 'connected' && data.connected === true) {
          isConnected.value = true;
          deviceName.value = data.device_name || 'Serveur inconnu';
          host.value = data.host;
        } else if (data.plugin_state === 'ready_to_connect') {
          isConnected.value = false;
          deviceName.value = null;
          host.value = null;
        }
      }
    }

    // Événements de changement d'état audio
    if (eventType === 'audio_state_changed') {
      if (data.current_state === 'macos') {
        fetchStatus();
      } else if (data.from_state === 'macos') {
        reset();
      }
    }

    // Erreurs de transition
    if (eventType === 'audio_transition_error' && data.error && data.error.includes('server')) {
      fetchStatus();
    }
  }

  /**
   * Réinitialise l'état du store.
   */
  function reset() {
    isActive.value = false;
    isConnected.value = false;
    deviceName.value = null;
    host.value = null;
    pluginState.value = 'inactive';
    discoveredServers.value = [];
    error.value = null;
    lastAction.value = null;
    // Ne pas réinitialiser la blacklist - conservée jusqu'au changement de source audio
  }

  return {
    // État
    isActive,
    isConnected,
    deviceName,
    host,
    pluginState,
    discoveredServers,
    error,
    lastAction,
    isLoading,

    // Getters
    hasServers,
    currentServer,
    blacklistedServers,

    // Actions
    fetchStatus,
    discoverServers,
    connectToServer,
    disconnectFromServer,
    handleWebSocketUpdate,
    reset,
    updateFromWebSocketEvent,
    updateFromStateEvent
  };
});