// frontend/src/stores/snapclient.js

/**
 * Store Pinia pour la gestion de l'état de Snapclient - Version optimisée.
 */
import { defineStore } from 'pinia';
import { ref, computed, watch } from 'vue';
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
  const lastStatusCheck = ref(0);

  // Getters
  const hasServers = computed(() => discoveredServers.value.length > 0);
  const currentServer = computed(() => {
    if (!isConnected.value) return null;
    return {
      name: deviceName.value,
      host: host.value
    };
  });

  /**
   * Met à jour l'état du store depuis un événement d'état
   */
  function updateFromStateEvent(data) {
    console.log("🔄 Mise à jour depuis événement d'état:", data.plugin_state);

    // Vérifier que les données viennent bien de snapclient
    if (data.source !== 'snapclient') return false;

    // Mise à jour de l'état
    pluginState.value = data.plugin_state;

    if (data.plugin_state === 'connected' && data.connected === true) {
      isConnected.value = true;
      deviceName.value = data.device_name || 'Serveur inconnu';
      host.value = data.host;
      error.value = null;

      // Notifier du changement d'état
      window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
        detail: { connected: true }
      }));

      console.log("✅ État mis à jour: connecté à", deviceName.value);
    }
    else if (data.plugin_state === 'ready_to_connect') {
      // Vérifier s'il y a un changement d'état réel
      if (isConnected.value) {
        isConnected.value = false;
        deviceName.value = null;
        host.value = null;

        // Notifier du changement d'état
        window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
          detail: { connected: false }
        }));
      }
      console.log("✅ État mis à jour: prêt à connecter");
    }

    return true;
  }

  /**
   * Met à jour l'état du store depuis un événement WebSocket
   */
  function updateFromWebSocketEvent(eventType, data) {
    console.log(`⚡ Mise à jour depuis événement WebSocket: ${eventType}`, data);

    // Gérer les événements de déconnexion
    if (eventType === 'snapclient_monitor_disconnected' || eventType === 'snapclient_server_disappeared') {
      console.log("🔴 Déconnexion détectée via WebSocket:", data.host);

      // Forcer la mise à jour de l'état quelle que soit la valeur actuelle
      if (isConnected.value) {
        console.log("🔄 Changement d'état: connecté -> déconnecté");
      }

      // Mise à jour immédiate de l'état
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';
      error.value = `Le serveur ${data.host} s'est déconnecté`;

      // Notifier du changement d'état avec force: true
      window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
        detail: { connected: false, source: eventType, force: true }
      }));

      return true;
    }

    // Gérer les événements de connexion
    if (eventType === 'snapclient_monitor_connected') {
      console.log(`⚡ Moniteur connecté à ${data.host}`);
      error.value = null;

      // Si on est connecté au même serveur, mettre à jour l'état
      if (host.value === data.host) {
        isConnected.value = true;
        pluginState.value = 'connected';
      }

      return true;
    }

    return false;
  }

  /**
   * Force la déconnexion sans appel API
   */
  function forceDisconnect(reason = 'manual') {
    console.log(`🔌 Forçage de la déconnexion (raison: ${reason})`);

    // Mise à jour forcée de l'état
    isConnected.value = false;
    deviceName.value = null;
    host.value = null;
    pluginState.value = 'ready_to_connect';

    // Forcer un rendu immédiat
    setTimeout(() => {
      // Envoyer une notification DOM explicite
      document.dispatchEvent(new CustomEvent('snapclient-disconnected', {
        detail: { timestamp: Date.now(), reason }
      }));

      // Événement Vue standard
      window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
        detail: { connected: false, source: 'force_disconnect', reason }
      }));
    }, 0);

    return { success: true, forced: true };
  }

  /**
   * Récupère le statut actuel du plugin Snapclient
   */
  async function fetchStatus(force = false) {
    try {
      // Éviter les requêtes trop fréquentes
      const now = Date.now();
      if (!force && isLoading.value && now - lastStatusCheck.value < 2000) {
        console.log("🔄 Requête fetchStatus ignorée (déjà en cours ou trop récente)");
        return { cached: true };
      }

      lastStatusCheck.value = now;
      isLoading.value = true;
      error.value = null;

      const response = await axios.get('/api/snapclient/status');
      const data = response.data;

      if (data.status === 'error') {
        throw new Error(data.message);
      }

      console.log("📊 Statut Snapclient reçu:", {
        is_active: data.is_active,
        device_connected: data.device_connected,
        servers: data.discovered_servers?.length || 0
      });

      // Mise à jour de l'état
      isActive.value = data.is_active === true;

      // Vérifier s'il y a un changement d'état de connexion
      const wasConnected = isConnected.value;
      isConnected.value = data.device_connected === true;

      // Notifier du changement d'état
      if (wasConnected !== isConnected.value) {
        console.log(`⚡ Changement connexion: ${wasConnected ? 'connecté' : 'déconnecté'} -> ${isConnected.value ? 'connecté' : 'déconnecté'}`);
        window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
          detail: { connected: isConnected.value }
        }));
      }

      // Mise à jour des propriétés
      deviceName.value = data.device_name;
      host.value = data.host;

      // Mise à jour des serveurs découverts
      if (data.discovered_servers) {
        discoveredServers.value = [...data.discovered_servers];
      }

      // Mise à jour de la blacklist
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

      // En cas d'erreur réseau, considérer comme déconnecté
      if (err.response?.status >= 500 || err.code === 'ECONNABORTED' || err.message.includes('Network Error')) {
        console.warn("Erreur serveur, marquage comme déconnecté");
        isConnected.value = false;
        deviceName.value = null;
        host.value = null;
        pluginState.value = 'ready_to_connect';
      }

      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Déclenche une découverte des serveurs
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

      // Mise à jour des serveurs découverts
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
   * Se connecte à un serveur Snapcast
   */
  async function connectToServer(serverHost) {
    if (!isActive.value) {
      return { success: false, inactive: true };
    }

    // Vérifier si on est déjà connecté à ce serveur
    if (isConnected.value && host.value === serverHost) {
      console.log(`🔌 Déjà connecté à ${serverHost}`);
      return { success: true, already_connected: true };
    }

    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'connect';

      // Vérifier si le serveur est blacklisté
      if (blacklistedServers.value.includes(serverHost)) {
        error.value = `Le serveur ${serverHost} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.`;
        throw new Error(error.value);
      }

      // Enregistrer comme dernier serveur utilisé
      try {
        localStorage.setItem('lastSnapclientServer', JSON.stringify({
          host: serverHost,
          timestamp: Date.now()
        }));
      } catch (e) {
        console.warn("Impossible d'enregistrer le dernier serveur:", e);
      }

      // Mise à jour optimiste de l'UI
      isConnected.value = true;
      pluginState.value = 'connected';
      host.value = serverHost;

      // Utiliser le nom du serveur s'il est connu
      const server = discoveredServers.value.find(s => s.host === serverHost);
      if (server) {
        deviceName.value = server.name;
      } else {
        deviceName.value = `Serveur (${serverHost})`;
      }

      // Envoyer la requête de connexion
      const response = await axios.post(`/api/snapclient/connect/${serverHost}`);
      const data = response.data;

      if (data.status === 'error') {
        // Annuler la mise à jour optimiste
        isConnected.value = false;
        pluginState.value = 'ready_to_connect';
        host.value = null;
        deviceName.value = null;
        throw new Error(data.message);
      }

      // Mise à jour complète après connexion réussie
      await fetchStatus(true);

      return data;
    } catch (err) {
      console.error(`Erreur lors de la connexion au serveur ${serverHost}:`, err);
      error.value = error.value || err.message || `Erreur lors de la connexion au serveur ${serverHost}`;

      // Annuler la mise à jour optimiste
      isConnected.value = false;
      pluginState.value = 'ready_to_connect';
      host.value = null;
      deviceName.value = null;

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
      lastAction.value = 'disconnect';

      const response = await axios.post('/api/snapclient/disconnect');
      const data = response.data;

      // Mise à jour de la blacklist
      if (data.blacklisted) {
        blacklistedServers.value = data.blacklisted;
      }

      // Mise à jour immédiate de l'UI
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';

      return data;
    } catch (err) {
      console.error('Erreur lors de la déconnexion:', err);
      error.value = err.message || 'Erreur lors de la déconnexion du serveur';

      // Forcer la déconnexion même en cas d'erreur
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';

      return { status: "forced_disconnect", message: "Déconnexion forcée" };
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Réinitialise l'état du store
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
    // Ne pas réinitialiser la blacklist
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
    reset,
    updateFromWebSocketEvent,
    updateFromStateEvent,
    forceDisconnect
  };
});