// frontend/tests/architecture/presenceKeys.test.js
/**
 * Guardrail over the proof of presence's two enums, declared on the backend
 * and named on screen by PresenceDialog: `system.presence.actions.<action>`
 * says what the gesture is for, `system.presence.gestures.<gesture>` which
 * gesture the unit waits for.
 *
 * The drift is silent: an action the backend adds — a new route that reaches
 * root, guarded under a new `PresenceAction` — with no key here puts the raw
 * dotted path in the dialog that asks the owner to confirm it, so the one
 * screen meant to say what is being confirmed says nothing. A key with no
 * member is dead weight. The other seven locales are held to english.json by
 * tests/i18n/.
 *
 * The unit's own screen names, in a header of its own, the gestures it can
 * wait through. A header the backend no longer reads, or a gesture it no
 * longer declares, would open on that screen a challenge a power cut proves
 * — one it goes down with — or refuse every guarded request from it.
 *
 * Reads `core/models/presence.py` and `core/system/presence.py` as text —
 * nothing of the backend is bundled.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'fs';
import { resolve, dirname } from 'path';
import { fileURLToPath } from 'url';
import english from '@/locales/english.json';
import { GESTURES_HEADER, ON_THE_UNIT_GESTURES } from '@/stores/presenceStore';

const HERE = dirname(fileURLToPath(import.meta.url));
const PRESENCE_PATH = resolve(HERE, '../../../backend/core/models/presence.py');
const SERVICE_PATH = resolve(HERE, '../../../backend/core/system/presence.py');
const source = readFileSync(PRESENCE_PATH, 'utf8');
const service = readFileSync(SERVICE_PATH, 'utf8');

/** The values a `class <name>(str, Enum)` declares, as the backend spells them. */
function enumValues(name) {
  const body = source.match(new RegExp(`class ${name}\\(str, Enum\\):[\\s\\S]*?(?=\\n\\n\\nclass |$)`));
  return body ? [...body[0].matchAll(/^\s{4}[A-Z_]+ = "([a-z_]+)"/gm)].map(m => m[1]).sort() : [];
}

describe('proof of presence keys', () => {
  it('found the members the backend declares', () => {
    // Guards the parse: an empty list would make both checks below vacuous.
    expect(enumValues('PresenceAction').length).toBeGreaterThanOrEqual(4);
    expect(enumValues('Gesture')).toHaveLength(3);
  });

  it('names every action a challenge can be opened for, and no other', () => {
    expect(Object.keys(english.system.presence.actions).sort()).toEqual(enumValues('PresenceAction'));
  });

  it('describes every gesture the unit can wait for, and no other', () => {
    expect(Object.keys(english.system.presence.gestures).sort()).toEqual(enumValues('Gesture'));
  });

  it("names the gestures the unit's own screen waits through as the backend reads them", () => {
    const header = service.match(/^PRESENCE_GESTURES_HEADER = "([^"]+)"$/m);
    expect(header, 'PRESENCE_GESTURES_HEADER not found').not.toBeNull();
    expect(GESTURES_HEADER).toBe(header[1]);
    expect(ON_THE_UNIT_GESTURES.length).toBeGreaterThan(0);
    for (const gesture of ON_THE_UNIT_GESTURES) expect(enumValues('Gesture')).toContain(gesture);
  });
});
