// frontend/src/services/logger.js
/**
 * Centralized logging service for Milo frontend
 *
 * Features:
 * - Categorized logs (store, api, websocket, component)
 * - Level-based filtering (debug, info, warn, error)
 * - Environment-aware (verbose in dev, minimal in prod)
 * - Consistent formatting with timestamps
 * - Performance tracking for API calls
 *
 * Usage:
 *   import { logger } from '@/services/logger';
 *   logger.info('store', 'State updated', { source: 'spotify' });
 *   logger.api('GET', '/api/status', { duration: 45 });
 *   logger.ws('received', 'source.state_changed', data);
 */

const LOG_LEVELS = {
  debug: 0,
  info: 1,
  warn: 2,
  error: 3,
  none: 4
};

// WS event types too chatty to log (high-frequency, low signal)
const MUTED_WS_EVENTS = new Set([
  'settings.fan_status_changed'
]);

// Category colors for visual distinction in console
const CATEGORY_STYLES = {
  store: 'color: #42b883; font-weight: bold',     // Vue green
  api: 'color: #3b82f6; font-weight: bold',       // Blue
  websocket: 'color: #f59e0b; font-weight: bold', // Amber
  component: 'color: #8b5cf6; font-weight: bold', // Purple
  system: 'color: #6b7280; font-weight: bold',    // Gray
  default: 'color: #374151; font-weight: bold'
};

class Logger {
  constructor() {
    // Default level based on environment
    this.level = import.meta.env.DEV ? LOG_LEVELS.debug : LOG_LEVELS.warn;
    this.enabled = true;
    this.categories = new Set(); // Empty = all categories enabled

    // Performance tracking for API calls
    this.apiTimings = new Map();

    // Expose for runtime configuration
    if (import.meta.env.DEV) {
      window.miloLogger = this;
    }
  }

  /**
   * Set minimum log level
   * @param {'debug'|'info'|'warn'|'error'|'none'} level
   */
  setLevel(level) {
    this.level = LOG_LEVELS[level] ?? LOG_LEVELS.warn;
  }

  /**
   * Enable/disable specific categories
   * @param {string[]} categories - Empty array enables all
   */
  setCategories(categories) {
    this.categories = new Set(categories);
  }

  /**
   * Toggle logging on/off
   */
  toggle(enabled) {
    this.enabled = enabled;
  }

  /**
   * Check if should log based on level and category
   */
  shouldLog(level, category) {
    if (!this.enabled) return false;
    if (LOG_LEVELS[level] < this.level) return false;
    if (this.categories.size > 0 && !this.categories.has(category)) return false;
    return true;
  }

  /**
   * Format timestamp for logs, as HH:MM:SS.mmm.
   *
   * Built by hand rather than through toLocaleTimeString: this used to pass
   * 'fr-FR', which is a locale chosen for a log line nobody reads in French —
   * and any locale here means the timestamp format changes with who is
   * looking. A log is compared against a journal, so it takes the one shape
   * that is the same everywhere.
   */
  getTimestamp() {
    const now = new Date();
    const pad = (value, width = 2) => String(value).padStart(width, '0');
    return `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}.${pad(now.getMilliseconds(), 3)}`;
  }

  /**
   * Core logging method
   */
  log(level, category, message, data = null) {
    if (!this.shouldLog(level, category)) return;

    const timestamp = this.getTimestamp();
    const style = CATEGORY_STYLES[category] || CATEGORY_STYLES.default;
    const prefix = `[${timestamp}] %c${category.toUpperCase()}`;

    const consoleMethod = level === 'error' ? 'error' :
                          level === 'warn' ? 'warn' :
                          level === 'debug' ? 'debug' : 'log';

    if (data !== null && data !== undefined) {
      console[consoleMethod](prefix, style, message, data);
    } else {
      console[consoleMethod](prefix, style, message);
    }
  }

  // Convenience methods for each level
  debug(category, message, data) {
    this.log('debug', category, message, data);
  }

  info(category, message, data) {
    this.log('info', category, message, data);
  }

  warn(category, message, data) {
    this.log('warn', category, message, data);
  }

  error(category, message, data) {
    this.log('error', category, message, data);
  }

  // === SPECIALIZED LOGGING METHODS ===

  /**
   * Log API calls with timing
   * @param {string} method - HTTP method
   * @param {string} url - API endpoint
   * @param {object} options - { duration?, status?, error?, data? }
   */
  api(method, url, options = {}) {
    const { duration, status, error, data } = options;

    if (error) {
      this.log('error', 'api', `${method} ${url} failed`, { error, duration });
    } else if (duration !== undefined) {
      const level = duration > 1000 ? 'warn' : 'debug';
      this.log(level, 'api', `${method} ${url} (${duration}ms)`, status ? { status, data } : data);
    } else {
      this.log('debug', 'api', `${method} ${url}`, data);
    }
  }

  /**
   * Start timing an API call
   * @returns {string} Timing ID
   */
  apiStart(method, url) {
    const id = `${method}-${url}-${Date.now()}`;
    this.apiTimings.set(id, { method, url, start: performance.now() });
    return id;
  }

  /**
   * End timing an API call
   */
  apiEnd(id, options = {}) {
    const timing = this.apiTimings.get(id);
    if (!timing) return;

    const duration = Math.round(performance.now() - timing.start);
    this.apiTimings.delete(id);
    this.api(timing.method, timing.url, { ...options, duration });
  }

  /**
   * Log WebSocket events
   * @param {'sent'|'received'|'error'} direction
   * @param {string} eventType - e.g., 'source.state_changed'
   * @param {object} data
   */
  ws(direction, eventType, data = null) {
    if (MUTED_WS_EVENTS.has(eventType)) return;
    const icon = direction === 'sent' ? '→' :
                 direction === 'received' ? '←' : '✕';
    const level = direction === 'error' ? 'error' : 'debug';
    this.log(level, 'websocket', `${icon} ${eventType}`, data);
  }

  /**
   * Log store state changes
   * @param {string} storeName
   * @param {string} action
   * @param {object} data
   */
  store(storeName, action, data = null) {
    this.log('debug', 'store', `[${storeName}] ${action}`, data);
  }

  /**
   * Log component lifecycle or events
   * @param {string} componentName
   * @param {string} event
   * @param {object} data
   */
  component(componentName, event, data = null) {
    this.log('debug', 'component', `[${componentName}] ${event}`, data);
  }

  /**
   * Group related logs together
   */
  group(label) {
    if (this.enabled && import.meta.env.DEV) {
      console.group(label);
    }
  }

  groupEnd() {
    if (this.enabled && import.meta.env.DEV) {
      console.groupEnd();
    }
  }
}

// Singleton instance
export const logger = new Logger();
