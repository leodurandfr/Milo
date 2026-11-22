// frontend/tests/architecture/thirdPartyNotices.test.js
/**
 * The third-party notices are written once, at the repository root, and reach a
 * unit only through the build: vite-plugins/thirdPartyNotices.js copies them
 * into dist/, at the path Settings › System › Third-party licenses reads.
 *
 * What breaks when this fails: the build stops carrying the file (the plugin
 * dropped from vite.config.js, or emitting under another name) and the licenses
 * screen shows the SPA's index.html, which nginx answers for any path it does
 * not find — while the written offer for GPL source the file holds no longer
 * accompanies the interface. pi-gen/inspect-image.sh catches it on an image;
 * this catches it on every push. The plugin is run, not mocked: its hook is
 * handed a recorder for what it emits.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { THIRD_PARTY_NOTICES_PATH } from '@/constants/thirdPartyNotices';
import { NOTICES_SOURCE, thirdPartyNotices } from '../../vite-plugins/thirdPartyNotices.js';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '../..');
const ROOT_NOTICES = resolve(FRONTEND, '../THIRD_PARTY_NOTICES.md');

function emitted(plugin) {
  const files = [];
  plugin.generateBundle.call({ emitFile: (file) => files.push(file) });
  return files;
}

describe('the build publishes the repository\'s third-party notices', () => {
  it('reads the file at the repository root, which holds the written offer', () => {
    expect(NOTICES_SOURCE).toBe(ROOT_NOTICES);
    const text = readFileSync(ROOT_NOTICES, 'utf8');
    expect(text).toMatch(/^## \d+\. Written offer for source code$/m);
  });

  it('emits it byte for byte, at the path the licenses screen reads', () => {
    const files = emitted(thirdPartyNotices());
    expect(files).toEqual([{
      type: 'asset',
      fileName: THIRD_PARTY_NOTICES_PATH.replace(/^\//, ''),
      source: readFileSync(ROOT_NOTICES, 'utf8'),
    }]);
  });

  it('is one of the build\'s plugins', () => {
    const config = readFileSync(resolve(FRONTEND, 'vite.config.js'), 'utf8');
    expect(config).toMatch(/from '\.\/vite-plugins\/thirdPartyNotices\.js'/);
    expect(config).toMatch(/plugins:\s*\[[^\]]*\bthirdPartyNotices\(\)/);
  });

  it('fails the build when the file is missing, rather than publishing none', () => {
    expect(() => emitted(thirdPartyNotices(resolve(FRONTEND, 'NO_SUCH_NOTICES.md')))).toThrow(/ENOENT/);
  });
});
