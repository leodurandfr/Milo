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

export const AudioSourceSchema = z.enum([
  'none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast'
]);

export const PluginStateSchema = z.enum([
  'starting', 'ready', 'connected', 'error'
]);

// Metadata varies by source, so we use a flexible schema
export const MetadataSchema = z.object({
  // Common fields
  title: z.string().optional(),
  artist: z.string().optional(),
  album: z.string().optional(),
  duration: z.number().optional(),
  position: z.number().optional(),
  is_playing: z.boolean().optional(),
  is_buffering: z.boolean().optional(),
  artwork_url: z.string().optional(),

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
  active_source: AudioSourceSchema,
  plugin_state: PluginStateSchema,
  transitioning: z.boolean(),
  metadata: MetadataSchema.optional().default({}),
  error: z.string().nullable().optional(),
  multiroom_enabled: z.boolean()
}).passthrough();

// === VOLUME ===

export const VolumeClientSchema = z.object({
  volume_db: z.number(),
  offset_db: z.number().default(0),
  mute: z.boolean().default(false),
  online: z.boolean().default(true)  // Renamed from 'available' for consistency with backend
});

export const VolumeZoneSchema = z.object({
  id: z.string(),
  name: z.string(),
  client_ids: z.array(z.string()),
  average_volume_db: z.number().optional(),
  all_muted: z.boolean().optional()
});

export const VolumeStateSchema = z.object({
  mode: z.enum(['direct', 'multiroom']),
  global_volume_db: z.number(),
  global_mute: z.boolean(),
  clients: z.record(z.string(), VolumeClientSchema).default({}),
  zones: z.record(z.string(), VolumeZoneSchema).default({})
});

// === WEBSOCKET EVENTS ===

export const WebSocketMessageSchema = z.object({
  category: z.string(),
  type: z.string(),
  source: z.string().optional(),
  data: z.unknown().optional()
});

export const VolumeEventDataSchema = z.object({
  show_bar: z.boolean().optional(),
  step_mobile_db: z.number().optional(),
  state: VolumeStateSchema.optional()
});

export const PluginEventDataSchema = z.object({
  state: PluginStateSchema.optional(),
  metadata: MetadataSchema.optional()
});

// === API RESPONSES ===

export const ApiResponseSchema = z.object({
  status: z.enum(['success', 'error']),
  message: z.string().optional(),
  error: z.string().optional()
});

export const HealthResponseSchema = z.object({
  status: z.enum(['healthy', 'degraded', 'unhealthy']),
  services: z.record(z.string(), z.object({
    status: z.string(),
    available: z.boolean().optional()
  })).optional()
});

// === DSP ===

export const DspFilterSchema = z.object({
  id: z.string(),
  freq: z.number(),
  gain: z.number(),
  q: z.number(),
  type: z.string(),
  enabled: z.boolean().default(true)
});

export const DspStatusSchema = z.object({
  state: z.enum(['disconnected', 'inactive', 'running', 'paused']),
  sample_rate: z.number().optional(),
  input_peak: z.tuple([z.number(), z.number()]).optional(),
  output_peak: z.tuple([z.number(), z.number()]).optional()
});

/**
 * Response from zone DSP endpoints (PATCH /api/dsp/zone/{zone_id}/...).
 * Backend applies changes to all ONLINE clients and returns status.
 */
export const DspZoneResponseSchema = z.object({
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
  enabled: z.boolean().optional()     // For zone DSP bypass toggle
});

export const DspCompressorSchema = z.object({
  enabled: z.boolean(),
  threshold: z.number(),
  ratio: z.number(),
  attack: z.number(),
  release: z.number(),
  makeup_gain: z.number()
});

export const DspLoudnessSchema = z.object({
  enabled: z.boolean(),
  high_boost: z.number(),
  low_boost: z.number()
});

export const DspPresetSchema = z.object({
  id: z.string(),
  name: z.string(),
  gains: z.array(z.number())
});

export const DspPresetsResponseSchema = z.object({
  presets: z.array(DspPresetSchema),
  manual_gains: z.array(z.number()),
  active_preset: z.string().nullable()
});

// === MULTIROOM / CLIENT REGISTRY ===

/**
 * Registered client metadata from ClientRegistryService.
 * Matches backend Client model (backend/core/multiroom/models.py).
 */
export const RegisteredClientSchema = z.object({
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
export const MultiroomStateSchema = z.object({
  clients: z.record(z.string(), RegisteredClientSchema),
  zones: z.record(z.string(), z.object({
    id: z.string(),
    name: z.string(),
    client_ids: z.array(z.string()),
    dsp_settings: z.object({}).passthrough().optional(),
    online_client_count: z.number().optional(),
    has_subwoofer: z.boolean().optional(),
    crossover_enabled: z.boolean().optional()
  }).passthrough())
});

// === RADIO ===

export const RadioStationSchema = z.object({
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

export const PodcastSchema = z.object({
  uuid: z.string(),
  name: z.string(),
  description: z.string().optional(),
  imageUrl: z.string().optional(),
  author: z.string().optional(),
  language: z.string().optional()
});

export const PodcastEpisodeSchema = z.object({
  uuid: z.string(),
  name: z.string(),
  description: z.string().optional(),
  audioUrl: z.string(),
  duration: z.number().optional(),
  datePublished: z.number().optional(),
  imageUrl: z.string().optional(),
  podcast: PodcastSchema.optional(),
  playback_progress: z.object({
    position: z.number(),
    duration: z.number(),
    lastPlayed: z.number().optional()
  }).optional()
});

// === SETTINGS ===

export const SettingsSchema = z.object({
  language: z.string().optional(),
  volume: z.object({
    max_db: z.number().optional(),
    default_db: z.number().optional(),
    step_mobile_db: z.number().optional()
  }).optional(),
  screen: z.object({
    brightness: z.number().optional(),
    timeout: z.number().optional(),
    screensaver: z.string().optional()
  }).optional(),
  dock: z.object({
    apps: z.array(z.string()).optional()
  }).optional()
}).passthrough();

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

/**
 * Validate and return data with fallback
 * @param {z.ZodSchema} schema
 * @param {unknown} data
 * @param {T} fallback
 * @returns {T}
 */
export function validateWithFallback(schema, data, fallback) {
  const result = schema.safeParse(data);
  return result.success ? result.data : fallback;
}
