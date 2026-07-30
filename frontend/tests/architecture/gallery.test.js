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
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { GROUPS, ENTRIES, SCOPE, EXCLUDED, isScreen, entriesOf } from '../../src/components/gallery/catalog.js';
import { REGISTRY } from '../../src/components/gallery/registry.js';
import { describeProps } from '../../src/components/gallery/controls.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

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
