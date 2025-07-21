// frontend/src/services/websocket.js - Version finale OPTIM
import { ref, computed, onMounted, onUnmounted } from 'vue';

/**
 * Singleton WebSocket avec déconnexion intelligente sur onglet caché
 */
class WebSocketSingleton {
  constructor() {
    this.socket = null;
    this.isConnected = ref(false);
    this.eventHandlers = new Map();
    this.subscribers = new Set();
    this.lastSystemState = null;
    this.visibilityHandler = null;
  }

  addSubscriber(subscriberId) {
    this.subscribers.add(subscriberId);
    
    if (this.subscribers.size === 1) {
      this.createConnection();
    }
  }

  removeSubscriber(subscriberId) {
    this.subscribers.delete(subscriberId);
    
    if (this.subscribers.size === 0) {
      this.closeConnection();
    }
  }

  createConnection() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      return;
    }

    // Configuration automatique de l'URL WebSocket
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    
    let wsUrl;
    if (import.meta.env.DEV && host === 'localhost') {
      wsUrl = `${protocol}//localhost:5173/ws`;
    } else if (host === 'milo.local' || host.endsWith('.local')) {
      wsUrl = `${protocol}//${host}/ws`;
    } else {
      const port = window.location.port || (window.location.protocol === 'https:' ? 443 : 80);
      wsUrl = `${protocol}//${host}:${port}/ws`;
    }
    
    console.log(`WebSocket connecting to: ${wsUrl}`);
    this.socket = new WebSocket(wsUrl);
    
    this.socket.onopen = () => {
      this.isConnected.value = true;
      this.setupVisibilityListener();
      console.log('WebSocket connected successfully');
    };
    
    this.socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      } catch (error) {
        console.error('WebSocket message error:', error);
      }
    };
    
    this.socket.onclose = () => {
      this.isConnected.value = false;
      this.socket = null;
      console.log('WebSocket disconnected');
      
      // Reconnexion automatique seulement si l'onglet est visible
      if (this.subscribers.size > 0 && !document.hidden) {
        setTimeout(() => this.createConnection(), 3000);
      }
    };
    
    this.socket.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  closeConnection() {
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.isConnected.value = false;
    this.eventHandlers.clear();
    this.lastSystemState = null;
    this.removeVisibilityListener();
  }

  setupVisibilityListener() {
    if (this.visibilityHandler) return;
    
    this.visibilityHandler = () => {
      if (document.hidden) {
        // Déconnecter quand l'onglet est caché
        if (this.socket) {
          this.socket.close();
        }
      } else {
        // Reconnecter quand l'onglet redevient visible
        if (this.subscribers.size > 0) {
          // Fermer toute connexion existante
          if (this.socket) {
            this.socket.close();
          }
          
          // Reconnecter après un court délai
          setTimeout(() => {
            this.createConnection();
          }, 200);
        }
      }
    };
    
    document.addEventListener('visibilitychange', this.visibilityHandler);
  }
  
  removeVisibilityListener() {
    if (this.visibilityHandler) {
      document.removeEventListener('visibilitychange', this.visibilityHandler);
      this.visibilityHandler = null;
    }
  }

  handleMessage(message) {
    if (message.category === 'system' && message.type === 'state_changed' && message.data?.full_state) {
      this.lastSystemState = message.data.full_state;
    }
    
    const eventKey = `${message.category}.${message.type}`;
    const handlers = this.eventHandlers.get(eventKey);
    
    if (handlers) {
      handlers.forEach(callback => {
        try {
          callback(message);
        } catch (error) {
          console.error(`WebSocket callback error (${eventKey}):`, error);
        }
      });
    }
  }

  on(category, type, callback) {
    const eventKey = `${category}.${type}`;
    
    if (!this.eventHandlers.has(eventKey)) {
      this.eventHandlers.set(eventKey, new Set());
    }
    
    this.eventHandlers.get(eventKey).add(callback);
    
    return () => {
      const handlers = this.eventHandlers.get(eventKey);
      if (handlers) {
        handlers.delete(callback);
        if (handlers.size === 0) {
          this.eventHandlers.delete(eventKey);
        }
      }
    };
  }
}

// Instance singleton globale
const wsInstance = new WebSocketSingleton();

/**
 * Composable WebSocket
 */
export default function useWebSocket() {
  const subscriberId = Symbol('WebSocketSubscriber');
  const cleanupFunctions = [];

  onMounted(() => {
    wsInstance.addSubscriber(subscriberId);
  });

  onUnmounted(() => {
    cleanupFunctions.forEach(cleanup => cleanup());
    cleanupFunctions.length = 0;
    wsInstance.removeSubscriber(subscriberId);
  });

  function on(category, type, callback) {
    const cleanup = wsInstance.on(category, type, callback);
    cleanupFunctions.push(cleanup);
    return cleanup;
  }

  return {
    isConnected: computed(() => wsInstance.isConnected.value),
    on
  };
}

// Debug pour développement
if (import.meta.env.DEV) {
  window.wsDebug = () => {
    console.log('WebSocket Debug:', {
      subscribers: wsInstance.subscribers.size,
      connected: wsInstance.isConnected.value,
      eventTypes: Array.from(wsInstance.eventHandlers.keys()),
      hasCachedState: !!wsInstance.lastSystemState,
      url: wsInstance.socket?.url,
      tabHidden: document.hidden
    });
  };
}