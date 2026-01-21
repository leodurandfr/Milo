// frontend/tests/stores/clientRegistryStore.test.js
/**
 * Unit tests for clientRegistryStore - multiroom event handling (Story 6.2)
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useClientRegistryStore } from '@/stores/clientRegistryStore';
import axios from 'axios';

// Mock axios
vi.mock('axios');

describe('clientRegistryStore', () => {
  let store;

  beforeEach(() => {
    // Fresh store for each test (Pinia reset in setup.js)
    store = useClientRegistryStore();
    vi.clearAllMocks();
  });

  describe('handleMultiroomEvent - client_state_changed', () => {
    it('should add a new client when client_state_changed event is received', () => {
      const event = {
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'Salon',
            ip: '192.168.1.100',
            zone_id: null,
            speaker_type: 'bookshelf',
            crossover_frequency: 80,
            online: true,
            volume_db: -30.0, // Runtime field - should be stripped
            mute: false // Runtime field - should be stripped
          }
        }
      };

      store.handleMultiroomEvent(event);

      expect(store.clients.size).toBe(1);
      const client = store.clients.get('dc:a6:32:7e:d3:43');
      expect(client).toBeDefined();
      expect(client.name).toBe('Salon');
      expect(client.online).toBe(true);
      expect(client.speaker_type).toBe('bookshelf');
      // Runtime fields should be stripped
      expect(client.volume_db).toBeUndefined();
      expect(client.mute).toBeUndefined();
    });

    it('should update an existing client when client_state_changed event is received', () => {
      // Pre-populate a client
      store.clients.set('dc:a6:32:7e:d3:43', {
        mac_id: 'dc:a6:32:7e:d3:43',
        name: 'Old Name',
        online: true,
        speaker_type: 'satellite'
      });

      const event = {
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'New Name',
            ip: '192.168.1.100',
            online: false,
            speaker_type: 'bookshelf'
          }
        }
      };

      store.handleMultiroomEvent(event);

      const client = store.clients.get('dc:a6:32:7e:d3:43');
      expect(client.name).toBe('New Name');
      expect(client.online).toBe(false);
      expect(client.speaker_type).toBe('bookshelf');
    });

    it('should ignore client_state_changed event with missing data', () => {
      const event = {
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43'
          // No client object
        }
      };

      store.handleMultiroomEvent(event);

      expect(store.clients.size).toBe(0);
    });
  });

  describe('handleMultiroomEvent - zone_changed', () => {
    it('should add a new zone when zone_changed event is received with zone data', () => {
      const event = {
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-123',
          zone: {
            id: 'uuid-zone-123',
            name: 'Living Room',
            client_ids: ['dc:a6:32:7e:d3:43', 'aa:bb:cc:dd:ee:ff'],
            online_client_count: 2,
            has_subwoofer: false,
            crossover_enabled: false
          }
        }
      };

      store.handleMultiroomEvent(event);

      expect(store.zones.size).toBe(1);
      const zone = store.zones.get('uuid-zone-123');
      expect(zone).toBeDefined();
      expect(zone.name).toBe('Living Room');
      expect(zone.client_ids).toHaveLength(2);
      expect(zone.online_client_count).toBe(2);
    });

    it('should update an existing zone when zone_changed event is received', () => {
      // Pre-populate a zone
      store.zones.set('uuid-zone-123', {
        id: 'uuid-zone-123',
        name: 'Old Zone',
        client_ids: ['client1'],
        online_client_count: 1
      });

      const event = {
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-123',
          zone: {
            id: 'uuid-zone-123',
            name: 'Updated Zone',
            client_ids: ['client1', 'client2', 'client3'],
            online_client_count: 3,
            has_subwoofer: true,
            crossover_enabled: true
          }
        }
      };

      store.handleMultiroomEvent(event);

      const zone = store.zones.get('uuid-zone-123');
      expect(zone.name).toBe('Updated Zone');
      expect(zone.client_ids).toHaveLength(3);
      expect(zone.has_subwoofer).toBe(true);
    });

    it('should delete a zone when zone_changed event has null zone', () => {
      // Pre-populate a zone
      store.zones.set('uuid-zone-123', {
        id: 'uuid-zone-123',
        name: 'Zone to Delete',
        client_ids: ['client1']
      });

      expect(store.zones.size).toBe(1);

      const event = {
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-123',
          zone: null // null indicates zone deletion
        }
      };

      store.handleMultiroomEvent(event);

      expect(store.zones.size).toBe(0);
      expect(store.zones.get('uuid-zone-123')).toBeUndefined();
    });

    it('should ignore zone_changed event with missing zone_id', () => {
      const event = {
        type: 'zone_changed',
        data: {
          // No zone_id
          zone: { name: 'Orphan Zone' }
        }
      };

      store.handleMultiroomEvent(event);

      expect(store.zones.size).toBe(0);
    });
  });

  describe('handleMultiroomEvent - unknown event types', () => {
    it('should ignore unknown event types silently', () => {
      const event = {
        type: 'unknown_event_type',
        data: { some: 'data' }
      };

      // Should not throw
      expect(() => store.handleMultiroomEvent(event)).not.toThrow();
      expect(store.clients.size).toBe(0);
      expect(store.zones.size).toBe(0);
    });

    it('should ignore dsp_changed and crossover_changed (handled by dspStore)', () => {
      const dspEvent = {
        type: 'dsp_changed',
        data: { target_type: 'zone', target_id: '123', dsp_settings: {} }
      };
      const crossoverEvent = {
        type: 'crossover_changed',
        data: { zone_id: '123', crossover_enabled: true }
      };

      store.handleMultiroomEvent(dspEvent);
      store.handleMultiroomEvent(crossoverEvent);

      // Neither should affect clientRegistryStore state
      expect(store.clients.size).toBe(0);
      expect(store.zones.size).toBe(0);
    });
  });

  describe('handleRegistryEvent (deprecated)', () => {
    it('should still work for backward compatibility', () => {
      const event = {
        type: 'client_connected',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'Legacy Client',
            online: true
          }
        }
      };

      // The deprecated handler should still work
      store.handleRegistryEvent(event);

      expect(store.clients.size).toBe(1);
      expect(store.clients.get('dc:a6:32:7e:d3:43').name).toBe('Legacy Client');
    });
  });

  // =============================================================================
  // Story 6.2: Reconnection State Sync Integration Test (Task 8.3)
  // =============================================================================

  describe('fetchState - reconnection state sync', () => {
    it('should fetch full registry state from API on reconnect', async () => {
      // Pre-populate with stale data
      store.clients.set('dc:a6:32:7e:d3:43', {
        mac_id: 'dc:a6:32:7e:d3:43',
        name: 'Stale Client',
        online: false
      });

      // Mock API response with fresh data (clients/zones are objects keyed by id)
      axios.get.mockResolvedValueOnce({
        data: {
          clients: {
            'dc:a6:32:7e:d3:43': {
              mac_id: 'dc:a6:32:7e:d3:43',
              name: 'Fresh Client',
              ip: '192.168.1.100',
              online: true,
              speaker_type: 'bookshelf'
            },
            'aa:bb:cc:dd:ee:ff': {
              mac_id: 'aa:bb:cc:dd:ee:ff',
              name: 'New Client',
              ip: '192.168.1.101',
              online: true,
              speaker_type: 'satellite'
            }
          },
          zones: {
            'zone-123': {
              id: 'zone-123',
              name: 'Living Room',
              client_ids: ['dc:a6:32:7e:d3:43', 'aa:bb:cc:dd:ee:ff']
            }
          }
        }
      });

      // Simulate reconnection by calling fetchState
      await store.fetchState();

      // Verify API was called
      expect(axios.get).toHaveBeenCalledWith('/api/multiroom/state');

      // Verify state was fully refreshed
      expect(store.clients.size).toBe(2);
      expect(store.clients.get('dc:a6:32:7e:d3:43').name).toBe('Fresh Client');
      expect(store.clients.get('dc:a6:32:7e:d3:43').online).toBe(true);
      expect(store.clients.get('aa:bb:cc:dd:ee:ff')).toBeDefined();

      // Verify zones were refreshed
      expect(store.zones.size).toBe(1);
      expect(store.zones.get('zone-123').name).toBe('Living Room');
    });

    it('should handle API error during reconnection gracefully', async () => {
      // Pre-populate with data
      store.clients.set('dc:a6:32:7e:d3:43', {
        mac_id: 'dc:a6:32:7e:d3:43',
        name: 'Existing Client',
        online: true
      });

      // Mock API error (server unavailable during reconnect)
      axios.get.mockRejectedValueOnce(new Error('Network error'));

      // Should not throw
      await expect(store.fetchState()).resolves.not.toThrow();

      // Existing state should be preserved (not cleared on error)
      expect(store.clients.size).toBe(1);
      expect(store.clients.get('dc:a6:32:7e:d3:43').name).toBe('Existing Client');
    });

    it('should clear removed clients/zones during reconnection sync', async () => {
      // Pre-populate with multiple clients
      store.clients.set('dc:a6:32:7e:d3:43', { mac_id: 'dc:a6:32:7e:d3:43', name: 'Client 1', online: true });
      store.clients.set('aa:bb:cc:dd:ee:ff', { mac_id: 'aa:bb:cc:dd:ee:ff', name: 'Client 2', online: true });
      store.zones.set('zone-old', { id: 'zone-old', name: 'Old Zone', client_ids: ['dc:a6:32:7e:d3:43'] });

      // Mock API response with only one client (second was removed) - objects keyed by id
      axios.get.mockResolvedValueOnce({
        data: {
          clients: {
            'dc:a6:32:7e:d3:43': { mac_id: 'dc:a6:32:7e:d3:43', name: 'Client 1', online: true }
          },
          zones: {}
        }
      });

      await store.fetchState();

      // Removed client should be gone
      expect(store.clients.size).toBe(1);
      expect(store.clients.has('aa:bb:cc:dd:ee:ff')).toBe(false);

      // Removed zone should be gone
      expect(store.zones.size).toBe(0);
    });
  });
});
