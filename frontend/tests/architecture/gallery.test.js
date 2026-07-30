// frontend/tests/architecture/gallery.test.js
/**
 * Structural guardrail over the primitive gallery at /components.
 *
 * The page is documentation, and documentation that silently falls behind the
 * code is worse than none: a reader who finds 23 primitives listed has no way to
 * know a 24th shipped. So the catalogue is checked against the filesystem in
 * both directions — a new `components/ui/*.vue` with no entry fails, and an
 * entry whose file was deleted or renamed fails too, the way a stale
 * `.stylelintrc.cjs` whitelist entry does.
 *
 * The second half guards the controls panel, which is *derived* rather than
 * declared (see controls.js). Derivation has one failure mode worth catching:
 * an enum prop whose validator is written in a shape the parser cannot read
 * downgrades to a free-text box — a control that looks like it works, offers
 * every wrong value, and never errors. So every String prop carrying a validator
 * must resolve to a list of options, either parsed or explicitly overridden in
 * registry.js. That is the check that keeps "derived" honest.
 *
 * Mounts nothing and asserts no markup: this is the one kind of test that can
 * cover a page whose whole purpose is to be looked at.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve, join } from 'node:path';
import { GROUPS, ENTRIES, entriesOf } from '../../src/components/gallery/catalog.js';
import { REGISTRY } from '../../src/components/gallery/registry.js';
import { describeProps } from '../../src/components/gallery/controls.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const UI_DIR = join(SRC_DIR, 'components/ui');

/** Every primitive on disk, by path relative to `src/`. */
const UI_FILES = readdirSync(UI_DIR)
  .filter(name => name.endsWith('.vue'))
  .map(name => `components/ui/${name}`)
  .sort();

const VIEW = readFileSync(join(SRC_DIR, 'views/ComponentsView.vue'), 'utf8');

/**
 * Every primitive needs a playground descriptor. There is no opt-out: the canvas
 * is an iframe with its own viewport and its own stores, so even the three
 * `position: fixed`, store-driven ones render there.
 */
const PLAYABLE = ENTRIES;

describe('component gallery catalogue', () => {
  it('read a plausible surface', () => {
    // A broken glob would make every assertion below vacuously pass.
    expect(UI_FILES.length).toBeGreaterThan(15);
    expect(ENTRIES.length).toBeGreaterThan(15);
    expect(Object.keys(REGISTRY).length).toBeGreaterThan(15);
  });

  it('lists every primitive in components/ui', () => {
    const listed = new Set(ENTRIES.map(entry => entry.file));
    const missing = UI_FILES.filter(file => !listed.has(file));

    // A primitive that reaches `ui/` without an entry is invisible on the page
    // that is supposed to be the design system's index.
    expect(missing).toEqual([]);
  });

  it('carries no entry for a file that no longer exists', () => {
    const stale = ENTRIES
      .map(entry => entry.file)
      .filter(file => !existsSync(join(SRC_DIR, file)));

    expect(stale).toEqual([]);
  });

  it('names each primitive after its file', () => {
    // GalleryItem looks entries up by `id`, and the demos pass the component's
    // own name — a mismatch renders the "missing from catalog.js" placeholder.
    const mismatched = ENTRIES
      .filter(entry => entry.file !== `components/ui/${entry.id}.vue`)
      .map(entry => `${entry.id} -> ${entry.file}`);

    expect(mismatched).toEqual([]);
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
        if (prop.required && (descriptor.args || {})[prop.name] === undefined) {
          gaps.push(`${id}.${prop.name}`);
        }
      }
    }

    // A required prop left unset renders a broken instance, and Vue only warns.
    expect(gaps).toEqual([]);
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
      }
    }

    expect(unresolved).toEqual([]);
  });

  it('overrides nothing that does not exist', () => {
    const stale = [];

    for (const [id, descriptor] of Object.entries(REGISTRY)) {
      const names = new Set(describeProps(descriptor.component).map(prop => prop.name));
      for (const name of Object.keys(descriptor.overrides || {})) {
        if (!names.has(name)) stale.push(`${id}.${name}`);
      }
      // `class` is not a prop but is a legitimate arg (it sizes LazyImage).
      for (const name of Object.keys(descriptor.args || {})) {
        if (!names.has(name) && name !== 'class') stale.push(`${id}.${name} (arg)`);
      }
    }

    // A renamed prop leaves an override behind that quietly does nothing.
    expect(stale).toEqual([]);
  });
});
