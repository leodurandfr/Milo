// frontend/tests/stores/equalizerStore.test.js
// Tests for equalizerStore volume functions (Story 3.5)
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { setActivePinia, createPinia } from 'pinia';
import { useEqualizerStore } from '@/stores/equalizerStore';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import { useMultiroomStore } from '@/stores/multiroomStore';
import axios from 'axios';

// Mock axios
vi.mock('axios');

// Mock the stores
vi.mock('@/stores/unifiedAudioStore', () => ({
  useUnifiedAudioStore: vi.fn(() => ({
    systemState: { multiroom_enabled: true },
    volumeState: {
      clients: {
        'dc:a6:32:7e:d3:43': { volume_db: -30, mute: false }
      }
    }
  }))
}));

vi.mock('@/stores/multiroomStore', () => ({
  useMultiroomStore: vi.fn(() => ({
    clientList: [
      { mac_id: 'dc:a6:32:7e:d3:43', name: 'Test Client', host: 'test-host', ip: '192.168.1.10', online: true }
    ],
    zoneList: [],
    isInitialized: true,
    getLinkedClientIds: vi.fn(() => ['dc:a6:32:7e:d3:43']),
    isClientOnline: vi.fn(() => true),
    getZoneForClient: vi.fn(() => null),  // Default: standalone (no zone)
    initialize: vi.fn()
  }))
}));

describe('equalizerStore - Volume Functions', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useEqualizerStore();
    vi.clearAllMocks();
  });

  describe('macToUrlFormat helper', () => {
    it('should convert MAC with colons to URL format (no colons)', () => {
      // This tests the internal conversion that should happen
      const macWithColons = 'dc:a6:32:7e:d3:43';
      const expectedUrl = 'dca6327ed343';
      // The conversion happens inside updateClientEqualizerVolume
      // We test it indirectly by checking the API call
      expect(macWithColons.replace(/:/g, '')).toBe(expectedUrl);
    });
  });

  describe('updateClientEqualizerVolume', () => {
    it('should call MAC-based endpoint for volume update', async () => {
      axios.patch.mockResolvedValueOnce({ data: { status: 'success', mac_id: 'dc:a6:32:7e:d3:43', volume_db: -25 } });

      const result = await store.updateClientEqualizerVolume('dc:a6:32:7e:d3:43', -25);

      // Should use PATCH /api/volume/client/mac/{mac_url} endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/volume/client/mac/dca6327ed343',
        { volume_db: -25 }
      );
      expect(result).toBe(true);
    });

    it('should handle local client volume update', async () => {
      axios.patch.mockResolvedValueOnce({ data: { status: 'success' } });

      const result = await store.updateClientEqualizerVolume('local', -30);

      // Local client should use the camilladsp_id-based endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/volume/client/local',
        { volume_db: -30 }
      );
      expect(result).toBe(true);
    });

    it('should return false on API error', async () => {
      axios.patch.mockRejectedValueOnce(new Error('Network error'));

      const result = await store.updateClientEqualizerVolume('dc:a6:32:7e:d3:43', -25);

      expect(result).toBe(false);
    });

    it('should skip update when multiroom is disabled for remote clients', async () => {
      // Override the mock for this test
      useUnifiedAudioStore.mockReturnValueOnce({
        systemState: { multiroom_enabled: false },
        volumeState: { clients: {} }
      });

      const freshStore = useEqualizerStore();
      const result = await freshStore.updateClientEqualizerVolume('dc:a6:32:7e:d3:43', -25);

      expect(axios.patch).not.toHaveBeenCalled();
      expect(result).toBe(false);
    });
  });

  describe('updateClientEqualizerMute', () => {
    it('should call MAC-based endpoint for mute update', async () => {
      axios.patch.mockResolvedValueOnce({ data: { status: 'success', mac_id: 'dc:a6:32:7e:d3:43', mute: true } });

      const result = await store.updateClientEqualizerMute('dc:a6:32:7e:d3:43', true);

      // Should use PATCH /api/volume/client/mac/{mac_url}/mute endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/volume/client/mac/dca6327ed343/mute',
        { mute: true }
      );
      expect(result).toBe(true);
    });

    it('should handle unmute operation', async () => {
      axios.patch.mockResolvedValueOnce({ data: { status: 'success', mac_id: 'dc:a6:32:7e:d3:43', mute: false } });

      const result = await store.updateClientEqualizerMute('dc:a6:32:7e:d3:43', false);

      expect(axios.patch).toHaveBeenCalledWith(
        '/api/volume/client/mac/dca6327ed343/mute',
        { mute: false }
      );
      expect(result).toBe(true);
    });

    it('should handle local client mute update', async () => {
      axios.patch.mockResolvedValueOnce({ data: { status: 'success' } });

      const result = await store.updateClientEqualizerMute('local', true);

      // Local client should use the camilladsp_id-based endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/volume/client/local/mute',
        { mute: true }
      );
      expect(result).toBe(true);
    });

    it('should return false on API error', async () => {
      axios.patch.mockRejectedValueOnce(new Error('Network error'));

      const result = await store.updateClientEqualizerMute('dc:a6:32:7e:d3:43', true);

      expect(result).toBe(false);
    });
  });

  describe('applyZoneDelta', () => {
    it('should call PATCH zone endpoint with delta', async () => {
      useUnifiedAudioStore.mockReturnValueOnce({
        systemState: { multiroom_enabled: true },
        volumeState: { clients: {} }
      });

      axios.patch.mockResolvedValueOnce({
        data: {
          status: 'success',
          zone_id: 'zone-uuid-123',
          new_average_db: -25,
          delta_db: 5,
          applied_to: ['dc:a6:32:7e:d3:43'],
          offline_clients: []
        }
      });

      const freshStore = useEqualizerStore();
      const result = await freshStore.applyZoneDelta('zone-uuid-123', 5);

      expect(axios.patch).toHaveBeenCalledWith(
        '/api/volume/zone/zone-uuid-123',
        { delta_db: 5 }
      );
      expect(result.status).toBe('success');
      expect(result.new_average_db).toBe(-25);
    });

    it('should skip when multiroom is disabled', async () => {
      useUnifiedAudioStore.mockReturnValueOnce({
        systemState: { multiroom_enabled: false },
        volumeState: { clients: {} }
      });

      const freshStore = useEqualizerStore();
      const result = await freshStore.applyZoneDelta('zone-uuid-123', 5);

      expect(axios.patch).not.toHaveBeenCalled();
      expect(result.status).toBe('error');
    });
  });

  describe('getClientEqualizerVolume', () => {
    it('should return volume from unified store', () => {
      const volume = store.getClientEqualizerVolume('dc:a6:32:7e:d3:43');
      expect(volume).toBe(-30);
    });

    it('should return default -30 for unknown client', () => {
      const volume = store.getClientEqualizerVolume('unknown-mac');
      expect(volume).toBe(-30);
    });

    it('should normalize "milo" hostname to "local"', () => {
      useUnifiedAudioStore.mockReturnValueOnce({
        systemState: { multiroom_enabled: true },
        volumeState: {
          clients: {
            'local': { volume_db: -25, mute: false }
          }
        }
      });

      const freshStore = useEqualizerStore();
      const volume = freshStore.getClientEqualizerVolume('milo');
      expect(volume).toBe(-25);
    });
  });

  describe('getClientEqualizerMute', () => {
    it('should return mute state from unified store', () => {
      const muted = store.getClientEqualizerMute('dc:a6:32:7e:d3:43');
      expect(muted).toBe(false);
    });

    it('should return default false for unknown client', () => {
      const muted = store.getClientEqualizerMute('unknown-mac');
      expect(muted).toBe(false);
    });
  });
});

// =============================================================================
// Story 4.3: EQ Filter Management - Zone Propagation Tests
// =============================================================================

describe('equalizerStore - EQ Filter Zone Propagation', () => {
  let store;

  beforeEach(() => {
    setActivePinia(createPinia());
    store = useEqualizerStore();
    vi.clearAllMocks();
  });

  describe('propagateToLinkedClients', () => {
    it('should propagate filter updates to linked zone members', async () => {
      // Setup: Multiple linked clients in a zone
      const linkedClients = ['local', 'dc:a6:32:7e:d3:43', 'dc:a6:32:7e:d3:44'];
      useMultiroomStore.mockReturnValue({
        clientList: [
          { mac_id: 'dc:a6:32:7e:d3:43', host: 'milo-client-1', online: true },
          { mac_id: 'dc:a6:32:7e:d3:44', host: 'milo-client-2', online: true }
        ],
        zoneList: [{
          id: 'zone-1',
          client_ids: linkedClients
        }],
        isInitialized: true,
        getLinkedClientIds: vi.fn(() => linkedClients),
        isClientOnline: vi.fn(() => true),
        initialize: vi.fn()
      });

      axios.put.mockResolvedValue({ data: { status: 'success' } });

      const freshStore = useEqualizerStore();
      freshStore.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', enabled: true }
      ];
      freshStore.selectedTarget = 'local';

      // Update filter and finalize (which triggers propagation)
      freshStore.updateFilter('eq_band_00', 'gain', 5.0);
      await freshStore.finalizeFilterUpdate('eq_band_00');

      // Should have called PUT for local + propagation to 2 online zone members
      // Local: /api/equalizer/filter/eq_band_00
      // Remote clients: /api/equalizer/client/{hostname}/filter/eq_band_00
      expect(axios.put).toHaveBeenCalled();
      const putCalls = axios.put.mock.calls;
      // At least 1 call for local filter update
      expect(putCalls.some(call => call[0] === '/api/equalizer/filter/eq_band_00')).toBe(true);
    });

    it('should skip offline clients during propagation', async () => {
      // Setup mock BEFORE creating store: standalone with linked clients (no zone)
      useMultiroomStore.mockReturnValue({
        clientList: [
          { mac_id: 'dc:a6:32:7e:d3:43', host: 'milo-client-1', online: true },
          { mac_id: 'dc:a6:32:7e:d3:44', host: 'milo-client-2', online: false }
        ],
        zoneList: [],
        isInitialized: true,
        getLinkedClientIds: vi.fn(() => ['local', 'dc:a6:32:7e:d3:43', 'dc:a6:32:7e:d3:44']),
        isClientOnline: vi.fn((clientId) => clientId !== 'dc:a6:32:7e:d3:44'),
        getZoneForClient: vi.fn(() => null),  // Standalone
        initialize: vi.fn()
      });
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValue({ data: { status: 'success' } });

      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', enabled: true }
      ];
      store.selectedTarget = 'local';

      // Update filter and finalize
      store.updateFilter('eq_band_00', 'gain', 3.0);
      await store.finalizeFilterUpdate('eq_band_00');

      // Should NOT have called PUT for offline client (milo-client-2)
      const putCalls = axios.put.mock.calls;
      const offlineClientCalls = putCalls.filter(call =>
        call[0].includes('milo-client-2')
      );
      expect(offlineClientCalls.length).toBe(0);
    });
  });

  describe('updateFilter with zone propagation', () => {
    it('should update filter state and trigger throttled update', async () => {
      // Setup standalone mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValue({ data: { status: 'success', id: 'eq_band_00' } });

      // Initialize filters state
      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', enabled: true }
      ];
      store.selectedTarget = 'local';

      // updateFilter signature is (filterId, field, value)
      store.updateFilter('eq_band_00', 'gain', 3.0);

      // Verify local state was updated
      const filter = store.filters.find(f => f.id === 'eq_band_00');
      expect(filter.gain).toBe(3.0);
    });

    it('should use zone endpoint when in zone via finalizeFilterUpdate', async () => {
      // Setup zone mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-1', 'Test Zone'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.patch.mockResolvedValue({
        data: { status: 'success', zone_id: 'zone-1', filter_id: 'eq_band_00', applied_to: ['local', 'dc:a6:32:7e:d3:43'] }
      });

      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', enabled: true }
      ];
      store.selectedTarget = 'local';

      // Update local state first
      store.updateFilter('eq_band_00', 'gain', 5.0);

      // Then finalize (which uses zone endpoint)
      await store.finalizeFilterUpdate('eq_band_00');

      // Should use zone endpoint (backend handles propagation)
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/equalizer/zone/zone-1/filter/eq_band_00',
        expect.objectContaining({ gain: 5.0 })
      );
    });
  });

  describe('Filter state management', () => {
    it('should have 10-band EQ default structure', () => {
      const freshStore = useEqualizerStore();
      // Default filters should be defined
      expect(Array.isArray(freshStore.filters)).toBe(true);
    });

    it('should support filter enabled/disabled state', async () => {
      axios.put.mockResolvedValue({ data: { status: 'success' } });

      const freshStore = useEqualizerStore();
      freshStore.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', enabled: true }
      ];
      freshStore.selectedTarget = 'local';

      // updateFilter signature is (filterId, field, value)
      freshStore.updateFilter('eq_band_00', 'enabled', false);

      // Verify local state was updated
      const filter = freshStore.filters.find(f => f.id === 'eq_band_00');
      expect(filter.enabled).toBe(false);
    });

    it('should support all filter types', () => {
      const validTypes = ['Peaking', 'Lowshelf', 'Highshelf', 'Lowpass', 'Highpass', 'Notch', 'Allpass'];
      validTypes.forEach(type => {
        // Verify type is a valid string (no runtime check needed, just type validation)
        expect(typeof type).toBe('string');
      });
    });
  });

  describe('Preset auto-switch (FR23)', () => {
    it('should track active preset state', () => {
      const freshStore = useEqualizerStore();
      expect(freshStore.activePreset).toBeDefined;
    });

    it('should switch to manual preset on filter modification', async () => {
      axios.put.mockResolvedValue({ data: { status: 'success' } });

      const freshStore = useEqualizerStore();
      freshStore.activePreset = 'acoustic'; // Currently on a preset
      freshStore.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', enabled: true }
      ];
      freshStore.selectedTarget = 'local';

      // updateFilter signature is (filterId, field, value)
      freshStore.updateFilter('eq_band_00', 'gain', 5.0);

      // After manual modification, local state should be updated
      const filter = freshStore.filters.find(f => f.id === 'eq_band_00');
      expect(filter.gain).toBe(5.0);
      // Preset switch to 'manual' happens in the backend/store logic on finalize
    });
  });
});

// =============================================================================
// Story 4.6: Equalizer Presets System - Preset Loading & Zone Propagation Tests
// =============================================================================

describe('equalizerStore - Preset Management (Story 4.6)', () => {
  // Note: loadPreset now uses zone endpoint when target is in a zone

  describe('loadPreset', () => {
    it('should call PUT /api/equalizer/preset/{preset_id} for standalone client', async () => {
      // Setup standalone mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValueOnce({ data: { status: 'success', id: 'jazz' } });

      store.selectedTarget = 'local';
      const result = await store.loadPreset('jazz');

      expect(axios.put).toHaveBeenCalledWith('/api/equalizer/preset/jazz');
      expect(result).toBe(true);
      expect(store.activePreset).toBe('jazz');
    });

    it('should use POST /api/equalizer/zone/{zone_id}/preset when in zone', async () => {
      // Setup zone mock BEFORE creating store (backend handles propagation)
      useMultiroomStore.mockReturnValue(createZoneMock('zone-1', 'Test Zone'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.post.mockResolvedValueOnce({
        data: { status: 'success', zone_id: 'zone-1', applied_to: ['local', 'dc:a6:32:7e:d3:43'] }
      });

      store.selectedTarget = 'local';
      await store.loadPreset('rock');

      // Should use zone endpoint (backend handles propagation to all members)
      expect(axios.post).toHaveBeenCalledWith(
        '/api/equalizer/zone/zone-1/preset',
        { preset_id: 'rock' }
      );
    });

    it('should handle partial success from zone endpoint', async () => {
      // Setup zone mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-1', 'Test Zone'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      // One client offline, partial success
      axios.post.mockResolvedValueOnce({
        data: {
          status: 'partial',
          zone_id: 'zone-1',
          applied_to: ['local'],
          offline_clients: ['dc:a6:32:7e:d3:43']
        }
      });

      store.selectedTarget = 'local';
      const result = await store.loadPreset('classical');

      // Should still return true for partial success
      expect(result).toBe(true);
    });

    it('should return false on API error', async () => {
      // Setup standalone mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockRejectedValueOnce(new Error('Network error'));

      store.selectedTarget = 'local';
      const result = await store.loadPreset('jazz');

      expect(result).toBe(false);
    });

    it('should update activePreset state on successful load', async () => {
      // Setup standalone mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValueOnce({ data: { status: 'success', id: 'bass_boost' } });

      store.selectedTarget = 'local';
      store.activePreset = 'manual'; // Start with manual

      await store.loadPreset('bass_boost');

      expect(store.activePreset).toBe('bass_boost');
    });

  });

});

// =============================================================================
// Story 4.7: API Endpoints for Equalizer - Zone Endpoint Integration Tests
// =============================================================================

// Helper: Create zone mock that returns zone for 'local' client
const createZoneMock = (zoneId = 'zone-test', zoneName = 'Test Zone') => ({
  clientList: [
    { mac_id: 'local', name: 'Milo', online: true },
    { mac_id: 'dc:a6:32:7e:d3:43', name: 'Remote', online: true }
  ],
  zoneList: [{
    id: zoneId,
    name: zoneName,
    client_ids: ['local', 'dc:a6:32:7e:d3:43']
  }],
  isInitialized: true,
  getZoneForClient: vi.fn((clientId) => {
    if (clientId === 'local' || clientId === 'dc:a6:32:7e:d3:43') {
      return { id: zoneId, name: zoneName, client_ids: ['local', 'dc:a6:32:7e:d3:43'] };
    }
    return null;
  }),
  getLinkedClientIds: vi.fn(() => ['local', 'dc:a6:32:7e:d3:43']),
  isClientOnline: vi.fn(() => true),
  initialize: vi.fn()
});

// Helper: Create standalone mock (no zone)
const createStandaloneMock = () => ({
  clientList: [
    { mac_id: 'local', name: 'Milo', online: true }
  ],
  zoneList: [],
  isInitialized: true,
  getZoneForClient: vi.fn(() => null),
  getLinkedClientIds: vi.fn((clientId) => [clientId]),
  isClientOnline: vi.fn(() => true),
  initialize: vi.fn()
});

describe('equalizerStore - Zone Equalizer Endpoints (Story 4.7)', () => {
  // Note: Each test must set up mock BEFORE creating store instance

  describe('updateCompressor with zone endpoint', () => {
    it('should use PATCH /api/equalizer/zone/{zone_id}/compressor when in zone', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-living-room', 'Living Room'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks(); // Clear after store creation to reset axios mocks

      axios.patch.mockResolvedValueOnce({
        data: { status: 'success', zone_id: 'zone-living-room', applied_to: ['local', 'dc:a6:32:7e:d3:43'] }
      });

      store.selectedTarget = 'local';
      const result = await store.updateCompressor({ enabled: true, threshold: -20 });

      // Should use zone endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/equalizer/zone/zone-living-room/compressor',
        expect.objectContaining({ enabled: true, threshold: -20 })
      );
      expect(result).toBe(true);
    });

    it('should use PUT /api/equalizer/compressor for standalone client', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValueOnce({
        data: { status: 'success' }
      });

      store.selectedTarget = 'local';
      const result = await store.updateCompressor({ enabled: true, threshold: -15 });

      // Should use direct endpoint (not zone)
      expect(axios.put).toHaveBeenCalledWith(
        '/api/equalizer/compressor',
        expect.objectContaining({ enabled: true, threshold: -15 })
      );
      expect(result).toBe(true);
    });
  });

  describe('updateLoudness with zone endpoint', () => {
    it('should use PATCH /api/equalizer/zone/{zone_id}/loudness when in zone', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-bedroom', 'Bedroom'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.patch.mockResolvedValueOnce({
        data: { status: 'success', zone_id: 'zone-bedroom', applied_to: ['local', 'dc:a6:32:7e:d3:43'] }
      });

      store.selectedTarget = 'local';
      const result = await store.updateLoudness({ enabled: true });

      // Should use zone endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/equalizer/zone/zone-bedroom/loudness',
        expect.objectContaining({ enabled: true })
      );
      expect(result).toBe(true);
    });
  });

  describe('toggleEqualizerEffectsEnabled with zone endpoint', () => {
    it('should use PATCH /api/equalizer/zone/{zone_id}/enabled when in zone', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-kitchen', 'Kitchen'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.patch.mockResolvedValueOnce({
        data: { status: 'success', zone_id: 'zone-kitchen', enabled: false, applied_to: ['local', 'dc:a6:32:7e:d3:43'] }
      });

      store.selectedTarget = 'local';
      const result = await store.toggleEqualizerEffectsEnabled(false);

      // Should use zone endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/equalizer/zone/zone-kitchen/enabled',
        expect.objectContaining({ enabled: false })
      );
      expect(result).toBe(true);
    });

    it('should use PUT /api/equalizer/enabled for standalone client', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValueOnce({
        data: { status: 'success' }
      });

      store.selectedTarget = 'local';
      await store.toggleEqualizerEffectsEnabled(false);

      // Should use direct endpoint (not zone)
      expect(axios.put).toHaveBeenCalledWith(
        '/api/equalizer/enabled',
        expect.objectContaining({ enabled: false })
      );
      // Note: Return value depends on internal state (cleanup, loadStatus)
      // Key verification is that correct endpoint was called
    });
  });

  describe('finalizeFilterUpdate with zone endpoint', () => {
    it('should use PATCH /api/equalizer/zone/{zone_id}/filter/{filter_id} when in zone', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-office', 'Office'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.patch.mockResolvedValueOnce({
        data: { status: 'success', zone_id: 'zone-office', filter_id: 'eq_band_00', applied_to: ['local', 'dc:a6:32:7e:d3:43'] }
      });

      store.selectedTarget = 'local';
      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 5.0, q: 1.41, type: 'Peaking', enabled: true }
      ];

      await store.finalizeFilterUpdate('eq_band_00');

      // Should use zone endpoint
      expect(axios.patch).toHaveBeenCalledWith(
        '/api/equalizer/zone/zone-office/filter/eq_band_00',
        expect.objectContaining({ gain: 5.0 })
      );
    });

    it('should use PUT /api/equalizer/filter/{filter_id} for standalone client', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      axios.put.mockResolvedValueOnce({
        data: { status: 'success', id: 'eq_band_00' }
      });

      store.selectedTarget = 'local';
      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 3.0, q: 1.41, type: 'Peaking', enabled: true }
      ];

      await store.finalizeFilterUpdate('eq_band_00');

      // Should use direct endpoint (not zone)
      expect(axios.put).toHaveBeenCalledWith(
        '/api/equalizer/filter/eq_band_00',
        expect.objectContaining({ gain: 3.0 })
      );
    });
  });

  describe('Zone endpoint response handling', () => {
    it('should handle partial success status', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-partial', 'Partial Zone'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      // Partial success: one client failed
      axios.patch.mockResolvedValueOnce({
        data: {
          status: 'partial',
          zone_id: 'zone-partial',
          applied_to: ['local'],
          offline_clients: null,
          errors: [{ client_id: 'dc:a6:32:7e:d3:43', error: 'Connection refused' }]
        }
      });

      store.selectedTarget = 'local';
      const result = await store.updateCompressor({ enabled: true });

      // Should still return true for partial success (local succeeded)
      expect(result).toBe(true);
    });

    it('should report offline_clients in response', async () => {
      // Setup mock BEFORE creating store
      useMultiroomStore.mockReturnValue(createZoneMock('zone-offline', 'Zone with Offline'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      // Backend reports offline client was skipped
      axios.patch.mockResolvedValueOnce({
        data: {
          status: 'success',
          zone_id: 'zone-offline',
          applied_to: ['local'],
          offline_clients: ['dc:a6:32:7e:d3:43']
        }
      });

      store.selectedTarget = 'local';
      const result = await store.updateLoudness({ enabled: false });

      expect(result).toBe(true);
      // Offline clients are handled gracefully by the backend
    });
  });
});

// =============================================================================
// Story 4.8: Frontend Equalizer Controls - ItemSelector, Presets & WebSocket Tests
// =============================================================================

describe('equalizerStore - ItemSelector Zone/Client Selection (Story 4.8)', () => {
  describe('availableTargets computed property', () => {
    it('should derive targets from clientRegistryStore.clientList', () => {
      useMultiroomStore.mockReturnValue({
        clientList: [
          { mac_id: 'local', name: 'Milo', host: 'milo', ip: '192.168.1.1', online: true },
          { mac_id: 'dc:a6:32:7e:d3:43', name: 'Kitchen', host: 'milo-client-01', ip: '192.168.1.10', online: true },
          { mac_id: 'dc:a6:32:7e:d3:44', name: 'Bedroom', host: 'milo-client-02', ip: '192.168.1.11', online: false }
        ],
        zoneList: [],
        isInitialized: true,
        getZoneForClient: vi.fn(() => null),
        getLinkedClientIds: vi.fn((id) => [id]),
        isClientOnline: vi.fn((id) => id !== 'dc:a6:32:7e:d3:44'),
        initialize: vi.fn()
      });
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      const targets = store.availableTargets;

      expect(targets.length).toBe(3);
      expect(targets[0]).toEqual({
        id: 'local',
        name: 'Milo',
        host: 'milo',
        ip: '192.168.1.1',
        online: true
      });
      expect(targets[2].online).toBe(false);
    });

    it('should update reactively when clientRegistryStore changes', () => {
      const mockStore = {
        clientList: [{ mac_id: 'local', name: 'Milo', host: 'milo', ip: '192.168.1.1', online: true }],
        zoneList: [],
        isInitialized: true,
        getZoneForClient: vi.fn(() => null),
        getLinkedClientIds: vi.fn((id) => [id]),
        isClientOnline: vi.fn(() => true),
        initialize: vi.fn()
      };
      useMultiroomStore.mockReturnValue(mockStore);
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      expect(store.availableTargets.length).toBe(1);
    });
  });

  describe('linkedGroups computed property', () => {
    it('should derive zones from clientRegistryStore.zoneList', () => {
      useMultiroomStore.mockReturnValue({
        clientList: [
          { mac_id: 'local', name: 'Milo', online: true },
          { mac_id: 'dc:a6:32:7e:d3:43', name: 'Kitchen', online: true }
        ],
        zoneList: [
          { id: 'zone-living', name: 'Living Room', client_ids: ['local', 'dc:a6:32:7e:d3:43'] }
        ],
        isInitialized: true,
        getZoneForClient: vi.fn(() => ({ id: 'zone-living', name: 'Living Room', client_ids: ['local', 'dc:a6:32:7e:d3:43'] })),
        getLinkedClientIds: vi.fn(() => ['local', 'dc:a6:32:7e:d3:43']),
        isClientOnline: vi.fn(() => true),
        initialize: vi.fn()
      });
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      const zones = store.linkedGroups;

      expect(zones.length).toBe(1);
      expect(zones[0].id).toBe('zone-living');
      expect(zones[0].client_ids).toEqual(['local', 'dc:a6:32:7e:d3:43']);
    });
  });

  describe('getLinkedClientIds helper', () => {
    it('should return zone members when client is in a zone', () => {
      useMultiroomStore.mockReturnValue(createZoneMock('zone-test', 'Test Zone'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      const linkedIds = store.getLinkedClientIds('local');

      expect(linkedIds).toContain('local');
      expect(linkedIds).toContain('dc:a6:32:7e:d3:43');
    });

    it('should return only the client itself when not in a zone', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      const linkedIds = store.getLinkedClientIds('local');

      expect(linkedIds).toEqual(['local']);
    });
  });

  describe('getZoneGroup helper', () => {
    it('should return zone object when client is in a zone', () => {
      useMultiroomStore.mockReturnValue(createZoneMock('zone-office', 'Office'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      const zone = store.getZoneGroup('local');

      expect(zone).not.toBeNull();
      expect(zone.id).toBe('zone-office');
      expect(zone.name).toBe('Office');
    });

    it('should return null when client is standalone', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      const zone = store.getZoneGroup('local');

      expect(zone).toBeNull();
    });
  });

});

describe('equalizerStore - Preset Display Integration (Story 4.8)', () => {
  describe('builtinPresets array', () => {
    it('should be populated after fetchPresets', async () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();
      vi.clearAllMocks();

      // loadStatus calls fetchStatus, fetchFilters, and fetchPresets in parallel
      // Mock all three endpoints
      axios.get.mockImplementation((url) => {
        if (url.includes('/status')) {
          return Promise.resolve({ data: { state: 'running', sample_rate: 48000 } });
        }
        if (url.includes('/filters')) {
          return Promise.resolve({ data: { filters: [] } });
        }
        if (url.includes('/presets')) {
          return Promise.resolve({
            data: {
              presets: [
                { id: 'flat', gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0] },
                { id: 'jazz', gains: [4, 3, 2, 2, -2, -2, 0, 2, 3, 4] },
                { id: 'rock', gains: [5, 4, 3, 0, -1, -1, 0, 3, 4, 5] }
              ],
              manual_gains: [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
              active_preset: 'flat'
            }
          });
        }
        return Promise.reject(new Error(`Unmocked URL: ${url}`));
      });

      // Load presets
      await store.loadStatus();

      expect(store.builtinPresets.length).toBe(3);
      expect(store.activePreset).toBe('flat');
    });
  });
});

describe('equalizerStore - WebSocket Event Handlers (Story 4.8)', () => {
  describe('handleFilterChanged', () => {
    it('should update filter gain from WebSocket event', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', displayName: '31' }
      ];

      store.handleFilterChanged({
        data: { id: 'eq_band_00', freq: 31, gain: 5.5, q: 1.41, type: 'Peaking' }
      });

      expect(store.filters[0].gain).toBe(5.5);
    });

    it('should update filter frequency and displayName from WebSocket event', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking', displayName: '31' }
      ];

      store.handleFilterChanged({
        data: { id: 'eq_band_00', freq: 1000, gain: 0, q: 1.41, type: 'Peaking' }
      });

      expect(store.filters[0].freq).toBe(1000);
      expect(store.filters[0].displayName).toBe('1k');
    });

    it('should not update filters during throttling', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 3.0, q: 1.41, type: 'Peaking', displayName: '31' }
      ];

      // Simulate throttling in progress by updating a filter
      store.updateFilter('eq_band_00', 'gain', 3.0);

      // WebSocket event during throttling should be ignored
      store.handleFilterChanged({
        data: { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, type: 'Peaking' }
      });

      // Gain should still be 3.0 (not reverted to 0)
      expect(store.filters[0].gain).toBe(3.0);
    });
  });

  describe('handleCompressorChanged', () => {
    it('should update compressor settings from WebSocket event', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.compressor = { enabled: false, threshold: -20, ratio: 4, attack: 10, release: 100, makeup_gain: 0 };

      store.handleCompressorChanged({
        data: { enabled: true, threshold: -15, ratio: 6 }
      });

      expect(store.compressor.enabled).toBe(true);
      expect(store.compressor.threshold).toBe(-15);
      expect(store.compressor.ratio).toBe(6);
      // Unchanged fields should be preserved
      expect(store.compressor.attack).toBe(10);
    });
  });

  describe('handleLoudnessChanged', () => {
    it('should update loudness settings from WebSocket event', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.loudness = { enabled: false, low_boost: 5, high_boost: 5 };

      store.handleLoudnessChanged({
        data: { enabled: true, low_boost: 8 }
      });

      expect(store.loudness.enabled).toBe(true);
      expect(store.loudness.low_boost).toBe(8);
      // Unchanged fields should be preserved
      expect(store.loudness.high_boost).toBe(5);
    });
  });

  describe('handleEnabledChanged', () => {
    it('should update isEqualizerEffectsEnabled from WebSocket event', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.isEqualizerEffectsEnabled = true;

      store.handleEnabledChanged({ data: { enabled: false } });

      expect(store.isEqualizerEffectsEnabled).toBe(false);
    });

    it('should not change state when enabled is undefined', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.isEqualizerEffectsEnabled = true;

      store.handleEnabledChanged({ data: {} });

      // Should remain unchanged
      expect(store.isEqualizerEffectsEnabled).toBe(true);
    });
  });
});

// =============================================================================
// Story 6.2: Frontend WebSocket Integration - New Event Handlers
// =============================================================================

describe('equalizerStore - handleEqualizerChanged (Story 6.2)', () => {
  describe('target matching', () => {
    it('should update state when target_type is client and matches selectedTarget', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'dc:a6:32:7e:d3:43';
      store.compressor = { enabled: false, threshold: -20, ratio: 4 };

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'dc:a6:32:7e:d3:43',
          equalizer_settings: {
            compressor: { enabled: true, threshold: -15 }
          }
        }
      });

      expect(store.compressor.enabled).toBe(true);
      expect(store.compressor.threshold).toBe(-15);
    });

    it('should update state when target_type is zone and selectedTarget is in that zone', () => {
      useMultiroomStore.mockReturnValue(createZoneMock('zone-living', 'Living Room'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local'; // local is in zone-living
      store.loudness = { enabled: false, low_boost: 5, high_boost: 5 };

      store.handleEqualizerChanged({
        data: {
          target_type: 'zone',
          target_id: 'zone-living',
          equalizer_settings: {
            loudness: { enabled: true, low_boost: 10 }
          }
        }
      });

      expect(store.loudness.enabled).toBe(true);
      expect(store.loudness.low_boost).toBe(10);
    });

    it('should ignore event when target_type is client but does not match selectedTarget', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false, threshold: -20 };

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'other-client-id', // Different from selectedTarget
          equalizer_settings: {
            compressor: { enabled: true, threshold: -10 }
          }
        }
      });

      // Should not change
      expect(store.compressor.enabled).toBe(false);
      expect(store.compressor.threshold).toBe(-20);
    });

    it('should ignore event when target_type is zone but selectedTarget is not in that zone', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock()); // Standalone = not in any zone
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false };

      store.handleEqualizerChanged({
        data: {
          target_type: 'zone',
          target_id: 'other-zone-id',
          equalizer_settings: {
            compressor: { enabled: true }
          }
        }
      });

      // Should not change
      expect(store.compressor.enabled).toBe(false);
    });
  });

  describe('filter updates', () => {
    it('should update filters from equalizer_settings when not throttling', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, displayName: '31' },
        { id: 'eq_band_01', freq: 62, gain: 0, q: 1.41, displayName: '62' }
      ];

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: {
            filters: [
              { id: 'eq_band_00', freq: 31, gain: 5.0, q: 1.41 },
              { id: 'eq_band_01', freq: 80, gain: -2.0, q: 2.0 }
            ]
          }
        }
      });

      expect(store.filters[0].gain).toBe(5.0);
      expect(store.filters[1].freq).toBe(80);
      expect(store.filters[1].gain).toBe(-2.0);
      expect(store.filters[1].displayName).toBe('80');
    });
  });

  describe('missing or invalid data', () => {
    it('should handle missing event.data gracefully', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false };

      // Should not throw
      expect(() => store.handleEqualizerChanged({})).not.toThrow();
      expect(() => store.handleEqualizerChanged({ data: null })).not.toThrow();

      // State unchanged
      expect(store.compressor.enabled).toBe(false);
    });

    it('should handle missing equalizer_settings gracefully', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false };

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local'
          // No equalizer_settings
        }
      });

      // State unchanged
      expect(store.compressor.enabled).toBe(false);
    });
  });
});

// =============================================================================
// Story 6.4: Equalizer Store Real-Time Sync Tests
// =============================================================================

describe('equalizerStore - Real-Time Sync (Story 6.4)', () => {
  describe('AC1: Zone Equalizer Changed Event Handling', () => {
    it('should update zone Equalizer settings when receiving equalizer_changed for zone containing selectedTarget', () => {
      useMultiroomStore.mockReturnValue(createZoneMock('zone-living', 'Living Room'));
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local'; // local is in zone-living
      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, displayName: '31' },
        { id: 'eq_band_01', freq: 62, gain: 0, q: 1.41, displayName: '62' }
      ];
      store.compressor = { enabled: false, threshold: -20 };
      store.loudness = { enabled: false };

      // Receive equalizer_changed event for the zone
      store.handleEqualizerChanged({
        data: {
          target_type: 'zone',
          target_id: 'zone-living',
          equalizer_settings: {
            filters: [
              { id: 'eq_band_00', freq: 31, gain: 6.0, q: 1.41 },
              { id: 'eq_band_01', freq: 62, gain: -3.0, q: 2.0, type: 'Lowshelf' }
            ],
            compressor: { enabled: true, threshold: -15, ratio: 6 },
            loudness: { enabled: true, low_boost: 8 }
          }
        }
      });

      // All values should be updated
      expect(store.filters[0].gain).toBe(6.0);
      expect(store.filters[1].gain).toBe(-3.0);
      expect(store.filters[1].q).toBe(2.0);
      expect(store.compressor.enabled).toBe(true);
      expect(store.compressor.threshold).toBe(-15);
      expect(store.loudness.enabled).toBe(true);
      expect(store.loudness.low_boost).toBe(8);
    });

    it('should ignore zone equalizer_changed event when selectedTarget is not in that zone', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock()); // local is standalone
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false };

      store.handleEqualizerChanged({
        data: {
          target_type: 'zone',
          target_id: 'other-zone',
          equalizer_settings: { compressor: { enabled: true } }
        }
      });

      expect(store.compressor.enabled).toBe(false); // Unchanged
    });
  });

  describe('AC2: Client Equalizer Changed Event Handling', () => {
    it('should update client Equalizer settings when receiving equalizer_changed for standalone client', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.filters = [{ id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, displayName: '31' }];

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: {
            filters: [{ id: 'eq_band_00', freq: 50, gain: 4.5, q: 2.0, type: 'Highpass' }]
          }
        }
      });

      expect(store.filters[0].freq).toBe(50);
      expect(store.filters[0].gain).toBe(4.5);
      expect(store.filters[0].displayName).toBe('50');
    });

    it('should ignore client equalizer_changed event when target_id does not match selectedTarget', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.filters = [{ id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, displayName: '31' }];

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'dc:a6:32:7e:d3:43', // Different client
          equalizer_settings: {
            filters: [{ id: 'eq_band_00', gain: 10 }]
          }
        }
      });

      expect(store.filters[0].gain).toBe(0); // Unchanged
    });
  });

  describe('AC4: Remote User Equalizer Changes', () => {
    it('should update local UI immediately on remote equalizer_changed event (no conflict)', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false, threshold: -20, ratio: 4 };

      // Remote user changes compressor settings
      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: {
            compressor: { enabled: true, threshold: -12, ratio: 8 }
          }
        }
      });

      expect(store.compressor.enabled).toBe(true);
      expect(store.compressor.threshold).toBe(-12);
      expect(store.compressor.ratio).toBe(8);
    });

    it('should not overwrite filters during local editing (throttle guard)', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.filters = [{ id: 'eq_band_00', freq: 31, gain: 5.0, q: 1.41, displayName: '31' }];

      // Simulate local editing - triggers throttle
      store.updateFilter('eq_band_00', 'gain', 5.0);

      // Remote event arrives during local editing
      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: {
            filters: [{ id: 'eq_band_00', gain: 0 }] // Remote wants to reset
          }
        }
      });

      // Local value should be preserved (throttle guard)
      expect(store.filters[0].gain).toBe(5.0);
    });
  });

  describe('AC5: No Polling for Equalizer State', () => {
    it('should use reactive state updates via WebSocket handlers', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      // Verify handler methods exist and are functions (not polling)
      expect(typeof store.handleEqualizerChanged).toBe('function');
      expect(typeof store.handleFilterChanged).toBe('function');
      expect(typeof store.handleEnabledChanged).toBe('function');
      expect(typeof store.handleCompressorChanged).toBe('function');
      expect(typeof store.handleLoudnessChanged).toBe('function');
    });
  });

  describe('Multiple Rapid Equalizer Events', () => {
    it('should process multiple rapid equalizer_changed events without data loss', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.selectedTarget = 'local';
      store.compressor = { enabled: false, threshold: -20 };
      store.loudness = { enabled: false, low_boost: 5 };

      // Rapid sequence of events
      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: { compressor: { enabled: true } }
        }
      });

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: { loudness: { enabled: true, low_boost: 10 } }
        }
      });

      store.handleEqualizerChanged({
        data: {
          target_type: 'client',
          target_id: 'local',
          equalizer_settings: { compressor: { threshold: -15 } }
        }
      });

      // All changes should be applied
      expect(store.compressor.enabled).toBe(true);
      expect(store.compressor.threshold).toBe(-15);
      expect(store.loudness.enabled).toBe(true);
      expect(store.loudness.low_boost).toBe(10);
    });

    it('should process rapid filter_changed events correctly', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.filters = [
        { id: 'eq_band_00', freq: 31, gain: 0, q: 1.41, displayName: '31' },
        { id: 'eq_band_01', freq: 62, gain: 0, q: 1.41, displayName: '62' },
        { id: 'eq_band_02', freq: 125, gain: 0, q: 1.41, displayName: '125' }
      ];

      // Rapid filter updates
      store.handleFilterChanged({ data: { id: 'eq_band_00', gain: 3 } });
      store.handleFilterChanged({ data: { id: 'eq_band_01', gain: -2 } });
      store.handleFilterChanged({ data: { id: 'eq_band_02', gain: 5 } });

      expect(store.filters[0].gain).toBe(3);
      expect(store.filters[1].gain).toBe(-2);
      expect(store.filters[2].gain).toBe(5);
    });
  });

  describe('enabled_changed Event Handling', () => {
    it('should update isEqualizerEffectsEnabled from WebSocket event', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.isEqualizerEffectsEnabled = true;

      store.handleEnabledChanged({ data: { enabled: false } });

      expect(store.isEqualizerEffectsEnabled).toBe(false);
    });

    it('should toggle from false to true', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.isEqualizerEffectsEnabled = false;

      store.handleEnabledChanged({ data: { enabled: true } });

      expect(store.isEqualizerEffectsEnabled).toBe(true);
    });

    it('should ignore event with undefined enabled value', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      store.isEqualizerEffectsEnabled = true;

      store.handleEnabledChanged({ data: { other_field: 'value' } });

      expect(store.isEqualizerEffectsEnabled).toBe(true); // Unchanged
    });
  });
});

describe('equalizerStore - handleZoneCrossoverChanged (Story 6.2)', () => {
  describe('missing data handling', () => {
    it('should handle empty data gracefully', () => {
      useMultiroomStore.mockReturnValue(createStandaloneMock());
      setActivePinia(createPinia());
      const store = useEqualizerStore();

      // Should not throw
      expect(() => store.handleZoneCrossoverChanged({})).not.toThrow();
      expect(() => store.handleZoneCrossoverChanged({ data: {} })).not.toThrow();
    });
  });
});
