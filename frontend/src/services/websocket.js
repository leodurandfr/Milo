/**
 * Service pour gérer la connexion WebSocket
 */
import { ref, onUnmounted } from 'vue';

// Identifiant unique pour cette instance
const CLIENT_ID = 'frontend-' + Date.now();
const DEBUG = true;

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const lastMessage = ref(null);
  const events = {};
  const connectionAttempts = ref(0);
  
  // Fonction pour créer une nouvelle connexion
  const createNewConnection = () => {
    // Déterminer l'URL WebSocket en fonction de l'environnement
    let wsUrl;
    
    if (import.meta.env.DEV) {
      // En développement, utiliser l'hôte du navigateur
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.hostname;
      wsUrl = `${protocol}//${host}:8000/ws`;
      
      console.log(`🔌 Connexion WebSocket en mode développement à ${wsUrl}`);
    } else {
      // En production, utiliser l'API proxy
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}/ws`;
      
      console.log(`🔌 Connexion WebSocket en mode production à ${wsUrl}`);
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
        
        // Ne pas logger les pings pour réduire le bruit
        if (message.type !== 'ping' && message.type !== 'heartbeat_ack') {
          // Log spécial pour les événements Snapclient
          if (message.type && message.type.startsWith('snapclient_')) {
            if (DEBUG) {
              console.log(`⚡ WebSocket reçu [${message.type}]:`, message.data);
            }
          }
        }
        
        lastMessage.value = message;
        
        // Traitement spécial pour certains types de messages
        if (message.type === 'ping') {
          // Répondre avec un pong pour maintenir la connexion
          socket.value.send(JSON.stringify({
            type: 'pong',
            data: { timestamp: Date.now() }
          }));
          return; // Ne pas propager les pings
        }
        
        // Traitement prioritaire des messages critiques
        if (message.type === 'snapclient_emergency_disconnect' || 
            message.type === 'snapclient_monitor_disconnected') {
          console.log(`🚨 Message WebSocket CRITIQUE reçu: ${message.type}`);
          
          // Exécution prioritaire des callbacks pour les messages critiques
          if (events[message.type]) {
            events[message.type].forEach(callback => {
              try {
                callback(message.data);
              } catch (e) {
                console.error(`Erreur dans callback de ${message.type}:`, e);
              }
            });
          }
          
          // Continuer le traitement normal (pour les autres callbacks potentiels)
        }
        
        // Déclencher les écouteurs d'événements standards
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
      console.log(`WebSocket déconnecté, code: ${event.code}, raison: ${event.reason}`);
      isConnected.value = false;
      
      // Incrémenter le compteur de tentatives
      connectionAttempts.value++;
      
      // Délai exponentiel pour les reconnexions
      const delay = Math.min(30000, Math.pow(1.5, connectionAttempts.value) * 1000);
      console.log(`Tentative de reconnexion dans ${(delay/1000).toFixed(1)}s (tentative #${connectionAttempts.value})`);
      
      // Tenter de se reconnecter après un délai
      setTimeout(connect, delay);
    };
    
    socket.value.onerror = (error) => {
      console.error('Erreur WebSocket:', error);
      // Ne pas fermer ici, laisser onclose gérer la reconnexion
    };
  };
  
  // Heartbeat pour maintenir la connexion active
  let heartbeatInterval = null;
  
  const startHeartbeat = () => {
    // Nettoyer l'ancien heartbeat si existant
    clearHeartbeat();
    
    // Configurer un nouveau heartbeat
    heartbeatInterval = setInterval(() => {
      if (socket.value && isConnected.value) {
        socket.value.send(JSON.stringify({
          type: 'heartbeat',
          data: { 
            timestamp: Date.now(),
            client_id: CLIENT_ID
          }
        }));
      }
    }, 30000); // 30 secondes
  };
  
  const clearHeartbeat = () => {
    if (heartbeatInterval) {
      clearInterval(heartbeatInterval);
      heartbeatInterval = null;
    }
  };
  
  const connect = () => {
    // Si une connexion existe déjà, la fermer proprement d'abord
    if (socket.value) {
      // Nettoyer les ressources
      clearHeartbeat();
      
      // Si socket n'est pas déjà fermé, le fermer proprement
      if (socket.value.readyState !== WebSocket.CLOSED && 
          socket.value.readyState !== WebSocket.CLOSING) {
        
        console.log("🔄 Fermeture de la connexion WebSocket existante avant reconnexion");
        
        // Désactiver la reconnexion automatique pour cette fermeture
        const oldSocket = socket.value;
        socket.value = null;
        
        // Fermer proprement
        try {
          oldSocket.close(1000, "Reconnexion intentionnelle");
        } catch (e) {
          console.warn("Erreur lors de la fermeture du socket:", e);
        }
      }
      
      // Création d'une nouvelle connexion après un court délai
      setTimeout(() => {
        createNewConnection();
      }, 100);
    } else {
      // Aucune connexion existante, créer directement
      createNewConnection();
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
    if (socket.value && isConnected.value) {
      socket.value.send(JSON.stringify(data));
    } else {
      console.warn('Tentative d\'envoi alors que WebSocket n\'est pas connecté', data);
    }
  };
  
  // Fonction de nettoyage pour la déconnexion
  const cleanup = () => {
    if (socket.value) {
      clearHeartbeat();
      
      // Fermeture propre
      try {
        socket.value.close(1000, "Démontage propre");
      } catch (e) {
        console.warn("Erreur lors de la fermeture du WebSocket:", e);
      }
      
      socket.value = null;
      isConnected.value = false;
    }
  };
  
  // Nettoyer la connexion quand le composant est démonté
  onUnmounted(() => {
    cleanup();
  });
  
  // Connexion initiale
  connect();
  
  return {
    isConnected,
    lastMessage,
    on,
    send,
    reconnect: connect,
    disconnect: cleanup
  };
}