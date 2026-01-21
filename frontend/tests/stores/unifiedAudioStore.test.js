// frontend/tests/stores/unifiedAudioStore.test.js
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { useUnifiedAudioStore } from '@/stores/unifiedAudioStore';
import axios from 'axios';

// Mock axios
vi.mock('axios');

describe('unifiedAudioStore', () => {
  let store;

  beforeEach(() => {
    // Fresh store for each test (Pinia reset in setup.js)
    store = useUnifiedAudioStore();
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('should have correct default system state', () => {
      expect(store.systemState.active_source).toBe('none');
      expect(store.systemState.plugin_state).toBe('ready');
      expect(store.systemState.transitioning).toBe(false);
      expect(store.systemState.multiroom_enabled).toBe(false);
    });

    it('should have correct default volume state', () => {
      expect(store.volumeState.mode).toBe('direct');
      expect(store.volumeState.global_volume_db).toBe(-30.0);
      expect(store.volumeState.global_mute).toBe(false);
      expect(store.volumeState.step_mobile_db).toBe(3.0);
    });

    it('should not show volume bar initially', () => {
      expect(store.showVolumeBar).toBe(false);
    });
  });

  describe('changeSource', () => {
    it('should call API and return true on success', async () => {
      axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

      const result = await store.changeSource('spotify');

      expect(axios.post).toHaveBeenCalledWith('/api/audio/source/spotify');
      expect(result).toBe(true);
    });

    it('should return false on API error', async () => {
      axios.post.mockRejectedValueOnce(new Error('Network error'));

      const result = await store.changeSource('spotify');

      expect(result).toBe(false);
    });

    it('should set isChangingSource during API call', async () => {
      let capturedState;
      axios.post.mockImplementationOnce(() => {
        capturedState = store.isChangingSource;
        return Promise.resolve({ data: { status: 'success' } });
      });

      await store.changeSource('spotify');

      expect(capturedState).toBe(true);
      expect(store.isChangingSource).toBe(false);
    });
  });

  describe('sendCommand', () => {
    it('should send command to correct endpoint', async () => {
      axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

      await store.sendCommand('spotify', 'play', { track_id: '123' });

      expect(axios.post).toHaveBeenCalledWith('/api/audio/control/spotify', {
        command: 'play',
        data: { track_id: '123' }
      });
    });

    it('should return true on success', async () => {
      axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

      const result = await store.sendCommand('spotify', 'pause');

      expect(result).toBe(true);
    });

    it('should return false on error', async () => {
      axios.post.mockRejectedValueOnce(new Error('Error'));

      const result = await store.sendCommand('spotify', 'play');

      expect(result).toBe(false);
    });
  });

  describe('volume actions', () => {
    describe('setVolume', () => {
      it('should call API with correct parameters', async () => {
        axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

        await store.setVolume(-20.0, true);

        expect(axios.post).toHaveBeenCalledWith('/api/volume/set', {
          volume_db: -20.0,
          show_bar: true
        });
      });

      it('should return true on success', async () => {
        axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

        const result = await store.setVolume(-25.0);

        expect(result).toBe(true);
      });
    });

    describe('adjustVolume', () => {
      it('should call API with delta', async () => {
        axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

        await store.adjustVolume(3.0);

        expect(axios.post).toHaveBeenCalledWith('/api/volume/adjust', {
          delta_db: 3.0,
          show_bar: true
        });
      });
    });

    describe('increaseVolume', () => {
      it('should use step_mobile_db value', async () => {
        axios.post.mockResolvedValueOnce({ data: { status: 'success' } });
        store.volumeState.step_mobile_db = 5.0;

        await store.increaseVolume();

        expect(axios.post).toHaveBeenCalledWith('/api/volume/adjust', {
          delta_db: 5.0,
          show_bar: true
        });
      });
    });

    describe('decreaseVolume', () => {
      it('should use negative step_mobile_db', async () => {
        axios.post.mockResolvedValueOnce({ data: { status: 'success' } });
        store.volumeState.step_mobile_db = 3.0;

        await store.decreaseVolume();

        expect(axios.post).toHaveBeenCalledWith('/api/volume/adjust', {
          delta_db: -3.0,
          show_bar: true
        });
      });
    });
  });

  describe('updateState (WebSocket handler)', () => {
    it('should update system state from valid event', () => {
      const event = {
        data: {
          full_state: {
            active_source: 'spotify',
            plugin_state: 'connected',
            transitioning: false,
            metadata: { title: 'Test Song' },
            multiroom_enabled: true,
            dsp_effects_enabled: true
          }
        }
      };

      store.updateState(event);

      expect(store.systemState.active_source).toBe('spotify');
      expect(store.systemState.plugin_state).toBe('connected');
      expect(store.systemState.multiroom_enabled).toBe(true);
      expect(store.systemState.metadata.title).toBe('Test Song');
    });

    it('should handle missing full_state gracefully', () => {
      const event = { data: {} };
      const originalState = { ...store.systemState };

      store.updateState(event);

      expect(store.systemState.active_source).toBe(originalState.active_source);
    });
  });

  describe('handleVolumeEvent', () => {
    it('should update volume state from event', () => {
      const event = {
        data: {
          state: {
            mode: 'multiroom',
            global_volume_db: -15.0,
            global_mute: false,
            clients: { client1: { volume_db: -10, mute: false } },
            zones: {}
          },
          show_bar: true
        }
      };

      store.handleVolumeEvent(event);

      expect(store.volumeState.mode).toBe('multiroom');
      expect(store.volumeState.global_volume_db).toBe(-15.0);
      expect(store.volumeState.clients).toHaveProperty('client1');
    });

    it('should update zone data from volume_changed event (Task 4.1)', () => {
      const event = {
        data: {
          state: {
            mode: 'multiroom',
            global_volume_db: -27.5,
            global_mute: false,
            clients: {
              'dc:a6:32:7e:d3:43': { volume_db: -25, mute: false, available: true },
              'aa:bb:cc:dd:ee:ff': { volume_db: -30, mute: false, available: true }
            },
            zones: {
              'zone-uuid-123': {
                id: 'zone-uuid-123',
                name: 'Living Room',
                client_ids: ['dc:a6:32:7e:d3:43', 'aa:bb:cc:dd:ee:ff'],
                average_volume_db: -27.5,
                all_muted: false
              }
            }
          },
          show_bar: false
        }
      };

      store.handleVolumeEvent(event);

      // Verify zones are updated
      expect(store.volumeState.zones).toHaveProperty('zone-uuid-123');
      expect(store.volumeState.zones['zone-uuid-123'].average_volume_db).toBe(-27.5);
      expect(store.volumeState.zones['zone-uuid-123'].all_muted).toBe(false);
    });

    it('should update client volumes reactively (Task 4.2)', () => {
      // Initial state
      expect(store.volumeState.clients).toEqual({});

      // Simulate WebSocket event with new client volumes
      const event = {
        data: {
          state: {
            mode: 'multiroom',
            global_volume_db: -25,
            global_mute: false,
            clients: {
              'dc:a6:32:7e:d3:43': { volume_db: -25, mute: false }
            },
            zones: {}
          }
        }
      };

      store.handleVolumeEvent(event);

      // Client volumes should be updated reactively
      expect(store.volumeState.clients['dc:a6:32:7e:d3:43'].volume_db).toBe(-25);
    });

    it('should handle remote volume change (Task 4.4)', () => {
      // Set initial volume
      store.volumeState.clients = {
        'dc:a6:32:7e:d3:43': { volume_db: -30, mute: false }
      };

      // Simulate remote volume change via WebSocket
      const event = {
        data: {
          state: {
            mode: 'multiroom',
            global_volume_db: -20,
            global_mute: false,
            clients: {
              'dc:a6:32:7e:d3:43': { volume_db: -20, mute: false } // Changed remotely
            },
            zones: {}
          },
          show_bar: false
        }
      };

      store.handleVolumeEvent(event);

      // Volume should be updated from remote change
      expect(store.volumeState.clients['dc:a6:32:7e:d3:43'].volume_db).toBe(-20);
    });

    it('should update step_mobile_db when provided', () => {
      const event = {
        data: {
          step_mobile_db: 5.0,
          state: {
            mode: 'direct',
            global_volume_db: -20,
            global_mute: false,
            clients: {},
            zones: {}
          }
        }
      };

      store.handleVolumeEvent(event);

      expect(store.volumeState.step_mobile_db).toBe(5.0);
    });

    it('should show volume bar when show_bar is true', () => {
      vi.useFakeTimers();

      const event = {
        data: {
          state: {
            mode: 'direct',
            global_volume_db: -20,
            global_mute: false,
            clients: {},
            zones: {}
          },
          show_bar: true
        }
      };

      store.handleVolumeEvent(event);

      expect(store.showVolumeBar).toBe(true);

      // Volume bar should hide after 3 seconds
      vi.advanceTimersByTime(3000);
      expect(store.showVolumeBar).toBe(false);

      vi.useRealTimers();
    });
  });

  describe('hideVolumeBar', () => {
    it('should hide volume bar immediately', () => {
      store.showVolumeBar = true;

      store.hideVolumeBar();

      expect(store.showVolumeBar).toBe(false);
    });
  });

  describe('setMultiroomEnabled', () => {
    it('should call API with correct endpoint', async () => {
      axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

      await store.setMultiroomEnabled(true);

      expect(axios.post).toHaveBeenCalledWith('/api/routing/multiroom/true');
    });
  });

  describe('setDspEnabled', () => {
    it('should call API with correct endpoint', async () => {
      axios.post.mockResolvedValueOnce({ data: { status: 'success' } });

      await store.setDspEnabled(false);

      expect(axios.post).toHaveBeenCalledWith('/api/routing/dsp/false');
    });
  });
});
