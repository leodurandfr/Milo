// frontend/src/services/websocket.js - Version corrigée pour oakos.local
import { ref, computed, onMounted, onUnmounted } from 'vue';

/**
 * Singleton WebSocket simplifié - Version corrigée pour oakos.local
 */
class WebSocketSingleton {
  constructor() {
    this.socket = null;
    this.isConnected = ref(false);
    this.eventHandlers = new Map(); // eventKey -> Set(callbacks)
    this.subscribers = new Set(); // Set des IDs de composants actifs
    this.lastSystemState = null; // État initial mis en cache
    this.visibilityHandler = null; // Handler pour visibility change
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

    // ✅ Configuration unifiée pour oakos.local
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = window.location.port || (window.location.protocol === 'https:' ? 443 : 80);
    
    // En développement via Vite, utiliser le proxy
    // En production via Nginx, utiliser oakos.local direct
    const wsUrl = import.meta.env.DEV 
      ? `${protocol}//${host}:5173/ws`  // Proxy Vite
      : `${protocol}//${host}:${port}/ws`;  // Nginx direct
    
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
      this.removeVisibilityListener();
      console.log('WebSocket disconnected');
      
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
    this.removeVisibilityListener();
  }

  // Gestion du retour sur l'onglet - Version OPTIM ciblée
  setupVisibilityListener() {
    if (this.visibilityHandler) return; // Éviter les doublons
    
    this.visibilityHandler = async () => {
      if (!document.hidden && this.isConnected.value) {
        // Onglet redevient actif - rafraîchir seulement l'état audio principal
        await this.fetchAudioStateOnly();
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
  
  async fetchAudioStateOnly() {
    try {
      // ✅ Utiliser des URLs relatives (passent par Nginx)
      const response = await fetch('/api/audio/state');
      if (response.ok) {
        const currentState = await response.json();
        
        // Diffuser seulement l'état audio principal (pas les données modals)
        const audioEvent = {
          category: "system",
          type: "state_changed",
          data: { full_state: currentState },
          source: "visibility_refresh",
          timestamp: Date.now()
        };
        
        this.handleMessage(audioEvent);
      }
    } catch (error) {
      console.error('Error fetching audio state on visibility change:', error);
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
 * Composable WebSocket avec interface identique
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