// frontend/src/services/websocket.js - Version Simplifiée (Singleton Essentiel)
import { ref, computed, onMounted, onUnmounted } from 'vue';

/**
 * Singleton WebSocket simplifié - Résout juste le problème d'accumulation + état initial
 */
class WebSocketSingleton {
  constructor() {
    this.socket = null;
    this.isConnected = ref(false);
    this.eventHandlers = new Map(); // eventKey -> Set(callbacks)
    this.subscribers = new Set(); // Set des IDs de composants actifs
    this.lastSystemState = null; // État initial mis en cache
  }

  addSubscriber(subscriberId) {
    this.subscribers.add(subscriberId);
    
    // Premier subscriber = créer la connexion
    if (this.subscribers.size === 1) {
      this.createConnection();
    } else {
      // Connexion existante = récupérer l'état frais du serveur
      this.fetchFreshInitialState();
    }
  }

  removeSubscriber(subscriberId) {
    this.subscribers.delete(subscriberId);
    
    // Plus de subscribers = fermer la connexion
    if (this.subscribers.size === 0) {
      this.closeConnection();
    }
  }

  createConnection() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = import.meta.env.DEV ? 8000 : window.location.port;
    const wsUrl = `${protocol}//${host}:${port}/ws`;
    
    this.socket = new WebSocket(wsUrl);
    
    this.socket.onopen = () => {
      this.isConnected.value = true;
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
      
      // Reconnexion automatique si on a encore des subscribers
      if (this.subscribers.size > 0) {
        setTimeout(() => this.createConnection(), 3000);
      }
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
  }

  async fetchFreshInitialState() {
    try {
      const response = await fetch('/api/audio/state');
      if (response.ok) {
        const currentState = await response.json();
        
        // Mettre à jour le cache
        this.lastSystemState = currentState;
        
        // Diffuser l'état frais à tous les handlers system.state_changed
        const freshEvent = {
          category: "system",
          type: "state_changed",
          data: { full_state: currentState },
          source: "api_refresh",
          timestamp: Date.now()
        };
        
        this.handleMessage(freshEvent);
      }
    } catch (error) {
      console.error('Error fetching fresh initial state:', error);
    }
  }

  handleMessage(message) {
    // Mettre en cache l'état système pour les nouveaux composants
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
    
    // Plus besoin d'envoyer l'état mis en cache ici - 
    // fetchFreshInitialState() s'en charge pour les nouveaux subscribers
    
    // Retourner fonction de cleanup
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
 * Composable WebSocket avec interface identique mais implémentation singleton
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

// Debug simple (optionnel)
if (import.meta.env.DEV) {
  window.wsDebug = () => {
    console.log('WebSocket Debug:', {
      subscribers: wsInstance.subscribers.size,
      connected: wsInstance.isConnected.value,
      eventTypes: Array.from(wsInstance.eventHandlers.keys()),
      hasCachedState: !!wsInstance.lastSystemState,
      cachedState: wsInstance.lastSystemState
    });
  };
}