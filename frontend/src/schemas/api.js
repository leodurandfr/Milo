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
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

// === AUDIO SOURCE & STATE ===

const AudioSourceSchema = z.enum(['none', ...ALL_AUDIO_SOURCES]);

const SourceStateSchema = z.enum([
  'starting', 'ready', 'active', 'error'
]);

const NetworkUnavailableSchema = z.enum(['no_network', 'no_internet']);

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

  // AirPlay-specific: artwork pixel width, used to gate the rich player
  // on cover quality (browser audio ships tiny favicons / app icons).
  album_art_width: z.number().nullable().optional(),

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
  source_state: SourceStateSchema.catch('ready'),
  transitioning: z.boolean().catch(false),
  // .catch({}) safety net: an unexpected metadata shape must never block
  // a SystemState update — losing the metadata is preferable to freezing
  // the UI on a stale source_state.
  metadata: MetadataSchema.catch({}).optional().default({}),
  error: z.string().nullable().optional().catch(null),
  multiroom_enabled: z.boolean().catch(false),
  equalizer_effects_enabled: z.boolean().catch(false),
  // Why the active source cannot work right now, or null when it can. The
  // backend already crossed the NetworkManager level with the source's own
  // requirement, so null here means "nothing to report", never "unknown".
  network_unavailable: NetworkUnavailableSchema.nullable().optional().catch(null)
}).passthrough();

// === VOLUME ===

const VolumeClientSchema = z.object({
  volume_db: z.number(),
  offset_db: z.number().default(0),
  mute: z.boolean().default(false),
  available: z.boolean().default(true)  // matches ClientVolume.to_dict() on the backend
});

const VolumeZoneSchema = z.object({
  id: z.string(),
  name: z.string(),
  client_ids: z.array(z.string()),
  average_volume_db: z.number().optional(),
  all_muted: z.boolean().optional()
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

// === SNAPCAST ===

// Backend: backend/core/multiroom/snapcast.py::get_server_config + route adds
// snapclient_buffer_time (defaults to 80 from settings). Flat, and the same
// shape PUT /server-config consumes — read and write agree on one body.
export const SnapcastServerConfigSchema = z.object({
  buffer_ms: z.number().int().catch(1000),
  chunk_ms: z.number().int().catch(20),
  codec: z.string().catch('flac'),
  sampleformat: z.string().catch('48000:32:2'),
  snapclient_buffer_time: z.number().int().catch(80),
});

// Backend: backend/core/multiroom/routes.py GET /server-config `capabilities`
// — codec whitelist + use-case presets (SUPPORTED_CODECS /
// NETWORK_PRESETS in snapcast.py). Single source for the UI options.
export const SnapcastCapabilitiesSchema = z.object({
  codecs: z.array(z.string()).catch([]),
  presets: z.array(z.object({
    id: z.string(),
    config: z.object({
      buffer_ms: z.number().int(),
      codec: z.string(),
      chunk_ms: z.number().int(),
      snapclient_buffer_time: z.number().int(),
    }),
  })).catch([]),
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
