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
      
      console.log('Statut snapclient récupéré:', data);
      
      isActive.value = data.is_active === true;
      isConnected.value = data.device_connected === true;
      deviceName.value = data.device_name;
      host.value = data.host;
      discoveredServers.value = data.discovered_servers || [];
      
      // Récupérer la liste des serveurs blacklistés si disponible
      if (data.blacklisted_servers) {
        blacklistedServers.value = data.blacklisted_servers;
      }
      
      // Déduire l'état du plugin à partir des données
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
      
      // En cas d'erreur, réinitialiser l'état 
      isActive.value = false;
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';
      
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Déclenche une découverte des serveurs Snapcast sur le réseau.
   */
  async function discoverServers() {
    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'discover';
      
      const response = await axios.post('/api/snapclient/discover');
      const data = response.data;
      
      if (data.status === 'error') {
        throw new Error(data.message);
      }
      
      discoveredServers.value = data.servers || [];
      
      // Mettre à jour l'état en fonction de l'action effectuée
      if (data.action === 'auto_connected') {
        await fetchStatus(); // Refresh status to get the latest state
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
   * @param {string} serverHost - Adresse du serveur
   */
  async function connectToServer(serverHost) {
    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'connect';
      
      // Vérifier si le serveur est blacklisté
      if (blacklistedServers.value.includes(serverHost)) {
        error.value = `Le serveur ${serverHost} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.`;
        throw new Error(error.value);
      }
      
      const response = await axios.post(`/api/snapclient/connect/${serverHost}`);
      const data = response.data;
      
      if (data.status === 'error') {
        throw new Error(data.message);
      }
      
      // Si le serveur est blacklisté selon le backend
      if (data.blacklisted === true) {
        error.value = `Le serveur ${serverHost} a été déconnecté manuellement. Changez de source audio pour pouvoir vous y reconnecter.`;
        throw new Error(error.value);
      }
      
      // Mettre à jour l'état après la connexion
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
      
      // Même si le statut est error, considérer que nous sommes déconnectés
      // pour éviter de bloquer l'interface utilisateur
      
      // Mettre à jour la liste des serveurs blacklistés
      if (data.blacklisted) {
        blacklistedServers.value = data.blacklisted;
        console.log('Serveurs blacklistés mis à jour:', blacklistedServers.value);
      }
      
      // Forcer l'état local à déconnecté
      isConnected.value = false;
      deviceName.value = null;
      host.value = null;
      pluginState.value = 'ready_to_connect';
      
      // Mettre à jour l'état après la déconnexion
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
      
      // Ne pas propager l'erreur pour éviter de bloquer l'utilisateur
      return { status: "forced_disconnect", message: "Déconnexion forcée après erreur" };
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Accepte une demande de connexion.
   * @param {Object} params - Paramètres pour l'acceptation
   * @param {string} [params.requestId] - ID de la demande à accepter
   * @param {string} [params.host] - Hôte de la demande à accepter
   */
  async function acceptRequest(params) {
    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'accept';
      
      const response = await axios.post('/api/snapclient/accept-request', params);
      const data = response.data;
      
      if (data.status === 'error') {
        throw new Error(data.message);
      }
      
      // Mettre à jour l'état après l'acceptation
      await fetchStatus();
      
      return data;
    } catch (err) {
      console.error('Erreur lors de l\'acceptation de la demande:', err);
      error.value = err.message || 'Erreur lors de l\'acceptation de la demande';
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Rejette une demande de connexion.
   * @param {Object} params - Paramètres pour le rejet
   * @param {string} [params.requestId] - ID de la demande à rejeter
   * @param {string} [params.host] - Hôte de la demande à rejeter
   */
  async function rejectRequest(params) {
    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'reject';
      
      const response = await axios.post('/api/snapclient/reject-request', params);
      const data = response.data;
      
      if (data.status === 'error') {
        throw new Error(data.message);
      }
      
      // Mettre à jour l'état après le rejet
      await fetchStatus();
      
      return data;
    } catch (err) {
      console.error('Erreur lors du rejet de la demande:', err);
      error.value = err.message || 'Erreur lors du rejet de la demande';
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Redémarre le processus Snapclient.
   */
  async function restartProcess() {
    try {
      isLoading.value = true;
      error.value = null;
      lastAction.value = 'restart';
      
      const response = await axios.post('/api/snapclient/restart');
      const data = response.data;
      
      if (data.status === 'error') {
        throw new Error(data.message);
      }
      
      // Mettre à jour l'état après le redémarrage
      await fetchStatus();
      
      return data;
    } catch (err) {
      console.error('Erreur lors du redémarrage du processus:', err);
      error.value = err.message || 'Erreur lors du redémarrage du processus';
      throw err;
    } finally {
      isLoading.value = false;
    }
  }

  /**
   * Gère les mises à jour WebSocket pour Snapclient.
   * @param {string} eventType - Type d'événement
   * @param {Object} data - Données de l'événement
   */
  function handleWebSocketUpdate(eventType, data) {
    if (!data) return;
    
    // Vérifier si l'événement concerne snapclient
    if (data.source === 'snapclient') {
      console.log('Événement WebSocket snapclient reçu:', eventType, data);
      
      // Mettre à jour l'état en fonction de l'événement
      if (eventType === 'audio_status_updated') {
        if (data.plugin_state) {
          pluginState.value = data.plugin_state;
          console.log(`État du plugin mis à jour: ${data.plugin_state}`);
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
    
    // Événements généraux de changement d'état audio
    if (eventType === 'audio_state_changed') {
      // Si on passe à macos, récupérer le statut
      if (data.current_state === 'macos') {
        console.log('Changement vers source macos détecté, récupération du statut');
        fetchStatus();
      } 
      // Si on quitte macos, réinitialiser l'état
      else if (data.from_state === 'macos') {
        console.log('Changement depuis source macos détecté, réinitialisation');
        reset();
      }
    }
    
    // Quand un serveur disparaît, forcer une mise à jour de statut
    if (eventType === 'audio_transition_error' && data.error && data.error.includes('server')) {
      console.log('Erreur de transition détectée, mise à jour du statut');
      fetchStatus();
    }
  }

  // Configurer un intervalle pour mettre à jour régulièrement l'état du store
  const refreshStore = () => {
    if (isActive.value) {
      fetchStatus().catch(err => console.error('Erreur de rafraîchissement automatique:', err));
    }
  };
  
  let storeRefreshInterval = null;
  
  // Démarrer l'intervalle de rafraîchissement
  const startRefreshInterval = () => {
    stopRefreshInterval(); // Arrêter tout intervalle existant
    storeRefreshInterval = setInterval(refreshStore, 3000); // Rafraîchir toutes les 3 secondes
    console.log('Intervalle de rafraîchissement du store démarré');
  };
  
  // Arrêter l'intervalle de rafraîchissement
  const stopRefreshInterval = () => {
    if (storeRefreshInterval) {
      clearInterval(storeRefreshInterval);
      storeRefreshInterval = null;
    }
  };

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
    // Ne pas réinitialiser la blacklist ici - elle est conservée jusqu'au changement de source audio
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
    acceptRequest,
    rejectRequest,
    restartProcess,
    handleWebSocketUpdate,
    startRefreshInterval,
    stopRefreshInterval,
    reset
  };
});