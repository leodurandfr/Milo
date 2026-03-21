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
  'none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast', 'airplay'
]);

const PluginStateSchema = z.enum([
  'starting', 'ready', 'connected', 'error'
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
  album_art_url: z.string().optional(),

  // Spotify-specific
  track_uri: z.string().optional(),
  album_uri: z.string().optional(),
  artist_uri: z.string().optional(),

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
  plugin_state: PluginStateSchema.catch('ready'),
  transitioning: z.boolean().catch(false),
  metadata: MetadataSchema.optional().default({}),
  error: z.string().nullable().optional().catch(null),
  multiroom_enabled: z.boolean().catch(false)
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
  all_muted: z.boolean().optional()
});

export const VolumeStateSchema = z.object({
  mode: z.enum(['direct', 'multiroom']).catch('direct'),
  global_volume_db: z.number().catch(-45.0),
  global_mute: z.boolean().catch(false),
  clients: z.record(z.string(), VolumeClientSchema).catch({}),
  zones: z.record(z.string(), VolumeZoneSchema).catch({})
});

// === WEBSOCKET EVENTS ===

const WebSocketMessageSchema = z.object({
  category: z.string(),
  type: z.string(),
  source: z.string().optional(),
  data: z.unknown().optional()
});

const VolumeEventDataSchema = z.object({
  show_bar: z.boolean().optional(),
  step_mobile_db: z.number().optional(),
  state: VolumeStateSchema.optional()
});

const PluginEventDataSchema = z.object({
  state: PluginStateSchema.optional(),
  metadata: MetadataSchema.optional()
});

// === API RESPONSES ===

const ApiResponseSchema = z.object({
  status: z.enum(['success', 'error']),
  message: z.string().optional(),
  error: z.string().optional()
});

const HealthResponseSchema = z.object({
  status: z.enum(['healthy', 'degraded', 'unhealthy']),
  services: z.record(z.string(), z.object({
    status: z.string(),
    available: z.boolean().optional()
  })).optional()
});

// === EQUALIZER ===

const EqualizerFilterSchema = z.object({
  id: z.string(),
  freq: z.number(),
  gain: z.number(),
  q: z.number(),
  type: z.string(),
  enabled: z.boolean().default(true)
});

const EqualizerStatusSchema = z.object({
  state: z.enum(['disconnected', 'inactive', 'running', 'paused']),
  sample_rate: z.number().optional(),
  input_peak: z.tuple([z.number(), z.number()]).optional(),
  output_peak: z.tuple([z.number(), z.number()]).optional()
});

/**
 * Response from zone equalizer endpoints (PATCH /api/equalizer/zone/{zone_id}/...).
 * Backend applies changes to all ONLINE clients and returns status.
 */
const EqualizerZoneResponseSchema = z.object({
  status: z.enum(['success', 'partial', 'error']),
  zone_id: z.string(),
  applied_to: z.array(z.string()),
  offline_clients: z.array(z.string()).nullable().optional(),
  errors: z.array(z.object({
    client_id: z.string(),
    error: z.string()
  })).nullable().optional(),
  // Optional fields for specific endpoint responses
  filter_id: z.string().optional(),  // For zone filter update
  enabled: z.boolean().optional()     // For zone equalizer bypass toggle
});

const EqualizerCompressorSchema = z.object({
  enabled: z.boolean(),
  threshold: z.number(),
  ratio: z.number(),
  attack: z.number(),
  release: z.number(),
  makeup_gain: z.number()
});

const EqualizerLoudnessSchema = z.object({
  enabled: z.boolean(),
  high_boost: z.number(),
  low_boost: z.number()
});

const EqualizerPresetSchema = z.object({
  id: z.string(),
  name: z.string(),
  gains: z.array(z.number())
});

const EqualizerPresetsResponseSchema = z.object({
  presets: z.array(EqualizerPresetSchema),
  custom_gains: z.array(z.number()),
  active_preset: z.string().nullable()
});

// === MULTIROOM / CLIENT REGISTRY ===

/**
 * Registered client metadata from ClientRegistryService.
 * Matches backend Client model (backend/core/multiroom/models.py).
 */
const RegisteredClientSchema = z.object({
  mac_id: z.string(),
  name: z.string(),
  ip: z.string(),
  online: z.boolean().default(false),
  zone_id: z.string().nullable(),
  speaker_type: z.enum(['satellite', 'bookshelf', 'tower', 'subwoofer']).default('bookshelf')
});

/**
 * Response from GET /api/multiroom/state endpoint.
 */
const MultiroomStateSchema = z.object({
  clients: z.record(z.string(), RegisteredClientSchema),
  zones: z.record(z.string(), z.object({
    id: z.string(),
    name: z.string(),
    client_ids: z.array(z.string()),
    equalizer_settings: z.object({}).passthrough().optional(),
    online_client_count: z.number().optional(),
    has_subwoofer: z.boolean().optional(),
    crossover_enabled: z.boolean().optional()
  }).passthrough())
});

// === RADIO ===

const RadioStationSchema = z.object({
  id: z.string(),
  name: z.string(),
  url: z.string(),
  favicon: z.string().optional(),
  country: z.string().optional(),
  language: z.string().optional(),
  tags: z.string().optional(),
  codec: z.string().optional(),
  bitrate: z.number().optional(),
  is_custom: z.boolean().optional()
});

// === PODCAST ===

const PodcastSchema = z.object({
  uuid: z.string(),
  name: z.string(),
  description: z.string().optional(),
  image_url: z.string().optional(),
  author: z.string().optional(),
  language: z.string().optional()
});

const PodcastEpisodeSchema = z.object({
  uuid: z.string(),
  name: z.string(),
  description: z.string().optional(),
  audio_url: z.string(),
  duration: z.number().optional(),
  date_published: z.number().optional(),
  image_url: z.string().optional(),
  podcast: PodcastSchema.optional(),
  playback_progress: z.object({
    position: z.number(),
    duration: z.number(),
    last_played: z.number().optional()
  }).optional()
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

