// frontend/tests/architecture/screenInset.test.js
/**
 * The kiosk's enclosure hides a strip of the panel on all four edges, and
 * `--screen-inset` (design-system.css, written by useScreenInset on the kiosk
 * only) is that strip in CSS px. Content keeps clear of it by ADDING the token
 * to its margin; backgrounds keep reaching the panel's edge and never read it.
 *
 * What breaks, silently and only on the panel: a new piece of fixed chrome,
 * or a shell that holds content at an edge, written with its margin alone —
 * its edge disappears under the bezel, and no screen but the kiosk's shows it.
 * So the stylesheets are read the way the browser reads them, the mobile
 * layout skipped (the kiosk never reaches it, and a phone has no bezel):
 *
 * 1. every rule that fixes an element against the viewport at an offset reads
 *    the token, or is listed in EXEMPT with what makes it right without it;
 * 2. every safe-area `env()` read outside the mobile layout sits in a rule that
 *    reads the token too — the kiosk's own edge margin is the token, env() is 0
 *    there — or is listed in EXEMPT;
 * 3. the shells that hold content without being fixed (SHELLS) keep their read,
 *    and a SHELLS entry that no longer matches a declaration fails, so the list
 *    cannot rot into a comment;
 * 4. no length is written in CSS `mm`, which is a 96-dpi convention and never
 *    the panel's millimeter (utils/screenInset.js converts).
 *
 * The blind spot: an absolutely positioned layer inside a new full-screen
 * surface is seen only once its holder is added to SHELLS.
 * Mounts nothing: it reads the sources the browser reads.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';
import { MOBILE_QUERY } from '../../src/constants/layout.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');
const TOKEN = '--screen-inset';

function walk(dir, pattern) {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return walk(path, pattern);
    return pattern.test(name) ? [path] : [];
  });
}

/** The CSS of a file: a .vue's <style> blocks, a stylesheet whole. */
function cssOf(file) {
  const source = stripComments(readFileSync(file, 'utf8'));
  if (!file.endsWith('.vue')) return source;
  return [...source.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)].map((m) => m[1]).join('\n');
}

/**
 * Every style rule as { file, selector, media, decls }: `media` the @media
 * conditions it is nested in, `decls` its declarations as [property, value]
 * pairs, in order.
 */
function parseRules(file, css) {
  const rules = [];
  const stack = [];
  let start = 0;
  for (let i = 0; i < css.length; i += 1) {
    const c = css[i];
    if (c === '{') {
      stack.push(css.slice(start, i).trim().replace(/\s+/g, ' '));
      start = i + 1;
    } else if (c === '}') {
      const prelude = stack.pop();
      const body = css.slice(start, i);
      start = i + 1;
      if (prelude === undefined || prelude.startsWith('@')) continue;
      const decls = body.split(';')
        .map((d) => d.trim())
        .filter((d) => d.includes(':') && !d.includes('{'))
        .map((d) => {
          const colon = d.indexOf(':');
          return [d.slice(0, colon).trim(), d.slice(colon + 1).trim().replace(/\s+/g, ' ')];
        });
      const media = stack.filter((p) => p.startsWith('@media')).map((p) => p.replace(/^@media\s*/, ''));
      rules.push({ file, selector: prelude, media, decls });
    } else if (c === ';' && stack.length > 0) {
      // A declaration ends; the body is sliced whole at its closing brace.
    }
  }
  return rules;
}

const isMobileOnly = (rule) => rule.media.some((m) => m === MOBILE_QUERY || m.startsWith(`${MOBILE_QUERY} and (`));
const readsToken = (rule) => rule.decls.some(([, value]) => value.includes(TOKEN) || value.includes('--layout-gutter'));
const keyOf = (rule) => `${rule.file} ${rule.selector}`;

const FILES = walk(SRC_DIR, /\.(vue|css)$/);
const RULES = FILES.flatMap((file) => parseRules(relative(SRC_DIR, file), cssOf(file)));
const DESKTOP = RULES.filter((rule) => !isMobileOnly(rule));

const OFFSETS = new Set(['top', 'right', 'bottom', 'left', 'inset']);
const FIXED = DESKTOP.filter((rule) =>
  rule.decls.some(([p, v]) => p === 'position' && v === 'fixed')
  && rule.decls.some(([p]) => OFFSETS.has(p)));
const ENV_READS = DESKTOP.filter((rule) => rule.decls.some(([, v]) => /env\(safe-area-inset-/.test(v)));

/**
 * `file selector` → why the rule is right without the token. A reason is a
 * sentence, not a label: what keeps its content off the bezel, or why it must
 * reach the panel's edge.
 */
const EXEMPT = {
  'assets/styles/reset.css #app::before': 'The app ground, painted to the panel edge under every screen; it holds no content.',
  'App.vue .sleep-shield': 'Catches the touch that wakes a dark panel, so it must cover the glass under the bezel too.',
  'assets/styles/design-system.css .theme-ghost': 'A copy of the whole screen at 0,0 that fades out on a theme change; its content carries its own offsets.',
  'components/system/HostnameConflictView.vue .hostname-conflict-view': 'A full-screen ground whose content is centered and at most 90vw wide, far from every edge.',
  'components/ui/Logo.vue .logo-container': 'Fixed here, offset elsewhere: .logo-top carries the top (pinned in SHELLS), .logo-center is centered.',
  'components/volume/VolumeOverlay.vue .volume-overlay': 'The veil reaches the panel edge; the only content, the volume plate, is centered on the screen.',
  'views/ComponentsView.vue .gallery__sidebar': 'The /components gallery takes no bezel inset (useScreenInset), as it takes no warm filter.',
  'views/ComponentsView.vue .gallery__scrim': 'The /components gallery takes no bezel inset (useScreenInset), as it takes no warm filter.',
  'components/setup/SetupWizard.vue .setup-card__footer': 'Positioned inside the card, which .setup-wizard already keeps clear of the bezel; the env() terms serve the phone, where the card is the screen.',
};

/**
 * [file, selector, property]: shells that hold content at a screen edge
 * without being fixed against the viewport, each with the declaration that
 * keeps the content clear of the bezel.
 */
const SHELLS = [
  ['components/audio/AudioPlayerFull.vue', '.now-playing', 'padding'],
  ['components/audio/AudioSourceLayout.vue', '.audio-source-layout', 'padding'],
  ['components/audio/AudioSourceLayout.vue', '.content-container', 'padding'],
  ['components/audio/AudioSourceLayout.vue', '.player-wrapper', 'padding-top'],
  ['components/audio/AudioSourceLayout.vue', '.player-wrapper.has-player::after', 'width'],
  ['components/lyrics/LyricsView.vue', '.lyrics-view-close', 'top'],
  ['components/lyrics/LyricsView.vue', '.lyrics-view-close', 'right'],
  ['components/lyrics/LyricsPlaybackBar.vue', '.lyrics-bar', 'padding'],
  ['components/lyrics/LyricsPlaybackBar.vue', '.lyrics-bar-swipe-hint', 'transform'],
  ['components/ui/Logo.vue', '.logo-top', 'top'],
  ['components/ui/Dock.vue', '.dock-indicator::after', 'bottom'],
  ['views/MainView.vue', '.SettingsAccess', 'width'],
  ['views/MainView.vue', '.SettingsAccess', 'height'],
  ['components/music-library/views/LibraryHome.vue', '.index-bucket', 'scroll-margin-top'],
  ['components/music-library/ArtistIndexRail.vue', '.index-rail', 'height'],
];

describe('screen inset', () => {
  it('reads the rules it guards', () => {
    // An empty or broken read is a failed scan, not a pass.
    expect(RULES.length).toBeGreaterThan(1000);
    expect(FIXED.length).toBeGreaterThan(10);
    expect(ENV_READS.length).toBeGreaterThan(5);
    const root = RULES.find((r) => r.file === 'assets/styles/design-system.css' && r.selector === ':root'
      && r.decls.some(([p]) => p === TOKEN));
    expect(root?.decls.find(([p]) => p === TOKEN)?.[1]).toBe('0px');
  });

  it('keeps every exemption current and explained', () => {
    const keys = new Set([...FIXED, ...ENV_READS].map(keyOf));
    for (const [key, reason] of Object.entries(EXEMPT)) {
      expect(keys.has(key), `${key} matches no rule`).toBe(true);
      expect(reason.length, `${key}: say why`).toBeGreaterThanOrEqual(40);
    }
  });

  it('keeps fixed chrome off the bezel', () => {
    const missing = FIXED.filter((rule) => !readsToken(rule) && !(keyOf(rule) in EXEMPT));
    expect(missing.map(keyOf)).toEqual([]);
  });

  it('adds the bezel to every safe-area margin outside the mobile layout', () => {
    const missing = ENV_READS.filter((rule) => !readsToken(rule) && !(keyOf(rule) in EXEMPT));
    expect(missing.map(keyOf)).toEqual([]);
  });

  it('keeps every content shell clear of the bezel', () => {
    const problems = SHELLS.flatMap(([file, selector, property]) => {
      const matches = DESKTOP.filter((r) => r.file === file && r.selector === selector);
      const values = matches.flatMap((r) => r.decls.filter(([p]) => p === property).map(([, v]) => v));
      if (values.length === 0) return [`${file} ${selector} { ${property} } matches nothing`];
      return values.some((v) => v.includes(TOKEN) || v.includes('--layout-gutter'))
        ? []
        : [`${file} ${selector} { ${property}: ${values.join(' / ')} }`];
    });
    expect(problems).toEqual([]);
  });

  it('never writes a length in CSS millimeters', () => {
    const found = RULES.flatMap((rule) => rule.decls
      .filter(([, value]) => /(^|[\s(,])-?\d*\.?\d+mm\b/.test(value))
      .map(([p, v]) => `${keyOf(rule)} { ${p}: ${v} }`));
    expect(found).toEqual([]);
  });
});
