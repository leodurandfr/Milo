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
    this.lastPingTime = Date.now();
    this.pingCheckInterval = null;
    this.reconnectCallbacks = new Set();
    this.reconnectAttempts = 0;
    this.maxReconnectDelay = 30000; // 30 secondes max
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
      this.closeConnection(true); // Full cleanup car plus de subscribers
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
    // En mode DEV, se connecter directement au backend sur le port 8000
    if (import.meta.env.DEV && (host === 'localhost' || host === '127.0.0.1')) {
      wsUrl = `${protocol}//${host}:8000/ws`;
    } else if (host === 'milo.local' || host.endsWith('.local')) {
      wsUrl = `${protocol}//${host}/ws`;
    } else {
      const port = window.location.port || (window.location.protocol === 'https:' ? 443 : 80);
      wsUrl = `${protocol}//${host}:${port}/ws`;
    }

    console.log(`WebSocket connecting to: ${wsUrl}`);
    this.socket = new WebSocket(wsUrl);
    
    this.socket.onopen = () => {
      const wasReconnecting = this.isConnected.value === false;
      this.isConnected.value = true;
      this.lastPingTime = Date.now();
      this.reconnectAttempts = 0; // Reset backoff counter on successful connection
      this.setupVisibilityListener();
      this.startPingCheck();

      if (wasReconnecting) {
        console.log('WebSocket reconnected - full state sync incoming');
        // Notifier les subscribers qu'une reconnexion a eu lieu
        this.notifyReconnect();
      } else {
        console.log('WebSocket connected successfully');
      }
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
        // Backoff exponentiel: 1s, 2s, 4s, 8s, 16s, jusqu'à 30s max
        this.reconnectAttempts++;
        const delay = Math.min(
          1000 * Math.pow(2, this.reconnectAttempts - 1),
          this.maxReconnectDelay
        );
        console.log(`WebSocket will reconnect in ${delay}ms (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.createConnection(), delay);
      }
    };
    
    this.socket.onerror = (error) => {
      console.error('WebSocket error:', error);
    };
  }

  closeConnection(fullCleanup = false) {
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    this.isConnected.value = false;
    this.stopPingCheck();

    // Ne clear les handlers et l'état que si c'est un cleanup complet (plus de subscribers)
    if (fullCleanup) {
      this.eventHandlers.clear();
      this.lastSystemState = null;
      this.removeVisibilityListener();
    }
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
          // Reset backoff counter on visibility change (user interaction)
          this.reconnectAttempts = 0;

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

    document.addEventListener('visibilitychange', this.visibilityHandler, { passive: true });
  }
  
  removeVisibilityListener() {
    if (this.visibilityHandler) {
      document.removeEventListener('visibilitychange', this.visibilityHandler);
      this.visibilityHandler = null;
    }
  }

  startPingCheck() {
    // Vérifier la connexion toutes les 60 secondes
    if (this.pingCheckInterval) {
      clearInterval(this.pingCheckInterval);
    }

    this.pingCheckInterval = setInterval(() => {
      const timeSinceLastPing = Date.now() - this.lastPingTime;

      // Si pas de ping depuis 90 secondes (3x l'intervalle), reconnect
      if (timeSinceLastPing > 90000 && !document.hidden) {
        console.warn('WebSocket ping timeout, reconnecting...');
        this.closeConnection();
        this.createConnection();
      }
    }, 60000);
  }

  stopPingCheck() {
    if (this.pingCheckInterval) {
      clearInterval(this.pingCheckInterval);
      this.pingCheckInterval = null;
    }
  }

  notifyReconnect() {
    this.reconnectCallbacks.forEach(callback => {
      try {
        callback();
      } catch (error) {
        console.error('Reconnect callback error:', error);
      }
    });
  }

  onReconnect(callback) {
    this.reconnectCallbacks.add(callback);
    return () => {
      this.reconnectCallbacks.delete(callback);
    };
  }

  handleMessage(message) {
    // Détecter les pings
    if (message.category === 'system' && message.type === 'ping') {
      this.lastPingTime = Date.now();
      return; // Ne pas propager les pings aux handlers
    }

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

  function onReconnect(callback) {
    const cleanup = wsInstance.onReconnect(callback);
    cleanupFunctions.push(cleanup);
    return cleanup;
  }

  return {
    isConnected: computed(() => wsInstance.isConnected.value),
    on,
    onReconnect
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