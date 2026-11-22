// frontend/vite-plugins/thirdPartyNotices.js
//
// Publishes the repository's THIRD_PARTY_NOTICES.md in the built interface, at
// THIRD_PARTY_NOTICES_PATH, byte for byte. The file is maintained once, at the
// repository root, where backend/tests/architecture/test_third_party_notices.py
// holds it to the manifests; this copies it at build time rather than keeping a
// second copy in public/, which nothing would hold to the first.
//
// It travels in dist/, which CI builds once and every unit installs as it is
// (CLAUDE.md invariant 9), so the written offer the file carries reaches each
// image and each update with the release it describes. A missing file fails the
// build: an interface without its notices is not one to publish.
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { THIRD_PARTY_NOTICES_PATH } from '../src/constants/thirdPartyNotices.js';

export const NOTICES_SOURCE = resolve(dirname(fileURLToPath(import.meta.url)), '../../THIRD_PARTY_NOTICES.md');

export function thirdPartyNotices(source = NOTICES_SOURCE) {
  const read = () => readFileSync(source, 'utf8');
  return {
    name: 'milo-third-party-notices',
    buildStart() {
      this.addWatchFile(source);
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: THIRD_PARTY_NOTICES_PATH.slice(1), source: read() });
    },
    // The dev server serves it from the same path, so the Settings screen reads
    // the same file under `npm run dev`.
    configureServer(server) {
      server.middlewares.use(THIRD_PARTY_NOTICES_PATH, (_req, res) => {
        res.setHeader('Content-Type', 'text/markdown; charset=utf-8');
        res.end(read());
      });
    },
  };
}
