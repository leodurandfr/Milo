// frontend/src/schemas/ws.js
/**
 * Zod schemas for WebSocket event payloads.
 *
 * Each entry maps a `(category, type)` pair (key format: `category.type`) to
 * the Zod schema describing the expected `event.data` shape. Handlers consume
 * the validated payload via `parsedOn(category, type, schema, handler)` — they
 * MUST NOT read `event.data.x` directly.
 *
 * The registry is intentionally partial: only fautive pairs (where the
 * frontend used to read `event.data.x` with dual-shape fallbacks) are
 * schematized. Other pairs continue to dispatch raw `event` via `on(...)`
 * until a future PR migrates them.
 *
 * To add a new schema: declare it below, expose it via `wsEventRegistry`,
 * and switch the consumer to `parsedOn('category', 'type', schema, handler)`.
 */
import { z } from 'zod';

// Backend: backend/core/equalizer/service.py — CamillaDspState enum.
const CamillaDspStateSchema = z.enum([
  'disconnected', 'inactive', 'running', 'paused',
]);

// Backend: backend/core/equalizer/service.py self._compressor dict.
const CompressorPayloadSchema = z.object({
  enabled: z.boolean(),
  threshold: z.number(),
  ratio: z.number(),
  attack: z.number(),
  release: z.number(),
  makeup_gain: z.number(),
});

// Backend: backend/core/equalizer/service.py self._loudness dict.
const LoudnessPayloadSchema = z.object({
  enabled: z.boolean(),
  low_boost: z.number(),
  high_boost: z.number(),
});

export const wsEventRegistry = {
  // Backend: service.py:306,353 → {state: CamillaDspState.value}.
  'equalizer.state_changed': z.object({
    state: CamillaDspStateSchema,
  }),
  // Backend: service.py:965 → {id: preset_id} (string).
  'equalizer.preset_loaded': z.object({
    id: z.string(),
  }),
  // Backend: service.py:699 → self._compressor.
  'equalizer.compressor_changed': CompressorPayloadSchema,
  // Backend: service.py:784 → self._loudness.
  'equalizer.loudness_changed': LoudnessPayloadSchema,
  // Backend: backend/core/multiroom/crossover.py:371 — canonical zone shape.
  // Other producers in crossover.py emit per-client variants (client_id-keyed)
  // on the same event type; those are not consumed by the frontend and will
  // surface as schema warnings in dev — see the _broadcast_event docstring.
  'multiroom.crossover_changed': z.object({
    zone_id: z.string(),
    crossover_enabled: z.boolean(),
    crossover_frequency: z.number(),
  }),
};
