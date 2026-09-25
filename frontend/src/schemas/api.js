// frontend/src/schemas/api.js
/**
 * Zod schemas for API response validation
 *
 * These schemas provide runtime type-safety for API responses,
 * helping catch backend/frontend contract mismatches early.
 *
 * Usage:
 *   import { AudioStateSchema, VolumeStateSchema } from '@/schemas/api';
 *   const result = AudioStateSchema.safeParse(response.data);
 *   if (!result.success) logger.warn('api', 'Invalid response', result.error);
 */
import { z } from 'zod';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

// === AUDIO STATE ===
//
// The audio state as the backend publishes it — the same object in
// GET /api/audio/state, the `source/state` event and system/initial_state
// (docs: "Développeurs : le fil", frozen 2026-09-24; backend
// core/models/audio_wire.py::AudioState).
//
// STRICT, unlike the resilience schemas below: `service` and `phase` are frozen
// enums and a state that does not parse is refused whole (the store keeps the
// last good one and logs it). A permissive .catch() here is how a renamed field
// became a silent "nothing is playing" — the spec forbids it.

const AudioSourceSchema = z.enum(['none', ...ALL_AUDIO_SOURCES]);

export const ServiceStateSchema = z.enum(['stopped', 'starting', 'running', 'failed']);

export const PhaseSchema = z.enum(['loading', 'playing', 'paused', 'connected']);

export const AvailabilityReasonSchema = z.enum([
  'no_network', 'no_internet', 'no_account',
  'no_drive', 'no_disc', 'reading_disc', 'unreadable_disc', 'ejecting',
  'no_storage', 'catalog_unavailable',
]);

/** The playhead: `ms` at the instant `at` (UTC seconds), moving at `rate` while playing. */
export const PositionAnchorSchema = z.object({
  ms: z.number().int(),
  at: z.number(),
  rate: z.number(),
});

const SessionSchema = z.object({
  id: z.string(),
  phase: PhaseSchema,
  title: z.string().nullable(),
  artist: z.string().nullable(),
  album: z.string().nullable(),
  artwork: z.string().nullable(),
  senders: z.array(z.string()),
  duration_ms: z.number().int().nullable(),
  position: PositionAnchorSchema.nullable(),
});

const ResumeSchema = z.object({
  title: z.string().nullable(),
  artist: z.string().nullable(),
  album: z.string().nullable(),
  artwork: z.string().nullable(),
  duration_ms: z.number().int().nullable(),
  position_ms: z.number().int().nullable(),
});

export const AudioStateSchema = z.object({
  source: AudioSourceSchema,
  switching: z.boolean(),
  service: ServiceStateSchema,
  service_error: z.object({
    reason: z.enum(['start_timeout', 'start_failed']),
    message: z.string(),
  }).nullable(),
  availability: z.object(
    Object.fromEntries(ALL_AUDIO_SOURCES.map((source) => [source, AvailabilityReasonSchema.nullable()])),
  ),
  session: SessionSchema.nullable(),
  controls: z.array(z.string()),
  resume: ResumeSchema.nullable(),
  // Typed by `kind` on the backend; not frozen, so read by each source's store
  // for the kind it knows (radio, podcast, music_library, cd, airplay).
  details: z.object({ kind: z.string() }).passthrough().nullable(),
  multiroom_enabled: z.boolean(),
  equalizer_effects_enabled: z.boolean(),
});

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
