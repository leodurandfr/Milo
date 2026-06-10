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

// Backend: backend/core/multiroom/models.py EqFilter.to_wire_dict() — the WS/HTTP
// wire shape (freq/type), NOT the persistence shape (frequency/filter_type).
const EqFilterWireSchema = z.object({
  id: z.string(),
  freq: z.number().optional(),
  gain: z.number().optional(),
  q: z.number().optional(),
  type: z.string().optional(),
  enabled: z.boolean().optional(),
});

// Backend: backend/hardware/fan.py FanController.get_status() — config + telemetry.
const FanStatusSchema = z.object({
  available: z.boolean(),
  enabled: z.boolean(),
  mode: z.enum(['auto', 'manual']),
  manual_percent: z.number(),
  curve: z.array(z.object({ temp_c: z.number(), percent: z.number() })),
  temp_c: z.number(),
  rpm: z.number(),
  pwm_percent: z.number(),
});

export const wsEventRegistry = {
  // Backend: service.py:306,353 → {state: CamillaDspState.value}.
  'equalizer.state_changed': z.object({
    state: CamillaDspStateSchema,
  }),
  // Backend: service.py:699 → self._compressor.
  'equalizer.compressor_changed': CompressorPayloadSchema,
  // Backend: service.py:784 → self._loudness.
  'equalizer.loudness_changed': LoudnessPayloadSchema,
  // Backend: two producers, both → state_machine.broadcast_event('multiroom',
  // 'equalizer_changed', ...):
  //  - client_registry.py set_client_equalizer → full record (to_wire_dict): all
  //    of enabled/filters/compressor/loudness/active_preset/mono/custom_gains.
  //  - multiroom_service.py _apply_partial_update → a partial sub-object
  //    ({filters,active_preset} | {compressor} | {loudness} | {mono}).
  // Hence every equalizer_settings field is optional; filters use the freq/type
  // wire shape (Pitfall #18 — one canonical key).
  'multiroom.equalizer_changed': z.object({
    target_type: z.enum(['client', 'zone']),
    target_id: z.string(),
    equalizer_settings: z.object({
      enabled: z.boolean().optional(),
      filters: z.array(EqFilterWireSchema).optional(),
      compressor: CompressorPayloadSchema.optional(),
      loudness: LoudnessPayloadSchema.optional(),
      active_preset: z.string().nullable().optional(),
      mono: z.boolean().optional(),
      custom_gains: z.array(z.number()).optional(),
    }),
  }),
  // Backend: backend/core/multiroom/crossover.py:371 — canonical zone shape.
  // Other producers in crossover.py emit per-client variants (client_id-keyed)
  // on the same event type; those are not consumed by the frontend and will
  // surface as schema warnings in dev — see the _broadcast_event docstring.
  'multiroom.crossover_changed': z.object({
    zone_id: z.string(),
    crossover_enabled: z.boolean(),
    crossover_frequency: z.number(),
  }),
  // Backend: backend/hardware/fan.py FanController.get_status() — emitted as
  // both fan_config_changed (config edit) and fan_status_changed (telemetry
  // tick). Same shape; the store routes config vs telemetry to separate slices.
  'settings.fan_config_changed': FanStatusSchema,
  'settings.fan_status_changed': FanStatusSchema,
  // Backend: backend/core/audio_source.py:583 — broadcast_position_update.
  // Position and duration are in milliseconds.
  'source.position_update': z.object({
    source: z.enum([
      'none', 'spotify', 'bluetooth', 'mac', 'radio', 'podcast', 'airplay', 'cd',
    ]),
    position: z.number(),
    duration: z.number(),
  }),
};
