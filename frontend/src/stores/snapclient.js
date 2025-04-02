// frontend/src/stores/snapclient.js

/**
 * Store Pinia pour la gestion de l'état de Snapclient.
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
  const statusCheckInterval = ref(null);

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

    // Mise à jour IMMÉDIATE de l'état sans appel réseau
    pluginState.value = data.plugin_state;

    if (data.plugin_state === 'connected' && data.connected === true) {
      isConnected.value = true;
      deviceName.value = data.device_name || 'Serveur inconnu';
      host.value = data.host;
      error.value = null;

      // Déclencher l'événement pour une propagation plus rapide
      window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
        detail: { connected: true }
      }));

      console.log("✅ État mis à jour: connecté à", deviceName.value);
    }
    else if (data.plugin_state === 'ready_to_connect') {
      // Vérifier s'il y a un changement d'état réel avant de mettre à jour
      if (isConnected.value) {
        isConnected.value = false;
        deviceName.value = null;
        host.value = null;

        // Déclencher l'événement uniquement si l'état a changé
        window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
          detail: { connected: false }
        }));
      }
      console.log("✅ État mis à jour: prêt à connecter");
    }

    return true;
  }

  /**
   * Met à jour l'état du store en fonction d'un événement WebSocket.
   */
  function updateFromWebSocketEvent(eventType, data) {
    console.log(`⚡ Mise à jour directe du store depuis: ${eventType}`);

    // PRIORITÉ AUX ÉVÉNEMENTS DE DÉCONNEXION - traitement immédiat et sans délai
    if (eventType === 'snapclient_monitor_disconnected' || eventType === 'snapclient_server_disappeared') {
      console.log("🔴 Déconnexion détectée via WebSocket, mise à jour instantanée de l'UI");

      // Mise à jour synchrone et immédiate de l'état
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';
      error.value = `Le serveur ${data.host} s'est déconnecté`;

      // SUPPRESSION DU DÉLAI - Ne pas attendre pour rafraîchir l'état
      // Appel synchrone au lieu d'utiliser setTimeout
      fetchStatus(true).catch(err => {
        console.warn('Erreur non bloquante lors du refresh après déconnexion:', err);
      });

      // Émettre un événement de mise à jour pour les autres composants
      try {
        window.dispatchEvent(new CustomEvent('snapclient-connection-changed', {
          detail: { connected: false, source: eventType }
        }));
      } catch (e) {
        console.warn("Impossible d'émettre l'événement custom:", e);
      }

      return true;
    }

    if (eventType === 'snapclient_monitor_connected') {
      console.log(`⚡ Moniteur connecté à ${data.host}`);
      error.value = null;

      // Optimisation: vérifier ici si nous sommes déjà connectés au même serveur
      if (host.value === data.host) {
        isConnected.value = true;
        pluginState.value = 'connected';
      }

      return true;
    }

    // Autres événements non gérés
    return false;
  }

  /**
   * Récupère le statut actuel du plugin Snapclient.
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

      // Si l'état de connexion a changé, le signaler
      if (wasConnected !== isConnected.value) {
        console.log(`⚡ Changement d'état de connexion détecté: ${wasConnected ? 'connecté' : 'déconnecté'} -> ${isConnected.value ? 'connecté' : 'déconnecté'}`);

        // Émettre un événement personnalisé pour informer les composants de ce changement
        try {
          const connectionEvent = new CustomEvent('snapclient-connection-changed', {
            detail: { connected: isConnected.value }
          });
          window.dispatchEvent(connectionEvent);
        } catch (e) {
          console.warn("Impossible d'émettre l'événement custom:", e);
        }
      }

      deviceName.value = data.device_name;
      host.value = data.host;

      // Mise à jour explicite des serveurs découverts
      if (data.discovered_servers) {
        console.log(`📊 Mise à jour des serveurs: ${data.discovered_servers.length} serveurs`);
        discoveredServers.value = [...data.discovered_servers];
      }

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

      // Si on reçoit une erreur 500 ou une erreur réseau, il y a peut-être un problème avec le serveur
      // ou la connexion. Dans ce cas, considérons que nous sommes déconnectés.
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

        // Si on n'est pas connecté et qu'on a exactement un serveur,
        // essayer de s'y connecter automatiquement
        if (!isConnected.value && data.servers.length === 1) {
          const server = data.servers[0];

          // Vérifier si c'est le dernier serveur connu
          let lastServer = null;
          try {
            const savedServer = localStorage.getItem('lastSnapclientServer');
            if (savedServer) {
              lastServer = JSON.parse(savedServer);
            }
          } catch (e) {
            console.error("Erreur lors de la lecture du dernier serveur:", e);
          }

          // Si c'est le même serveur que le dernier utilisé ou s'il n'y a qu'un serveur,
          // se connecter automatiquement
          if (lastServer && lastServer.host === server.host) {
            console.log("🔄 Reconnexion automatique au dernier serveur:", server.name);
            setTimeout(() => {
              connectToServer(server.host);
            }, 500);
          }
        }
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
   * Se connecte à un serveur Snapcast spécifique avec vérification préalable.
   */
  async function connectToServer(serverHost) {
    if (!isActive.value) {
      return { success: false, inactive: true };
    }

    // Vérifier si nous sommes déjà connectés à ce serveur
    if (isConnected.value && host.value === serverHost) {
      console.log(`🔌 Déjà connecté à ${serverHost}, connexion ignorée`);
      return { success: true, already_connected: true };
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

      // Enregistrer ce serveur comme dernier serveur utilisé
      try {
        localStorage.setItem('lastSnapclientServer', JSON.stringify({
          host: serverHost,
          timestamp: Date.now()
        }));
      } catch (e) {
        console.warn("Impossible d'enregistrer le dernier serveur:", e);
      }

      // Mettre à jour l'état immédiatement (optimistic UI update)
      // Cela assure que l'UI se met à jour même si la requête est lente
      isConnected.value = true;
      pluginState.value = 'connected';
      host.value = serverHost;

      // Si le serveur est présent dans la liste des découverts, utiliser son nom
      const server = discoveredServers.value.find(s => s.host === serverHost);
      if (server) {
        deviceName.value = server.name;
      } else {
        deviceName.value = `Serveur (${serverHost})`;
      }

      // Envoyer la requête de connexion au backend
      const response = await axios.post(`/api/snapclient/connect/${serverHost}`);
      const data = response.data;

      if (data.status === 'error') {
        // Annuler la mise à jour optimiste en cas d'échec
        isConnected.value = false;
        pluginState.value = 'ready_to_connect';
        host.value = null;
        deviceName.value = null;
        throw new Error(data.message);
      }

      if (data.blacklisted === true) {
        // Annuler la mise à jour optimiste en cas de blacklist
        isConnected.value = false;
        pluginState.value = 'ready_to_connect';
        host.value = null;
        deviceName.value = null;
        error.value = `Le serveur ${serverHost} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.`;
        throw new Error(error.value);
      }

      // Mise à jour complète après connexion (but keep optimistic values if API fails)
      try {
        await fetchStatus(true);
      } catch (statusErr) {
        console.warn("Erreur lors de la mise à jour du statut après connexion:", statusErr);
        // Ne pas réinitialiser l'état optimiste même si fetchStatus échoue
      }

      // Démarrer le polling pour maintenir l'état synchronisé
      startPolling();

      return data;
    } catch (err) {
      console.error(`Erreur lors de la connexion au serveur ${serverHost}:`, err);
      error.value = error.value || err.message || `Erreur lors de la connexion au serveur ${serverHost}`;

      // S'assurer que l'état reflète l'échec de connexion
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

      // Forcer l'état local à déconnecté immédiatement (pour une UI plus réactive)
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
        startPolling();
      } else if (data.from_state === 'macos') {
        reset();
        stopPolling();
      }
    }

    // Erreurs de transition
    if (eventType === 'audio_transition_error' && data.error && data.error.includes('server')) {
      fetchStatus();
    }
  }

  /**
   * Démarre le polling périodique du statut
   */
  function startPolling() {
    // Arrêter tout polling existant
    stopPolling();

    // Démarrer un nouveau polling toutes les 5 secondes
    statusCheckInterval.value = setInterval(() => {
      if (isConnected.value) {
        // Si connecté, vérifier que la connexion est toujours valide
        fetchStatus(false).catch(err => {
          console.warn("Erreur dans le polling de statut:", err);
        });
      }
    }, 5000);

    console.log("📡 Polling de statut démarré");
  }

  /**
   * Arrête le polling périodique
   */
  function stopPolling() {
    if (statusCheckInterval.value) {
      clearInterval(statusCheckInterval.value);
      statusCheckInterval.value = null;
      console.log("📡 Polling de statut arrêté");
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

    // Arrêter le polling
    stopPolling();
  }

  // Démarrer/arrêter le polling en fonction de l'état de connexion
  watch(isConnected, (newValue) => {
    if (newValue === true) {
      console.log("⚡ Connexion détectée, démarrage du polling");
      startPolling();
    }
  });

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
    updateFromStateEvent,
    startPolling,
    stopPolling
  };
});