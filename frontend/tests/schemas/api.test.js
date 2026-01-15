// frontend/tests/schemas/api.test.js
import { describe, it, expect } from 'vitest';
import {
  SystemStateSchema,
  VolumeStateSchema,
  AudioSourceSchema,
  PluginStateSchema,
  SnapcastClientSchema,
  RadioStationSchema,
  validateSchema,
  validateWithFallback
} from '@/schemas/api';

describe('API Schemas', () => {
  describe('AudioSourceSchema', () => {
    it('should accept valid sources', () => {
      const validSources = ['none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast'];

      validSources.forEach(source => {
        expect(AudioSourceSchema.safeParse(source).success).toBe(true);
      });
    });

    it('should reject invalid sources', () => {
      expect(AudioSourceSchema.safeParse('invalid').success).toBe(false);
      expect(AudioSourceSchema.safeParse(123).success).toBe(false);
      expect(AudioSourceSchema.safeParse(null).success).toBe(false);
    });
  });

  describe('PluginStateSchema', () => {
    it('should accept valid states', () => {
      const validStates = ['starting', 'ready', 'connected', 'error'];

      validStates.forEach(state => {
        expect(PluginStateSchema.safeParse(state).success).toBe(true);
      });
    });

    it('should reject invalid states', () => {
      expect(PluginStateSchema.safeParse('invalid').success).toBe(false);
      expect(PluginStateSchema.safeParse('READY').success).toBe(false);
    });
  });

  describe('SystemStateSchema', () => {
    it('should validate complete system state', () => {
      const validState = {
        active_source: 'spotify',
        plugin_state: 'connected',
        transitioning: false,
        metadata: { title: 'Song', artist: 'Artist' },
        multiroom_enabled: true
      };

      const result = SystemStateSchema.safeParse(validState);
      expect(result.success).toBe(true);
      expect(result.data.active_source).toBe('spotify');
    });

    it('should provide defaults for optional fields', () => {
      const minimalState = {
        active_source: 'none',
        plugin_state: 'ready',
        transitioning: false,
        multiroom_enabled: false
      };

      const result = SystemStateSchema.safeParse(minimalState);
      expect(result.success).toBe(true);
      expect(result.data.metadata).toEqual({});
    });

    it('should reject invalid active_source', () => {
      const invalidState = {
        active_source: 'invalid_source',
        plugin_state: 'ready',
        transitioning: false,
        multiroom_enabled: false
      };

      const result = SystemStateSchema.safeParse(invalidState);
      expect(result.success).toBe(false);
    });

    it('should reject invalid plugin_state', () => {
      const invalidState = {
        active_source: 'spotify',
        plugin_state: 'invalid_state',
        transitioning: false,
        multiroom_enabled: false
      };

      const result = SystemStateSchema.safeParse(invalidState);
      expect(result.success).toBe(false);
    });
  });

  describe('VolumeStateSchema', () => {
    it('should validate complete volume state', () => {
      const validState = {
        mode: 'multiroom',
        global_volume_db: -25.5,
        global_mute: false,
        clients: {
          client1: { volume_db: -20, offset_db: 0, mute: false, available: true }
        },
        zones: {
          zone1: { id: 'z1', name: 'Zone 1', client_ids: ['client1'] }
        }
      };

      const result = VolumeStateSchema.safeParse(validState);
      expect(result.success).toBe(true);
      expect(result.data.global_volume_db).toBe(-25.5);
    });

    it('should accept minimal volume state with defaults', () => {
      const minimalState = {
        mode: 'direct',
        global_volume_db: -30,
        global_mute: false
      };

      const result = VolumeStateSchema.safeParse(minimalState);
      expect(result.success).toBe(true);
      expect(result.data.clients).toEqual({});
      expect(result.data.zones).toEqual({});
    });

    it('should reject invalid mode', () => {
      const invalidState = {
        mode: 'invalid',
        global_volume_db: -30,
        global_mute: false
      };

      const result = VolumeStateSchema.safeParse(invalidState);
      expect(result.success).toBe(false);
    });
  });

  describe('SnapcastClientSchema', () => {
    it('should validate snapcast client', () => {
      const client = {
        id: 'abc123',
        name: 'Living Room',
        host: 'milo-client',
        ip: '192.168.1.100',
        volume: 80,
        muted: false,
        available: true,
        dsp_id: 'local'
      };

      const result = SnapcastClientSchema.safeParse(client);
      expect(result.success).toBe(true);
    });

    it('should provide defaults for optional fields', () => {
      const minimalClient = {
        id: 'abc123',
        name: 'Client',
        host: 'host',
        volume: 50,
        muted: false
      };

      const result = SnapcastClientSchema.safeParse(minimalClient);
      expect(result.success).toBe(true);
      expect(result.data.available).toBe(true);
    });
  });

  describe('RadioStationSchema', () => {
    it('should validate radio station', () => {
      const station = {
        id: 'station123',
        name: 'Jazz FM',
        url: 'https://stream.example.com/jazz',
        favicon: 'https://example.com/logo.png',
        country: 'FR',
        language: 'French',
        bitrate: 128
      };

      const result = RadioStationSchema.safeParse(station);
      expect(result.success).toBe(true);
    });

    it('should accept minimal station', () => {
      const minimalStation = {
        id: 's1',
        name: 'Station',
        url: 'https://stream.example.com'
      };

      const result = RadioStationSchema.safeParse(minimalStation);
      expect(result.success).toBe(true);
    });
  });

  describe('validateSchema helper', () => {
    it('should return success result for valid data', () => {
      const result = validateSchema(AudioSourceSchema, 'spotify', 'test');
      expect(result.success).toBe(true);
      expect(result.data).toBe('spotify');
    });

    it('should return error result for invalid data', () => {
      const result = validateSchema(AudioSourceSchema, 'invalid', 'test');
      expect(result.success).toBe(false);
      expect(result.error).toBeDefined();
    });
  });

  describe('validateWithFallback helper', () => {
    it('should return validated data when valid', () => {
      const result = validateWithFallback(AudioSourceSchema, 'spotify', 'none');
      expect(result).toBe('spotify');
    });

    it('should return fallback when invalid', () => {
      const result = validateWithFallback(AudioSourceSchema, 'invalid', 'none');
      expect(result).toBe('none');
    });
  });
});
