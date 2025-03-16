/**
 * Service pour gérer la connexion WebSocket
 */
import { ref, onUnmounted } from 'vue';

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const lastMessage = ref(null);
  const events = {};
  
  const connect = () => {
    // URL fixe en développement
    const wsUrl = 'ws://127.0.0.1:8000/ws';
    
    console.log('Trying to connect to WebSocket:', wsUrl);
    
    socket.value = new WebSocket(wsUrl);
    
    socket.value.onopen = () => {
      console.log('WebSocket connected successfully');
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
        lastMessage.value = message;
        
        // Déclencher les écouteurs d'événements
        if (message.type && events[message.type]) {
          events[message.type].forEach(callback => callback(message.data));
        }
      } catch (error) {
        console.error('Error parsing WebSocket message:', error);
      }
    };
    
    socket.value.onclose = () => {
      console.log('WebSocket disconnected');
      isConnected.value = false;
      // Tenter de se reconnecter après un délai
      setTimeout(connect, 5000);
    };
    
    socket.value.onerror = (error) => {
      console.error('WebSocket error:', error);
      socket.value.close();
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
    }
  };
  
  // Nettoyer la connexion quand le composant est démonté
  onUnmounted(() => {
    if (socket.value) {
      socket.value.close();
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