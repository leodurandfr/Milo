// frontend/tests/architecture/settingsCatalog.test.js
/**
 * Structural guardrail over SettingsModal's category surface — the concept its
 * `git log` says keeps costing. Its 25 `fix` commits are presentational (modal
 * height, scroll, cross-fade), but ~20 of its 120 commits are a feature coming
 * to register its own settings page, and that used to mean six edits in six
 * places: icon import, component import, grid row in the right section,
 * v-else-if mount, header-title row, and sometimes the section gate. Four of the
 * six restated the same three facts, and the last removal (93818762, dropping
 * the Podcast page) got five of the six — leaving the "Sources" heading rendered
 * over an empty grid for anyone who kept only Podcasts.
 *
 * A category is now declared once, in HOME_SECTIONS. These rules keep the pieces
 * that genuinely stayed separate — the component import and its mount — tied to
 * that declaration, and pin the two facts the previous pass fixed.
 *
 * They are STRUCTURAL on purpose: a behavioural test only covers the view
 * someone thought to write one for, while these fail on the *next* category
 * added, because they know the shape of the tables and nothing about which
 * category is supposed to exist.
 *
 * Known limit, stated rather than papered over: rule 4 checks that the header
 * actions' outer gate *mentions* each inner condition, not that the disjunction
 * is logically equivalent. A gate reading `showFanToggle && false` still names
 * it. Proving equivalence needs evaluation, not parsing; what recurred is a
 * condition being forgotten entirely, and that is what this catches.
 *
 * Every extraction asserts it found a plausible surface first — a broken parse
 * must fail loudly, not pass on an empty set.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const MODAL = join(SRC_DIR, 'components/settings/SettingsModal.vue');

const source = readFileSync(MODAL, 'utf8');

/** Comments are stripped: a rule must read code, never the prose above it. */
function stripComments(text) {
  return text
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
    .replace(/^\s*\/\/.*$/gm, '');
}

const CODE = stripComments(source);

function block(re, what) {
  const m = CODE.match(re);
  if (!m) throw new Error(`${what} not found in SettingsModal.vue — the extractor is broken`);
  return m[1];
}

/** Views offered as a tile on the settings home screen. */
function catalogViews() {
  const table = block(/const HOME_SECTIONS = \[([\s\S]*?)\n\];/, 'HOME_SECTIONS');
  const sections = [...table.matchAll(/key: '([\w-]+)',\s*titleKey: '([\w.]+)'/g)].map(m => m[1]);
  const views = [...table.matchAll(/\{ view: '([\w-]+)', titleKey: '([\w.]+)'/g)].map(m => m[1]);
  if (sections.length < 3 || views.length < 10) {
    throw new Error(`parsed ${sections.length} sections / ${views.length} rows out of HOME_SECTIONS — the extractor is broken`);
  }
  return { sections, views };
}

/** Views reached from somewhere else, so they have no tile — only a title. */
function subViewTitles() {
  const map = block(/const titles = \{([\s\S]*?)\n {2}\};/, 'the sub-view title map');
  const keys = [...map.matchAll(/^\s*'([\w-]+)':/gm)].map(m => m[1]);
  if (keys.length < 5) {
    throw new Error(`parsed ${keys.length} sub-view titles — the extractor is broken`);
  }
  return keys;
}

/** Views the <Transition> actually mounts a component for. */
function mountedViews() {
  const views = [...CODE.matchAll(/v-(?:else-)?if="currentView === '([\w-]+)'/g)].map(m => m[1]);
  if (views.length < 20) {
    throw new Error(`parsed ${views.length} mounted views — the extractor is broken`);
  }
  return [...new Set(views)];
}

/** Every view any handler navigates to, beyond the home tiles. */
function pushedViews() {
  const views = [...CODE.matchAll(/(?:push|goTo)\('([\w-]+)'\)/g)].map(m => m[1]);
  if (views.length < 5) {
    throw new Error(`parsed ${views.length} push()/goTo() targets — the extractor is broken`);
  }
  return [...new Set(views)];
}

/** The header #actions slot: its outer gate, and the conditions used inside it. */
function headerActions() {
  const m = CODE.match(/<template v-if="([^"]+)" #actions>([\s\S]*?)<\/template>\s*<\/NavigationHeader>/);
  if (!m) throw new Error('the header #actions slot was not found — the extractor is broken');
  const inner = [...m[2].matchAll(/\sv-if="([^"]+)"/g)].map(x => x[1].trim());
  if (inner.length < 4) {
    throw new Error(`parsed ${inner.length} header actions — the extractor is broken`);
  }
  return { gate: m[1], inner };
}

/** Every .vue under src/, so "mounted once" is asked of the whole app. */
function vueFiles(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory()
      ? vueFiles(full)
      : full.endsWith('.vue') ? [full] : [];
  });
}

const CATALOG = catalogViews();
const SUB_VIEWS = subViewTitles();
const MOUNTED = mountedViews();
const PUSHED = pushedViews();
const ACTIONS = headerActions();

describe('SettingsModal category catalog', () => {
  it('parsed a plausible surface', () => {
    // Guards every rule below: an empty table would make them vacuous.
    expect(CATALOG.sections).toContain('sources');
    expect(CATALOG.views).toContain('network');
    expect(SUB_VIEWS).toContain('home');
    expect(MOUNTED).toContain('updates');
    expect(ACTIONS.inner.length).toBeGreaterThanOrEqual(4);
  });

  it('every navigable view is mounted', () => {
    // A catalog row or a push() with no mount renders an empty content area:
    // the tile reacts, the header retitles, and nothing appears.
    const navigable = [...new Set([...CATALOG.views, ...PUSHED])].filter(v => v !== 'home');
    expect(navigable.filter(v => !MOUNTED.includes(v))).toEqual([]);
  });

  it('every mounted view is titled exactly once', () => {
    // Untitled falls back to the generic "Settings" header; titled twice is the
    // 16-row duplication this pass removed, reintroduced one row at a time.
    const untitled = MOUNTED.filter(v => !CATALOG.views.includes(v) && !SUB_VIEWS.includes(v));
    const twice = MOUNTED.filter(v => CATALOG.views.includes(v) && SUB_VIEWS.includes(v));
    expect({ untitled, twice }).toEqual({ untitled: [], twice: [] });
  });

  it('the section gate is derived, never restated', () => {
    // A hand-kept "does this section have anything in it" condition is exactly
    // what went stale when the Podcast row was removed and its flag was not.
    const visible = block(/const visibleSections = computed\(([\s\S]*?)\n\);/, 'visibleSections');
    expect(visible).toMatch(/rows\.length/);
    expect(CODE).not.toMatch(/hasAny\w+ = computed/);
  });

  it('the header actions gate names every condition used inside it', () => {
    // The outer v-if decides whether the slot is passed at all, so an action
    // whose condition it omits never renders — silently, with no error.
    const missing = ACTIONS.inner.filter(cond => !ACTIONS.gate.includes(cond));
    expect(missing).toEqual([]);
  });

  it('the settings modal has exactly one mount site', () => {
    // MainView carried a second instance with its own open state, invisible to
    // App.vue's pending-client guard — which then opened a second one over it.
    const mounts = vueFiles(SRC_DIR)
      .filter(f => f !== MODAL)
      .filter(f => /<SettingsModal[\s/>]/.test(readFileSync(f, 'utf8')));
    expect(mounts.map(f => f.slice(SRC_DIR.length + 1))).toEqual(['App.vue']);
  });
});
