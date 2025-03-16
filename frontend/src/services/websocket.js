/**
 * file: frontend/src/services/websocket.js
 * Service pour gérer la connexion WebSocket
 */
import { ref, onUnmounted } from 'vue';

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const lastMessage = ref(null);
  const events = {};
  
  const connect = () => {
    // Déterminer l'URL WebSocket en fonction de l'environnement
    let wsUrl;
    
    if (import.meta.env.DEV) {
      // En développement, utiliser l'hôte du navigateur
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      const host = window.location.hostname;
      wsUrl = `${protocol}//${host}:8000/ws`;
      
      console.log(`Connexion WebSocket en mode développement à ${wsUrl}`);
    } else {
      // En production, utiliser l'API proxy
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      wsUrl = `${protocol}//${window.location.host}/ws`;
      
      console.log(`Connexion WebSocket en mode production à ${wsUrl}`);
    }
    
    socket.value = new WebSocket(wsUrl);
    
    socket.value.onopen = () => {
      console.log('WebSocket connecté avec succès');
      isConnected.value = true;
      
      // Envoyer un message initial pour établir la communication
      socket.value.send(JSON.stringify({
        type: 'client_connected',
        data: { client_id: 'frontend-' + Date.now() }
      }));
    };
    
    socket.value.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        console.log('Message WebSocket reçu:', message);
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
        
        // Déclencher les écouteurs d'événements
        if (message.type && events[message.type]) {
          events[message.type].forEach(callback => callback(message.data));
        }
      } catch (error) {
        console.error('Erreur de parsing WebSocket:', error);
      }
    };
    
    socket.value.onclose = (event) => {
      console.log(`WebSocket déconnecté, code: ${event.code}, raison: ${event.reason}`);
      isConnected.value = false;
      // Tenter de se reconnecter après un délai
      setTimeout(connect, 5000);
    };
    
    socket.value.onerror = (error) => {
      console.error('Erreur WebSocket:', error);
      // Ne pas fermer ici, laisser onclose gérer la reconnexion
    };
  };
  
  const on = (eventType, callback) => {
    if (!events[eventType]) {
      events[eventType] = [];
    }
    events[eventType].push(callback);
    
    // Retourner une fonction pour se désabonner
    return () => {
      events[eventType] = events[eventType].filter(cb => cb !== callback);
    };
  };
  
  const send = (data) => {
    if (socket.value && isConnected.value) {
      socket.value.send(JSON.stringify(data));
    } else {
      console.warn('Tentative d\'envoi alors que WebSocket n\'est pas connecté', data);
    }
  };
  
  // Nettoyer la connexion quand le composant est démonté
  onUnmounted(() => {
    if (socket.value) {
      socket.value.close(1000, "Démontage du composant");
    }
  });
  
  // Connexion initiale
  connect();
  
  return {
    isConnected,
    lastMessage,
    on,
    send
  };
}