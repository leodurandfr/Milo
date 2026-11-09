/**
 * The dock's non-source apps are listed on both sides: the backend validates a
 * saved dock against `UTILITY_DOCK_APPS` (backend/config/constants.py), and
 * settingsStore builds what it saves from `DOCK_UTILITY_APPS`. An id on the
 * frontend only is refused on every save (422 from DockAppsRequest); one on the
 * backend only is never offered as a toggle — both silent until someone tries.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const CONSTANTS = readFileSync(resolve(HERE, '../../../backend/config/constants.py'), 'utf8');
const STORE = readFileSync(resolve(HERE, '../../src/stores/settingsStore.js'), 'utf8');

function quotedIds(text) {
  return [...text.matchAll(/'([a-z_]+)'/g)].map(m => m[1]);
}

function backendUtilities() {
  const m = CONSTANTS.match(/^UTILITY_DOCK_APPS = frozenset\(\{([^}]*)\}\)/m);
  if (!m) throw new Error('UTILITY_DOCK_APPS not found — the extractor is broken');
  return quotedIds(m[1]);
}

function frontendUtilities() {
  const m = STORE.match(/^const DOCK_UTILITY_APPS = \[([^\]]*)\];/m);
  if (!m) throw new Error('DOCK_UTILITY_APPS not found — the extractor is broken');
  return quotedIds(m[1]);
}

describe('dock utilities', () => {
  it('parsed both lists', () => {
    expect(backendUtilities()).toContain('settings');
    expect(frontendUtilities()).toContain('settings');
  });

  it('lists the same ids on both sides', () => {
    expect([...frontendUtilities()].sort()).toEqual([...backendUtilities()].sort());
  });
});
