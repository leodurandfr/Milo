// frontend/src/services/websocket.js - Version Singleton Intelligent
import { ref, computed, onMounted, onUnmounted } from 'vue';

/**
 * Singleton WebSocket intelligent avec reference counting
 * Une seule connexion partagée entre tous les composants
 */
class WebSocketSingleton {
  constructor() {
    this.socket = null;
    this.isConnected = ref(false);
    this.eventHandlers = new Map(); // eventKey -> Map(subscriberId -> Set(callbacks))
    this.subscribers = new Set(); // Set des IDs de composants actifs
    this.reconnectTimer = null;
    this.reconnectDelay = 3000;
    this.maxReconnectAttempts = 10;
    this.reconnectAttempts = 0;
    
    console.log('🔌 WebSocket Singleton created');
  }

  /**
   * Ajoute un subscriber (composant) et démarre la connexion si nécessaire
   */
  addSubscriber(subscriberId) {
    this.subscribers.add(subscriberId);
    
    console.log(`📱 Subscriber added: ${subscriberId.toString().slice(7, 15)}... (total: ${this.subscribers.size})`);
    
    // Premier subscriber = créer la connexion
    if (this.subscribers.size === 1) {
      this.createConnection();
    }
  }

  /**
   * Supprime un subscriber et ferme la connexion si plus personne n'en a besoin
   */
  removeSubscriber(subscriberId) {
    // Nettoyer tous les event listeners de ce subscriber
    this.cleanupSubscriberEvents(subscriberId);
    
    this.subscribers.delete(subscriberId);
    
    console.log(`📱 Subscriber removed: ${subscriberId.toString().slice(7, 15)}... (total: ${this.subscribers.size})`);
    
    // Plus de subscribers = fermer la connexion
    if (this.subscribers.size === 0) {
      this.closeConnection();
    }
  }

  /**
   * Crée la connexion WebSocket
   */
  createConnection() {
    if (this.socket && this.socket.readyState === WebSocket.OPEN) {
      console.log('🔌 WebSocket already connected');
      return;
    }

    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const host = window.location.hostname;
    const port = import.meta.env.DEV ? 8000 : window.location.port;
    const wsUrl = `${protocol}//${host}:${port}/ws`;
    
    console.log(`🔌 Creating WebSocket connection to: ${wsUrl}`);
    
    try {
      this.socket = new WebSocket(wsUrl);
      
      this.socket.onopen = () => {
        console.log('✅ WebSocket connected successfully');
        this.isConnected.value = true;
        this.reconnectAttempts = 0;
        
        // Nettoyer le timer de reconnexion
        if (this.reconnectTimer) {
          clearTimeout(this.reconnectTimer);
          this.reconnectTimer = null;
        }
      };
      
      this.socket.onmessage = (event) => {
        try {
          const message = JSON.parse(event.data);
          this.handleMessage(message);
        } catch (error) {
          console.error('❌ WebSocket message parse error:', error);
        }
      };
      
      this.socket.onclose = (event) => {
        console.log(`🔌 WebSocket disconnected (code: ${event.code})`);
        this.isConnected.value = false;
        this.socket = null;
        
        // Reconnexion automatique si on a encore des subscribers
        if (this.subscribers.size > 0) {
          this.scheduleReconnect();
        }
      };
      
      this.socket.onerror = (error) => {
        console.error('❌ WebSocket error:', error);
      };
      
    } catch (error) {
      console.error('❌ WebSocket creation error:', error);
      this.scheduleReconnect();
    }
  }

  /**
   * Ferme la connexion WebSocket
   */
  closeConnection() {
    console.log('🔌 Closing WebSocket connection (no more subscribers)');
    
    // Nettoyer le timer de reconnexion
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    
    // Fermer la socket
    if (this.socket) {
      this.socket.close();
      this.socket = null;
    }
    
    this.isConnected.value = false;
    this.reconnectAttempts = 0;
    
    // Nettoyer tous les event handlers
    this.eventHandlers.clear();
  }

  /**
   * Programme une reconnexion avec backoff exponentiel
   */
  scheduleReconnect() {
    if (this.subscribers.size === 0) {
      console.log('🔌 No subscribers, skipping reconnect');
      return;
    }

    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      console.error(`❌ Max reconnect attempts reached (${this.maxReconnectAttempts})`);
      return;
    }

    this.reconnectAttempts++;
    const delay = Math.min(this.reconnectDelay * Math.pow(2, this.reconnectAttempts - 1), 30000);
    
    console.log(`🔄 Scheduling reconnect #${this.reconnectAttempts} in ${delay}ms`);
    
    this.reconnectTimer = setTimeout(() => {
      this.createConnection();
    }, delay);
  }

  /**
   * Traite un message WebSocket reçu
   */
  handleMessage(message) {
    const eventKey = `${message.category}.${message.type}`;
    
    console.log(`📨 WebSocket received: ${eventKey}`, message);
    
    const handlersForEvent = this.eventHandlers.get(eventKey);
    if (!handlersForEvent) {
      console.warn(`⚠️ No handlers for event: ${eventKey}`);
      return;
    }

    // Appeler tous les callbacks de tous les subscribers pour cet événement
    let totalCallbacks = 0;
    for (const [subscriberId, callbacks] of handlersForEvent) {
      callbacks.forEach(callback => {
        try {
          callback(message);
          totalCallbacks++;
        } catch (error) {
          console.error(`❌ Error in event callback (${eventKey}):`, error);
        }
      });
    }
    
    console.log(`📨 Event ${eventKey} handled by ${totalCallbacks} callbacks`);
  }

  /**
   * Enregistre un event listener pour un subscriber
   */
  on(category, type, callback, subscriberId) {
    const eventKey = `${category}.${type}`;
    
    // Créer la structure si elle n'existe pas
    if (!this.eventHandlers.has(eventKey)) {
      this.eventHandlers.set(eventKey, new Map());
    }
    
    const handlersForEvent = this.eventHandlers.get(eventKey);
    if (!handlersForEvent.has(subscriberId)) {
      handlersForEvent.set(subscriberId, new Set());
    }
    
    handlersForEvent.get(subscriberId).add(callback);
    
    console.log(`🎯 Handler registered: ${eventKey} for subscriber ${subscriberId.toString().slice(7, 15)}...`);
    
    // Retourner une fonction de cleanup pour ce callback spécifique
    return () => {
      this.removeEventCallback(eventKey, subscriberId, callback);
    };
  }

  /**
   * Supprime un callback spécifique
   */
  removeEventCallback(eventKey, subscriberId, callback) {
    const handlersForEvent = this.eventHandlers.get(eventKey);
    if (!handlersForEvent) return;
    
    const subscriberCallbacks = handlersForEvent.get(subscriberId);
    if (!subscriberCallbacks) return;
    
    subscriberCallbacks.delete(callback);
    
    // Nettoyer les structures vides
    if (subscriberCallbacks.size === 0) {
      handlersForEvent.delete(subscriberId);
    }
    
    if (handlersForEvent.size === 0) {
      this.eventHandlers.delete(eventKey);
    }
    
    console.log(`🗑️ Handler removed: ${eventKey} for subscriber ${subscriberId.toString().slice(7, 15)}...`);
  }

  /**
   * Nettoie tous les event listeners d'un subscriber
   */
  cleanupSubscriberEvents(subscriberId) {
    let cleanedEvents = 0;
    
    for (const [eventKey, handlersForEvent] of this.eventHandlers) {
      if (handlersForEvent.has(subscriberId)) {
        const callbackCount = handlersForEvent.get(subscriberId).size;
        handlersForEvent.delete(subscriberId);
        cleanedEvents += callbackCount;
        
        // Supprimer l'event si plus de handlers
        if (handlersForEvent.size === 0) {
          this.eventHandlers.delete(eventKey);
        }
      }
    }
    
    if (cleanedEvents > 0) {
      console.log(`🧹 Cleaned ${cleanedEvents} event handlers for subscriber ${subscriberId.toString().slice(7, 15)}...`);
    }
  }

  /**
   * Debug: affiche l'état actuel du singleton
   */
  getDebugInfo() {
    const eventStats = {};
    for (const [eventKey, handlersForEvent] of this.eventHandlers) {
      eventStats[eventKey] = handlersForEvent.size;
    }
    
    return {
      subscribers: this.subscribers.size,
      connected: this.isConnected.value,
      events: Object.keys(eventStats).length,
      eventStats,
      reconnectAttempts: this.reconnectAttempts
    };
  }
}

// Instance singleton globale
const wsInstance = new WebSocketSingleton();

// Debug global pour le développement
if (import.meta.env.DEV) {
  window.wsDebug = () => {
    console.log('🔍 WebSocket Debug Info:', wsInstance.getDebugInfo());
  };
}

/**
 * Composable WebSocket avec interface identique mais implémentation singleton
 */
export default function useWebSocket() {
  // ID unique pour ce composant/instance
  const subscriberId = Symbol('WebSocketSubscriber');
  
  // Stocker les fonctions de cleanup pour les nettoyer
  const cleanupFunctions = [];

  onMounted(() => {
    wsInstance.addSubscriber(subscriberId);
  });

  onUnmounted(() => {
    // Nettoyer tous les event listeners enregistrés
    cleanupFunctions.forEach(cleanup => cleanup());
    cleanupFunctions.length = 0;
    
    // Supprimer ce subscriber
    wsInstance.removeSubscriber(subscriberId);
  });

  /**
   * Enregistre un event listener (interface identique à l'ancienne version)
   */
  function on(category, type, callback) {
    const cleanup = wsInstance.on(category, type, callback, subscriberId);
    cleanupFunctions.push(cleanup);
    
    // Retourner la fonction de cleanup pour compatibilité
    return cleanup;
  }

  return {
    isConnected: computed(() => wsInstance.isConnected.value),
    on
  };
}