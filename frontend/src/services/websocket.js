// frontend/src/services/websocket.js
import { ref, onUnmounted } from 'vue';

export default function useWebSocket() {
  const socket = ref(null);
  const isConnected = ref(false);
  const events = {};
  const connectionAttempts = ref(0);
  let reconnectTimer = null;
  let heartbeatInterval = null;
  
  const createNewConnection = () => {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = import.meta.env.DEV ? 8000 : window.location.port;
    const wsUrl = `${protocol}//${host}:${port}/ws`;
    
    console.log(`🔌 Connexion WebSocket à ${wsUrl}`);
    
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    try {
      if (socket.value && socket.value.readyState !== WebSocket.CLOSED) {
        socket.value.close();
      }
      
      socket.value = new WebSocket(wsUrl);
      
      socket.value.onopen = () => {
        console.log('✅ WebSocket connecté');
        isConnected.value = true;
        connectionAttempts.value = 0;
        startHeartbeat();
      };
      
      socket.value.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          
          // Filtrer les messages de ping/pong pour le debug
          if (message.type !== 'ping' && message.type !== 'heartbeat_ack') {
            console.log(`📡 WebSocket: ${message.type}`);
          }
          
          // Répondre aux pings
          if (message.type === 'ping') {
            sendMessage({ type: 'pong', data: { timestamp: Date.now() } });
            return;
          }
          
          // Gérer les événements standardisés
          if (message.type === 'standard_event') {
            const standardEvent = message.data;
            console.log(`🔄 Standard event: ${standardEvent.category}.${standardEvent.type}`);
            
            // Créer une clé d'événement standard
            const eventKey = `${standardEvent.category}.${standardEvent.type}`;
            
            // Dispatcher aux abonnés
            if (events[eventKey]) {
              events[eventKey].forEach(callback => {
                try {
                  callback(standardEvent);
                } catch (err) {
                  console.error(`Erreur dans callback de ${eventKey}:`, err);
                }
              });
            }
            
            // Pour la compatibilité, dispatcher aussi sous l'ancien format "state_update"
            if (standardEvent.data.full_state) {
              if (events['state_update']) {
                events['state_update'].forEach(callback => {
                  try {
                    callback(standardEvent.data);
                  } catch (err) {
                    console.error('Erreur dans callback de state_update:', err);
                  }
                });
              }
            }
          } else {
            // Gérer les événements legacy
            if (message.type && events[message.type]) {
              events[message.type].forEach(callback => {
                try {
                  callback(message.data);
                } catch (err) {
                  console.error(`Erreur dans callback de ${message.type}:`, err);
                }
              });
            }
          }
        } catch (error) {
          console.error('Erreur de parsing WebSocket:', error);
        }
      };
      
      socket.value.onclose = (event) => {
        console.log(`WebSocket déconnecté: ${event.code}`);
        isConnected.value = false;
        clearHeartbeat();
        
        if (event.code === 1000 && event.reason === "Démontage propre") {
          return;
        }
        
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
  
  const on = (eventType, callback) => {
    if (!events[eventType]) {
      events[eventType] = [];
    }
    events[eventType].push(callback);
    
    return () => {
      if (events[eventType]) {
        events[eventType] = events[eventType].filter(cb => cb !== callback);
      }
    };
  };
  
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
  
  createNewConnection();
  
  onUnmounted(cleanup);
  
  return {
    isConnected,
    on,
    send: sendMessage,
    reconnect: createNewConnection,
    disconnect: cleanup
  };
}