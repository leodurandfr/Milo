// frontend/tests/architecture/settingsRefusal.test.js
/**
 * useSettingsAPI undoes a refused write and holds the server's detail in
 * `refusal`; the screen is what says so. A screen that writes through it and
 * draws no SettingsRefusal moves a control back with no word of why — the
 * refusal is undone but never seen, the state every settings screen but Dock
 * was in before the undo was shared. So every component that takes the
 * composable also takes `refusal` and hands it to a SettingsRefusal.
 *
 * A write refused once its screen has left (a debounced write flushed on
 * unmount: Dock, Dock › Volume, the normalization switch) has no screen of its
 * own left to be said on, so the composable hands it to the settings store and
 * SettingsModal says it under the next view. A writer mounted anywhere else, or
 * a modal that stops drawing it, undoes that refusal with no word of why.
 *
 * Mounts nothing: it reads the sources.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, relative, resolve } from 'node:path';
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

const writers = vueFiles(SRC)
  .map(path => ({ file: relative(SRC, path), text: stripComments(readFileSync(path, 'utf8')) }))
  .filter(({ text }) => /\buseSettingsAPI\(/.test(text));

describe('a settings write that is refused is said', () => {
  it('finds the screens that write settings', () => {
    expect(writers.length).toBeGreaterThanOrEqual(11);
  });

  it('every one of them draws its refusal', () => {
    const silent = writers
      .filter(({ text }) => !(
        /\{[^}]*\brefusal\b[^}]*\}\s*=\s*useSettingsAPI\(/.test(text)
        && /<SettingsRefusal\b[^>]*:detail="refusal"/.test(text)
      ))
      .map(({ file }) => file);

    expect(silent).toEqual([]);
  });

  it('the settings modal mounts every one of them and says what they refused after leaving', () => {
    const modal = stripComments(readFileSync(join(SRC, 'components/settings/SettingsModal.vue'), 'utf8'));
    const notMounted = writers
      .map(({ file }) => file.split('/').pop().replace(/\.vue$/, ''))
      .filter(name => !new RegExp(`import ${name} from`).test(modal));

    expect(notMounted).toEqual([]);
    expect(modal).toMatch(/<SettingsRefusal\b[^>]*:detail="settingsStore\.leftScreenRefusal"/);
    // How long it is said: tests/composables/useLeftScreenRefusal.test.js.
    expect(modal).toMatch(/\buseLeftScreenRefusal\(currentView\)/);
  });
});
