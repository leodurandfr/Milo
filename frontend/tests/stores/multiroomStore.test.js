// frontend/tests/stores/multiroomStore.test.js
/**
 * Integration tests for multiroomStore - Real-time sync via WebSocket (Story 6.3)
 *
 * Tests verify the reactive chain:
 * WebSocket Event → clientRegistryStore.handleMultiroomEvent() → clients Map → multiroomStore.clients computed
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useMultiroomStore } from '@/stores/multiroomStore';
import { useClientRegistryStore } from '@/stores/clientRegistryStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

// Mock axios
vi.mock('axios');

// Create base mock for unifiedAudioStore
const createUnifiedAudioStoreMock = (volumeClients = {}) => ({
  volumeState: {
    clients: volumeClients
  },
  systemState: {
    multiroom_enabled: true
  }
});

// Mock unifiedAudioStore
vi.mock('@/stores/unifiedAudioStore', () => ({
  useUnifiedAudioStore: vi.fn(() => createUnifiedAudioStoreMock({
    'dc:a6:32:7e:d3:43': { volume_db: -30, mute: false },
    'aa:bb:cc:dd:ee:ff': { volume_db: -25, mute: true },
    'local': { volume_db: -20, mute: false }
  }))
}));

describe('multiroomStore - Real-Time Sync (Story 6.3)', () => {
  let multiroomStore;
  let clientRegistryStore;

  beforeEach(() => {
    setActivePinia(createPinia());
    multiroomStore = useMultiroomStore();
    clientRegistryStore = useClientRegistryStore();
    vi.clearAllMocks();
  });

  // ===========================================================================
  // AC1: Client State Changed Event Handling
  // ===========================================================================

  describe('AC1: client_state_changed → multiroomStore.clients reflects new state', () => {
    it('should update multiroomStore.clients when client_state_changed event adds a new client', () => {
      // Simulate WebSocket event via handleMultiroomEvent
      const event = {
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            snapcast_id: 'dc:a6:32:7e:d3:43',
            name: 'Living Room',
            host: 'milo-client-01',
            ip: '192.168.1.100',
            zone_id: null,
            speaker_type: 'bookshelf',
            online: true
          }
        }
      };

      clientRegistryStore.handleMultiroomEvent(event);

      // multiroomStore.clients should reflect the new client
      const clients = multiroomStore.clients;
      expect(clients.length).toBe(1);
      expect(clients[0].name).toBe('Living Room');
      expect(clients[0].mac_id).toBe('dc:a6:32:7e:d3:43');
      expect(clients[0].online).toBe(true);
    });

    it('should update multiroomStore.clients when client goes offline', () => {
      // Add initial client
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            snapcast_id: 'dc:a6:32:7e:d3:43',
            name: 'Bedroom',
            online: true
          }
        }
      });

      expect(multiroomStore.clients[0].online).toBe(true);

      // Client goes offline
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            snapcast_id: 'dc:a6:32:7e:d3:43',
            name: 'Bedroom',
            online: false
          }
        }
      });

      // multiroomStore.clients should reflect offline status
      expect(multiroomStore.clients[0].online).toBe(false);
    });

    it('should update multiroomStore.clients when client name changes', () => {
      // Add initial client
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            snapcast_id: 'dc:a6:32:7e:d3:43',
            name: 'Old Name',
            speaker_type: 'satellite',
            online: true
          }
        }
      });

      expect(multiroomStore.clients[0].name).toBe('Old Name');

      // Name changes
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            snapcast_id: 'dc:a6:32:7e:d3:43',
            name: 'New Name',
            speaker_type: 'bookshelf',
            online: true
          }
        }
      });

      // multiroomStore.clients should reflect the new name
      expect(multiroomStore.clients[0].name).toBe('New Name');
    });
  });

  // ===========================================================================
  // AC2: Zone Changed Event Handling
  // ===========================================================================

  describe('AC2: zone_changed (create) → zones list updated', () => {
    it('should add zone to clientRegistryStore.zoneList when zone_changed event creates zone', () => {
      // Simulate zone creation via WebSocket
      const event = {
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-living',
          zone: {
            id: 'uuid-zone-living',
            name: 'Living Room',
            client_ids: ['dc:a6:32:7e:d3:43', 'aa:bb:cc:dd:ee:ff'],
            online_client_count: 2,
            has_subwoofer: false,
            crossover_enabled: false
          }
        }
      };

      clientRegistryStore.handleMultiroomEvent(event);

      // clientRegistryStore.zoneList should have the new zone
      const zones = clientRegistryStore.zoneList;
      expect(zones.length).toBe(1);
      expect(zones[0].name).toBe('Living Room');
      expect(zones[0].client_ids).toHaveLength(2);
    });

    it('should update zone when zone_changed event modifies zone', () => {
      // Create initial zone
      clientRegistryStore.handleMultiroomEvent({
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-1',
          zone: {
            id: 'uuid-zone-1',
            name: 'Bedroom',
            client_ids: ['client1']
          }
        }
      });

      expect(clientRegistryStore.zoneList[0].client_ids).toHaveLength(1);

      // Zone gets more members
      clientRegistryStore.handleMultiroomEvent({
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-1',
          zone: {
            id: 'uuid-zone-1',
            name: 'Bedroom Extended',
            client_ids: ['client1', 'client2', 'client3']
          }
        }
      });

      // Zone should be updated
      expect(clientRegistryStore.zoneList[0].name).toBe('Bedroom Extended');
      expect(clientRegistryStore.zoneList[0].client_ids).toHaveLength(3);
    });
  });

  describe('AC2: zone_changed (delete) → zone removed from list', () => {
    it('should remove zone when zone_changed event has null zone', () => {
      // Create zone first
      clientRegistryStore.handleMultiroomEvent({
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-delete',
          zone: {
            id: 'uuid-zone-delete',
            name: 'Zone to Delete',
            client_ids: ['client1']
          }
        }
      });

      expect(clientRegistryStore.zoneList.length).toBe(1);

      // Delete zone (zone: null)
      clientRegistryStore.handleMultiroomEvent({
        type: 'zone_changed',
        data: {
          zone_id: 'uuid-zone-delete',
          zone: null
        }
      });

      // Zone should be removed
      expect(clientRegistryStore.zoneList.length).toBe(0);
    });
  });

  // ===========================================================================
  // AC4: No Polling Requirement (FR30)
  // ===========================================================================

  describe('AC4: No polling patterns exist', () => {
    it('should derive clients reactively without polling mechanisms', () => {
      // Verify store uses reactive derivation, not polling
      // The store relies on WebSocket events and Vue computed, not setInterval

      // Verify clients is defined and reactive
      expect(multiroomStore.clients).toBeDefined();
      expect(Array.isArray(multiroomStore.clients)).toBe(true);

      // Verify isLoading derives from clientRegistryStore (not internal polling state)
      expect(typeof multiroomStore.isLoading).toBe('boolean');

      // Verify no fetchClients or refresh methods that would indicate polling
      // The store should NOT have auto-refresh methods
      expect(multiroomStore.fetchClients).toBeUndefined();
      expect(multiroomStore.startPolling).toBeUndefined();
      expect(multiroomStore.refreshInterval).toBeUndefined();
    });

    it('should use Vue computed (not ref with polling) for clients', () => {
      // multiroomStore.clients should be a computed that derives from clientRegistryStore
      // Adding a client via event should automatically update multiroomStore.clients

      // Initial state
      expect(multiroomStore.clients.length).toBe(0);

      // Add client via event (simulating WebSocket)
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'test-client',
          client: { mac_id: 'test-client', snapcast_id: 'test-client', name: 'Test', online: true }
        }
      });

      // Should automatically reflect in multiroomStore.clients without any polling
      expect(multiroomStore.clients.length).toBe(1);
    });
  });

  // ===========================================================================
  // AC5: Reactive Chain Verification
  // ===========================================================================

  describe('AC5: Reactive chain - WebSocket → clientRegistryStore → multiroomStore', () => {
    it('should propagate multiple rapid events without data loss', async () => {
      // Simulate multiple rapid WebSocket events
      const events = [
        { type: 'client_state_changed', data: { mac_id: 'client1', client: { mac_id: 'client1', snapcast_id: 'client1', name: 'Client 1', online: true } } },
        { type: 'client_state_changed', data: { mac_id: 'client2', client: { mac_id: 'client2', snapcast_id: 'client2', name: 'Client 2', online: true } } },
        { type: 'client_state_changed', data: { mac_id: 'client3', client: { mac_id: 'client3', snapcast_id: 'client3', name: 'Client 3', online: true } } },
        { type: 'client_state_changed', data: { mac_id: 'client1', client: { mac_id: 'client1', snapcast_id: 'client1', name: 'Client 1 Updated', online: false } } },
        { type: 'zone_changed', data: { zone_id: 'zone1', zone: { id: 'zone1', name: 'Zone 1', client_ids: ['client1', 'client2'] } } }
      ];

      // Process all events rapidly
      events.forEach(event => {
        clientRegistryStore.handleMultiroomEvent(event);
      });

      // All data should be present without loss
      expect(multiroomStore.clients.length).toBe(3);
      expect(clientRegistryStore.zoneList.length).toBe(1);

      // Verify final states
      const client1 = multiroomStore.clients.find(c => c.mac_id === 'client1');
      expect(client1.name).toBe('Client 1 Updated');
      expect(client1.online).toBe(false);
    });

    it('should maintain consistency between clientRegistryStore.clientList and multiroomStore.clients', () => {
      // Add clients
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: { mac_id: 'aa:bb:cc:dd:ee:ff', client: { mac_id: 'aa:bb:cc:dd:ee:ff', snapcast_id: 'local', name: 'Milo', online: true, is_local: true } }
      });
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: { mac_id: 'remote1', client: { mac_id: 'remote1', snapcast_id: 'remote1', name: 'Remote', online: true, is_local: false } }
      });

      // Both stores should have consistent data
      expect(clientRegistryStore.clientList.length).toBe(2);
      expect(multiroomStore.clients.length).toBe(2);

      // Verify data is derived (not duplicated)
      const registryClient = clientRegistryStore.clientList.find(c => c.is_local);
      const multiroomClient = multiroomStore.clients.find(c => c.is_local);

      expect(registryClient.name).toBe('Milo');
      expect(multiroomClient.name).toBe('Milo');
    });

    it('should update computed properties when Map is modified via set()', () => {
      // This tests Vue 3 reactivity with Map.set()
      const initialCount = multiroomStore.clients.length;
      expect(initialCount).toBe(0);

      // Direct Map modification via handleMultiroomEvent
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'reactive-test',
          client: {
            mac_id: 'reactive-test',
            snapcast_id: 'reactive-test',
            name: 'Reactive Test',
            online: true
          }
        }
      });

      // Computed should recalculate
      expect(multiroomStore.clients.length).toBe(1);
    });

    it('should update computed properties when Map is modified via delete()', () => {
      // Add a zone
      clientRegistryStore.handleMultiroomEvent({
        type: 'zone_changed',
        data: {
          zone_id: 'zone-to-delete',
          zone: { id: 'zone-to-delete', name: 'Test Zone', client_ids: ['c1'] }
        }
      });

      expect(clientRegistryStore.zoneList.length).toBe(1);

      // Delete the zone
      clientRegistryStore.handleMultiroomEvent({
        type: 'zone_changed',
        data: {
          zone_id: 'zone-to-delete',
          zone: null
        }
      });

      // Computed should recalculate
      expect(clientRegistryStore.zoneList.length).toBe(0);
    });
  });

  // ===========================================================================
  // AC3: Crossover Changed Event Handling
  // Note: crossover_changed events are handled by dspStore, not multiroomStore.
  // See dspStore.test.js "handleZoneCrossoverChanged" tests for AC3 coverage.
  // App.vue wiring: on('multiroom', 'crossover_changed', (event) => dspStore.handleZoneCrossoverChanged(event))
  // ===========================================================================

  // ===========================================================================
  // Integration: Volume data from unifiedAudioStore
  // ===========================================================================

  describe('Integration: Volume data derives from unifiedAudioStore', () => {
    it('should include volume in derived client objects', () => {
      // Add client that has volume data in unifiedAudioStore mock
      clientRegistryStore.handleMultiroomEvent({
        type: 'client_state_changed',
        data: {
          mac_id: 'dc:a6:32:7e:d3:43',
          client: {
            mac_id: 'dc:a6:32:7e:d3:43',
            snapcast_id: 'dc:a6:32:7e:d3:43',
            name: 'Client with Volume',
            online: true
          }
        }
      });

      const client = multiroomStore.clients[0];

      // Volume should be derived from unifiedAudioStore.volumeState.clients
      // The mock returns -30 dB for this mac_id, which converts to percentage
      // dbToPercent(-30) = ((−30 − (−72)) / (0 − (−72))) * 100 = (42/72) * 100 ≈ 58%
      expect(client.volume).toBeGreaterThan(0);
      expect(client.muted).toBe(false);
    });
  });
});

// =============================================================================
// Isolated unit tests for multiroomStore helpers
// =============================================================================

describe('multiroomStore - Volume Conversion Helpers', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useMultiroomStore();
  });

  describe('dbToPercent', () => {
    it('should convert -72 dB to 0%', () => {
      expect(store.dbToPercent(-72)).toBe(0);
    });

    it('should convert 0 dB to 100%', () => {
      expect(store.dbToPercent(0)).toBe(100);
    });

    it('should convert -36 dB to 50%', () => {
      expect(store.dbToPercent(-36)).toBe(50);
    });

    it('should clamp values below -72 dB to 0%', () => {
      expect(store.dbToPercent(-100)).toBe(0);
    });

    it('should clamp values above 0 dB to 100%', () => {
      expect(store.dbToPercent(10)).toBe(100);
    });
  });

  describe('percentToDb', () => {
    it('should convert 0% to -72 dB', () => {
      expect(store.percentToDb(0)).toBe(-72);
    });

    it('should convert 100% to 0 dB', () => {
      expect(store.percentToDb(100)).toBe(0);
    });

    it('should convert 50% to -36 dB', () => {
      expect(store.percentToDb(50)).toBe(-36);
    });

    it('should clamp values below 0% to -72 dB', () => {
      expect(store.percentToDb(-10)).toBe(-72);
    });

    it('should clamp values above 100% to 0 dB', () => {
      expect(store.percentToDb(150)).toBe(0);
    });
  });
});

describe('multiroomStore - Display Cache', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useMultiroomStore();
    // Clear localStorage before each test
    localStorage.clear();
  });

  describe('saveDisplayCache / preloadDisplayCache', () => {
    it('should save and load display cache for skeleton rendering', () => {
      // Save display items
      store.saveDisplayCache([
        { zoneClients: ['c1', 'c2'] }, // Zone item
        {}, // Client item
        {} // Client item
      ]);

      // Should have saved structure
      expect(store.lastKnownDisplayItems.length).toBe(3);
      expect(store.lastKnownDisplayItems[0].type).toBe('zone');
      expect(store.lastKnownDisplayItems[1].type).toBe('client');

      // Create new store instance to test preload
      setActivePinia(createPinia());
      const newStore = useMultiroomStore();
      newStore.preloadDisplayCache();

      // Should load from cache
      expect(newStore.lastKnownDisplayItems.length).toBe(3);
    });
  });
});
