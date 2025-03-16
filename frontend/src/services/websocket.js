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
    // Utiliser le même hôte que le navigateur, mais avec le WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    // En développement, se connecter directement au backend
    // const wsUrl = 'ws://localhost:8000/ws';
    
    socket.value = new WebSocket(wsUrl);
    
    socket.value.onopen = () => {
      console.log('WebSocket connected');
      isConnected.value = true;
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