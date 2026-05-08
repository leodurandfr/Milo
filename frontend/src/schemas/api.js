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

// Metadata varies by source, so we use a flexible schema.
// String/number fields are nullable: backend emits null for "unknown"
// (e.g. CD with failed MusicBrainz lookup → album/artist/year=null).
const MetadataSchema = z.object({
  // Common fields
  title: z.string().nullable().optional(),
  artist: z.string().nullable().optional(),
  album: z.string().nullable().optional(),
  duration: z.number().nullable().optional(),
  position: z.number().nullable().optional(),
  is_playing: z.boolean().optional(),
  is_buffering: z.boolean().optional(),
  album_art_url: z.string().nullable().optional(),

  // Radio-specific
  station_name: z.string().nullable().optional(),
  station_id: z.string().nullable().optional(),

  // Podcast-specific
  episode_uuid: z.string().nullable().optional(),
  podcast_name: z.string().nullable().optional(),
  playback_speed: z.number().nullable().optional()
}).passthrough(); // Allow additional fields

export const SystemStateSchema = z.object({
  active_source: AudioSourceSchema.catch('none'),
  source_state: SourceStateSchema.catch('waiting'),
  transitioning: z.boolean().catch(false),
  // .catch({}) safety net: an unexpected metadata shape must never block
  // a SystemState update — losing the metadata is preferable to freezing
  // the UI on a stale source_state.
  metadata: MetadataSchema.catch({}).optional().default({}),
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
  any_volume_control: z.boolean().catch(true),  // True if any device manages volume via Milo
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
