// frontend/src/services/websocket.js
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { logger } from '@/services/logger';

/**
 * WebSocket singleton with smart disconnect when the tab is hidden
 *
 * Event Categories & Handlers:
 * ────────────────────────────────────────────────────────────────
 * system:
 *   - initial_state, state_changed, transition_*, error → App.vue → unifiedAudioStore
 *   - ping → handled internally (health check)
 *
 * volume:
 *   - volume_changed → App.vue (global), Dock.vue, MultiroomControl.vue (local updates)
 *
 * plugin:
 *   - state_changed, metadata → App.vue → unifiedAudioStore, podcastStore
 *
 * settings:
 *   - language_changed → App.vue, LanguageSettings.vue
 *   - dock_apps_changed → Dock.vue, DockSettings.vue
 *   - volume_steps_changed → Dock.vue
 *   - podcast_credentials_changed → PodcastSettings.vue
 *   - spotify_disconnect_changed → SpotifySettings.vue
 *
 * radio:
 *   - favorite_added, favorite_removed → RadioSource.vue → radioStore
 *
 * multiroom: (Standardized format per architecture spec, Story 6.1/6.2)
 *   - client_state_changed → multiroomStore (client online/offline, volume, mute, speaker_type)
 *     Data: { mac_id, client: { complete client object with all fields } }
 *   - zone_changed → multiroomStore (zone create/delete/update, membership changes)
 *     Data: { zone_id, zone: { enriched zone with online_client_count, has_subwoofer, crossover_enabled } | null }
 *   - dsp_changed → dspStore (zone/client DSP settings)
 *     Data: { target_type: "zone"|"client", target_id, dsp_settings }
 *   - crossover_changed → dspStore (crossover enable/disable, frequency)
 *     Data: { zone_id, crossover_enabled, crossover_frequency }
 *
 * snapcast: (low-level Snapcast events - kept for debugging/monitoring)
 *   - client_* events → MultiroomControl.vue → multiroomStore
 *   - client_name_changed → also DspSettings.vue, MultiroomSettings.vue (sync names)
 *
 * dsp:
 *   - filter_*, state_changed, preset_*, compressor_*, loudness_* → DspSettings.vue → dspStore
 *   - links_changed, enabled_changed → DspSettings, MultiroomSettings, MultiroomControl
 *   - client_volumes_pushed → MultiroomSettings, MultiroomControl
 *
 * routing:
 *   - multiroom_enabling, multiroom_disabling → MultiroomModal, MultiroomControl, SettingsModal
 *   - multiroom_ready, multiroom_error → MultiroomModal, MultiroomControl
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
    this.visibilityChangeCallbacks = new Set();
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

    logger.info('websocket', `Connecting to ${wsUrl}`);
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
        logger.info('websocket', 'Reconnected - state sync requested');
        // Notify subscribers that a reconnection occurred
        this.notifyReconnect();
      } else {
        logger.info('websocket', 'Connected - initial state requested');
      }
    };

    this.socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      } catch (error) {
        logger.error('websocket', 'Message parse error', { error: error.message });
      }
    };
    
    this.socket.onclose = () => {
      this.isConnected.value = false;
      this.socket = null;
      logger.info('websocket', 'Disconnected');

      // Auto-reconnect only if the tab is visible
      if (this.subscribers.size > 0 && !document.hidden) {
        // Exponential backoff: 1s, 2s, 4s, 8s, 16s, up to 30s max
        this.reconnectAttempts++;
        const delay = Math.min(
          1000 * Math.pow(2, this.reconnectAttempts - 1),
          this.maxReconnectDelay
        );
        logger.info('websocket', `Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);
        setTimeout(() => this.createConnection(), delay);
      }
    };

    this.socket.onerror = (error) => {
      logger.error('websocket', 'Connection error', { error });
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

    this.visibilityHandler = async () => {
      if (!document.hidden) {
        if (this.socket?.readyState === WebSocket.OPEN) {
          // Socket is open - fetch fresh state via HTTP
          logger.debug('websocket', 'Tab visible - fetching fresh state');
          try {
            const [audioRes, volumeRes] = await Promise.all([
              fetch('/api/audio/state'),
              fetch('/api/volume/state')
            ]);

            if (audioRes.ok) {
              const audioState = await audioRes.json();
              this.handleMessage({
                category: 'system',
                type: 'state_changed',
                source: 'system',
                data: { full_state: audioState }
              });
            }

            if (volumeRes.ok) {
              const volumeData = await volumeRes.json();
              if (volumeData.status === 'success') {
                this.handleMessage({
                  category: 'volume',
                  type: 'volume_changed',
                  source: 'volume',
                  data: { show_bar: false, state: volumeData.data }
                });
              }
            }
          } catch (error) {
            logger.warn('websocket', 'Failed to fetch state on visibility change', { error: error.message });
          }
          this.notifyVisibilityChange();
        } else if (this.subscribers.size > 0) {
          // Socket is closed - trigger reconnection
          logger.info('websocket', 'Tab visible - socket closed, reconnecting');
          this.createConnection();
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
    // Check the connection every 60 seconds
    if (this.pingCheckInterval) {
      clearInterval(this.pingCheckInterval);
    }

    this.pingCheckInterval = setInterval(() => {
      const timeSinceLastPing = Date.now() - this.lastPingTime;

      // If no ping for 90 seconds (3x the interval), reconnect
      if (timeSinceLastPing > 90000 && !document.hidden) {
        logger.warn('websocket', 'Ping timeout, reconnecting...');
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
        logger.error('websocket', 'Reconnect callback error', { error: error.message });
      }
    });
  }

  onReconnect(callback) {
    this.reconnectCallbacks.add(callback);
    return () => {
      this.reconnectCallbacks.delete(callback);
    };
  }

  onVisibilityChange(callback) {
    this.visibilityChangeCallbacks.add(callback);
    return () => {
      this.visibilityChangeCallbacks.delete(callback);
    };
  }

  notifyVisibilityChange() {
    this.visibilityChangeCallbacks.forEach(callback => {
      try {
        callback();
      } catch (error) {
        logger.error('websocket', 'Visibility callback error', { error: error.message });
      }
    });
  }

  handleMessage(message) {
    // Validate message structure
    if (!message || typeof message !== 'object') {
      logger.warn('websocket', 'Invalid message format (not an object)');
      return;
    }

    if (!message.category || typeof message.category !== 'string') {
      logger.warn('websocket', 'Missing or invalid message.category', { message });
      return;
    }

    if (!message.type || typeof message.type !== 'string') {
      logger.warn('websocket', 'Missing or invalid message.type', { message });
      return;
    }

    // Detect pings
    if (message.category === 'system' && message.type === 'ping') {
      this.lastPingTime = Date.now();
      return;
    }

    // Cache full state from both initial_state and state_changed events
    if (message.category === 'system' &&
        (message.type === 'initial_state' || message.type === 'state_changed') &&
        message.data?.full_state) {
      this.lastSystemState = message.data.full_state;
    }

    const eventKey = `${message.category}.${message.type}`;
    const handlers = this.eventHandlers.get(eventKey);

    // Log received events (except frequent ones like levels)
    if (message.type !== 'levels' && message.type !== 'ping') {
      logger.ws('received', eventKey, message.data);
    }

    if (handlers) {
      handlers.forEach(callback => {
        try {
          callback(message);
        } catch (error) {
          logger.error('websocket', `Callback error for ${eventKey}`, { error: error.message });
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

  function onVisibilityChange(callback) {
    const cleanup = wsInstance.onVisibilityChange(callback);
    cleanupFunctions.push(cleanup);
    return cleanup;
  }

  return {
    isConnected: computed(() => wsInstance.isConnected.value),
    on,
    onReconnect,
    onVisibilityChange
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