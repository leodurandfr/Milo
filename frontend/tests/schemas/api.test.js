// frontend/tests/schemas/api.test.js
/**
 * The API schemas are *resilience* schemas, not validators: every field carries
 * a `.catch()` default so a malformed field degrades to a sane value instead of
 * rejecting the whole payload — losing one field beats freezing the UI on a
 * stale state.
 *
 * These tests pin that contract (coercion, not rejection) plus the enum
 * vocabularies, which are shared with the backend.
 */
import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import {
  SystemStateSchema,
  VolumeStateSchema,
  SnapcastServerConfigSchema,
  SnapcastCapabilitiesSchema,
  validateSchema,
} from '@/schemas/api';
import { ALL_AUDIO_SOURCES } from '@/constants/audioSources';

const VALID_SYSTEM_STATE = {
  active_source: 'spotify',
  source_state: 'active',
  transitioning: false,
  metadata: { title: 'Song', artist: 'Artist' },
  multiroom_enabled: true,
  equalizer_effects_enabled: true,
};

const VALID_VOLUME_STATE = {
  mode: 'multiroom',
  global_volume_db: -25.5,
  global_mute: false,
  volume_control: true,
  any_volume_control: true,
  clients: { 'dc:a6:32:7e:d3:43': { volume_db: -20, offset_db: 0, mute: false, available: true } },
  zones: { z1: { id: 'z1', name: 'Zone 1', client_ids: ['dc:a6:32:7e:d3:43'] } },
};

describe('SystemStateSchema', () => {
  it('parses a complete state', () => {
    const result = SystemStateSchema.safeParse(VALID_SYSTEM_STATE);

    expect(result.success).toBe(true);
    expect(result.data.active_source).toBe('spotify');
    expect(result.data.metadata.title).toBe('Song');
  });

  it('defaults metadata to an empty object when absent', () => {
    const { active_source, source_state, transitioning, multiroom_enabled } = VALID_SYSTEM_STATE;
    const result = SystemStateSchema.safeParse({
      active_source, source_state, transitioning, multiroom_enabled,
    });

    expect(result.success).toBe(true);
    expect(result.data.metadata).toEqual({});
  });

  it('coerces an unknown source to none instead of rejecting', () => {
    const result = SystemStateSchema.safeParse({ ...VALID_SYSTEM_STATE, active_source: 'gramophone' });

    expect(result.success).toBe(true);
    expect(result.data.active_source).toBe('none');
    // The rest of the payload survives the coercion.
    expect(result.data.source_state).toBe('active');
  });

  it('coerces an unknown source_state to ready', () => {
    const result = SystemStateSchema.safeParse({ ...VALID_SYSTEM_STATE, source_state: 'levitating' });

    expect(result.success).toBe(true);
    expect(result.data.source_state).toBe('ready');
  });

  it('coerces non-boolean flags to false', () => {
    const result = SystemStateSchema.safeParse({
      ...VALID_SYSTEM_STATE,
      transitioning: 'yes',
      multiroom_enabled: 1,
    });

    expect(result.data.transitioning).toBe(false);
    expect(result.data.multiroom_enabled).toBe(false);
  });

  it('drops a metadata payload of the wrong type rather than blocking the update', () => {
    const result = SystemStateSchema.safeParse({ ...VALID_SYSTEM_STATE, metadata: 'not an object' });

    expect(result.success).toBe(true);
    expect(result.data.metadata).toEqual({});
  });

  it('accepts nulls for metadata fields the backend could not resolve', () => {
    // A CD whose MusicBrainz lookup failed sends album/artist/year as null.
    const result = SystemStateSchema.safeParse({
      ...VALID_SYSTEM_STATE,
      metadata: { title: 'Track 1', artist: null, album: null, duration: null },
    });

    expect(result.success).toBe(true);
    expect(result.data.metadata.artist).toBeNull();
  });

  it('passes through source-specific metadata fields', () => {
    const result = SystemStateSchema.safeParse({
      ...VALID_SYSTEM_STATE,
      metadata: { station_id: 's1', track_title: 'So What', custom_field: 42 },
    });

    expect(result.data.metadata.station_id).toBe('s1');
    expect(result.data.metadata.custom_field).toBe(42);
  });

  it('accepts every canonical audio source', () => {
    for (const source of ['none', ...ALL_AUDIO_SOURCES]) {
      const result = SystemStateSchema.safeParse({ ...VALID_SYSTEM_STATE, active_source: source });
      expect(result.data.active_source, `${source} was coerced away`).toBe(source);
    }
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
    const result = validateSchema(SystemStateSchema, VALID_SYSTEM_STATE, 'test');

    expect(result.success).toBe(true);
    expect(result.data.active_source).toBe('spotify');
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
  const HERE = dirname(fileURLToPath(import.meta.url));
  const AUDIO_STATE_PATH = resolve(HERE, '../../../backend/core/models/audio_state.py');

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
