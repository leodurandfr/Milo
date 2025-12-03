// frontend/src/services/websocket.js
import { ref, computed, onMounted, onUnmounted } from 'vue';

/**
 * WebSocket singleton with smart disconnect when the tab is hidden
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
    this.maxReconnectDelay = 30000; // Max 30 seconds
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
      this.closeConnection(true); // Full cleanup because there are no more subscribers
    }
  }

  createConnection() {
    // Prevent overlapping connections (both OPEN and CONNECTING states)
    if (this.socket &&
        (this.socket.readyState === WebSocket.OPEN ||
         this.socket.readyState === WebSocket.CONNECTING)) {
      return;
    }

    // Automatic WebSocket URL configuration
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;

    let wsUrl;
    // In DEV mode, connect directly to the backend on port 8000
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

      // Send ready signal to request initial state
      if (this.socket.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ type: "ready" }));
      }

      if (wasReconnecting) {
        console.log('WebSocket reconnected - requested state sync');
        // Notify subscribers that a reconnection occurred
        this.notifyReconnect();
      } else {
        console.log('WebSocket connected - requested initial state');
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

      // Auto-reconnect only if the tab is visible
      if (this.subscribers.size > 0 && !document.hidden) {
        // Exponential backoff: 1s, 2s, 4s, 8s, 16s, up to 30s max
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

    // Only clear handlers and state if this is a full cleanup (no more subscribers)
    if (fullCleanup) {
      this.eventHandlers.clear();
      this.lastSystemState = null;
      this.removeVisibilityListener();
    }
  }

  setupVisibilityListener() {
    if (this.visibilityHandler) return;

    this.visibilityHandler = () => {
      // When tab becomes visible, request fresh state (no disconnect/reconnect needed)
      if (!document.hidden && this.socket?.readyState === WebSocket.OPEN) {
        this.socket.send(JSON.stringify({ type: "ready" }));
        console.log('Tab visible - requested state refresh');
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
    // Check the connection every 60 seconds
    if (this.pingCheckInterval) {
      clearInterval(this.pingCheckInterval);
    }

    this.pingCheckInterval = setInterval(() => {
      const timeSinceLastPing = Date.now() - this.lastPingTime;

      // If no ping for 90 seconds (3x the interval), reconnect
      if (timeSinceLastPing > 90000 && !document.hidden) {
        console.warn('WebSocket ping timeout, reconnecting...');
        this.closeConnection();
        // onclose handler will reconnect with exponential backoff
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
    // Detect pings
    if (message.category === 'system' && message.type === 'ping') {
      this.lastPingTime = Date.now();
      return; // Do not propagate pings to handlers
    }

    // Cache full state from both initial_state and state_changed events
    if (message.category === 'system' &&
        (message.type === 'initial_state' || message.type === 'state_changed') &&
        message.data?.full_state) {
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

// Global singleton instance
const wsInstance = new WebSocketSingleton();

/**
 * WebSocket composable
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

// Debug for development
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