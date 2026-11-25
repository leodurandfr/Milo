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
 *
 * Settings › Network draws its preferred network in a row of its own, and
 * whether that row offers Forget and whether the list leaves the network out is
 * `useNetwork()`'s to decide (`preferredForgettable`, `listExcludedSsid`). What
 * breaks if the view binds anything else: over the recovery AP the list hides
 * the very network the "WiFi lost" banner sends a phone to type the key of, and
 * the row's one action is a Forget the unit refuses.
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

describe('Settings › Network\'s preferred row', () => {
  const view = stripComments(readFileSync(join(SRC_DIR, 'components/settings/categories/NetworkSettings.vue'), 'utf8'));
  const list = view.match(/<NetworkSelector\b[^>]*>/g) ?? [];
  const forgets = view.match(/<Button\b[^>]*\bhandleForget\([^>]*>/g) ?? [];

  it('is found, with the list beside it', () => {
    // Guards the two rules below: a parse that finds nothing would pass them.
    expect(list).toHaveLength(1);
    expect(forgets).toHaveLength(1);
  });

  it('leaves out of the list what useNetwork says to', () => {
    expect(list[0]).toMatch(/:exclude-ssid="listExcludedSsid"/);
  });

  it('draws its Forget only where useNetwork says it can be forgotten', () => {
    expect(forgets[0]).toMatch(/\bv-if="preferredForgettable"/);
  });
});

/**
 * Whether the list saves a network or joins it is one rule: `overRecoveryAp`
 * (useNetwork) — a browser on the recovery AP saves, since joining takes the
 * radio from under it, while the kiosk, a loopback client the AP does not
 * hold, joins at once. The wizard's step read `hotspotActive` alone, so on the
 * kiosk over the AP a mistyped key was saved untried and cost a reboot to be
 * found wrong, where Settings › Network refused it on the spot.
 */
describe('the list saves or joins by one rule', () => {
  const choosers = vueFiles(SRC_DIR).flatMap((file) => {
    const source = stripComments(readFileSync(file, 'utf8'));
    return (source.match(/<NetworkSelector\b[^>]*>/g) ?? [])
      .filter(tag => /\s:action="/.test(tag))
      .map(tag => ({ file: relative(SRC_DIR, file), source, action: tag.match(/\s:action="([^"]*)"/)[1] }));
  });

  it('finds the views that pick between saving and joining', () => {
    // Guards the rule below: a parse that finds nothing would pass it.
    expect(choosers.map(({ file }) => file).sort()).toEqual([
      'components/settings/categories/NetworkSettings.vue',
      'components/setup/NetworkStep.vue',
    ]);
  });

  it('every one of them takes the action from useNetwork\'s overRecoveryAp', () => {
    const astray = choosers
      .filter(({ source, action }) => !(
        action === "overRecoveryAp ? 'save' : 'connect'"
        && /const \{[^}]*\boverRecoveryAp\b[^}]*\} = useNetwork\(\)/.test(source)
      ))
      .map(({ file, action }) => `${file}: :action="${action}"`);
    expect(astray).toEqual([]);
  });
});
