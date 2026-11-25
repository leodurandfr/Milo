// frontend/tests/architecture/settingsFailure.test.js
/**
 * Every action a settings screen sends that fails says so, and one way: a
 * translated sentence, with the server's own words folded under it (NoticeBox
 * or SettingsRefusal, `:detail`). Three ways coexisted before — the backend's
 * English line drawn as the message, a translated sentence with the cause
 * thrown away, and nothing at all: a refused Apply and reboot put the button back
 * without a word, the card left unapplied and the reader none the wiser.
 *
 * The detail reaches a screen through apiCall's `errorRef`, so the rule is read
 * off the sources, three ways:
 *   1. every write a settings component sends through apiCall carries an
 *      errorRef;
 *   2. every call it makes to a store or composable action that writes either
 *      passes one — an action that takes none cannot hand the cause back, and
 *      is where the silent screens came from — and the action hands it on to
 *      apiCall, or reaches an action that holds its own (updatesStore's
 *      `startRefusal`, useNetwork's `wifiRefusal`), which the component then
 *      draws;
 *   3. every ref handed as an errorRef is drawn as a `:detail`, here or by the
 *      component it is passed to, and never as the message itself.
 * An action is reached however it is named: `store.action(`, or a bare
 * `action(` once destructured out of a store or a composable — UpdateManager
 * takes its actions that way, and a guardrail reading only the first spelling
 * passed with every update start silent. A settings component is every one
 * under components/settings/, every step of the setup wizard under
 * components/setup/ (whose apply drew the server's English refusal as its only
 * message), and every component one mounts, wherever it lives
 * (WifiCountrySelector, from components/network/).
 * useSettingsAPI's own refusal is held by settingsRefusal.test.js.
 *
 * Mounts nothing: it reads the sources.
 */
import { describe, it, expect } from 'vitest';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');
const COMPONENTS = join(SRC, 'components');
const SETTINGS = join(COMPONENTS, 'settings');
const SETUP = join(COMPONENTS, 'setup');

// Writes a settings screen sends with no cause to show, each with why. A key is
// `file::label` for every such call, or `file::label(args)` for the one call
// passing exactly those arguments.
const EXCUSED = {
  'settings/categories/ScreenSettings.vue::apiCall.post':
    'the live brightness preview while the slider moves; its release saves through useSettingsAPI, whose refusal is said',
  'settings/categories/FanSection.vue::fanStore.testSpeed':
    'the live fan preview while the slider moves; its release saves the config, whose refusal is said',
  'settings/SettingsModal.vue::snapcastStore.discardUnappliedTuning':
    'run as the multiroom view is left: nobody is in front of it any more',
  'settings/SettingsModal.vue::macLinkStore.discardUnapplied':
    'run as the Mac link view is left: nobody is in front of it any more',
  'settings/SettingsModal.vue::snapcastStore.discardUnappliedTuning (on unmount)':
    'run as the panel closes on the multiroom view: nobody is in front of it any more',
  'settings/SettingsModal.vue::macLinkStore.discardUnapplied (on unmount)':
    'run as the panel closes on the Mac link view: nobody is in front of it any more',
  'settings/categories/MacSettings.vue::macLinkStore.resetDraft':
    'the reset is local; its DELETE only drops a stored analysis, which the next panel open would bring back',
  'settings/categories/MacSettings.vue::macLinkStore.startCalibration':
    'a refused start is the analysis section\'s own start_failed, translated by AnalysisSection',
  'settings/categories/multiroom/MultiroomSettings.vue::snapcastStore.startCalibration':
    'a refused start is the analysis section\'s own start_failed, translated by AnalysisSection',
  'settings/categories/IrRemoteSettings.vue::settingsStore.cancelIrRemotePairing (on unmount)':
    'run as the IR remote view is left: nobody is in front of it any more',
  'settings/categories/UpdateManager.vue::updatesStore.loadLocalPrograms()':
    'the read as the screen opens and the Retry of a failed one, a GET: its failure is the screen\'s own read error',
  'network/NetworkSelector.vue::useNetwork.setCountry(mapped)':
    'the country guessed from the language as the picker opens, which nobody chose: refused, the country row stays empty for the reader to pick, and that pick is said',
};

// Composables whose writes another guardrail holds: useSettingsAPI keeps its
// refusal in `refusal`, which settingsRefusal.test.js requires every screen
// taking it to draw.
const HELD_ELSEWHERE = new Set(['useSettingsAPI']);

const WRITE = /\bapiCall\.(post|put|patch|delete)\(/;

function filesUnder(dir, ext) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return filesUnder(path, ext);
    return entry.name.endsWith(ext) ? [path] : [];
  });
}

/** The text between the parenthesis at `open` and the one that closes it. */
function balanced(text, open) {
  let depth = 0;
  for (let i = open; i < text.length; i++) {
    if (text[i] === '(') depth++;
    else if (text[i] === ')' && --depth === 0) return text.slice(open + 1, i);
  }
  return text.slice(open + 1);
}

const NOT_A_METHOD = new Set(['if', 'for', 'while', 'switch', 'catch', 'return']);

/**
 * Each function a store or a composable returns — or, with `methods`, each
 * method of a service's class: its name, signature and body.
 */
function providerFunctions(text, { methods = false } = {}) {
  const found = [];
  const head = methods ? /\n {2}(?:async )?(\w+)\(/g : /\n {2}(?:async )?function (\w+)\(/g;
  let match;
  while ((match = head.exec(text))) {
    if (NOT_A_METHOD.has(match[1])) continue;
    const params = balanced(text, match.index + match[0].length - 1);
    const end = text.indexOf('\n  }\n', match.index);
    found.push({ name: match[1], params, body: text.slice(match.index, end < 0 ? undefined : end) });
  }
  return found;
}

// Actions that take an errorRef and drop it: a caller passing one is told
// nothing, and the screen falls silent again with the call looking right.
const unforwarded = [];

/**
 * A store's or a composable's writing actions: name → `{ takes, holds }`, where
 * `takes` says whether its signature accepts an errorRef and `holds` names the
 * refs of its own it hands apiCall instead (updatesStore's `startRefusal`,
 * useNetwork's `wifiRefusal`), which a screen reads back and must draw.
 */
function writingActions(label, text, { methods = false } = {}) {
  const fns = providerFunctions(stripComments(text), { methods });
  const byName = new Map(fns.map(fn => [fn.name, fn]));
  const held = fn => new Set([...fn.body.matchAll(/\berrorRef: (\w+)/g)]
    .map(([, ref]) => ref).filter(ref => ref !== 'errorRef'));
  const writing = new Map(fns.filter(fn => WRITE.test(fn.body)).map(fn => [fn.name, held(fn)]));
  // An action that writes through another of its own writes too, and holds
  // what that one holds.
  let grew = true;
  while (grew) {
    grew = false;
    for (const fn of fns) {
      for (const [name, refs] of [...writing]) {
        if (name === fn.name || !new RegExp(`(?<![.\\w])${name}\\(`).test(fn.body)) continue;
        const own = writing.get(fn.name) ?? held(fn);
        const before = writing.has(fn.name) ? own.size : -1;
        refs.forEach(ref => own.add(ref));
        writing.set(fn.name, own);
        if (own.size !== before) grew = true;
      }
    }
  }
  const actions = {};
  for (const [name, holds] of writing) {
    const fn = byName.get(name);
    const takes = /\berrorRef\b/.test(fn.params);
    actions[name] = { takes, holds: [...holds] };
    // The signature's own mention aside, the body must hand it on.
    if (takes && (fn.body.match(/\berrorRef\b/g) ?? []).length < 2) unforwarded.push(`${label}.${name}`);
  }
  return actions;
}

const storeActions = Object.fromEntries(filesUnder(join(SRC, 'stores'), '.js').map((path) => {
  const stem = path.split('/').pop().replace(/\.js$/, '');
  return [stem, writingActions(stem, readFileSync(path, 'utf8'))];
}));

// A composable a scanned component imports from a file of this checkout that
// cannot be read: its writes would go unchecked, as useI18n's did while only
// composables/ was read.
const unresolved = [];

/**
 * Where a component's `name` comes from: the file its `import { name } from`
 * names, or null for a package (vue) or a name the script defines itself.
 */
function composableFile(component, name) {
  const source = [...component.script.matchAll(/import \{([^}]*)\} from '([^']+)'/g)]
    .find(([, list]) => destructured(list).some(({ action }) => action === name))?.[2];
  if (!source || !(source.startsWith('@/') || source.startsWith('.'))) return null;
  const path = source.startsWith('@/') ? join(SRC, source.slice(2)) : resolve(dirname(component.path), source);
  const file = [path, `${path}.js`].find(candidate => existsSync(candidate) && statSync(candidate).isFile());
  if (!file) unresolved.push(`${component.file}: ${name} from '${source}'`);
  return file ?? null;
}

/**
 * A service's composable hands out its class's methods bound, under the key it
 * returns them as (`setLanguage: i18n.setLanguage.bind(i18n)`): the writes are
 * the methods', read by the key a screen destructures.
 */
function serviceActions(name, text) {
  const methods = writingActions(name, text, { methods: true });
  return Object.fromEntries([...stripComments(text).matchAll(/\b(\w+): \w+\.(\w+)\.bind\(/g)]
    .filter(([, , method]) => method in methods)
    .map(([, key, method]) => [key, methods[method]]));
}

const composableCache = new Map();
/** A composable's writing actions, read the first time a scanned component takes it. */
function composableActions(name, file) {
  if (!composableCache.has(file)) {
    const text = readFileSync(file, 'utf8');
    composableCache.set(file, /^(?:export )?class \w+/m.test(text)
      ? serviceActions(name, text)
      : writingActions(name, text));
  }
  return composableCache.get(file);
}

function splitSfc(text) {
  const script = text.match(/<script setup>([\s\S]*?)<\/script>/)?.[1] ?? '';
  const template = text.match(/^<template>([\s\S]*)^<\/template>/m)?.[1] ?? '';
  return { script, template };
}

function readComponent(path) {
  const text = stripComments(readFileSync(path, 'utf8'));
  return { path, file: relative(COMPONENTS, path), text, ...splitSfc(text) };
}

/** The components a script imports, resolved to their paths. */
function importedComponents(path, script) {
  return [...script.matchAll(/import \w+ from '([^']+\.vue)'/g)].map(([, source]) =>
    (source.startsWith('@/') ? join(SRC, source.slice(2)) : resolve(dirname(path), source)));
}

// Every settings component, every step of the setup wizard — the first screen a
// new owner reads, in any of the eight languages — and every component one
// mounts wherever it lives: WifiCountrySelector is a settings row drawn from
// components/network/.
const byPath = new Map();
const pending = [...filesUnder(SETTINGS, '.vue'), ...filesUnder(SETUP, '.vue')];
while (pending.length) {
  const path = pending.pop();
  if (byPath.has(path)) continue;
  const component = readComponent(path);
  byPath.set(path, component);
  pending.push(...importedComponents(path, component.script));
}
const components = [...byPath.values()];

/** The spans of a component's unmount hooks: a write sent there has no reader left. */
function unmountSpans(text) {
  return [...text.matchAll(/\b(?:onBeforeUnmount|onUnmounted)\(/g)].map((match) => {
    const open = match.index + match[0].length - 1;
    return [open, open + balanced(text, open).length + 1];
  });
}

/** `a, b: c` → the local names it binds, each with the action it names. */
function destructured(list) {
  return list.split(',').map(entry => entry.trim()).filter(Boolean).map((entry) => {
    const [action, local = action] = entry.split(':').map(part => part.trim());
    return { action, local };
  });
}

/**
 * The actions a component reaches: `{ pattern, label, action }` for each way it
 * calls one — `store.action(` on a store instance, and a bare `action(` once it
 * was destructured out of a store or a composable.
 */
function reachedActions(component) {
  const { script } = component;
  const reached = [];
  const instances = new Map();
  for (const [, name, store] of script.matchAll(/const (\w+) = use(\w+)Store\(\)/g)) {
    const stem = `${store[0].toLowerCase()}${store.slice(1)}Store`;
    instances.set(name, stem);
    for (const [action, info] of Object.entries(storeActions[stem] ?? {})) {
      reached.push({ pattern: `\\b${name}\\.${action}\\(`, label: `${name}.${action}`, info });
    }
  }
  // `const { a, b } = xStore` or `= useXStore()`, never through storeToRefs.
  for (const [, list, source] of script.matchAll(/const \{([^}]*)\} = (\w+(?:\(\))?)/g)) {
    const store = source.match(/^use(\w+)Store\(\)$/);
    const stem = store ? `${store[1][0].toLowerCase()}${store[1].slice(1)}Store` : instances.get(source);
    const composable = !stem && source.match(/^(use\w+)\(\)$/)?.[1];
    if (composable && HELD_ELSEWHERE.has(composable)) continue;
    const file = composable && composableFile(component, composable);
    const actions = stem ? storeActions[stem] ?? {} : file ? composableActions(composable, file) : {};
    const owner = stem && instances.has(source) ? source : stem ?? composable;
    for (const { action, local } of destructured(list)) {
      if (!(action in actions)) continue;
      reached.push({ pattern: `(?<![.\\w])${local}\\(`, label: `${owner}.${action}`, info: actions[action] });
    }
  }
  return reached;
}

/** Every write a component sends: `{ file, label, args, ... }`, labelled apart when sent on unmount. */
function writeCalls() {
  const calls = [];
  // The whole file, template included: a click handler can call an action inline.
  for (const component of components) {
    const { file, text } = component;
    const spans = unmountSpans(text);
    const where = index => (spans.some(([start, end]) => index > start && index < end) ? ' (on unmount)' : '');
    for (const match of text.matchAll(/\bapiCall\.(post|put|patch|delete)\(/g)) {
      calls.push({
        component,
        file,
        label: `apiCall.${match[1]}${where(match.index)}`,
        args: balanced(text, match.index + match[0].length - 1),
      });
    }
    for (const { pattern, label, info } of reachedActions(component)) {
      for (const match of text.matchAll(new RegExp(pattern, 'g'))) {
        // The action's own definition, were a component to name one alike.
        if (/function\s+$/.test(text.slice(0, match.index))) continue;
        calls.push({
          component,
          file,
          label: `${label}${where(match.index)}`,
          info,
          args: balanced(text, match.index + match[0].length - 1),
        });
      }
    }
  }
  return calls;
}

const calls = writeCalls();
const keys = call => [`${call.file}::${call.label}`, `${call.file}::${call.label}(${call.args.trim()})`];
const excused = call => keys(call).some(key => key in EXCUSED);

/** Why a write sent by `call` cannot hand its cause to the screen, or null when it can. */
function silence(call) {
  if (!call.info) return /\berrorRef\b/.test(call.args) ? null : 'no errorRef';
  const { takes, holds } = call.info;
  if (/\berrorRef\b/.test(call.args)) {
    // A call passing one to an action that takes none hands it to nobody.
    return takes ? null : 'the action takes no errorRef';
  }
  if (holds.length) {
    const undrawnRef = holds.find(ref => undrawn(call.component, ref) !== null);
    return undrawnRef ? `the action's ${undrawnRef} is ${undrawn(call.component, undrawnRef)} here` : null;
  }
  return takes ? 'no errorRef' : 'the action takes no errorRef';
}

const camel = (kebab) => kebab.replace(/-(\w)/g, (_, c) => c.toUpperCase());

/** The component a template attribute at `index` sits on, resolved through its import. */
function childAt({ path, script, template }, index) {
  const tag = [...template.slice(0, index).matchAll(/<([A-Z]\w*)/g)].pop()?.[1];
  const source = tag && script.match(new RegExp(`import ${tag} from '([^']+)'`))?.[1];
  if (!source) return undefined;
  return byPath.get(source.startsWith('@/') ? join(SRC, source.slice(2)) : resolve(dirname(path), source));
}

/** Why a ref handed as an errorRef is not drawn as a detail, or null when it is. */
function undrawn(component, ref) {
  const { template } = component;
  if (new RegExp(`\\{\\{\\s*${ref}\\s*\\}\\}`).test(template)) return 'drawn as the message';
  const bindings = [...template.matchAll(new RegExp(`:([\\w-]+)="${ref}"`, 'g'))];
  if (!bindings.length) return 'never drawn';
  for (const binding of bindings) {
    if (binding[1] === 'detail') continue;
    const child = childAt(component, binding.index);
    if (!child) return `handed to <${binding[1]}> outside the scanned components`;
    if (!new RegExp(`:detail="${camel(binding[1])}"`).test(child.template)) {
      return `handed to ${child.file} as ${binding[1]}, which never draws it as a detail`;
    }
  }
  return null;
}

describe('a settings action that fails says so, one way', () => {
  it('finds the writes the settings screens send', () => {
    expect(calls.length).toBeGreaterThanOrEqual(50);
    expect(calls.filter(call => call.info).length).toBeGreaterThanOrEqual(35);
    // Reached by destructuring, and through a composable.
    expect(calls.some(call => call.label === 'updatesStore.startLocalUpdate')).toBe(true);
    expect(calls.some(call => call.label === 'useNetwork.setCountry')).toBe(true);
    // Through a composable a service exports, found by the component's import.
    expect(calls.some(call => call.label === 'useI18n.setLanguage')).toBe(true);
  });

  it('every composable a settings screen takes is read', () => {
    expect(unresolved).toEqual([]);
  });

  it('every one of them asks apiCall for the cause', () => {
    const silent = calls
      .filter(call => !excused(call))
      .map(call => ({ call, why: silence(call) }))
      .filter(({ why }) => why !== null)
      .map(({ call, why }) => `${call.file}: ${call.label} (${why})`);
    expect(silent).toEqual([]);
  });

  it('every store or composable action that takes an errorRef hands it on to apiCall', () => {
    expect(unforwarded).toEqual([]);
  });

  it('every excuse still names a write that is sent', () => {
    const stale = Object.keys(EXCUSED)
      .filter(key => !calls.some(call => keys(call).includes(key)));
    expect(stale).toEqual([]);
  });

  it('every cause is folded under a sentence, never drawn as one', () => {
    const refs = components.flatMap(component =>
      [...component.script.matchAll(/\berrorRef: (\w+)/g)].map(([, ref]) => ({ component, ref })));
    expect(refs.length).toBeGreaterThanOrEqual(35);

    const wrong = refs
      .map(({ component, ref }) => ({ file: component.file, ref, why: undrawn(component, ref) }))
      .filter(({ why }) => why !== null)
      .map(({ file, ref, why }) => `${file}: ${ref} ${why}`);
    expect([...new Set(wrong)]).toEqual([]);
  });
});
