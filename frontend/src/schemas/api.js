// frontend/src/schemas/api.js
/**
 * Zod schemas for API response validation
 *
 * These schemas provide runtime type-safety for API responses,
 * helping catch backend/frontend contract mismatches early.
 *
 * Usage:
 *   import { SystemStateSchema, VolumeStateSchema } from '@/schemas/api';
 *   const result = SystemStateSchema.safeParse(response.data);
 *   if (!result.success) logger.warn('api', 'Invalid response', result.error);
 */
import { z } from 'zod';

// === AUDIO SOURCE & STATE ===

const AudioSourceSchema = z.enum([
  'none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast', 'airplay', 'cd'
]);

const SourceStateSchema = z.enum([
  'starting', 'waiting', 'active', 'error'
]);

// Metadata varies by source, so we use a flexible schema
const MetadataSchema = z.object({
  // Common fields
  title: z.string().optional(),
  artist: z.string().optional(),
  album: z.string().optional(),
  duration: z.number().optional(),
  position: z.number().optional(),
  is_playing: z.boolean().optional(),
  is_buffering: z.boolean().optional(),
  album_art_url: z.string().nullable().optional(),

  // Radio-specific
  station_name: z.string().optional(),
  station_id: z.string().optional(),

  // Podcast-specific
  episode_uuid: z.string().optional(),
  podcast_name: z.string().optional(),
  playback_speed: z.number().optional()
}).passthrough(); // Allow additional fields

export const SystemStateSchema = z.object({
  active_source: AudioSourceSchema.catch('none'),
  source_state: SourceStateSchema.catch('waiting'),
  transitioning: z.boolean().catch(false),
  metadata: MetadataSchema.optional().default({}),
  error: z.string().nullable().optional().catch(null),
  multiroom_enabled: z.boolean().catch(false),
  equalizer_effects_enabled: z.boolean().catch(false)
}).passthrough();

// === VOLUME ===

const VolumeClientSchema = z.object({
  volume_db: z.number(),
  offset_db: z.number().default(0),
  mute: z.boolean().default(false),
  online: z.boolean().default(true)  // Renamed from 'available' for consistency with backend
});

const VolumeZoneSchema = z.object({
  id: z.string(),
  name: z.string(),
  client_ids: z.array(z.string()),
  average_volume_db: z.number().optional(),
  all_muted: z.boolean().optional(),
  all_external_volume: z.boolean().optional().default(false)
});

export const VolumeStateSchema = z.object({
  mode: z.enum(['direct', 'multiroom']).catch('direct'),
  global_volume_db: z.number().catch(-45.0),
  global_mute: z.boolean().catch(false),
  volume_control: z.boolean().catch(true),  // False = DAC mode (external amp)
  clients: z.record(z.string(), VolumeClientSchema).catch({}),
  zones: z.record(z.string(), VolumeZoneSchema).catch({})
});

// === HELPER FUNCTIONS ===

/**
 * Validate data against a schema with logging
 * @param {z.ZodSchema} schema
 * @param {unknown} data
 * @param {string} context - For error messages
 * @returns {{ success: boolean, data?: T, error?: z.ZodError }}
 */
export function validateSchema(schema, data, context = 'unknown') {
  const result = schema.safeParse(data);
  if (!result.success) {
    // Log validation errors in development
    if (import.meta.env.DEV) {
      console.warn(`Schema validation failed for ${context}:`, result.error.issues);
    }
  }
  return result;
}
