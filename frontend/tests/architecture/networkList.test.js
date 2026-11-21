// frontend/tests/architecture/networkList.test.js
/**
 * One WiFi network list: `components/network/NetworkSelector.vue`.
 *
 * The wizard's step, Settings › Network and the satellite adoption all pick a
 * network, and Settings used to redraw the list by hand — rows, skeletons, the
 * empty state, the password and its button. The two copies had already drifted
 * (the Refresh button a different variant in a different place), and every
 * correction to the list had to be made twice, or was made once.
 *
 * What a row does is three calls of `useNetwork()`: select it, join it, save it.
 * A view other than the list calling one of them is drawing a list of its own.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';
import { stripComments } from '../helpers/stripComments';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const THE_LIST = 'components/network/NetworkSelector.vue';
const ROW_ACTIONS = ['selectNetwork', 'connectToNetwork', 'saveNetwork'];

function vueFiles(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory()
      ? vueFiles(full)
      : full.endsWith('.vue') ? [full] : [];
  });
}

/** Every `.vue` naming a row action, with the actions it names. */
function viewsActingOnRows() {
  const found = {};
  for (const file of vueFiles(SRC_DIR)) {
    const source = stripComments(readFileSync(file, 'utf8'));
    const named = ROW_ACTIONS.filter(name => new RegExp(`\\b${name}\\b`).test(source));
    if (named.length) found[relative(SRC_DIR, file)] = named;
  }
  return found;
}

describe('the WiFi network list', () => {
  const views = viewsActingOnRows();

  it('is found where it lives, acting on its rows', () => {
    // Guards the rule below: a parse that finds nothing would pass it.
    expect(views[THE_LIST]).toEqual(ROW_ACTIONS);
  });

  it('is drawn by NetworkSelector alone', () => {
    // A view selecting, joining or saving a network from its own rows is a
    // second copy of the list: draw NetworkSelector, and give it a prop.
    expect(Object.keys(views).filter(file => file !== THE_LIST)).toEqual([]);
  });
});
