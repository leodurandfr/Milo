// frontend/tests/setup.js
/**
 * Vitest global setup
 * This file runs before each test file
 */
import { vi } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { logger } from '@/services/logger';

// Stores log liberally at debug level under import.meta.env.DEV; that noise would
// bury the test report. Failures still surface through assertions, not logs.
logger.setLevel('none');

// Create a fresh Pinia instance for each test
beforeEach(() => {
  setActivePinia(createPinia());
});

// Mock localStorage
const localStorageMock = {
  getItem: vi.fn(),
  setItem: vi.fn(),
  removeItem: vi.fn(),
  clear: vi.fn()
};
global.localStorage = localStorageMock;

// Mock matchMedia (for responsive components)
Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn().mockImplementation(query => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn()
  }))
});

// Mock WebSocket
global.WebSocket = vi.fn().mockImplementation(() => ({
  send: vi.fn(),
  close: vi.fn(),
  readyState: 1, // OPEN
  addEventListener: vi.fn(),
  removeEventListener: vi.fn()
}));

// Reset mocks after each test
afterEach(() => {
  vi.clearAllMocks();
  localStorageMock.getItem.mockReset();
  localStorageMock.setItem.mockReset();
});
