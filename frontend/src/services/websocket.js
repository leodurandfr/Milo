// frontend/src/services/websocket.js
/**
 * Service pour gérer la connexion WebSocket avec reconnexion automatique - Version optimisée.
 */
import { ref, onUnmounted } from 'vue';

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const events = {};
  const connectionAttempts = ref(0);
  let reconnectTimer = null;
  let heartbeatInterval = null;
  
  // Fonction pour créer une nouvelle connexion
  const createNewConnection = () => {
    // Déterminer l'URL WebSocket en fonction de l'environnement
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = import.meta.env.DEV ? 8000 : window.location.port;
    const wsUrl = `${protocol}//${host}:${port}/ws`;
    
    console.log(`🔌 Connexion WebSocket à ${wsUrl}`);
    
    // Annuler toute tentative de reconnexion en cours
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    try {
      // Nettoyer la connexion précédente
      if (socket.value && socket.value.readyState !== WebSocket.CLOSED) {
        socket.value.close();
      }
      
      // Créer la nouvelle instance WebSocket
      socket.value = new WebSocket(wsUrl);
      
      socket.value.onopen = () => {
        console.log('✅ WebSocket connecté');
        isConnected.value = true;
        connectionAttempts.value = 0;
        startHeartbeat();
        
        // Message d'identification
        sendMessage({
          type: 'client_connected',
          data: { timestamp: Date.now() }
        });
      };
      
      socket.value.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          // Filtrer les messages de ping/pong pour le debug
          if (message.type !== 'ping' && message.type !== 'heartbeat_ack') {
            // Log sélectif des événements importants
            if (message.type.startsWith('snapclient_') || 
                message.type.startsWith('audio_state_')) {
              console.log(`📡 WebSocket: ${message.type}`);
            }
          }
          
          // Répondre aux pings
          if (message.type === 'ping') {
            sendMessage({ type: 'pong', data: { timestamp: Date.now() } });
            return;
          }
          
          // Dispatcher l'événement aux abonnés
          if (message.type && events[message.type]) {
            events[message.type].forEach(callback => {
              try {
                callback(message.data);
              } catch (err) {
                console.error(`Erreur dans callback de ${message.type}:`, err);
              }
            });
          }
        } catch (error) {
          console.error('Erreur de parsing WebSocket:', error);
        }
      };
      
      socket.value.onclose = (event) => {
        console.log(`WebSocket déconnecté: ${event.code}`);
        isConnected.value = false;
        clearHeartbeat();
        
        // Ne pas tenter de reconnexion si c'est une fermeture propre
        if (event.code === 1000 && event.reason === "Démontage propre") {
          return;
        }
        
        // Programmation de la reconnexion avec délai exponentiel
        connectionAttempts.value++;
        const delay = Math.min(30000, Math.pow(1.5, Math.min(connectionAttempts.value, 10)) * 1000);
        
        reconnectTimer = setTimeout(() => {
          console.log("Tentative de reconnexion...");
          createNewConnection();
        }, delay);
      };
      
      socket.value.onerror = () => {
        console.error('Erreur WebSocket');
      };
    } catch (e) {
      console.error("Erreur lors de la création du WebSocket:", e);
      reconnectTimer = setTimeout(createNewConnection, 3000);
    }
  };
  
  // Fonctions heartbeat simplifiées
  const startHeartbeat = () => {
    clearHeartbeat();
    heartbeatInterval = setInterval(() => {
      if (socket.value?.readyState === WebSocket.OPEN) {
        sendMessage({ type: 'heartbeat', data: { timestamp: Date.now() } });
      } else if (socket.value?.readyState !== WebSocket.CONNECTING) {
        isConnected.value = false;
        clearHeartbeat();
        createNewConnection();
      }
    }, 20000);
  };
  
  const clearHeartbeat = () => {
    if (heartbeatInterval) {
      clearInterval(heartbeatInterval);
      heartbeatInterval = null;
    }
  };
  
  // Fonction pour envoyer un message
  const sendMessage = (data) => {
    if (socket.value?.readyState === WebSocket.OPEN) {
      try {
        socket.value.send(JSON.stringify(data));
        return true;
      } catch (e) {
        console.error("Erreur d'envoi WebSocket:", e);
        isConnected.value = false;
        createNewConnection();
        return false;
      }
    }
    return false;
  };
  
  // S'abonner à un type d'événement
  const on = (eventType, callback) => {
    if (!events[eventType]) {
      events[eventType] = [];
    }
    events[eventType].push(callback);
    
    // Retourner une fonction de désabonnement
    return () => {
      if (events[eventType]) {
        events[eventType] = events[eventType].filter(cb => cb !== callback);
      }
    };
  };
  
  // Nettoyer les ressources
  const cleanup = () => {
    if (reconnectTimer) clearTimeout(reconnectTimer);
    if (heartbeatInterval) clearInterval(heartbeatInterval);
    
    if (socket.value) {
      if (socket.value.readyState === WebSocket.OPEN) {
        socket.value.close(1000, "Démontage propre");
      }
      socket.value = null;
    }
    
    isConnected.value = false;
  };
  
  // Établir la connexion
  createNewConnection();
  
  // Nettoyer lors du démontage
  onUnmounted(cleanup);
  
  return {
    isConnected,
    on,
    send: sendMessage,
    reconnect: createNewConnection,
    disconnect: cleanup
  };
}