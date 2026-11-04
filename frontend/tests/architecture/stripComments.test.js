import { describe, it, expect } from 'vitest';
import { stripComments } from '../helpers/stripComments.js';

// Every rule under tests/architecture/ that scans src/ reads it through this
// helper. If it eats the rest of a line after a URL, whatever sits there is
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
});
