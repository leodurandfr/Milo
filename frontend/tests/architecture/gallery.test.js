// frontend/tests/architecture/gallery.test.js
/**
 * Structural guardrail over the component gallery at /components.
 *
 * The page is documentation, and documentation that silently falls behind the
 * code is worse than none: a reader who finds 23 components listed has no way to
 * know a 24th shipped. So the catalogue is checked against the filesystem in
 * both directions — a new `.vue` in scope with no entry fails, and an entry
 * whose file was deleted or renamed fails too, the way a stale
 * `.stylelintrc.cjs` whitelist entry does.
 *
 * Scope is no longer one directory, so "listed" now means *catalogued or
 * excluded with a reason*. The escape hatch is the point: `AudioSourceView` and
 * `SettingsModal` genuinely do not belong on the page, and a reason written down
 * is what the next person has to disagree with before adding an entry — whereas
 * a file quietly missing from a glob is indistinguishable from an oversight.
 *
 * The second half guards the controls panel, which is *derived* rather than
 * declared (see controls.js). Derivation has one failure mode worth catching:
 * an enum prop whose validator is written in a shape the parser cannot read
 * downgrades to a free-text box — a control that looks like it works, offers
 * every wrong value, and never errors. So every String prop carrying a validator
 * must resolve to a list of options, either parsed or explicitly overridden in
 * registry.js. That is the check that keeps "derived" honest.
 *
 * The stage tones are guarded the same way: a `surface` rule that names a tone
 * CanvasApp.vue has no class for, or that never fires for any value its own
 * controls can produce, leaves a dark-surface variant on the light stage looking
 * broken — and reports nothing.
 *
 * Mounts nothing and asserts no markup: this is the one kind of test that can
 * cover a page whose whole purpose is to be looked at.
 */
import { describe, it, expect } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { GROUPS, ENTRIES, SCOPE, EXCLUDED, isScreen, entriesOf } from '../../src/components/gallery/catalog.js';
import { REGISTRY, SOURCE_REGISTRY, entryFor, overridesFor, AUDIO_SOURCES_ID } from '../../src/components/gallery/registry.js';
import { describeProps } from '../../src/components/gallery/controls.js';
import {
  SOURCE_PAGES,
  METADATA_READERS,
  DECIDERS,
  BEHAVIOURAL_FIELDS,
  SOURCE_PAGE_PREFIX,
  allEvents,
  allMetadata,
  settledState,
  scenarioId,
  sourcePageById
} from '../../src/components/gallery/sources.js';
import {
  SECTIONS,
  MOBILE,
  TYPE_STYLES,
  EXCLUDED_SECTIONS,
  FOUNDATION_PAGES,
  FOUNDATION_PAGE_PREFIX,
  foundationPageById
} from '../../src/components/gallery/foundations.js';
import { ALL_AUDIO_SOURCES } from '../../src/constants/audioSources.js';
import { useRadioStore } from '../../src/stores/radioStore.js';
import { useMusicLibraryStore } from '../../src/stores/musicLibraryStore.js';
import { usePodcastStore } from '../../src/stores/podcastStore.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

/**
 * The backend's own models, read as text at test time and never bundled — the
 * same arrangement `tests/schemas/api.test.js` uses for the AudioSource enum.
 * It is what lets the source pages borrow a vocabulary instead of inventing
 * one, without the gallery depending on anything at runtime.
 */
const BACKEND_DIR = resolve(HERE, '../../../backend');
const WS_EVENTS_PY = readFileSync(join(BACKEND_DIR, 'core/models/ws_events.py'), 'utf8');
const AUDIO_STATE_PY = readFileSync(join(BACKEND_DIR, 'core/models/audio_state.py'), 'utf8');

/** `SourceState`'s own members — the only state names a scenario may settle on. */
const SOURCE_STATES = [
  ...(AUDIO_STATE_PY.split('class SourceState')[1] ?? '').split('\nclass ')[0]
    .matchAll(/^\s+[A-Z_]+\s*=\s*"([a-z_]+)"/gm)
].map(match => match[1]);

/** Every `.vue` directly inside a scoped directory, screens included. */
const SCANNED_FILES = SCOPE
  .flatMap(dir =>
    readdirSync(join(SRC_DIR, dir))
      .filter(name => name.endsWith('.vue'))
      .map(name => `${dir}/${name}`)
  )
  .sort();

/**
 * The shared parts, which is what the catalogue answers for. One level deep —
 * a nested directory is per-feature screens — and minus the screens a source
 * directory keeps beside its parts, told apart by name (see isScreen).
 */
const SCOPED_FILES = SCANNED_FILES.filter(file => !isScreen(file));

const VIEW = readFileSync(join(SRC_DIR, 'views/ComponentsView.vue'), 'utf8');
const CANVAS = readFileSync(join(SRC_DIR, 'components/gallery/CanvasApp.vue'), 'utf8');

/** Stage tones the canvas declares a class for — `.canvas--contrast`, … */
const SURFACES = [...CANVAS.matchAll(/\.canvas--([a-z-]+)\s*\{/g)].map(match => match[1]);

/**
 * The args a primitive's controls can produce: what it starts from, then each
 * enum option in turn, which is how the panel edits — one prop at a time.
 */
function argSweep(descriptor) {
  const base = descriptor.args || {};
  const sweep = [base];

  for (const prop of describeProps(descriptor.component, descriptor.overrides || {})) {
    for (const option of prop.options || []) sweep.push({ ...base, [prop.name]: option });
  }
  return sweep;
}

/**
 * Every primitive needs a playground descriptor. There is no opt-out: the canvas
 * is an iframe with its own viewport and its own stores, so even the three
 * `position: fixed`, store-driven ones render there.
 */
const PLAYABLE = ENTRIES;

describe('component gallery catalogue', () => {
  it('read a plausible surface', () => {
    // A broken scan would make every assertion below vacuously pass.
    expect(SCOPE.length).toBeGreaterThan(1);
    expect(SCOPED_FILES.length).toBeGreaterThan(30);
    expect(ENTRIES.length).toBeGreaterThan(30);
    expect(Object.keys(REGISTRY).length).toBeGreaterThan(30);
  });

  it('accounts for every component in scope', () => {
    const listed = new Set(ENTRIES.map(entry => entry.file));
    const missing = SCOPED_FILES.filter(file => !listed.has(file) && !EXCLUDED[file]);

    // A shared composite that lands in scope without an entry is invisible on
    // the page that is supposed to be the design system's index. Catalogue it,
    // or say in EXCLUDED why it does not belong there.
    expect(missing).toEqual([]);
  });

  it('excludes nothing twice, nothing absent, and nothing without a reason', () => {
    const problems = [];

    for (const [file, reason] of Object.entries(EXCLUDED)) {
      if (!existsSync(join(SRC_DIR, file))) problems.push(`${file} (no such file)`);
      if (!SCANNED_FILES.includes(file)) problems.push(`${file} (not in scope — nothing to exclude)`);
      // The by-name rule already answers for it; a second answer is one more
      // thing to keep true, and the two can disagree.
      if (isScreen(file)) problems.push(`${file} (already out by name)`);
      if ((reason || '').length < 40) problems.push(`${file} (thin reason)`);
      if (ENTRIES.some(entry => entry.file === file)) problems.push(`${file} (also catalogued)`);
    }

    // An exclusion that outlives its file, or that never applied, is the same
    // stale-whitelist failure the catalogue side is checked for.
    expect(problems).toEqual([]);
  });

  it('separates screens from parts by name, and catalogues no screen', () => {
    // The rule is only worth stating if it actually divides the scanned set:
    // a pattern that matches everything, or nothing, is not a rule.
    const screens = SCANNED_FILES.filter(file => isScreen(file));
    expect(screens.length).toBeGreaterThan(5);
    expect(SCOPED_FILES.length).toBeGreaterThan(screens.length);

    // A skeleton is a part whatever it is named after — SkeletonPodcastDetails
    // ends in Details and is the exception the pattern is written around.
    const skeletons = SCANNED_FILES.filter(file => file.includes('/Skeleton'));
    expect(skeletons.length).toBeGreaterThan(0);
    expect(skeletons.filter(file => isScreen(file))).toEqual([]);

    // And an entry that names a screen would be catalogued *and* out of scope.
    expect(ENTRIES.map(entry => entry.file).filter(file => isScreen(file))).toEqual([]);
  });

  it('carries no entry for a file that no longer exists', () => {
    const stale = ENTRIES
      .map(entry => entry.file)
      .filter(file => !existsSync(join(SRC_DIR, file)));

    expect(stale).toEqual([]);
  });

  it('names each entry after its file', () => {
    // GalleryItem looks entries up by `id`, and the demos pass the component's
    // own name — a mismatch renders the "missing from catalog.js" placeholder.
    const mismatched = ENTRIES
      .filter(entry => !entry.file.endsWith(`/${entry.id}.vue`))
      .map(entry => `${entry.id} -> ${entry.file}`);

    expect(mismatched).toEqual([]);
  });

  it('keeps every entry inside a scoped directory', () => {
    // An entry pointing outside SCOPE would be catalogued but unguarded: nothing
    // would notice when its file moved or its neighbours grew a new one.
    const outside = ENTRIES
      .filter(entry => !SCOPED_FILES.includes(entry.file))
      .map(entry => entry.file);

    expect(outside).toEqual([]);
  });

  it('gives every entry a group that exists, and every group entries', () => {
    const groupIds = new Set(GROUPS.map(group => group.id));
    const orphans = ENTRIES.filter(entry => !groupIds.has(entry.group)).map(entry => entry.id);
    const empty = GROUPS.filter(group => entriesOf(group.id).length === 0).map(group => group.id);

    expect(orphans).toEqual([]);
    expect(empty).toEqual([]);
  });

  it('wires every group to a demo component', () => {
    // The view maps group id -> demo for the Variants tab. A group missing from
    // that object renders as a blank tab with no error anywhere.
    const mapped = [...VIEW.matchAll(/^\s{2}(\w+): \w+Demo,?$/gm)].map(match => match[1]);

    expect(mapped.sort()).toEqual(GROUPS.map(group => group.id).sort());
  });

  it('says something substantive about every primitive', () => {
    // The summary is the only prose a reader gets, and it is what explains why a
    // `coupling` primitive behaves unlike the rest.
    const thin = ENTRIES.filter(entry => (entry.summary || '').length < 40).map(entry => entry.id);

    expect(thin).toEqual([]);
  });
});

describe('component gallery playground', () => {
  it('gives every renderable primitive a playground descriptor', () => {
    const missing = PLAYABLE.filter(entry => !REGISTRY[entry.id]).map(entry => entry.id);

    // Without a descriptor the canvas shows a placeholder instead of the component.
    expect(missing).toEqual([]);
  });

  it('carries no descriptor for something the catalogue does not render', () => {
    const catalogued = new Set(PLAYABLE.map(entry => entry.id));
    const extra = Object.keys(REGISTRY).filter(id => !catalogued.has(id));

    expect(extra).toEqual([]);
  });

  it('declares a writer and a default for every state control', () => {
    // A `state` control is a store write, so unlike a prop nothing about it is
    // derivable — a missing `apply` silently does nothing when the panel changes.
    const broken = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      for (const [name, state] of Object.entries(descriptor.state || {})) {
        if (typeof state.apply !== 'function') broken.push(`${id}.${name} (no apply)`);
        if (state.default === undefined) broken.push(`${id}.${name} (no default)`);
        if (state.kind === 'enum' && !state.options?.length) broken.push(`${id}.${name} (no options)`);
      }
      for (const [name, action] of Object.entries(descriptor.actions || {})) {
        if (typeof action !== 'function') broken.push(`${id}.${name} (action not callable)`);
      }
    }

    expect(broken).toEqual([]);
  });

  it('gives an always-mounted primitive nothing the panel cannot bind', () => {
    // An `alwaysMounted` descriptor is rendered by the canvas outside the
    // selection, so it never receives `bound` props or resolved slots — args,
    // overrides and slots declared on one would be edited in the panel and change
    // nothing on screen. Only actions and state reach it.
    const unbindable = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      if (!descriptor.alwaysMounted) continue;
      for (const field of ['args', 'overrides', 'slots']) {
        if (Object.keys(descriptor[field] || {}).length) unbindable.push(`${id}.${field}`);
      }
      if (describeProps(descriptor.component).length) unbindable.push(`${id} (declares props)`);
    }

    expect(unbindable).toEqual([]);
  });

  it('offers at least one choice per slot it declares as choosable', () => {
    const empty = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      for (const [name, definition] of Object.entries(descriptor.slots || {})) {
        if (typeof definition === 'string') continue;
        // A choice map the panel would render as an empty select.
        if (!Object.keys(definition).length) empty.push(`${id}.${name}`);
      }
    }

    expect(empty).toEqual([]);
  });

  it('supplies a value for every required prop', () => {
    const gaps = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      for (const prop of describeProps(descriptor.component, descriptor.overrides || {})) {
        const fromArgs = (descriptor.args || {})[prop.name] !== undefined;
        const fromPreset = !!(descriptor.presets || {})[prop.name];
        if (prop.required && !fromArgs && !fromPreset) gaps.push(`${id}.${prop.name}`);
      }
    }

    // A required prop left unset renders a broken instance, and Vue only warns.
    // A preset counts: the canvas resolves one before the component mounts.
    expect(gaps).toEqual([]);
  });

  it('invents no store field the component does not read', () => {
    // A `state` descriptor that writes a whole record is the page simulating an
    // app state, which is its job — but a simulation nothing consumes is the
    // failure this page exists to prevent, reproduced inside it: rename
    // `album_art_url` in the component and a fabricated record keeps rendering a
    // convincing player from a key that reaches nothing. So each key is checked
    // against the source of the files declared as reading it.
    const orphans = [];
    let checked = 0;

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      for (const [name, state] of Object.entries(descriptor.state || {})) {
        if (!state.records) continue;

        if (!state.readBy?.length) {
          orphans.push(`${id}.${name} (records with no readBy)`);
          continue;
        }

        const missing = state.readBy.filter(file => !existsSync(join(SRC_DIR, file)));
        if (missing.length) {
          orphans.push(`${id}.${name} (readBy gone: ${missing.join(', ')})`);
          continue;
        }

        const consumers = state.readBy.map(file => readFileSync(join(SRC_DIR, file), 'utf8')).join('\n');
        for (const record of state.records) {
          for (const key of Object.keys(record)) {
            checked += 1;
            // Property access, not a bare word: a key named in a comment is not
            // a key anything reads.
            if (!new RegExp(`\\.${key}\\b`).test(consumers)) orphans.push(`${id}.${name}.${key}`);
          }
        }
      }
    }

    // A `readBy` list that resolved to nothing would pass vacuously.
    expect(checked).toBeGreaterThan(0);
    expect(orphans).toEqual([]);
  });

  it('offers at least one choice per preset, on a prop that takes one', () => {
    const broken = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      const kinds = new Map(
        describeProps(descriptor.component, descriptor.overrides || {}).map(prop => [prop.name, prop.kind])
      );

      for (const [name, choices] of Object.entries(descriptor.presets || {})) {
        if (!Object.keys(choices || {}).length) broken.push(`${id}.${name} (no choices)`);
        if (!kinds.has(name)) broken.push(`${id}.${name} (no such prop)`);
        // A preset on a prop the panel can already edit hides a working control
        // behind a fixed list — presets exist for the values no widget carries.
        else if (kinds.get(name) !== 'fixed') broken.push(`${id}.${name} (${kinds.get(name)} is editable)`);
      }
    }

    expect(broken).toEqual([]);
  });

  it('resolves every enum prop to a list of options', () => {
    const unresolved = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      for (const prop of describeProps(descriptor.component, descriptor.overrides || {})) {
        // A String prop with a validator has a finite set of accepted values by
        // definition. If neither the parse nor an override produced it, the panel
        // is showing a text box for an enum — the silent degradation this test exists
        // to prevent. Add an `overrides` entry in registry.js.
        const isConstrainedString = prop.hasValidator && prop.types.includes('String');
        if (isConstrainedString && prop.kind !== 'enum') {
          unresolved.push(`${id}.${prop.name} (${prop.kind})`);
        }
        if (prop.kind === 'enum' && !prop.options?.length) {
          unresolved.push(`${id}.${prop.name} (empty options)`);
        }
        // `null` in a validator's array literal is a value, not a name. Reading
        // it as the string 'null' offers an option the validator then rejects —
        // a select that produces a Vue warning on the one entry a reader picks
        // to see "no icon", "no gradient".
        if (prop.options?.includes('null')) {
          unresolved.push(`${id}.${prop.name} ('null' parsed as a string)`);
        }
      }
    }

    expect(unresolved).toEqual([]);
  });

  it('paints only stage tones the canvas can render', () => {
    // A tone with no class behind it does not fail — the stage silently stays
    // light, which is the exact outcome a `surface` rule exists to prevent.
    expect(SURFACES.length).toBeGreaterThan(0);

    const unknown = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      if (!descriptor.surface) continue;
      for (const args of argSweep(descriptor)) {
        const tone = descriptor.surface(args);
        if (tone && !SURFACES.includes(tone)) unknown.push(`${id} -> ${tone}`);
      }
    }

    expect(unknown).toEqual([]);
  });

  it('declares no surface rule that never fires', () => {
    const dead = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      if (!descriptor.surface) continue;
      const fires = argSweep(descriptor).some(args => descriptor.surface(args));
      if (!fires) dead.push(id);
    }

    // A rule that answers "light" for every value its own controls can produce is
    // a variant that was renamed or dropped, not a primitive that turned out to
    // live on one surface.
    expect(dead).toEqual([]);
  });

  it('offers every slot its component declares', () => {
    // The panel derives props and events from the component; slots it cannot
    // derive, so a slot nobody declared is simply absent from the page — the
    // reader is told the component takes three slots when it takes five, and
    // nothing anywhere disagrees.
    const missing = [];

    for (const entry of ENTRIES) {
      const descriptor = REGISTRY[entry.id];
      const source = readFileSync(join(SRC_DIR, entry.file), 'utf8');
      const declared = new Set(
        [...source.matchAll(/<slot\s[^>]*name="([^"]+)"/g)].map(match => match[1])
      );
      if (/<slot(\s[^>]*)?\/?>/.test(source.replace(/<slot\s[^>]*name="[^"]+"/g, ''))) {
        declared.add('default');
      }

      const offered = new Set(Object.keys(descriptor.slots || {}));
      for (const name of declared) {
        if (!offered.has(name)) missing.push(`${entry.id}.${name}`);
      }
    }

    expect(missing).toEqual([]);
  });

  it('leaves no prop the panel cannot show and nothing supplies', () => {
    // A prop the controls degrade to `fixed` (an object, an array, a callback)
    // is read-only in the panel, so the descriptor is the only thing that can
    // give it a value. Left unset it renders as `undefined` next to its name —
    // an input the reader is shown and cannot use.
    const blank = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      if (descriptor.alwaysMounted) continue;

      for (const prop of describeProps(descriptor.component, descriptor.overrides || {})) {
        if (prop.kind !== 'fixed') continue;
        // A callback prop needs nothing: the canvas stubs it with a reporter.
        if (prop.types.includes('Function')) continue;
        if (prop.default !== undefined) continue;
        if ((descriptor.args || {})[prop.name] !== undefined) continue;
        if ((descriptor.presets || {})[prop.name]) continue;
        blank.push(`${id}.${prop.name} (${prop.types})`);
      }
    }

    expect(blank).toEqual([]);
  });

  it('drives all ten sources from one descriptor, with narrowing selects', () => {
    // The ten share a single entry, so `scenario`'s options depend on `page` —
    // the one place a descriptor's overrides are a function. The check that
    // matters is that the narrowing actually tracks sources.js: a scenario
    // added there has to reach the select without a second edit, and the
    // descriptor must never offer a scenario the selected page does not have
    // (which SourceStage would silently swallow by falling back to the first).
    const descriptor = SOURCE_REGISTRY[AUDIO_SOURCES_ID];
    expect(descriptor).toBeDefined();
    expect(entryFor(AUDIO_SOURCES_ID)).toBe(descriptor);

    const broken = [];
    const pageOptions = overridesFor(descriptor, descriptor.args).page.options;
    if (pageOptions.join('|') !== SOURCE_PAGES.map(page => page.id).join('|')) {
      broken.push('page select does not list the ten pages');
    }

    for (const page of SOURCE_PAGES) {
      const resolved = overridesFor(descriptor, { ...descriptor.args, page: page.id });
      const declared = page.scenarios.map(scenario => scenario.id);
      if (resolved.scenario.options.join('|') !== declared.join('|')) {
        broken.push(`${page.id} (scenario select out of step)`);
      }

      // Same rule as a primitive: a required prop with no value renders a
      // broken instance and Vue only warns.
      for (const prop of describeProps(descriptor.component, resolved)) {
        if (prop.required && (descriptor.args || {})[prop.name] === undefined) {
          broken.push(`${page.id}.${prop.name} (required, unset)`);
        }
      }
    }

    // And it has to open on a real one.
    const first = SOURCE_PAGES.find(page => page.id === descriptor.args.page);
    if (!first?.scenarios.some(scenario => scenario.id === descriptor.args.scenario)) {
      broken.push('opens on no scenario');
    }

    expect(broken).toEqual([]);
  });

  it('overrides nothing that does not exist', () => {
    const stale = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      const names = new Set(describeProps(descriptor.component).map(prop => prop.name));
      for (const name of Object.keys(descriptor.overrides || {})) {
        if (!names.has(name)) stale.push(`${id}.${name}`);
      }
      // `class` is not a prop but is a legitimate arg: it is how a descriptor
      // sizes a component the stage would otherwise collapse.
      for (const name of Object.keys(descriptor.args || {})) {
        if (!names.has(name) && name !== 'class') stale.push(`${id}.${name} (arg)`);
      }
    }

    // A renamed prop leaves an override behind that quietly does nothing.
    expect(stale).toEqual([]);
  });
});

/**
 * The second axis: ten source pages, each a list of WebSocket events the canvas
 * replays into the app's own handler before mounting its dispatcher.
 *
 * Three things can rot here and none of them shows on screen. A fabricated
 * metadata key can outlive the field it stands for — the player keeps rendering
 * beautifully from a record nothing consumes. An event can drift from the model
 * that produces it, at which point the page documents a payload the backend
 * never sends. And a scenario can quietly become *unsafe*: the three browser
 * sources dispatch to components that fetch on mount and POST outside
 * `sendCommand`, so the properties that keep this page from driving the
 * appliance are pinned here rather than left to the comments explaining them.
 *
 * The checks against `ws_events.py` and `audio_state.py` are the anti-invention
 * half, and they are the reason a scenario is named after what it *sends*:
 * every token of an id is a field the backend declares, so a name cannot
 * describe a state that does not exist. Reading the `.py` here — at test time,
 * in Node — is also the only place either file is touched: nothing about this
 * page depends on a running backend, or on a running unit.
 */
describe('component gallery source pages', () => {
  const EVENTS = allEvents();
  const METADATA = allMetadata();

  it('read a plausible surface', () => {
    // A page list that collapsed to nothing would make every check below pass.
    expect(SOURCE_PAGES.length).toBe(ALL_AUDIO_SOURCES.length);
    expect(EVENTS.length).toBeGreaterThan(30);
    expect(Object.keys(SOURCE_REGISTRY)).toHaveLength(1);
    // And the two models have to have been read, or every check derived from
    // them passes against an empty string.
    expect(WS_EVENTS_PY).toContain('class SourceStateChanged');
    expect(AUDIO_STATE_PY).toContain('class SourceState');
  });

  it('covers exactly the sources the app ships', () => {
    // Derived from the shared constant, which is itself pinned to the backend
    // enum — a source added there arrives here as a missing page, not as a gap
    // a reader would have to notice.
    const covered = SOURCE_PAGES.map(page => page.source).sort();

    expect(covered).toEqual([...ALL_AUDIO_SOURCES].sort());
  });

  it('derives every scenario name instead of writing one', () => {
    // The rule the page is built on, and the one worth a test of its own: an id
    // is `scenarioId(events)` and nothing else. A hand-written one is how
    // "small cover" and "sender stopped" came to name two scenarios that render
    // the same screen — a name invented from a cause the UI never reads. Any
    // literal scenario object added later fails here rather than shipping.
    const problems = [];

    for (const page of SOURCE_PAGES) {
      if (sourcePageById(page.id) !== page) problems.push(`${page.id} (not findable by id)`);
      if ((page.summary || '').length < 40) problems.push(`${page.id} (thin summary)`);
      if (!['dispatcher', 'browser'].includes(page.via)) problems.push(`${page.id} (unknown via: ${page.via})`);
      if (!page.scenarios?.length) problems.push(`${page.id} (no scenarios)`);

      const seen = new Set();
      for (const scenario of page.scenarios || []) {
        if (seen.has(scenario.id)) problems.push(`${page.id}.${scenario.id} (duplicate id)`);
        seen.add(scenario.id);
        // The note is the only prose a scenario gets, and it is where the gate
        // that produced this screen is named.
        if ((scenario.note || '').length < 40) problems.push(`${page.id}.${scenario.id} (thin note)`);
        if (!scenario.label) problems.push(`${page.id}.${scenario.id} (no label)`);

        if (!scenario.events?.length) {
          problems.push(`${page.id}.${scenario.id} (no events)`);
          continue;
        }
        if (scenario.id !== scenarioId(scenario.events, scenario.browser)) {
          problems.push(`${page.id}.${scenario.id} (id not derived from its events)`);
        }

        // A record naming another source would render the wrong page's view and
        // look like a bug in the dispatcher.
        const settled = settledState(scenario);
        if (settled.active_source !== page.source) {
          problems.push(`${page.id}.${scenario.id} (record names ${settled.active_source})`);
        }
      }
    }

    expect(problems).toEqual([]);
  });

  it('emits only events the backend declares, with only the fields it declares', () => {
    // The anti-invention check. A scenario's payload is compared against the
    // model that produces it — the (CATEGORY, TYPE) pair has to exist, and every
    // key in `data` has to be a field of that class or the injected `full_state`.
    // Without this the page could document a payload no source ever sends, which
    // is worse than documenting nothing: it reads as evidence.
    const models = [];
    for (const block of WS_EVENTS_PY.split(/\nclass /).slice(1)) {
      const category = block.match(/^\s+CATEGORY\s*=\s*"([a-z_]+)"/m)?.[1];
      const type = block.match(/^\s+TYPE\s*=\s*"([a-z_]+)"/m)?.[1];
      if (!category || !type) continue;
      // Annotated attributes only: `name: type`, skipping the ClassVars above.
      const fields = [...block.matchAll(/^ {4}([a-z_]+):\s*(?!ClassVar)/gm)].map(match => match[1]);
      models.push({ category, type, fields });
    }

    // A parse that found nothing would let every envelope through.
    expect(models.length).toBeGreaterThan(10);

    const problems = [];
    for (const event of EVENTS) {
      const matches = models.filter(model => model.category === event.category && model.type === event.type);
      if (!matches.length) {
        problems.push(`${event.category}/${event.type} (no model declares this pair)`);
        continue;
      }
      // `source/favorite_*` is a union discriminated by data.source, so a pair
      // can have several models — a key declared by any of them is declared.
      const declared = new Set(matches.flatMap(model => model.fields));
      for (const key of Object.keys(event.data)) {
        if (key === 'full_state') continue; // injected by broadcast(), not a field
        if (!declared.has(key)) problems.push(`${event.category}/${event.type}.${key} (not a field of the model)`);
      }
      // And the envelope's own shape, which `to_envelope` fixes.
      if (Object.keys(event).sort().join() !== 'category,data,origin,timestamp,type') {
        problems.push(`${event.category}/${event.type} (envelope shape)`);
      }
      // `origin` is the event's own `source`, falling back to the category.
      if (event.origin !== (event.data.source ?? event.category)) {
        problems.push(`${event.category}/${event.type} (origin ${event.origin})`);
      }
    }

    expect(problems).toEqual([]);
  });

  it('builds full_state the way the state machine does', () => {
    // `unifiedAudioStore` mirrors this snapshot field for field, so a fabricated
    // one that drifted from `SystemAudioState.to_dict()` would put the gallery a
    // schema behind the app — visible only as a field that silently stops
    // arriving. The two globals are appended by `get_current_state()` rather
    // than the dataclass, hence the pair added by hand here.
    const toDict = AUDIO_STATE_PY.split('def to_dict')[1] ?? '';
    const declared = [...toDict.matchAll(/"([a-z_]+)":/g)].map(match => match[1]);
    expect(declared).toContain('active_source');

    const expected = [...declared, 'multiroom_enabled', 'equalizer_effects_enabled'].sort();

    const problems = [];
    for (const event of EVENTS) {
      const snapshot = event.data.full_state;
      if (Object.keys(snapshot).sort().join() !== expected.join()) {
        problems.push(`${event.origin}/${event.type} (full_state keys)`);
      }
      if (!SOURCE_STATES.includes(snapshot.source_state)) {
        problems.push(`${event.origin}/${event.type} (source_state "${snapshot.source_state}" is not in the enum)`);
      }
    }

    // The enum has to have been read, or the state check above is vacuous.
    expect(SOURCE_STATES).toContain('active');
    expect(problems).toEqual([]);
  });

  it('covers every state the backend can put a source in', () => {
    // The completeness half: the page is a matrix, and a missing column is
    // exactly what a reader cannot notice. `error` is the one this caught —
    // reachable from `BaseAudioSource` on any failed start, drawn by nothing.
    //
    // Only the dispatcher pages are held to it. For radio, podcasts and music
    // library `hasRichDisplay` returns true whatever the state, so an errored
    // one renders the browser exactly as a healthy one does — three more tabs
    // showing the same screen would document nothing. That is itself a finding,
    // and it is written down in those pages' summaries.
    expect(SOURCE_STATES.length).toBeGreaterThan(3);

    const gaps = [];
    for (const page of SOURCE_PAGES.filter(entry => entry.via === 'dispatcher')) {
      const reached = new Set(page.scenarios.map(scenario => settledState(scenario).source_state));
      for (const state of SOURCE_STATES) {
        if (!reached.has(state)) gaps.push(`${page.id} (never reaches "${state}")`);
      }
    }

    expect(gaps).toEqual([]);
  });

  it('invents no metadata field the app does not read', () => {
    // Same check the REGISTRY records get, applied to the half that actually
    // drifts. Rename `album_art_width` in useRichDisplay and the AirPlay gate
    // scenarios stop meaning anything — they would keep rendering a status
    // card, for the wrong reason.
    const missing = METADATA_READERS.filter(file => !existsSync(join(SRC_DIR, file)));
    expect(missing).toEqual([]);

    const consumers = METADATA_READERS.map(file => readFileSync(join(SRC_DIR, file), 'utf8')).join('\n');
    const orphans = [];
    let checked = 0;

    for (const record of METADATA) {
      for (const key of Object.keys(record)) {
        checked += 1;
        // Property access, not a bare word: a key named in a comment is not a
        // key anything reads.
        if (!new RegExp(`\\.${key}\\b`).test(consumers)) orphans.push(key);
      }
    }

    expect(checked).toBeGreaterThan(0);
    expect([...new Set(orphans)]).toEqual([]);
  });

  it('names a scenario only with fields the app branches on', () => {
    // BEHAVIOURAL_FIELDS is what a name is spelled from, so it decides which
    // differences the page presents as different scenarios. Left unchecked it
    // becomes a taxonomy of our own: a field nothing branches on splits one
    // screen into two tabs, and a field that stops being read leaves two
    // scenarios that were distinct silently documenting the same thing.
    const deciders = DECIDERS.map(file => readFileSync(join(SRC_DIR, file), 'utf8')).join('\n');
    expect(deciders).toContain('UNTRUSTED_SENDER_MIN_ARTWORK_PX');

    const unread = BEHAVIOURAL_FIELDS.filter(field => !new RegExp(`\\.${field}\\b`).test(deciders));
    expect(unread).toEqual([]);

    // And every fact a name spells has to come from that list — the ids are
    // derived, so this fails only if `scenarioId` grew a second source of
    // tokens, which is the drift that would let prose back in.
    const strays = [];
    for (const page of SOURCE_PAGES) {
      for (const scenario of page.scenarios) {
        const conditions = scenario.browser?.condition ?? [];
        const facts = scenario.id.split(' ').slice(1).filter(token => !conditions.includes(token));
        for (const fact of facts) {
          if (!BEHAVIOURAL_FIELDS.includes(fact.split('=')[0])) strays.push(`${page.id}.${scenario.id} (${fact})`);
        }
      }
    }

    expect(strays).toEqual([]);
  });

  it('spells a browser condition with fields its own fixture carries', () => {
    // The three browsers are always `active`, so their names need a second axis
    // — the catalogue condition, which arrives over HTTP. Same rule as the
    // events axis, enforced the same way: a token has to name something the
    // scenario itself supplies, or it is prose again. `stations=0` passes
    // because the fixture answers `{ stations: [] }`; `no-favourites` would not.
    const keysOf = (node, into = new Set()) => {
      if (!node || typeof node !== 'object') return into;
      if (Array.isArray(node)) {
        node.forEach(item => keysOf(item, into));
        return into;
      }
      for (const [key, value] of Object.entries(node)) {
        into.add(key);
        keysOf(value, into);
      }
      return into;
    };

    const strays = [];
    let checked = 0;

    for (const page of SOURCE_PAGES) {
      for (const scenario of page.scenarios) {
        const browser = scenario.browser;
        if (!browser) continue;
        if (!browser.condition?.length) {
          strays.push(`${page.id}.${scenario.id} (no condition)`);
          continue;
        }
        const supplied = keysOf({ api: browser.api, seed: browser.seed, props: browser.props, player: browser.player });
        for (const token of browser.condition) {
          checked += 1;
          const field = token.split('=')[0];
          if (!supplied.has(field)) strays.push(`${page.id}.${scenario.id} (${field} is in no fixture)`);
        }
      }
    }

    expect(checked).toBeGreaterThan(5);
    expect(strays).toEqual([]);
  });

  it('routes every event it emits, and opens no socket to carry one', () => {
    // Two halves of the same promise. The stage's DISPATCH map is what makes
    // "the app applied it" true rather than a claim — a pair with no row is
    // silently dropped, and the scenario renders the one before it.
    const stage = readFileSync(join(SRC_DIR, 'components/gallery/SourceStage.vue'), 'utf8');
    const unrouted = [...new Set(EVENTS.map(event => `${event.category}.${event.type}`))]
      .filter(pair => !stage.includes(`'${pair}'`));
    expect(unrouted).toEqual([]);

    // And the events must stay fabricated. `useWebSocket()` registers a
    // subscriber, which *connects* — same origin, so on a unit the gallery
    // would start receiving the living room's real state mid-scenario. Today no
    // catalogued component subscribes, which is luck rather than a rule; this
    // is the rule.
    const galleryDir = join(SRC_DIR, 'components/gallery');
    const wired = readdirSync(galleryDir)
      .filter(name => name.endsWith('.vue') || name.endsWith('.js'))
      .filter(name => /useWebSocket|services\/websocket/.test(readFileSync(join(galleryDir, name), 'utf8')));

    expect(wired).toEqual([]);
  });

  it('keeps the browser sources away from their own components', () => {
    // The load-bearing safety rule. Radio, Podcasts and Music Library dispatch
    // to *Source.vue files that fetch on mount and whose play paths POST
    // straight through apiCall — outside the one call CanvasApp neuters. A
    // scenario of theirs reaches AudioSourceView only while `transitioning`,
    // which short-circuits useRichDisplay before it can name the source; every
    // other one must carry its own stand-in.
    const unsafe = [];

    for (const page of SOURCE_PAGES.filter(entry => entry.via === 'browser')) {
      for (const scenario of page.scenarios) {
        if (!scenario.browser && !settledState(scenario).transitioning) {
          unsafe.push(`${page.id}.${scenario.id} (would mount the real ${page.source} browser)`);
        }
        if (!scenario.browser) continue;
        if (!scenario.browser.view) unsafe.push(`${page.id}.${scenario.id} (no view)`);
        // Each source's info block reads different fields — radio a station and
        // maybe a track, podcasts an episode and its show, music library a
        // title and artist — so a player is checked against its own source's
        // shape. A missing one renders a blank line rather than failing.
        const player = scenario.browser.player;
        if (!player) continue;
        const REQUIRED = {
          radio: () => !!player.station?.name && (!player.track || (!!player.track.title && !!player.track.artist)),
          podcast: () => !!player.episodeName,
          music_library: () => !!player.title && !!player.artist
        };
        if (!REQUIRED[page.source]?.()) unsafe.push(`${page.id}.${scenario.id} (player shape)`);
      }
    }

    // And a dispatcher page may not grow a stand-in: it would stop exercising
    // the rule the page exists to document.
    for (const page of SOURCE_PAGES.filter(entry => entry.via === 'dispatcher')) {
      for (const scenario of page.scenarios) {
        if (scenario.browser) unsafe.push(`${page.id}.${scenario.id} (stand-in on a dispatcher page)`);
      }
    }

    expect(unsafe).toEqual([]);
  });

  it('seeds only store fields that exist and can be written', () => {
    // The failure this catches has no symptom on screen. `storagesLoaded`,
    // `artistIndex` and `scanning` are private to musicLibraryStore, and
    // `likedSongsCount` is a computed — assigning any of them lands a property
    // nothing reads, so the view renders its empty state and the scenario looks
    // like a deliberate choice rather than a seed that missed. Writing a
    // sentinel and reading it back is the only check that catches both halves
    // (absent, and present but derived).
    setActivePinia(createPinia());
    const stores = {
      radio: useRadioStore(),
      musicLibrary: useMusicLibraryStore(),
      podcast: usePodcastStore()
    };

    const orphans = [];
    let checked = 0;

    for (const page of SOURCE_PAGES) {
      for (const scenario of page.scenarios) {
        for (const [name, fields] of Object.entries(scenario.browser?.seed ?? {})) {
          const store = stores[name];
          if (!store) {
            orphans.push(`${page.id}.${scenario.id} (no store "${name}")`);
            continue;
          }
          for (const key of Object.keys(fields)) {
            checked += 1;
            if (!(key in store)) {
              orphans.push(`${page.id}.${scenario.id}.${name}.${key} (not exported)`);
              continue;
            }
            // A string, not a Symbol: Vue's reactive set trap rejects a Symbol
            // outright, which would fail every key rather than the derived ones.
            // A computed shows up either way — Vue throws on the write in dev,
            // and returns the derived value if it does not, so both are caught.
            const sentinel = `__probe_${key}__`;
            const before = store[key];
            try {
              store[key] = sentinel;
              if (store[key] !== sentinel) orphans.push(`${page.id}.${scenario.id}.${name}.${key} (read-only)`);
              store[key] = before;
            } catch {
              orphans.push(`${page.id}.${scenario.id}.${name}.${key} (read-only)`);
            }
          }
        }
      }
    }

    // A prime names an action by string, so the same question applies to it:
    // `loadStations` taking a `favoritesOnly` argument is why radio's grid was
    // empty the first time, and a renamed action would be a silent no-op.
    for (const page of SOURCE_PAGES) {
      for (const scenario of page.scenarios) {
        for (const [name, action] of scenario.browser?.prime ?? []) {
          checked += 1;
          if (typeof stores[name]?.[action] !== 'function') {
            orphans.push(`${page.id}.${scenario.id}.${name}.${action} (not an action)`);
          }
        }
      }
    }

    expect(checked).toBeGreaterThan(0);
    expect([...new Set(orphans)]).toEqual([]);
  });

  it('names a view it can mount and a header key that is translated', () => {
    // A `view` the stage has no entry for renders nothing at all, and a
    // titleKey with no string behind it renders the key — both of which read as
    // "the browser is broken" rather than "the fixture is wrong".
    const stage = readFileSync(join(SRC_DIR, 'components/gallery/SourceStage.vue'), 'utf8');
    const english = JSON.parse(readFileSync(join(SRC_DIR, 'locales/english.json'), 'utf8'));
    const lookup = path => path.split('.').reduce((node, key) => (node ?? {})[key], english);

    const problems = [];
    let checked = 0;

    for (const page of SOURCE_PAGES) {
      for (const scenario of page.scenarios) {
        const browser = scenario.browser;
        if (!browser) continue;
        checked += 1;

        // The stage's VIEWS map is the closed list; a name missing from it is
        // an `<component :is="undefined">`, which Vue renders as nothing.
        if (!new RegExp(`'${browser.view}':`).test(stage)) {
          problems.push(`${page.id}.${scenario.id} (view "${browser.view}" not in VIEWS)`);
        }
        if (typeof lookup(browser.layout?.titleKey) !== 'string') {
          problems.push(`${page.id}.${scenario.id} (titleKey "${browser.layout?.titleKey}")`);
        }
      }
    }

    expect(checked).toBeGreaterThan(0);
    expect(problems).toEqual([]);
  });

  it('reproduces each browser source’s own transport, by its own class names', () => {
    // The #controls slot has a default — a lone play/pause — and all three
    // browser sources replace it: radio with a text Button and a favourite,
    // podcasts with a seek pair and a speed dropdown, music library with a
    // five-button row. Falling back to the default is silent, and it is what
    // the gallery did until someone noticed the wrong button on screen.
    //
    // Checked by class name because the class *is* the contract: AudioPlayer's
    // CSS lays these rows out, sizes them and hides half of them by name, so a
    // rename in the source leaves the gallery rendering an unstyled row that
    // still looks plausible.
    //
    // And the layout has to live in AudioPlayer. A `<style scoped>` reaches only
    // the markup its own file authors, so the same rule written in the source
    // dresses the app's copy of the row and leaves the stage's copy bare — which
    // is exactly what happened: radio's stop button was not full width, the
    // speed selector was not pinned left, and the music-library transport row
    // was not a flex column, on a page whose entire promise is that it renders
    // what the unit renders. Both halves are checked, because either one alone
    // passes while the screen is wrong.
    const stage = readFileSync(join(SRC_DIR, 'components/gallery/SourceStage.vue'), 'utf8');
    const player = readFileSync(join(SRC_DIR, 'components/audio/AudioPlayer.vue'), 'utf8');

    /** A file's `<style>` blocks, comments stripped — a class named in prose is not a rule. */
    const stylesOf = source =>
      (source.match(/<style[\s\S]*?<\/style>/g) || []).join('\n').replace(/\/\*[\s\S]*?\*\//g, '');

    /** Everything before `<script>`: where a class is *used* rather than styled. */
    const templateOf = source => source.split(/<script[\s>]/)[0];

    const CONTRACTS = [
      {
        owner: 'components/radio/RadioSource.vue',
        classes: ['radio-controls', 'radio-controls-main', 'horizontal-layout']
      },
      {
        owner: 'components/podcasts/PodcastSource.vue',
        classes: ['speed-selector', 'desktop-only']
      },
      {
        owner: 'components/music-library/MusicLibrarySource.vue',
        classes: ['ml-controls', 'ml-transport-main', 'ml-transport-extra']
      }
    ];

    const playerStyles = stylesOf(player);
    const broken = [];
    let checked = 0;

    for (const { owner, classes } of CONTRACTS) {
      const source = readFileSync(join(SRC_DIR, owner), 'utf8');
      const ownerStyles = stylesOf(source);

      for (const name of classes) {
        checked += 1;
        if (!stage.includes(name)) broken.push(`${name} (missing from SourceStage)`);
        if (!templateOf(source).includes(name)) broken.push(`${name} (gone from ${owner})`);
        // A selector, not a mention: `.name` followed by what can continue one
        // — including `)`, since AudioPlayer reaches slotted markup via :deep().
        const selector = new RegExp(`\\.${name}[\\s.,:>){]`);
        if (!selector.test(playerStyles)) broken.push(`${name} (AudioPlayer styles it no more)`);
        if (selector.test(ownerStyles)) {
          broken.push(`${name} (styled in ${owner}'s scoped CSS — the stage's copy cannot see it)`);
        }
      }
    }

    // A CONTRACTS list that emptied itself would pass vacuously.
    expect(checked).toBeGreaterThan(5);
    expect(broken).toEqual([]);
  });

  it('shows both of StationCard’s branches, and fetches neither', () => {
    // The radio grid is the one fixture that documents a *card* rather than a
    // layout, and StationCard has two branches that look nothing alike: an
    // image, or the generated monogram. A fixture that drifted to one branch
    // would still render a convincing grid while documenting half the card.
    const grid = sourcePageById(`${SOURCE_PAGE_PREFIX}radio`)
      ?.scenarios.find(scenario => scenario.browser?.condition?.includes('stations=6'))
      ?.browser.api['/api/radio/stations'].stations;

    expect(grid?.length).toBeGreaterThan(3);
    expect(grid.filter(station => station.favicon).length).toBeGreaterThan(0);
    expect(grid.filter(station => !station.favicon).length).toBeGreaterThan(0);

    // And every image is same-origin. getFaviconUrl sends anything else to
    // /api/radio/favicon, i.e. one outbound fetch per card from the unit — the
    // property this page gives up its third branch to keep.
    const external = grid.map(station => station.favicon).filter(favicon => favicon && !favicon.startsWith('/'));
    expect(external).toEqual([]);
  });

  it('resolves a station’s image the same way in the grid and in the pane', () => {
    // Two halves of one screen, and they read different fields: the card takes
    // `favicon` through getFaviconUrl, the pane takes the transcribed
    // `station.artwork`. Nothing but this ties them together, so a station that
    // shows its logo in the grid and a monogram in the player beside it is a
    // silent, entirely plausible-looking fixture bug.
    const mismatched = [];
    let checked = 0;

    for (const scenario of sourcePageById(`${SOURCE_PAGE_PREFIX}radio`)?.scenarios ?? []) {
      const station = scenario.browser?.props?.currentStation;
      const pane = scenario.browser?.player?.station;
      if (!station || !pane) continue;
      checked += 1;
      if (pane.name !== station.name) mismatched.push(`${scenario.id} (name)`);
      if ((pane.artwork || '') !== (station.favicon || '')) mismatched.push(`${scenario.id} (artwork)`);
    }

    expect(checked).toBeGreaterThan(1);
    expect(mismatched).toEqual([]);
  });

  it('blocks every write the canvas could make', () => {
    // The browsing views act through apiCall rather than sendCommand — radio's
    // playStation POSTs, a playlist is created, a share is mounted. If the
    // harness stops covering a verb, the gallery starts driving the appliance
    // and nothing says so.
    const http = readFileSync(join(SRC_DIR, 'components/gallery/canvasHttp.js'), 'utf8');
    const verbs = http.match(/const WRITE_VERBS = \[([^\]]+)\]/)?.[1] ?? '';

    for (const verb of ['post', 'put', 'patch', 'delete']) {
      expect(verbs).toContain(verb);
    }
    // And it has to actually be installed, or the list above is decoration.
    expect(CANVAS).toMatch(/installApiHarness\(/);
  });

  it('rests on two guards that are still there', () => {
    // The rule above is only safe because of these two, and both live in files
    // this suite would otherwise never look at. Asserted as text because that
    // is what they are — one line each, and deleting either is silent.
    const richDisplay = readFileSync(join(SRC_DIR, 'composables/useRichDisplay.js'), 'utf8');

    // `transitioning` short-circuiting is what makes a browser source's
    // `starting` scenario reach the status card instead of its own component.
    expect(richDisplay).toMatch(/!transitioning\s*&&/);

    // And every action the seven dispatcher sources offer — the Bluetooth
    // disconnect, cdStore's eject and playTrack, AudioPlayerFull's transport —
    // goes through sendCommand, which the canvas replaces with a reporter.
    expect(CANVAS).toMatch(/context\.unified\.sendCommand\s*=/);
  });

  it('hands the source stage the viewport its preset is named after', () => {
    // The stage pads itself, which is right for a primitive sitting on it and
    // wrong for a source: the presets are labelled "1280 × 800 — the unit", and
    // an inset stage hands the source 1232 × 752 instead — a different aspect
    // ratio (which is what every mobile branch in the app switches on), a
    // different column count in every grid, and a different share of the row for
    // the 340 px player pane. So the padding is zeroed for this one selection.
    // Both halves are asserted because either alone is silently a no-op: the
    // class with no rule pads anyway, the rule with no binding never applies.
    expect(CANVAS).toMatch(/'canvas--bleed':\s*bleed/);
    expect(CANVAS).toMatch(/bleed\s*=\s*computed\(\(\)\s*=>\s*id\.value === AUDIO_SOURCES_ID\)/);

    // Zeroing the pad rather than the padding is load-bearing: the stage's
    // height is `100vh - 2 * var(--canvas-pad)`, so a rule that set `padding: 0`
    // alone would leave the source 48 px taller than its own viewport.
    expect(CANVAS).toMatch(/\.canvas\.canvas--bleed\s*\{\s*--canvas-pad:\s*0/);
  });
});

/**
 * The third axis: the design tokens, parsed out of design-system.css rather
 * than restated beside it.
 *
 * Deriving the page from the stylesheet removes the drift a hand-written token
 * list would have, and replaces it with a narrower one: the parse can quietly
 * stop finding things. A renamed `=== SECTION ===` marker, a heading nobody
 * assigned to a page, a utility class whose font-size token moved — each of
 * them subtracts a block from the page and reports nothing, which is the exact
 * failure mode a design-system index cannot afford. So the mapping is checked
 * in both directions, the way the catalogue's is: every section in the file is
 * on a page or excluded *with a reason*, and every exclusion still names a
 * section that exists.
 */
describe('component gallery foundations', () => {
  const CSS = readFileSync(join(SRC_DIR, 'assets/styles/design-system.css'), 'utf8');
  const RENDERER = readFileSync(join(SRC_DIR, 'components/gallery/FoundationsPage.vue'), 'utf8');
  const TITLES = SECTIONS.map(section => section.title);
  const TOKENS = SECTIONS.flatMap(section => section.tokens);

  /** Kinds the renderer declares a branch for — `block.kind === 'swatch'`, … */
  const DRAWABLE = [...RENDERER.matchAll(/block\.kind === '([a-z]+)'/g)].map(match => match[1]);

  it('read a plausible surface', () => {
    // Every check below is over what the parse produced, so a parse that fell
    // over — a moved `:root`, a comment that swallowed the file — would make
    // them all vacuously pass.
    expect(TITLES.length).toBeGreaterThan(10);
    expect(TOKENS.length).toBeGreaterThan(60);
    expect(TYPE_STYLES.length).toBeGreaterThan(5);
    expect(Object.keys(MOBILE).length).toBeGreaterThan(5);
    expect(FOUNDATION_PAGES.length).toBeGreaterThan(3);
    expect(DRAWABLE.length).toBeGreaterThan(3);

    // The `:root` body is sliced to its first closing brace. TRANSITIONS is the
    // last section in it, so finding it proves the slice reached the end rather
    // than stopping at something that looked like one.
    expect(TITLES).toContain('TRANSITIONS');
  });

  it('parses declarations, not prose', () => {
    // The comment branch of the regex is what keeps a token named inside a
    // paragraph out of the list, and a value from swallowing the comment that
    // follows it. Both failures look like a plausible page.
    const malformed = TOKENS
      .filter(token => !/^--[\w-]+$/.test(token.name) || !token.value || /[{}]|\/\*/.test(token.value))
      .map(token => token.name);

    expect(malformed).toEqual([]);
  });

  it('accounts for every section the stylesheet declares', () => {
    const owned = new Set(FOUNDATION_PAGES.flatMap(page => page.sections));
    const orphans = TITLES.filter(title => !owned.has(title) && !EXCLUDED_SECTIONS[title]);

    // A section added to design-system.css that reaches no page is invisible on
    // the page that is supposed to be the design system's index. Put it on one,
    // or say in EXCLUDED_SECTIONS why it does not belong there.
    expect(orphans).toEqual([]);
  });

  it('excludes nothing absent, nothing owned, and nothing without a reason', () => {
    const owned = new Set(FOUNDATION_PAGES.flatMap(page => page.sections));
    const problems = [];

    for (const [title, reason] of Object.entries(EXCLUDED_SECTIONS)) {
      if (!TITLES.includes(title)) problems.push(`${title} (no such section)`);
      if (owned.has(title)) problems.push(`${title} (also on a page)`);
      if ((reason || '').length < 40) problems.push(`${title} (thin reason)`);
    }

    // An exclusion that outlives its section is the same stale-whitelist failure
    // the catalogue side is checked for.
    expect(problems).toEqual([]);
  });

  it('gives every page an id it can be reached by, and something to read', () => {
    const problems = [];
    const seen = new Set();

    for (const page of FOUNDATION_PAGES) {
      if (!page.id.startsWith(FOUNDATION_PAGE_PREFIX)) problems.push(`${page.id} (unprefixed)`);
      if (seen.has(page.id)) problems.push(`${page.id} (duplicate id)`);
      seen.add(page.id);
      // The route carries the id, and the view resolves it back through this.
      if (foundationPageById(page.id) !== page) problems.push(`${page.id} (not findable by id)`);
      if ((page.summary || '').length < 40) problems.push(`${page.id} (thin summary)`);
      if (!page.sections.length) problems.push(`${page.id} (owns no section)`);
    }

    expect(problems).toEqual([]);
  });

  it('builds a drawable, non-empty block for every section a page owns', () => {
    const problems = [];

    for (const page of FOUNDATION_PAGES) {
      // A section title that no longer matches the stylesheet is dropped by
      // `sectionBlock`, which is silent: the page renders one block short.
      const built = page.blocks.filter(block => page.sections.includes(block.title));
      if (built.length !== page.sections.length) problems.push(`${page.id} (${built.length}/${page.sections.length} sections built)`);

      for (const block of page.blocks) {
        // An unmapped kind falls through to the plain name/value list — the
        // colours would render as text, and nothing would say so.
        if (!DRAWABLE.includes(block.kind)) problems.push(`${page.id}.${block.title} (kind "${block.kind}" has no branch)`);
        if (block.tokens && !block.tokens.length) problems.push(`${page.id}.${block.title} (no tokens)`);
      }
    }

    expect(problems).toEqual([]);
  });

  it('reads each typography class off the stylesheet, tokens and all', () => {
    const declared = new Set(SECTIONS.find(section => section.title === 'TEXT STYLES')?.tokens.map(token => token.name));
    const problems = [];

    expect(declared.size).toBeGreaterThan(0);

    for (const style of TYPE_STYLES) {
      // Read from the file independently of the parse: a class the parse
      // invented, or one it kept after a rename, renders a specimen in the
      // browser's fallback font and looks like a font-loading bug.
      if (!CSS.includes(`.${style.className} {`)) problems.push(`${style.className} (no such class)`);
      if (!style.family || style.family.includes('var(')) problems.push(`${style.className} (no family)`);

      for (const field of ['size', 'lineHeight', 'letterSpacing']) {
        const operand = style[field];
        if (!operand) {
          problems.push(`${style.className}.${field} (declares no token)`);
        } else if (!declared.has(operand.token)) {
          // The class reads a token TEXT STYLES does not declare — the specimen
          // would report an empty value beside a correctly rendered sample.
          problems.push(`${style.className}.${field} (${operand.token} not in TEXT STYLES)`);
        }
      }
    }

    expect(problems).toEqual([]);
  });

  it('overrides only tokens that exist below 4:3', () => {
    const base = new Set(TOKENS.map(token => token.name));
    const orphans = Object.keys(MOBILE).filter(name => !base.has(name));

    // A mobile-only token is undefined on every other viewport, which is a
    // stylesheet bug this page is in a position to catch — and, short of that,
    // a value shown beside a base value that was never parsed.
    expect(orphans).toEqual([]);
  });
});
