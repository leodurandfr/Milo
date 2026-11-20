// frontend/tests/architecture/playerSurfaces.test.js
/**
 * Two rules over the components that draw a source's playback, read from the
 * sources (mounts nothing, asserts no markup).
 *
 * 1. A component that fixes its source at setup — `useSourceProgress(props.source)`,
 *    `usePlayerState(props.source)`, … read once — is keyed on that source
 *    wherever it is mounted for a source that can change under it. Without the
 *    key, the state naming another source in one step (a reconnection's initial
 *    state, a resync) leaves it reading the first one: the Lyrics bar kept
 *    Music Library's playhead under a Spotify track, an empty progress bar for
 *    a track that had a length, until the view was closed and opened again.
 *
 * 2. Every IconButton the players and their sources draw — the transport, the
 *    CD's tracklist and eject, the radio's search, the Lyrics bar — has a name a
 *    screen reader can say. IconButton names nothing itself, and a glyph alone
 *    is announced "button".
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve, basename } from 'node:path';
import { stripComments } from '../helpers/stripComments';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(HERE, '../../src');

function vueFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return vueFiles(path);
    return entry.name.endsWith('.vue') ? [path] : [];
  });
}

const rel = (file) => relative(SRC, file);
const templateOf = (text) => text.split(/<script[\s>]/)[0];
const scriptOf = (text) => text.slice(text.search(/<script[\s>]/));

/** Every `<Name …>` opening tag in a template, attributes included. */
function tagsOf(template, name) {
  return [...template.matchAll(new RegExp(`<${name}\\b[^>]*>`, 'g'))].map((match) => match[0]);
}

/**
 * A mount whose `:source` is fixed for the life of the component mounting it
 * — so a child reading it once reads the right one. The gallery's stage is
 * locked to one page per mount (SourceStage's `page` prop).
 */
const FIXED_FOR_THE_MOUNT = {
  'components/gallery/SourceStage.vue': ['page.source']
};

describe('player surfaces', () => {
  const files = vueFiles(join(SRC, 'components'));

  it('keys a component that fixes its source on the source it is mounted for', () => {
    // A composable call at the top level of the setup, with the prop itself:
    // read once, never again.
    const FIXES_SOURCE = /^(?:const|let)?[^\n]*?\buse[A-Z]\w*\(\s*props\.source\b/m;
    const fixing = files
      .filter((file) => FIXES_SOURCE.test(stripComments(scriptOf(readFileSync(file, 'utf8')))))
      .map((file) => basename(file, '.vue'));

    // The extractor first: the shells, the body, the transport and the Lyrics
    // view's two parts all read their source once.
    expect(fixing).toEqual(expect.arrayContaining([
      'AudioPlayer', 'AudioPlayerFull', 'PlayerBody', 'PlayerTransport', 'LyricsContent', 'LyricsPlaybackBar'
    ]));

    const unkeyed = [];
    let mounts = 0;
    for (const file of files) {
      const template = templateOf(readFileSync(file, 'utf8'));
      for (const name of fixing) {
        for (const tag of tagsOf(template, name)) {
          const bound = tag.match(/\s:source="([^"]+)"/)?.[1];
          if (!bound) continue;
          mounts += 1;
          // Forwarding its own `source` prop: the mounting component is fixed
          // too, and keyed where it is mounted.
          if (bound === 'source' || FIXED_FOR_THE_MOUNT[rel(file)]?.includes(bound)) continue;
          const key = tag.match(/\s:key="([^"]+)"/)?.[1] ?? '';
          if (!key.includes(bound)) unkeyed.push(`${rel(file)}: <${name} :source="${bound}"> (key: ${key || 'none'})`);
        }
      }
    }

    expect(mounts).toBeGreaterThan(5);
    expect(unkeyed).toEqual([]);
  });

  it('names every IconButton the players and their sources draw', () => {
    const SCOPE = ['components/audio', 'components/cd', 'components/radio', 'components/lyrics'];
    const unnamed = [];
    let checked = 0;
    for (const file of files.filter((path) => SCOPE.some((dir) => rel(path).startsWith(`${dir}/`)))) {
      const template = stripComments(templateOf(readFileSync(file, 'utf8')));
      for (const tag of tagsOf(template, 'IconButton')) {
        checked += 1;
        if (!/\s:?aria-label="[^"]+"/.test(tag)) unnamed.push(`${rel(file)}: ${tag.replace(/\s+/g, ' ')}`);
      }
    }

    // The transport, the CD's two buttons and radio's search and favorite at
    // least: an extractor that found none would pass on nothing.
    expect(checked).toBeGreaterThan(6);
    expect(unnamed).toEqual([]);
  });
});
