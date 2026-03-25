// frontend/tests/schemas/api.test.js
import { describe, it, expect } from 'vitest';
import {
  SystemStateSchema,
  VolumeStateSchema,
  AudioSourceSchema,
  SourceStateSchema,
  RegisteredClientSchema,
  MultiroomStateSchema,
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

  describe('SourceStateSchema', () => {
    it('should accept valid states', () => {
      const validStates = ['starting', 'waiting', 'active', 'error'];

      validStates.forEach(state => {
        expect(SourceStateSchema.safeParse(state).success).toBe(true);
      });
    });

    it('should reject invalid states', () => {
      expect(SourceStateSchema.safeParse('invalid').success).toBe(false);
      expect(SourceStateSchema.safeParse('READY').success).toBe(false);
    });
  });

  describe('SystemStateSchema', () => {
    it('should validate complete system state', () => {
      const validState = {
        active_source: 'spotify',
        source_state: 'active',
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
        source_state: 'waiting',
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
        source_state: 'waiting',
        transitioning: false,
        multiroom_enabled: false
      };

      const result = SystemStateSchema.safeParse(invalidState);
      expect(result.success).toBe(false);
    });

    it('should reject invalid source_state', () => {
      const invalidState = {
        active_source: 'spotify',
        source_state: 'invalid_state',
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
          client1: { volume_db: -20, offset_db: 0, mute: false, online: true }
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

  describe('RegisteredClientSchema', () => {
    it('should validate registered client', () => {
      const client = {
        mac_id: 'dc:a6:32:7e:d3:43',
        name: 'Living Room',
        ip: '192.168.1.100',
        online: true,
        zone_id: null,
        speaker_type: 'bookshelf'
      };

      const result = RegisteredClientSchema.safeParse(client);
      expect(result.success).toBe(true);
      expect(result.data.mac_id).toBe('dc:a6:32:7e:d3:43');
    });

    it('should provide defaults for optional fields', () => {
      const minimalClient = {
        mac_id: 'local',
        name: 'Main',
        ip: '127.0.0.1',
        zone_id: null
      };

      const result = RegisteredClientSchema.safeParse(minimalClient);
      expect(result.success).toBe(true);
      expect(result.data.online).toBe(false);
      expect(result.data.speaker_type).toBe('bookshelf');
    });

    it('should validate speaker_type enum', () => {
      const validTypes = ['satellite', 'bookshelf', 'tower', 'subwoofer'];

      validTypes.forEach(type => {
        const client = {
          mac_id: 'test',
          name: 'Test',
          ip: '127.0.0.1',
          zone_id: null,
          speaker_type: type
        };
        expect(RegisteredClientSchema.safeParse(client).success).toBe(true);
      });

      // Invalid type should fail
      const invalidClient = {
        mac_id: 'test',
        name: 'Test',
        ip: '127.0.0.1',
        zone_id: null,
        speaker_type: 'invalid_type'
      };
      expect(RegisteredClientSchema.safeParse(invalidClient).success).toBe(false);
    });
  });

  describe('MultiroomStateSchema', () => {
    it('should validate complete multiroom state', () => {
      const state = {
        clients: {
          'dc:a6:32:7e:d3:43': {
            mac_id: 'dc:a6:32:7e:d3:43',
            name: 'Living Room',
            ip: '192.168.1.100',
            online: true,
            zone_id: 'zone-1',
            speaker_type: 'bookshelf'
          },
          'local': {
            mac_id: 'local',
            name: 'Main',
            ip: '127.0.0.1',
            online: true,
            zone_id: 'zone-1',
            speaker_type: 'tower'
          }
        },
        zones: {
          'zone-1': {
            id: 'zone-1',
            name: 'Living Room',
            client_ids: ['local', 'dc:a6:32:7e:d3:43'],
            online_client_count: 2,
            has_subwoofer: false,
            crossover_enabled: false
          }
        }
      };

      const result = MultiroomStateSchema.safeParse(state);
      expect(result.success).toBe(true);
      expect(Object.keys(result.data.clients)).toHaveLength(2);
      expect(Object.keys(result.data.zones)).toHaveLength(1);
    });

    it('should accept empty multiroom state', () => {
      const emptyState = {
        clients: {},
        zones: {}
      };

      const result = MultiroomStateSchema.safeParse(emptyState);
      expect(result.success).toBe(true);
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
