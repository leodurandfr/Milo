// frontend/tests/pure/miloRelease.test.js
/**
 * How Milō's own release reaches the screen: the version string the Updates
 * row and Settings › Info print, and the action the Updates row offers. If
 * these break, a pre-release reads as the stable release it is a candidate
 * for, the two screens disagree about the running version, and a development
 * checkout claims to be up to date while the channel offers it nothing.
 */
import { describe, it, expect } from 'vitest';
import { stripTagPrefix, miloVersionLabel, miloRowAction } from '@/utils/miloRelease';

describe('stripTagPrefix', () => {
  it('drops the leading v and keeps a pre-release suffix', () => {
    expect(stripTagPrefix('v0.2.0-rc1')).toBe('0.2.0-rc1');
  });
});

describe('miloVersionLabel', () => {
  it('prints the describe string off a tag, never the regex-extracted number', () => {
    // The shape of GET /api/programs/milo/installed, which carries no release_tag.
    const installed = {
      status: 'installed',
      versions: { main: '0.1.1' },
      raw_version: 'v0.1.1-27-g51240bd5',
    };
    expect(miloVersionLabel(installed)).toBe('0.1.1-27-g51240bd5');
  });

  it('prints a pre-release tag whole', () => {
    const installed = { release_tag: 'v0.2.0-rc1', raw_version: 'v0.2.0-rc1', versions: { main: '0.2.0' } };
    expect(miloVersionLabel(installed)).toBe('0.2.0-rc1');
  });

  it('answers null when nothing was read, so the caller prints its own fallback', () => {
    expect(miloVersionLabel(null)).toBeNull();
    expect(miloVersionLabel({ versions: { main: '0.1.1' } })).toBeNull();
  });
});

describe('miloRowAction', () => {
  it('names a development build rather than calling it up to date', () => {
    expect(miloRowAction({ development_build: true, update_available: false })).toBe('developmentBuild');
  });

  it('is up to date on the published tag', () => {
    expect(miloRowAction({ development_build: false, update_available: false })).toBe('upToDate');
  });

  it('offers the update the backend offers', () => {
    expect(miloRowAction({ development_build: false, update_available: true })).toBe('update');
  });
});
