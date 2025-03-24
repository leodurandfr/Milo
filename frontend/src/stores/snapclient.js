// frontend/src/stores/snapclient.js
import { defineStore } from 'pinia';
import { ref, computed } from 'vue';
import axios from 'axios';

export const useSnapclientStore = defineStore('snapclient', () => {
  // État
  const deviceInfo = ref({});
  const isConnected = ref(false);
  const discoveredServers = ref([]);
  const currentRequest = ref(null);
  const showConnectionRequest = ref(false);
  const lastStatus = ref(null);

  // Getters
  const hasValidDeviceInfo = computed(() => {
    return deviceInfo.value &&
      (deviceInfo.value.deviceName ||
        deviceInfo.value.host ||
        deviceInfo.value.device_name);
  });

  // Actions
  async function checkStatus() {
    try {
      const response = await axios.get('/api/snapclient/status');
      lastStatus.value = response.data;

      // Mettre à jour l'état de connexion
      isConnected.value = response.data.device_connected === true;

      // Si connecté, mettre à jour les informations de l'appareil
      if (isConnected.value && response.data.device_info) {
        deviceInfo.value = response.data.device_info;
      }

      return isConnected.value;
    } catch (error) {
      console.error("Erreur lors de la vérification du statut Snapclient:", error);
      isConnected.value = false;
      return false;
    }
  }

  async function discoverServers() {
    try {
      const response = await axios.post('/api/audio/control/macos', {
        command: 'discover',
        data: {}
      });

      if (response.data && response.data.result && response.data.result.servers) {
        discoveredServers.value = response.data.result.servers;
        return discoveredServers.value;
      }

      return [];
    } catch (error) {
      console.error("Erreur lors de la découverte des serveurs:", error);
      return [];
    }
  }

  async function connectToServer(host) {
    try {
      const response = await axios.post('/api/audio/control/macos', {
        command: 'connect',
        data: { host }
      });

      if (response.data && response.data.success) {
        await checkStatus();
        return true;
      }

      return false;
    } catch (error) {
      console.error(`Erreur lors de la connexion au serveur ${host}:`, error);
      return false;
    }
  }

  async function disconnectFromServer() {
    try {
      const response = await axios.post('/api/audio/control/macos', {
        command: 'disconnect',
        data: {}
      });

      if (response.data && response.data.success) {
        isConnected.value = false;
        deviceInfo.value = {};
        return true;
      }

      return false;
    } catch (error) {
      console.error("Erreur lors de la déconnexion du serveur:", error);
      return false;
    }
  }

  async function acceptConnectionRequest(host) {
    try {
      const response = await axios.post('/api/audio/control/macos', {
        command: 'accept_connection',
        data: { host }
      });

      if (response.data && response.data.success) {
        currentRequest.value = null;
        showConnectionRequest.value = false;
        await checkStatus();
        return true;
      }

      return false;
    } catch (error) {
      console.error(`Erreur lors de l'acceptation de la demande de connexion ${host}:`, error);
      return false;
    }
  }

  async function rejectConnectionRequest(host) {
    try {
      const response = await axios.post('/api/audio/control/macos', {
        command: 'reject_connection',
        data: { host }
      });

      if (response.data && response.data.success) {
        currentRequest.value = null;
        showConnectionRequest.value = false;
        return true;
      }

      return false;
    } catch (error) {
      console.error(`Erreur lors du rejet de la demande de connexion ${host}:`, error);
      return false;
    }
  }

  function dismissConnectionRequest() {
    currentRequest.value = null;
    showConnectionRequest.value = false;
  }

  async function handleCommand(command, data = {}) {
    try {
      console.log(`Exécution de la commande snapclient: ${command}`, data);

      // Envoyer la commande à l'API
      const response = await axios.post(`/api/audio/control/macos`, {
        command,
        data
      });

      if (response.data.status === 'error') {
        throw new Error(response.data.message);
      }

      return response.data.result || true;
    } catch (error) {
      console.error(`Erreur lors de l'exécution de la commande ${command}:`, error);
      return false;
    }
  }

  function handleEvent(eventType, data) {
    console.log(`Événement snapclient reçu: ${eventType}`, data);

    if (eventType === 'snapclient_connection_request') {
      // Nouvelle demande de connexion
      currentRequest.value = {
        host: data.host,
        deviceName: data.device_name || data.host,
        requestId: data.request_id,
        currentDevice: data.current_host
      };
      showConnectionRequest.value = true;
    }
    else if (eventType === 'audio_status_updated' && data.source === 'snapclient') {
      if (data.status === 'connected' || data.connected === true || data.deviceConnected === true) {
        // Connexion établie
        isConnected.value = true;

        // Mettre à jour les informations du périphérique et conserver les infos précédentes en cas de données partielles
        if (data.host || data.device_name) {
          deviceInfo.value = {
            ...deviceInfo.value,  // Garder les informations existantes
            host: data.host || deviceInfo.value.host,
            deviceName: data.device_name || data.host || deviceInfo.value.deviceName || "Snapcast",
            // Ajouter un timestamp pour savoir quand les données ont été mises à jour
            lastUpdated: Date.now()
          };
        }

        // Log de débogage
        console.log("État snapclient mis à jour (connecté)", deviceInfo.value);
      }
      else if (data.status === 'disconnected' || data.connected === false) {
        // En cas de déconnexion explicite, mettre à jour l'état
        console.log("Déconnexion snapclient détectée");

        // Ne réinitialiser l'état que si c'est une déconnexion explicite
        if (data.status === 'disconnected') {
          isConnected.value = false;
          deviceInfo.value = {};
        } else {
          // Pour les autres cas, vérifier l'état actuel via l'API
          console.log("Vérification de l'état de connexion via API...");
          checkStatus();
        }
      }
    }
  }


  async function checkStatus() {
    try {
      const response = await axios.get('/api/snapclient/status');
      lastStatus.value = response.data;

      // Mettre à jour l'état de connexion
      const newConnectionState = response.data.device_connected === true;

      // Ne changer l'état que s'il est différent, pour éviter des boucles de mise à jour
      if (newConnectionState !== isConnected.value) {
        isConnected.value = newConnectionState;
        console.log(`État de connexion snapclient mis à jour: ${isConnected.value}`);
      }

      // Si connecté, mettre à jour les informations de l'appareil
      if (isConnected.value && response.data.device_info) {
        // Fusionner avec les informations existantes
        deviceInfo.value = {
          ...deviceInfo.value,
          ...response.data.device_info,
          lastUpdated: Date.now()
        };
        console.log("Informations appareil snapclient mises à jour", deviceInfo.value);
      }

      return isConnected.value;
    } catch (error) {
      console.error("Erreur lors de la vérification du statut Snapclient:", error);

      // Ne pas modifier l'état de connexion en cas d'erreur API
      // On considère que l'erreur peut être temporaire
      return isConnected.value;
    }
  }

  return {
    // État
    deviceInfo,
    isConnected,
    discoveredServers,
    currentRequest,
    showConnectionRequest,
    lastStatus,

    // Getters
    hasValidDeviceInfo,

    // Actions
    checkStatus,
    discoverServers,
    connectToServer,
    disconnectFromServer,
    acceptConnectionRequest,
    rejectConnectionRequest,
    dismissConnectionRequest,
    handleCommand,
    handleEvent
  };
});