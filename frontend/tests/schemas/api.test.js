// frontend/tests/schemas/api.test.js
import { describe, it, expect } from 'vitest';
import {
  SystemStateSchema,
  VolumeStateSchema,
  validateSchema
} from '@/schemas/api';

describe('API Schemas', () => {
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

  describe('validateSchema helper', () => {
    it('should return success result for valid data', () => {
      const result = validateSchema(SystemStateSchema, {
        active_source: 'spotify',
        source_state: 'active',
        transitioning: false,
        multiroom_enabled: false
      }, 'test');
      expect(result.success).toBe(true);
    });

    it('should return error result for invalid data', () => {
      const result = validateSchema(SystemStateSchema, { active_source: 'invalid' }, 'test');
      expect(result.success).toBe(false);
      expect(result.error).toBeDefined();
    });
  });
});
