// frontend/src/schemas/ws.js
/**
 * Zod schemas for WebSocket event payloads, consumed via
 * `parsedOn(category, type, schema, handler)` — handlers MUST NOT read
 * `event.data.x` directly. Each entry maps a `(category, type)` pair
 * (key format: `category.type`) to the schema for its `event.data` shape.
 *
 * Admission rule — a `(category, type)` earns an entry here IFF:
 *   (a) more than one app consumes it (e.g. Milo-Mac + frontend), or
 *   (b) it has already caused a shape bug.
 * This partiality is by design, NOT an unfinished migration: pairs meeting
 * neither test stay on raw `on(...)` dispatch — schematizing them would be
 * churn with no payoff. Do NOT bulk-schematize the remaining pairs.
 *
 * This registry is not the only validation seam. The two highest-traffic
 * payloads are already validated by their own schemas in unifiedAudioStore.js
 * — `full_state` (SystemStateSchema) and `volume_changed` state
 * (VolumeStateSchema) — so they need no entry here.
 *
 * To add a schema (when the rule above is met): declare it below, expose it
 * via `wsEventRegistry`, and switch the consumer to `parsedOn(...)`.
 *
 * Backend-side shapes are the typed WsEvent classes in
 * backend/core/models/ws_events.py — each entry names its class. Cross-checked
 * field-by-field against those models on 2026-07-02 (WS contract phase 6).
 */
import { z } from 'zod';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

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

// Backend: backend/api/programs.py _create_background_update — progress events
// carry {program|mac_id, status}; completion events carry {program|mac_id, success}.
const ProgramUpdateProgressSchema = z.object({
  program: z.string(),
  status: z.string(),
});
const ProgramUpdateCompleteSchema = z.object({
  program: z.string(),
  success: z.boolean(),
});
const SatelliteUpdateProgressSchema = z.object({
  mac_id: z.string(),
  status: z.string(),
});
const SatelliteUpdateCompleteSchema = z.object({
  mac_id: z.string(),
  success: z.boolean(),
});

// Backend: backend/hardware/fan.py FanController.get_status() — config + telemetry.
const FanStatusSchema = z.object({
  available: z.boolean(),
  enabled: z.boolean(),
  mode: z.enum(['auto', 'manual', 'target']),
  manual_percent: z.number(),
  target_temp_c: z.number(),
  curve: z.array(z.object({ temp_c: z.number(), percent: z.number() })),
  temp_c: z.number(),
  rpm: z.number(),
  pwm_percent: z.number(),
});

export const wsEventRegistry = {
  // Backend: EqualizerStateChanged → {state: CamillaDspState.value}.
  'equalizer.state_changed': z.object({
    state: CamillaDspStateSchema,
  }),
  // Backend: EqualizerLevels — pushed at ~4 Hz while an EQ view holds the
  // levels-monitor keepalive.
  'equalizer.levels': z.object({
    available: z.boolean(),
    output_peak: z.array(z.number()),
  }),
  // Backend: EqualizerCompressorChanged (equalizer/service.py self._compressor).
  'equalizer.compressor_changed': CompressorPayloadSchema,
  // Backend: EqualizerLoudnessChanged (equalizer/service.py self._loudness).
  'equalizer.loudness_changed': LoudnessPayloadSchema,
  // Backend: MultiroomEqualizerChanged — equalizer_settings is a PARTIAL wire
  // dict by design, two producers:
  //  - multiroom/client_registry.py registry forward → full record
  //    (to_wire_dict): enabled/filters/compressor/loudness/active_preset/mono/
  //    custom_gains.
  //  - equalizer/multiroom_service.py _apply_partial_update → only the changed
  //    sub-object ({filters,active_preset} | {compressor} | {loudness} | {mono}).
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
  // Backend: MultiroomCrossoverChanged — single canonical zone shape.
  'multiroom.crossover_changed': z.object({
    zone_id: z.string(),
    crossover_enabled: z.boolean(),
    crossover_frequency: z.number(),
  }),
  // Backend: FanConfigChanged/FanStatusChanged (payload = FanController.
  // get_status(), config edit vs telemetry tick). Same shape; the store routes
  // config vs telemetry to separate slices.
  'settings.fan_config_changed': FanStatusSchema,
  'settings.fan_status_changed': FanStatusSchema,
  // Backend: the ProgramsProgressEvent/ProgramsCompleteEvent subclasses —
  // local program + satellite update progress/completion (updatesStore).
  'programs.program_update_progress': ProgramUpdateProgressSchema,
  'programs.program_update_complete': ProgramUpdateCompleteSchema,
  'programs.satellite_update_progress': SatelliteUpdateProgressSchema,
  'programs.satellite_update_complete': SatelliteUpdateCompleteSchema,
  'programs.satellite_app_update_progress': SatelliteUpdateProgressSchema,
  'programs.satellite_app_update_complete': SatelliteUpdateCompleteSchema,
  'programs.satellite_camilladsp_update_progress': SatelliteUpdateProgressSchema,
  'programs.satellite_camilladsp_update_complete': SatelliteUpdateCompleteSchema,
  // Backend: SourcePositionUpdate (audio_source.py broadcast_position_update).
  // Position and duration are in milliseconds.
  'source.position_update': z.object({
    source: z.enum(['none', ...ALL_AUDIO_SOURCES]),
    position: z.number(),
    duration: z.number(),
  }),
};
