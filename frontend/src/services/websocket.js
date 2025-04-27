// frontend/src/services/websocket.js
import { ref, onUnmounted } from 'vue';

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const eventHandlers = new Map();
  
  function createConnection() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = import.meta.env.DEV ? 8000 : window.location.port;
    const wsUrl = `${protocol}//${host}:${port}/ws`;
    
    socket.value = new WebSocket(wsUrl);
    
    socket.value.onopen = () => {
      console.log('WebSocket connected');
      isConnected.value = true;
    };
    
    socket.value.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        const eventKey = `${message.category}.${message.type}`;
        
        console.log('WebSocket received:', eventKey, message); // Ajout pour debug
        
        if (eventHandlers.has(eventKey)) {
          eventHandlers.get(eventKey).forEach(callback => callback(message));
        } else {
          console.warn('No handler for event:', eventKey);
        }
      } catch (error) {
        console.error('WebSocket message error:', error);
      }
    };
    
    socket.value.onclose = () => {
      console.log('WebSocket disconnected');
      isConnected.value = false;
      // Reconnexion automatique
      setTimeout(createConnection, 3000);
    };
  }
  
  function on(category, type, callback) {
    const eventKey = `${category}.${type}`;
    if (!eventHandlers.has(eventKey)) {
      eventHandlers.set(eventKey, new Set());
    }
    eventHandlers.get(eventKey).add(callback);
    
    console.log('Registered handler for:', eventKey); // Ajout pour debug
    
    return () => {
      eventHandlers.get(eventKey).delete(callback);
    };
  }
  
  createConnection();
  
  onUnmounted(() => {
    if (socket.value) {
      socket.value.close();
    }
  });
  
  return {
    isConnected,
    on
  };
}