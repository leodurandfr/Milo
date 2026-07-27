// frontend/tests/architecture/pressFeedback.test.js
/**
 * Structural guardrail over the app's single press affordance, `v-press`.
 *
 * Milō is driven by finger on a kiosk, and `v-press` is the only thing that
 * tells the user a tap registered: it shrinks the element, holds the state for
 * 150 ms so a quick tap is still visible, cancels once the finger travels far
 * enough to be a scroll, and replays the click when the finger lifts outside the
 * shrunken box. Every `ui/` component applies it internally, so a feature that
 * builds its tap target out of a native `<button>` or `<div>` is the only way to
 * end up with a control that does nothing when pressed. That is how the podcast
 * grid ended up as the one grid in the app whose tiles stayed still, while
 * `AlbumCard`, `MediaRow`, `StationCard` and `EpisodeCard` all pressed.
 *
 * The rule concerns NATIVE elements only. A component tag owns its own feedback;
 * asking about `<Button @click>` here would just restate what `Button.vue` does.
 *
 * Two exemptions are mechanical, and need no entry below:
 *   - a `@click` with no handler (`@click.stop`, `@click.self` used bare) is a
 *     propagation guard on a container, not a tap target;
 *   - an element already carrying `v-press`.
 *
 * Everything else is listed, with a reason. The list is split in two because the
 * honest state of this surface is split in two: elements that are legitimately
 * not press surfaces, and elements that look like buttons and were never
 * decided. Whether a `.zone-header` should shrink is an eye-on-the-kiosk call,
 * not a grep — so the undecided ones are enumerated here rather than described
 * in a document nobody re-reads. Both lists are checked for staleness: an entry
 * matching nothing fails, the way a `.stylelintrc.cjs` whitelist entry for a
 * deleted file did.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join, resolve, relative } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const SRC_DIR = resolve(HERE, '../../src');

/** Native tap targets that are legitimately not press surfaces. */
const NOT_A_PRESS_SURFACE = {
  'components/audio/AudioPlayer.vue::audio-player':
    'the mini-player itself — a swipe surface (touchstart/move/end); a shrink would fight the drag',
  'components/audio/AudioPlayer.vue::audio-player-expanded':
    'the expanded sheet scrim, @click.self to dismiss',
  'components/audio/ProgressBar.vue::progress-container':
    'a seek bar: the tap position is the value, and the bar must not move under the finger',
  'components/audio/DetailHeader.vue::detail-header-subtitle':
    'an inline text link (tap the artist name), not a button — same treatment as EpisodeCard',
  'components/podcasts/EpisodeCard.vue::podcast-name':
    'an inline text link inside a card that presses as a whole',
  'components/lyrics/LyricsPlaybackBar.vue::lyrics-bar-swipe-hint':
    'the swipe affordance for the lyrics bar — a gesture hint, and pressing it is a fallback',
  'components/ui/Modal.vue::modal-overlay':
    'the modal scrim, @click.self to dismiss',
  'components/ui/VolumeBar.vue::volume-bar':
    'the volume overlay: any tap dismisses it, there is nothing to acknowledge',
  'components/ui/Dock.vue::dock-indicator':
    'the dock drag handle; the dock animates its own press and drag states',
  'components/ui/VirtualKeyboard.vue::keyboard-key':
    'the keyboard draws its own press and accent popups (components/ui/keyboard/geometry.js)',
  'views/MainView.vue::SettingsAccess':
    'the invisible five-tap hotspot that opens Settings — it must stay invisible',
};

/**
 * Native tap targets with no feedback at all today, and no `:active` style
 * either. Each looks like a button; whether it should shrink is a call to make
 * with the kiosk in front of you, not from a grep. Listed so a new one cannot
 * hide among them.
 */
const UNDECIDED = [
  'components/audio/AudioPlayer.vue::player-artwork-frame',
  'components/audio/AudioPlayer.vue::player-info-inner',
  'components/audio/AudioPlayer.vue::expanded-info',
  'components/audio/AudioSourceStatus.vue::action-button',
  'components/equalizer/ItemSelector.vue::tab-button',
  'components/multiroom/MultiroomItem.vue::expand-button',
  'components/network/NetworkSelector.vue::network-item',
  'components/settings/categories/NetworkSettings.vue::network-item',
  'components/settings/categories/FanSettings.vue::curve__add',
  'components/settings/categories/multiroom/MultiroomSettings.vue::zone-header',
  'components/settings/categories/music-library/WizardBrowse.vue::wb-crumb',
  'components/setup/AudioStep.vue::audio-step__volume-control',
  'components/setup/SetupWizard.vue::setup-card__back',
  'components/ui/Dropdown.vue::dropdown-item',
  'components/ui/NotificationBanner.vue::dismiss-btn',
];

function vueFiles(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory()
      ? vueFiles(full)
      : full.endsWith('.vue') ? [full] : [];
  });
}

/**
 * Every native element carrying `@click`, keyed by file and first static class
 * (its handler expression when it has no class). Comments are stripped: a rule
 * must read code, never the prose above it.
 */
function clickableNatives() {
  const found = [];
  for (const file of vueFiles(SRC_DIR)) {
    const source = readFileSync(file, 'utf8');
    const end = source.lastIndexOf('</template>');
    if (end < 0) continue;
    const template = source.slice(0, end).replace(/<!--[\s\S]*?-->/g, '');
    const tags = template.matchAll(/<([a-z][\w-]*)((?:"[^"]*"|'[^']*'|[^>"'])*?)\/?>/g);
    for (const [, tag, attrs] of tags) {
      const click = attrs.match(/@click[.\w]*(?:="([^"]*)")?/);
      if (!click) continue;
      const handler = click[1] ?? null;
      const staticClass = attrs.match(/(?:^|\s)class="([^"{]*)"/);
      const name = staticClass ? staticClass[1].trim().split(/\s+/)[0] : handler;
      found.push({
        key: `${relative(SRC_DIR, file)}::${name}`,
        tag,
        handler,
        pressed: /\bv-press\b/.test(attrs),
      });
    }
  }
  if (found.length < 40) {
    throw new Error(`parsed ${found.length} native @click elements — the extractor is broken`);
  }
  return found;
}

const NATIVES = clickableNatives();
const TAP_TARGETS = NATIVES.filter(el => el.handler !== null);
const UNPRESSED = [...new Set(TAP_TARGETS.filter(el => !el.pressed).map(el => el.key))].sort();
const LISTED = new Set([...Object.keys(NOT_A_PRESS_SURFACE), ...UNDECIDED]);

describe('press feedback on native tap targets', () => {
  it('parsed a plausible surface', () => {
    // Guards every rule below: an empty parse would make them vacuous.
    expect(TAP_TARGETS.length).toBeGreaterThanOrEqual(30);
    expect(TAP_TARGETS.filter(el => el.pressed).length).toBeGreaterThanOrEqual(15);
    expect(NATIVES.some(el => el.handler === null)).toBe(true);
  });

  it('every native tap target either presses or is listed with a reason', () => {
    // A new `<button @click>` with no press is the one way to ship a control
    // that does nothing when tapped on the kiosk.
    expect(UNPRESSED.filter(key => !LISTED.has(key))).toEqual([]);
  });

  it('carries no stale entry', () => {
    // An exemption for an element that no longer exists reads as a decision
    // someone made, and hides that the case is gone.
    const live = new Set(UNPRESSED);
    expect([...LISTED].filter(key => !live.has(key)).sort()).toEqual([]);
  });

  it('presses the tile a card or row component is', () => {
    // The concrete inconsistency this pass fixed: six components render one
    // tappable tile in a grid, and two of them stayed still under the finger.
    // Derived from the naming, so the next `*Card.vue` is covered without being
    // added here. Only the tile itself is asked about — the first clickable
    // native in the template — never the links and buttons nested inside it.
    const tiles = vueFiles(SRC_DIR).filter(f => /(Card|Row)\.vue$/.test(f));
    if (tiles.length < 5) {
      throw new Error(`found ${tiles.length} card/row components — the extractor is broken`);
    }
    const still = tiles.filter((file) => {
      const prefix = `${relative(SRC_DIR, file)}::`;
      const root = TAP_TARGETS.find(el => el.key.startsWith(prefix));
      return root && !root.pressed;
    });
    expect(still.map(f => relative(SRC_DIR, f))).toEqual([]);
  });
});
