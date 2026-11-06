import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { stripComments } from '../helpers/stripComments.js';

const SRC = resolve(dirname(fileURLToPath(import.meta.url)), '../../src');

// Every rule under tests/architecture/ that scans src/ reads it through this
// helper. If it eats code it takes for a comment, whatever sits there is
// invisible to all of them, and they pass on code they never saw.
describe('stripComments', () => {
  it("a URL's :// is not a comment", () => {
    const out = stripComments("const u = 'http://host/a'; const k = 1;");
    expect(out).toContain("'http://host/a'");
    expect(out).toContain('const k = 1');
  });

  it('removes line, block and HTML comments', () => {
    expect(stripComments('x // note')).toBe('x ');
    expect(stripComments('a /* b */ c')).toBe('a  c');
    expect(stripComments('<p><!-- b --></p>')).toBe('<p></p>');
    expect(stripComments("f('http://a') // note")).toBe("f('http://a') ");
  });

  it('a comment marker inside a string is not a comment', () => {
    const glob = stripComments("const m = glob('icons/*.svg');\nconst k = 1;\n/** doc */\nconst j = 2;");
    expect(glob).toBe("const m = glob('icons/*.svg');\nconst k = 1;\n\nconst j = 2;");
    expect(stripComments("const u = '//cdn/x'; const k = 1;")).toBe("const u = '//cdn/x'; const k = 1;");
    expect(stripComments('s.split("//"); const k = 1;')).toBe('s.split("//"); const k = 1;');
    expect(stripComments("const h = '<!--'; const k = 1; // c")).toBe("const h = '<!--'; const k = 1; ");
  });

  it('a comment marker inside a template literal is not a comment, its ${} is code', () => {
    expect(stripComments('const t = `//${a /* c */}/*`; const k = 1;'))
      .toBe('const t = `//${a }/*`; const k = 1;');
  });

  it('a comment marker or a quote inside a regex literal is not one', () => {
    expect(stripComments('const re = /\\/\\//; const k = 1;')).toBe('const re = /\\/\\//; const k = 1;');
    expect(stripComments("const q = /['\"]/; const k = 1; // c")).toBe("const q = /['\"]/; const k = 1; ");
    expect(stripComments('const c = /[/]/; const k = 1;')).toBe('const c = /[/]/; const k = 1;');
  });

  it('a division is not a regex, and a closing tag is not either', () => {
    expect(stripComments('const r = a / b; // c\nconst k = 1 / 2;')).toBe('const r = a / b; \nconst k = 1 / 2;');
    expect(stripComments('<p>a</p><!-- c --><b>/x/</b>')).toBe('<p>a</p><b>/x/</b>');
  });

  it('a quote inside a comment opens nothing', () => {
    expect(stripComments("/* it's */ const k = 1; // don't\nconst j = 2;")).toBe(' const k = 1; \nconst j = 2;');
    expect(stripComments("<!-- don't --><p>{{ k }}</p>")).toBe('<p>{{ k }}</p>');
  });

  it('a quote in template prose costs one line at most', () => {
    expect(stripComments("<p>It's here</p>\n<!-- c --><b>k</b>")).toBe("<p>It's here</p>\n<b>k</b>");
  });

  it('keeps the code after a glob pattern in the real tree', () => {
    const icon = readFileSync(resolve(SRC, 'components/ui/AppIcon.vue'), 'utf8');
    const genres = readFileSync(resolve(SRC, 'constants/podcastGenres.js'), 'utf8');
    expect(icon).toContain("import.meta.glob('@/assets/app-icons/*.svg'");
    expect(stripComments(icon)).toContain('const prepareSvg = ');
    expect(genres).toContain("import.meta.glob('@/assets/podcasts/genres/*.jpg'");
    expect(stripComments(genres)).toContain('export const PODCAST_GENRE_IDS = [');
  });
});
