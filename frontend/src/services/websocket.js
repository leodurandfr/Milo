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
    
    // Envoyer l'état initial si disponible pour les événements système
    if (category === 'system' && type === 'state_changed' && this.lastSystemState && this.isConnected.value) {
      setTimeout(() => {
        const initialEvent = {
          category: "system",
          type: "state_changed",
          data: { full_state: this.lastSystemState },
          timestamp: Date.now()
        };
        callback(initialEvent);
      }, 10);
    }
    
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