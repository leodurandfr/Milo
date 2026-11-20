// frontend/tests/architecture/hardwarePinContract.test.js
/**
 * The Hardware page's GPIO dropdowns offer only what HardwareConfigRequest
 * accepts (utils/hardwarePins.js), which is the backend's rule restated on the
 * frontend. When a peripheral or a pin field joins the request, the page must
 * learn it too: otherwise its dropdowns offer a pin the PUT refuses, or let two
 * enabled peripherals share one, and Apply & Reboot answers a 422 after two
 * confirmations. So every pin of an enabled-flag section of the request, read
 * from backend/api/models.py, is a field pinOptionsByField knows, reached
 * through the page's own payload, and it is held from every other field
 * exactly while its section's switch is on.
 *
 * Mounts nothing: it reads the sources.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { pinOptionsByField } from '@/utils/hardwarePins';

const HERE = dirname(fileURLToPath(import.meta.url));
const MODELS = readFileSync(resolve(HERE, '../../../backend/api/models.py'), 'utf8');
const PAGE = readFileSync(resolve(HERE, '../../src/components/settings/categories/HardwareSettings.vue'), 'utf8');

function classBody(name) {
  const match = MODELS.match(new RegExp(`^class ${name}\\b[^\\n]*\\n((?:(?:[ \\t][^\\n]*)?\\n)*)`, 'm'));
  return match ? match[1] : '';
}

// section → { pins: [field], enabled: bool } for every section of the request
// that carries an enabled flag.
const backendSections = Object.fromEntries(
  [...classBody('HardwareConfigRequest').matchAll(/^ {4}(\w+): (Hardware\w+Request)\s*$/gm)]
    .map(([, section, cls]) => [section, classBody(cls)])
    .filter(([, body]) => /^ {4}enabled: bool\b/m.test(body))
    .map(([section, body]) => [section, [...body.matchAll(/^ {4}(\w+_pin): int\b/gm)].map(m => m[1])])
);

// section → { backend field → config key }, from the payload Apply sends.
function pagePayload(section) {
  const match = PAGE.match(new RegExp(`\\b${section}: \\{([^}]*)\\}`));
  if (!match) return {};
  return Object.fromEntries(
    [...match[1].matchAll(/(\w+): config\.value\.(\w+)/g)].map(([, field, key]) => [field, key])
  );
}

// config key of each backend pin → config key of its section's switch.
const pins = Object.fromEntries(
  Object.entries(backendSections).flatMap(([section, fields]) => {
    const payload = pagePayload(section);
    return fields.map(field => [payload[field] ?? `${section}.${field} (not sent)`, payload.enabled]);
  })
);

describe('the Hardware page knows every pin the PUT validates', () => {
  it('reads the request\'s pin sections and the page\'s payload', () => {
    expect(Object.keys(backendSections).length).toBeGreaterThanOrEqual(3);
    expect(Object.keys(pins).length).toBeGreaterThanOrEqual(5);
  });

  it('offers a dropdown for every pin the request carries, and for no other', () => {
    expect(Object.keys(pinOptionsByField({}, [])).sort()).toEqual(Object.keys(pins).sort());
  });

  it('holds each pin from every other field exactly while its switch is on', () => {
    // Every switch on, every pin on its own GPIO, one GPIO left free.
    const keys = Object.keys(pins);
    const on = Object.fromEntries(Object.values(pins).map(flag => [flag, true]));
    keys.forEach((key, i) => { on[key] = i + 1; });
    const free = [...keys.map((_, i) => ({ value: i + 1 })), { value: keys.length + 1 }];
    const offered = (config, field) => pinOptionsByField(config, free)[field].map(pin => pin.value);

    const wrong = [];
    for (const key of keys) {
      const off = { ...on, [pins[key]]: false };
      for (const other of keys.filter(k => k !== key)) {
        if (offered(on, other).includes(on[key])) {
          wrong.push(`${other} is offered ${key}'s pin while its switch is on`);
        }
        // A sibling switched off with it is not validated at all: nothing to offer.
        if (pins[other] !== pins[key] && !offered(off, other).includes(on[key])) {
          wrong.push(`${other} is refused ${key}'s pin while its switch is off`);
        }
      }
    }
    expect(wrong).toEqual([]);
  });
});
