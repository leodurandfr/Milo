// frontend/tests/schemas/ws.test.js
/**
 * Structural guardrail over the WS payload schemas.
 *
 * Every entry of `wsEventRegistry` is checked against the backend model that
 * produces it (backend/core/models/ws_events.py) — the payloads are derived from
 * those models, never hand-written, so backend drift surfaces here instead of at
 * runtime as a silently dropped event.
 *
 * Nothing is mounted and no markup is involved: this stays green through any UI
 * refactor and only goes red when the wire contract actually moves.
 */
import { describe, it, expect } from 'vitest';
import { wsEventRegistry } from '@/schemas/ws';
import {
  parseWsEventModels,
  samplePayload,
  requiredKeys,
  WS_EVENTS_PATH,
} from '../helpers/wsEventModels';

const backendEvents = parseWsEventModels();
const registryEntries = Object.entries(wsEventRegistry);

describe('WS schema registry ↔ backend ws_events.py', () => {
  it('extracts a non-trivial event surface from the backend', () => {
    // Guards the extractor itself: a broken parse must fail loudly rather than
    // let every check below pass vacuously on an empty map.
    expect(backendEvents.size).toBeGreaterThan(20);
    expect(backendEvents.has('equalizer.levels')).toBe(true);
  });

  it('registry is not empty', () => {
    expect(registryEntries.length).toBeGreaterThan(0);
  });

  describe.each(registryEntries)('%s', (eventKey, schema) => {
    const model = backendEvents.get(eventKey);

    it('is still broadcast by a backend event model', () => {
      // A missing model means the frontend validates an event nobody sends —
      // every `parsedOn` for it is dead code.
      expect(model, `no WsEvent subclass declares ${eventKey} in ${WS_EVENTS_PATH}`)
        .toBeDefined();
    });

    it('accepts the payload the backend model declares', () => {
      const payload = samplePayload(model, schema);
      const result = schema.safeParse(payload);

      expect(
        result.success,
        `${model.className} payload rejected: ${JSON.stringify(result.error?.issues)}`,
      ).toBe(true);
    });

    it('requires nothing the backend does not send', () => {
      const backendFields = new Set(model.fields.map(f => f.name));
      const missing = requiredKeys(schema).filter(key => !backendFields.has(key));

      // A required key the backend never emits would reject every real event.
      expect(missing, `${eventKey} requires fields absent from ${model.className}`).toEqual([]);
    });

    it('covers every field the backend declares', () => {
      const schemaKeys = new Set(Object.keys(schema.shape));
      const uncovered = model.fields
        .map(f => f.name)
        // `source` is the envelope discriminator, carried outside `data` as
        // `origin`; a schema may legitimately ignore it.
        .filter(name => name !== 'source' && !schemaKeys.has(name));

      // Zod strips unknown keys: a new backend field nobody added here is
      // silently invisible to the store that consumes the event.
      expect(uncovered, `${eventKey} ignores fields of ${model.className}`).toEqual([]);
    });

    it('rejects a payload missing a required field', () => {
      const required = requiredKeys(schema);
      if (required.length === 0) return; // fully-optional payload (partial updates)

      for (const key of required) {
        const payload = samplePayload(model, schema);
        delete payload[key];

        expect(schema.safeParse(payload).success, `${eventKey} accepted without ${key}`)
          .toBe(false);
      }
    });

    it('rejects a payload whose types are wrong', () => {
      const payload = samplePayload(model, schema);
      const typed = Object.fromEntries(
        Object.keys(schema.shape).map(key => [key, Symbol.for('not-a-wire-value')]),
      );

      expect(schema.safeParse({ ...payload, ...typed }).success).toBe(false);
    });
  });
});

describe('WS payload semantics', () => {
  it('equalizer.levels keeps output_peak a number array', () => {
    const schema = wsEventRegistry['equalizer.levels'];

    expect(schema.safeParse({ available: true, output_peak: [-12.5, -14] }).success).toBe(true);
    expect(schema.safeParse({ available: true, output_peak: ['-12.5'] }).success).toBe(false);
  });

  it('multiroom.equalizer_changed accepts a partial settings dict', () => {
    // Two producers: a full record forward and a single-key partial update.
    const schema = wsEventRegistry['multiroom.equalizer_changed'];

    expect(schema.safeParse({
      target_type: 'zone',
      target_id: 'z1',
      equalizer_settings: { compressor: { enabled: true, threshold: -20, ratio: 4, attack: 10, release: 100, makeup_gain: 0 } },
    }).success).toBe(true);

    expect(schema.safeParse({
      target_type: 'client',
      target_id: 'dc:a6:32:7e:d3:43',
      equalizer_settings: {},
    }).success).toBe(true);
  });

  it('multiroom.equalizer_changed rejects an unknown target_type', () => {
    const schema = wsEventRegistry['multiroom.equalizer_changed'];

    expect(schema.safeParse({
      target_type: 'group',
      target_id: 'z1',
      equalizer_settings: {},
    }).success).toBe(false);
  });

  it('multiroom.equalizer_changed keeps filters on the freq/type wire shape', () => {
    // The persistence shape (frequency/filter_type) must never reach the wire.
    const schema = wsEventRegistry['multiroom.equalizer_changed'];
    const parsed = schema.safeParse({
      target_type: 'client',
      target_id: 'mac',
      equalizer_settings: {
        filters: [{ id: 'eq_band_00', frequency: 31, filter_type: 'Peaking' }],
      },
    });

    expect(parsed.success).toBe(true);
    expect(parsed.data.equalizer_settings.filters[0]).toEqual({ id: 'eq_band_00' });
  });

  it('source.position_update only accepts a known audio source', () => {
    const schema = wsEventRegistry['source.position_update'];

    expect(schema.safeParse({ source: 'spotify', position: 1000, duration: 2000 }).success).toBe(true);
    expect(schema.safeParse({ source: 'gramophone', position: 1000, duration: 2000 }).success).toBe(false);
  });

  it('settings.fan_*_changed only accepts the three fan modes', () => {
    const base = {
      available: true, enabled: true, mode: 'auto', manual_percent: 50,
      target_temp_c: 55, curve: [{ temp_c: 40, percent: 30 }], temp_c: 42.5,
      rpm: 2400, pwm_percent: 30,
    };
    const schema = wsEventRegistry['settings.fan_status_changed'];

    expect(schema.safeParse(base).success).toBe(true);
    expect(schema.safeParse({ ...base, mode: 'turbo' }).success).toBe(false);
  });
});
