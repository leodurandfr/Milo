// frontend/src/services/websocket.js
import { ref, computed, onMounted, onUnmounted } from 'vue';
import { apiCall } from '@/services/apiCall';
import { logger } from '@/services/logger';

/**
 * WebSocket singleton with smart disconnect when the tab is hidden.
 *
 * Wire format: { category, type, origin, data, timestamp } (built by
 * WsEvent.to_envelope() — backend/core/models/ws_events.py, one typed class
 * per (category, type) pair). Handlers are registered
 * centrally in App.vue via on()/parsedOn() and dispatch into Pinia stores —
 * components react to store state, never to raw events. parsedOn() validates
 * payloads against the Zod registry in @/schemas/ws.js before dispatching.
 *
 * Categories currently emitted by the backend:
 *   system    → unifiedAudioStore / systemStore (initial_state, state_changed,
 *               transition_*, hostname_conflict_changed, connectivity_changed;
 *               ping is consumed internally as the keepalive)
 *   source    → unifiedAudioStore + per-source stores (state_changed,
 *               position_update, favorite_* with data.source discriminator)
 *   volume    → unifiedAudioStore (volume_changed)
 *   routing   → multiroomStore (multiroom_* transition events)
 *   multiroom → multiroomStore / equalizerStore (client_state_changed,
 *               zone_changed, equalizer_changed, crossover_changed)
 *   equalizer → equalizerStore (state_changed, filter_changed,
 *               compressor_changed, loudness_changed, mono_changed,
 *               enabled_changed, zone_enabled_changed)
 *   settings  → settingsStore / fanStore (settings.*_changed)
 *   programs  → update/install progress (UpdateManager)
 *   network   → networkStore (status_changed)
 */
class WebSocketSingleton {
  constructor() {
    this.socket = null;
    this.isConnected = ref(false);
    this.hasEverConnected = false;
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
    this.pingStaleMs = 90000; // 3x the backend keepalive interval (30s)
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
    const socket = new WebSocket(wsUrl);
    this.socket = socket;

    socket.onopen = () => {
      if (this.socket !== socket) return;

      // isConnected starts false, so it can't distinguish the first connection
      // of a page load from a real reconnection — hasEverConnected can
      const wasReconnecting = this.hasEverConnected;
      this.hasEverConnected = true;
      this.isConnected.value = true;
      this.lastPingTime = Date.now();
      this.reconnectAttempts = 0; // Reset backoff counter on successful connection
      this.setupVisibilityListener();
      this.startPingCheck();

      // Send ready signal to request initial state
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: "ready" }));
      }

      if (wasReconnecting) {
        logger.info('websocket', 'Reconnected - state sync requested');
        // Notify subscribers that a reconnection occurred
        this.notifyReconnect();
      } else {
        logger.info('websocket', 'Connected - initial state requested');
      }
    };

    socket.onmessage = (event) => {
      try {
        const message = JSON.parse(event.data);
        this.handleMessage(message);
      } catch (error) {
        logger.error('websocket', 'Message parse error', { error: error.message });
      }
    };

    socket.onclose = () => {
      if (this.socket && this.socket !== socket) return;

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

    socket.onerror = (error) => {
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
        if (this.subscribers.size === 0) return;

        // readyState can still report OPEN on a dead connection after system
        // sleep or tab suspension; trust the keepalive age instead and force
        // a clean reconnect (onclose reschedules, onopen re-requests state)
        if (this.socket?.readyState === WebSocket.OPEN &&
            Date.now() - this.lastPingTime > this.pingStaleMs) {
          logger.warn('websocket', 'Tab visible - stale connection, forcing reconnect');
          this.closeConnection();
          return;
        }

        if (this.socket?.readyState === WebSocket.OPEN) {
          // Socket is open and alive - fetch fresh state via HTTP
          logger.debug('websocket', 'Tab visible - fetching fresh state');
          const [audioRes, volumeRes] = await Promise.all([
            apiCall.get('/api/audio/state', {
              category: 'websocket',
              message: 'Failed to fetch audio state on visibility change',
              logLevel: 'warn',
            }),
            apiCall.get('/api/volume/state', {
              category: 'websocket',
              message: 'Failed to fetch volume state on visibility change',
              logLevel: 'warn',
            }),
          ]);

          if (audioRes.ok) {
            this.handleMessage({
              category: 'system',
              type: 'state_changed',
              source: 'system',
              data: { full_state: audioRes.data },
            });
          }

          if (volumeRes.ok && volumeRes.data.status === 'success') {
            this.handleMessage({
              category: 'volume',
              type: 'volume_changed',
              source: 'volume',
              data: { show_bar: false, state: volumeRes.data.data },
            });
          }
          this.notifyVisibilityChange();
        } else {
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
    if (this.pingCheckInterval) {
      clearInterval(this.pingCheckInterval);
    }

    this.pingCheckInterval = setInterval(() => {
      const timeSinceLastPing = Date.now() - this.lastPingTime;

      // If the keepalive went stale, reconnect
      if (timeSinceLastPing > this.pingStaleMs && !document.hidden) {
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

    // Keepalive ping: refresh liveness timestamp and skip handler dispatch
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

    // Log received events (except the keepalive ping)
    if (message.type !== 'ping') {
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

  /**
   * Subscribe with Zod schema validation on `event.data`.
   *
   * On success, the callback receives `(payload, event)` where `payload` is
   * the validated (and possibly coerced) `event.data`. On validation failure,
   * a warning is logged and the callback is STILL invoked with the raw
   * `event.data` (tolerant fallback) — the runtime must never break on
   * payload drift; the warning surfaces the bug to the dev cycle.
   */
  parsedOn(category, type, schema, callback) {
    const eventKey = `${category}.${type}`;
    const wrapped = (event) => {
      const result = schema.safeParse(event.data);
      if (result.success) {
        callback(result.data, event);
      } else {
        logger.warn('websocket', `Schema validation failed for ${eventKey}`, {
          issues: result.error.issues,
        });
        callback(event.data, event);
      }
    };
    return this.on(category, type, wrapped);
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

  function parsedOn(category, type, schema, callback) {
    const cleanup = wsInstance.parsedOn(category, type, schema, callback);
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
    parsedOn,
    onReconnect,
    onVisibilityChange
  };
}

// Debug for development
if (import.meta.env.DEV) {
  window.wsDebug = () => {
    logger.debug('websocket', 'WebSocket Debug', {
      subscribers: wsInstance.subscribers.size,
      connected: wsInstance.isConnected.value,
      eventTypes: Array.from(wsInstance.eventHandlers.keys()),
      hasCachedState: !!wsInstance.lastSystemState,
      url: wsInstance.socket?.url,
      tabHidden: document.hidden
    });
  };
}