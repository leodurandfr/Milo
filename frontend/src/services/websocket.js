// frontend/src/services/websocket.js
/**
 * Service pour gérer la connexion WebSocket avec reconnexion automatique - Version optimisée.
 */
import { ref, onUnmounted } from 'vue';

// Identifiant unique pour cette instance
const CLIENT_ID = 'frontend-' + Date.now();

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const lastMessage = ref(null);
  const events = {};
  const connectionAttempts = ref(0);
  let reconnectTimer = null;
  
  // Fonction pour créer une nouvelle connexion
  const createNewConnection = () => {
    // Déterminer l'URL WebSocket en fonction de l'environnement
    let wsUrl;
    
    if (import.meta.env.DEV) {
      // En développement, utiliser l'hôte du navigateur
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.hostname;
      const port = 8000; // Port du backend
      wsUrl = `${protocol}//${host}:${port}/ws`;
    } else {
      // En production, utiliser l'API proxy
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}/ws`;
    }
    
    console.log(`🔌 Connexion WebSocket à ${wsUrl}`);
    
    // Annuler toute tentative de reconnexion en cours
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    try {
      // Nettoyer la connexion précédente si elle existe
      if (socket.value && socket.value.readyState !== WebSocket.CLOSED) {
        try {
          socket.value.close();
        } catch (e) {
          console.warn('Erreur lors de la fermeture du WebSocket:', e);
        }
      }
      
      // Créer la nouvelle instance WebSocket
      socket.value = new WebSocket(wsUrl);
      
      socket.value.onopen = () => {
        console.log('✅ WebSocket connecté avec succès');
        isConnected.value = true;
        connectionAttempts.value = 0;
        
        // Envoyer un message initial pour établir la communication
        socket.value.send(JSON.stringify({
          type: 'client_connected',
          data: { 
            client_id: CLIENT_ID,
            timestamp: Date.now(),
            user_agent: navigator.userAgent
          }
        }));
        
        // Configurer le heartbeat
        startHeartbeat();
      };
      
      socket.value.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          // Traitement prioritaire des événements critiques
          if (message.type === 'snapclient_monitor_disconnected' || 
              message.type === 'snapclient_server_disappeared') {
            console.log(`🚨 ÉVÉNEMENT CRITIQUE REÇU: ${message.type}`, message.data);
            
            // Notification globale pour forcer tous les composants à réagir
            window.dispatchEvent(new CustomEvent('global-state-change', { 
              detail: { type: message.type, data: message.data }
            }));
          }
          
          // Ne pas logger les pings pour réduire le bruit
          if (message.type !== 'ping' && message.type !== 'heartbeat_ack') {
            // Log autres événements importants
            if (message.type.startsWith('snapclient_') || 
                message.type.startsWith('audio_state_')) {
              console.log(`📡 WebSocket: ${message.type}`);
            }
          }
          
          lastMessage.value = message;
          
          // Traitement des messages
          if (message.type === 'ping') {
            // Répondre aux pings
            socket.value.send(JSON.stringify({
              type: 'pong',
              data: { timestamp: Date.now() }
            }));
            return;
          }
          
          // Déclencher les écouteurs d'événements
          if (message.type && events[message.type]) {
            events[message.type].forEach(callback => {
              try {
                callback(message.data);
              } catch (callbackError) {
                console.error(`Erreur dans callback de ${message.type}:`, callbackError);
              }
            });
          }
        } catch (error) {
          console.error('Erreur de parsing WebSocket:', error);
        }
      };
      
      socket.value.onclose = (event) => {
        console.log(`WebSocket déconnecté, code: ${event.code}, raison: ${event.reason || 'inconnue'}`);
        isConnected.value = false;
        clearHeartbeat();
        
        // Ne pas tenter de reconnexion si c'est une fermeture propre
        if (event.code === 1000 && event.reason === "Démontage propre") {
          console.log("Fermeture propre, pas de reconnexion automatique");
          return;
        }
        
        // Incrémenter le compteur de tentatives
        connectionAttempts.value++;
        
        // Délai exponentiel pour les reconnexions (max 30 secondes)
        const delay = Math.min(30000, Math.pow(1.5, Math.min(connectionAttempts.value, 10)) * 1000);
        console.log(`Tentative de reconnexion dans ${(delay/1000).toFixed(1)}s (tentative #${connectionAttempts.value})`);
        
        // Tenter de se reconnecter après un délai
        reconnectTimer = setTimeout(() => {
          console.log("Tentative de reconnexion...");
          createNewConnection();
        }, delay);
      };
      
      socket.value.onerror = (error) => {
        console.error('Erreur WebSocket:', error);
      };
    } catch (e) {
      console.error("Erreur lors de la création du WebSocket:", e);
      // En cas d'erreur, planifier une nouvelle tentative
      reconnectTimer = setTimeout(() => {
        console.log("Nouvelle tentative après erreur...");
        createNewConnection();
      }, 3000);
    }
  };
  
  // Heartbeat pour maintenir la connexion active
  let heartbeatInterval = null;
  
  const startHeartbeat = () => {
    // Nettoyer l'ancien heartbeat si existant
    clearHeartbeat();
    
    // Configurer un nouveau heartbeat
    heartbeatInterval = setInterval(() => {
      if (socket.value && socket.value.readyState === WebSocket.OPEN) {
        try {
          socket.value.send(JSON.stringify({
            type: 'heartbeat',
            data: { 
              timestamp: Date.now(),
              client_id: CLIENT_ID
            }
          }));
        } catch (e) {
          console.error("Erreur d'envoi de heartbeat:", e);
          // Si l'envoi échoue, tenter de reconnecter
          isConnected.value = false;
          clearHeartbeat();
          createNewConnection();
        }
      } else if (socket.value && socket.value.readyState !== WebSocket.CONNECTING) {
        // Si le socket n'est pas en train de se connecter et n'est pas ouvert, reconnecter
        console.warn("Heartbeat: WebSocket n'est pas ouvert, tentative de reconnexion");
        isConnected.value = false;
        clearHeartbeat();
        createNewConnection();
      }
    }, 20000); // 20 secondes
  };
  
  const clearHeartbeat = () => {
    if (heartbeatInterval) {
      clearInterval(heartbeatInterval);
      heartbeatInterval = null;
    }
  };
  
  const on = (eventType, callback) => {
    if (!events[eventType]) {
      events[eventType] = [];
    }
    events[eventType].push(callback);
    
    // Retourner une fonction pour se désabonner
    return () => {
      if (events[eventType]) {
        events[eventType] = events[eventType].filter(cb => cb !== callback);
      }
    };
  };
  
  const send = (data) => {
    if (socket.value && socket.value.readyState === WebSocket.OPEN) {
      try {
        socket.value.send(JSON.stringify(data));
        return true;
      } catch (e) {
        console.error("Erreur d'envoi WebSocket:", e);
        // Tenter de reconnecter en cas d'erreur
        isConnected.value = false;
        createNewConnection();
        return false;
      }
    } else {
      console.warn("WebSocket non connecté, impossible d'envoyer:", data);
      // Tenter de reconnecter
      if (!isConnected.value) {
        console.log("Tentative de reconnexion après échec d'envoi");
        createNewConnection();
      }
      return false;
    }
  };
  
  // Fonction de nettoyage pour la déconnexion
  const cleanup = () => {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }
    
    if (socket.value) {
      clearHeartbeat();
      
      // Fermeture propre
      try {
        if (socket.value.readyState === WebSocket.OPEN) {
          socket.value.close(1000, "Démontage propre");
        }
      } catch (e) {
        console.warn("Erreur lors de la fermeture du WebSocket:", e);
      }
      
      socket.value = null;
    }
    
    isConnected.value = false;
  };
  
  // Vérification périodique de la connexion
  let connectionCheckInterval = null;
  
  const startConnectionCheck = () => {
    connectionCheckInterval = setInterval(() => {
      if (!isConnected.value && !reconnectTimer) {
        console.log("Vérification: WebSocket déconnecté, tentative de reconnexion");
        createNewConnection();
      }
    }, 10000); // 10 secondes
  };
  
  // Nettoyer la connexion quand le composant est démonté
  onUnmounted(() => {
    if (connectionCheckInterval) {
      clearInterval(connectionCheckInterval);
    }
    cleanup();
  });
  
  // Connexion initiale
  createNewConnection();
  startConnectionCheck();
  
  return {
    isConnected,
    lastMessage,
    on,
    send,
    reconnect: createNewConnection,
    disconnect: cleanup
  };
}