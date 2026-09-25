// frontend/tests/schemas/api.test.js
/**
 * Two kinds of schema live in schemas/api.js, on purpose:
 *
 *  - the audio state is STRICT: its vocabularies are frozen by the spec, and a
 *    state that does not parse is refused whole — a coerced default is how a
 *    renamed field became a silent "nothing is playing". Because refusal is
 *    total, the schema is held equal to the backend model it mirrors
 *    (core/models/audio_wire.py), read from source, so drift fails here and
 *    not on the kiosk.
 *  - the others are *resilience* schemas: every field carries a `.catch()`
 *    default so a malformed field degrades to a sane value instead of rejecting
 *    the whole payload. Their tests pin coercion, not rejection.
 */
import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import {
  AudioStateSchema,
  AvailabilityReasonSchema,
  PhaseSchema,
  PositionAnchorSchema,
  ServiceStateSchema,
  VolumeStateSchema,
  SnapcastServerConfigSchema,
  SnapcastCapabilitiesSchema,
  validateSchema,
} from '@/schemas/api';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';
import { makeAudioState, makeSession } from '../helpers/audioState';

const HERE = dirname(fileURLToPath(import.meta.url));
const MODELS_DIR = resolve(HERE, '../../../backend/core/models');

const VALID_STATE = makeAudioState({
  source: 'spotify',
  service: 'running',
  session: makeSession({ title: 'Song', artist: 'Artist', position: { ms: 1000, at: 1_750_000_000.5, rate: 1 } }),
  controls: ['pause', 'seek'],
  multiroom_enabled: true,
  equalizer_effects_enabled: true,
});

/** The annotated fields of one pydantic class, in declaration order. */
function modelFields(source, className) {
  const body = new RegExp(`^class ${className}\\(BaseModel\\):\\n((?:    .*\\n|\\n)*)`, 'm').exec(source);
  if (!body) throw new Error(`class ${className} not found — the extractor is broken`);
  const fields = [...body[1].matchAll(/^    ([a-z_]+):/gm)].map(m => m[1]);
  if (fields.length === 0) throw new Error(`class ${className} has no fields — the extractor is broken`);
  return fields;
}

/** The string values of a `class X(str, Enum)` or of a `X = Literal[...]`. */
function enumValues(source, name) {
  const cls = new RegExp(`^class ${name}\\(str, Enum\\):\\n((?:    .*\\n|\\n)*)`, 'm').exec(source);
  const literal = new RegExp(`^${name} = Literal\\[([^\\]]*)\\]`, 'm').exec(source);
  const text = cls ? cls[1].replace(/"""[\s\S]*?"""/, '') : literal?.[1];
  if (!text) throw new Error(`${name} not found — the extractor is broken`);
  const values = [...text.matchAll(cls ? /=\s*"([a-z_]+)"/g : /"([a-z_]+)"/g)].map(m => m[1]);
  if (values.length < 2) throw new Error(`${name} has fewer than two values — the extractor is broken`);
  return values;
}

const VALID_VOLUME_STATE = {
  mode: 'multiroom',
  global_volume_db: -25.5,
  global_mute: false,
  volume_control: true,
  any_volume_control: true,
  clients: { 'dc:a6:32:7e:d3:43': { volume_db: -20, offset_db: 0, mute: false, available: true } },
  zones: { z1: { id: 'z1', name: 'Zone 1', client_ids: ['dc:a6:32:7e:d3:43'] } },
};

describe('AudioStateSchema', () => {
  it('parses a complete state', () => {
    const result = AudioStateSchema.safeParse(VALID_STATE);

    expect(result.success).toBe(true);
    expect(result.data).toEqual(VALID_STATE);
  });

  it('refuses an unknown source, service or phase rather than coercing it', () => {
    const cases = [
      { ...VALID_STATE, source: 'gramophone' },
      { ...VALID_STATE, service: 'levitating' },
      { ...VALID_STATE, session: { ...VALID_STATE.session, phase: 'stopped' } },
      { ...VALID_STATE, availability: { ...VALID_STATE.availability, radio: 'on_holiday' } },
    ];
    for (const state of cases) expect(AudioStateSchema.safeParse(state).success).toBe(false);
  });

  it('refuses a state missing a key', () => {
    for (const key of Object.keys(VALID_STATE)) {
      const { [key]: _missing, ...partial } = VALID_STATE;
      expect(AudioStateSchema.safeParse(partial).success, key).toBe(false);
    }
  });

  it("keeps a source's details it does not type", () => {
    const details = { kind: 'radio', station: { id: 's1', name: 'FIP' }, track: null };
    const result = AudioStateSchema.safeParse({ ...VALID_STATE, details });

    expect(result.data.details).toEqual(details);
  });

  it('accepts every canonical audio source', () => {
    for (const source of ALL_AUDIO_SOURCES) {
      expect(AudioStateSchema.safeParse({ ...VALID_STATE, source }).success).toBe(true);
    }
  });
});

describe('AudioStateSchema ↔ backend core/models/audio_wire.py', () => {
  // A strict schema refuses the whole state on one unknown key or value, so a
  // field added or renamed on the backend alone blanks the kiosk. Read the
  // backend's own declarations, never a copy of them.
  const wire = readFileSync(resolve(MODELS_DIR, 'audio_wire.py'), 'utf8');
  const session = readFileSync(resolve(MODELS_DIR, 'session.py'), 'utf8');

  const keysOf = (schema) => Object.keys(schema.shape).sort();
  const shape = AudioStateSchema.shape;

  it('declares exactly the fields of AudioState and of each nested model', () => {
    expect(keysOf(AudioStateSchema)).toEqual(modelFields(wire, 'AudioState').sort());
    expect(keysOf(shape.session.unwrap())).toEqual(modelFields(wire, 'SessionView').sort());
    expect(keysOf(shape.resume.unwrap())).toEqual(modelFields(wire, 'ResumeView').sort());
    expect(keysOf(shape.service_error.unwrap())).toEqual(modelFields(wire, 'ServiceError').sort());
    expect(keysOf(shape.availability)).toEqual(modelFields(wire, 'Availability').sort());
    expect(keysOf(PositionAnchorSchema)).toEqual(modelFields(wire, 'PositionAnchor').sort());
  });

  it('speaks the vocabularies the backend declares', () => {
    expect([...ServiceStateSchema.options].sort()).toEqual(enumValues(session, 'ServiceState').sort());
    expect([...PhaseSchema.options].sort()).toEqual(enumValues(session, 'Phase').sort());
    expect([...AvailabilityReasonSchema.options].sort()).toEqual(enumValues(wire, 'AvailabilityReason').sort());
  });
});

describe('VolumeStateSchema', () => {
  it('parses a complete multiroom state', () => {
    const result = VolumeStateSchema.safeParse(VALID_VOLUME_STATE);

    expect(result.success).toBe(true);
    expect(result.data.global_volume_db).toBe(-25.5);
    expect(result.data.zones.z1.client_ids).toEqual(['dc:a6:32:7e:d3:43']);
  });

  it('defaults clients and zones to empty maps', () => {
    const result = VolumeStateSchema.safeParse({
      mode: 'direct', global_volume_db: -30, global_mute: false,
    });

    expect(result.success).toBe(true);
    expect(result.data.clients).toEqual({});
    expect(result.data.zones).toEqual({});
  });

  it('fills per-client defaults for fields the backend omitted', () => {
    const result = VolumeStateSchema.safeParse({
      ...VALID_VOLUME_STATE,
      clients: { 'dc:a6:32:7e:d3:43': { volume_db: -20 } },
    });

    expect(result.data.clients['dc:a6:32:7e:d3:43']).toEqual({
      volume_db: -20, offset_db: 0, mute: false, available: true,
    });
  });

  it('coerces an unknown mode to direct', () => {
    const result = VolumeStateSchema.safeParse({ ...VALID_VOLUME_STATE, mode: 'surround' });

    expect(result.success).toBe(true);
    expect(result.data.mode).toBe('direct');
  });

  it('drops a malformed client map without losing the scalar fields', () => {
    const result = VolumeStateSchema.safeParse({
      ...VALID_VOLUME_STATE,
      clients: { 'dc:a6:32:7e:d3:43': { volume_db: 'loud' } },
    });

    expect(result.success).toBe(true);
    expect(result.data.clients).toEqual({});
    expect(result.data.global_volume_db).toBe(-25.5);
  });

  it('defaults the volume-control flags to true (volume managed by Milō)', () => {
    const result = VolumeStateSchema.safeParse({
      mode: 'direct', global_volume_db: -30, global_mute: false,
    });

    expect(result.data.volume_control).toBe(true);
    expect(result.data.any_volume_control).toBe(true);
  });
});

describe('Snapcast schemas', () => {
  it('falls back to the documented defaults on a malformed server config', () => {
    const result = SnapcastServerConfigSchema.safeParse({
      buffer_ms: 'lots', chunk_ms: null, codec: 42, sampleformat: undefined,
      snapclient_buffer_time: 'soon',
    });

    expect(result.success).toBe(true);
    expect(result.data).toEqual({
      buffer_ms: 1000, chunk_ms: 20, codec: 'flac', sampleformat: '48000:32:2',
      snapclient_buffer_time: 80,
    });
  });

  it('keeps a valid server config untouched', () => {
    const config = {
      buffer_ms: 500, chunk_ms: 10, codec: 'opus', sampleformat: '44100:16:2',
      snapclient_buffer_time: 40,
    };

    expect(SnapcastServerConfigSchema.parse(config)).toEqual(config);
  });

  it('empties the capability lists rather than rejecting a bad payload', () => {
    const result = SnapcastCapabilitiesSchema.safeParse({ codecs: 'flac', presets: [{ id: 'x' }] });

    expect(result.success).toBe(true);
    expect(result.data.codecs).toEqual([]);
    expect(result.data.presets).toEqual([]);
  });
});

describe('validateSchema', () => {
  it('reports success and returns the parsed data', () => {
    const result = validateSchema(AudioStateSchema, VALID_STATE, 'test');

    expect(result.success).toBe(true);
    expect(result.data.source).toBe('spotify');
  });

  it('reports failure with the issues when the payload cannot be salvaged', () => {
    // validateSchema warns on the console in DEV; that is the behaviour under
    // test, so silence it rather than let it pollute the run.
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});

    // Every field carries .catch(), so only a non-object is unsalvageable.
    const result = validateSchema(SnapcastServerConfigSchema, 'nope', 'test');

    expect(result.success).toBe(false);
    expect(result.error.issues.length).toBeGreaterThan(0);
    expect(warn).toHaveBeenCalled();
    warn.mockRestore();
  });
});

describe('ALL_AUDIO_SOURCES ↔ backend AudioSource enum', () => {
  // The Zod source enums are built from this constant: a source added on the
  // backend but not here is coerced to 'none' on every state update, which
  // reads as "the new source silently does nothing".
  const AUDIO_STATE_PATH = resolve(MODELS_DIR, 'audio_state.py');

  it('lists exactly the backend enum values, minus the none sentinel', () => {
    const source = readFileSync(AUDIO_STATE_PATH, 'utf8');
    const enumBody = /class AudioSource\(Enum\):(.*?)\n\n\nclass /s.exec(source);
    expect(enumBody, 'AudioSource enum not found — the extractor is broken').not.toBeNull();

    const backendSources = [...enumBody[1].matchAll(/^\s+[A-Z_]+\s*=\s*"([a-z_]+)"/gm)]
      .map(m => m[1])
      .filter(value => value !== 'none');
    expect(backendSources.length).toBeGreaterThan(5);

    expect([...ALL_AUDIO_SOURCES].sort()).toEqual([...backendSources].sort());
  });
});
