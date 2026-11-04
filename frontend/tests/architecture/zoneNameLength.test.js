// frontend/tests/architecture/zoneNameLength.test.js
/**
 * The zone name field must allow exactly what the backend accepts.
 *
 * `ZoneCreate` and `ZoneUpdate` bound the name with `MAX_ZONE_NAME_LENGTH`;
 * `ZoneEdit.vue` bounds the field with its own `maxlength`, and the field's
 * bound also reaches the kiosk's virtual keyboard. The two once disagreed (16
 * against 15): a 16-character name was typed, the POST or PATCH answered 422,
 * and the zone was silently not created or not renamed.
 *
 * Both sides are read as source, neither is restated here, and each extractor
 * asserts it found a number before comparing.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const DOMAIN_PATH = resolve(HERE, '../../../backend/core/multiroom/models.py');
const API_MODELS_PATH = resolve(HERE, '../../../backend/api/models.py');
const ZONE_EDIT_PATH = resolve(HERE, '../../src/components/settings/categories/multiroom/ZoneEdit.vue');

/** The body of `class Name(...):` up to the next top-level statement. */
function classBody(source, name) {
  const match = new RegExp(`^class ${name}\\b[^\\n]*\\n((?:[ \\t]+[^\\n]*\\n|\\n)*)`, 'm').exec(source);
  return match ? match[1] : '';
}

describe('zone name length', () => {
  it("the zone name field allows exactly the backend's bound", () => {
    const backend = Number(/^MAX_ZONE_NAME_LENGTH\s*=\s*(\d+)/m.exec(readFileSync(DOMAIN_PATH, 'utf8'))?.[1]);
    const input = /<InputText\b[^>]*v-model="zoneName"[^>]*>/.exec(readFileSync(ZONE_EDIT_PATH, 'utf8'))?.[0] ?? '';
    const field = Number(/:maxlength="(\d+)"/.exec(input)?.[1]);

    expect(Number.isFinite(backend) && backend > 0).toBe(true);
    expect(Number.isFinite(field) && field > 0).toBe(true);
    expect(field).toBe(backend);
  });

  it('the bound read is the one the zone models apply', () => {
    const source = readFileSync(API_MODELS_PATH, 'utf8');
    for (const model of ['ZoneCreate', 'ZoneUpdate']) {
      const body = classBody(source, model);
      expect(body.length).toBeGreaterThan(0);
      expect(body).toMatch(/^\s+name:[^\n]*max_length=MAX_ZONE_NAME_LENGTH/m);
    }
  });
});
