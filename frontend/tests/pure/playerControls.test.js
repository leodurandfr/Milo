// frontend/tests/pure/playerControls.test.js
/**
 * Which controls AudioPlayerFull draws, from the wire alone.
 *
 * The control lists below are the ones each source's `_controls()` publishes in
 * the named phase (backend/sources/<source>/source.py). The browser sources'
 * cases are what the full player must offer to stand in for AudioPlayer
 * without losing a function; the others freeze what the player drew before it
 * learned the browser sources' controls — prev / play-pause / next whenever
 * pause or resume is listed, the missing step disabled, the bar seekable iff
 * seek is — so a rule added for one family cannot move a button in the other.
 *
 * Goes red if a listed command stops producing its control, an unlisted one
 * starts producing one, or a toggle stops reading its state from `details`.
 */
import { describe, it, expect } from 'vitest';
import { playerControls, swipeMove, swipeTarget, swipeable } from '@/utils/playerControls';

/** One readable token per control: `prev`, `next(disabled)`, `main:pause`. */
function summary(items) {
  return items.map((item) => {
    const name = item.id === 'main' ? `main:${item.command}` : item.id;
    return item.enabled ? name : `${name}(disabled)`;
  });
}

function controlsOf(controls, phase = null, details = null) {
  return playerControls({ controls, details, phase });
}

describe('the browser sources', () => {
  it('radio: a live stream stops, steps through the favorites, and has no seek', () => {
    const items = controlsOf(['stop', 'next', 'prev'], 'playing', { kind: 'radio' });
    expect(summary(items)).toEqual(['prev', 'main:stop', 'next']);
    expect(items.find(item => item.id === 'main').icon).toBe('stop');
  });

  it('radio: a stopped station re-tunes with resume_playback, drawn as play', () => {
    const items = controlsOf(['resume_playback', 'next', 'prev'], null, { kind: 'radio' });
    const main = items.find(item => item.id === 'main');
    expect(main.command).toBe('resume_playback');
    expect(main.icon).toBe('play');
  });

  it('radio: with no favorites there is nothing to step to, only the main button', () => {
    expect(summary(controlsOf(['stop'], 'playing', { kind: 'radio' }))).toEqual(['main:stop']);
  });

  it('podcast: −15 / +30 flank the pause', () => {
    const items = controlsOf(['pause', 'seek', 'skip'], 'playing', { kind: 'podcast' });
    expect(summary(items)).toEqual(['seek', 'skip-back', 'main:pause', 'skip-forward']);
    const [back, forward] = items.filter(item => item.command === 'skip');
    expect(back.seconds).toBeLessThan(0);
    expect(forward.seconds).toBeGreaterThan(0);
  });

  it('podcast: a loading episode can be paused but not yet moved', () => {
    expect(summary(controlsOf(['pause'], 'loading', { kind: 'podcast' })))
      .toEqual(['main:pause']);
  });

  it('music library: steps win over skip, and shuffle reads details.music_library', () => {
    const items = controlsOf(
      ['pause', 'seek', 'skip', 'next', 'prev', 'set_shuffle', 'play_index', 'stop'],
      'playing',
      { kind: 'music_library', shuffle: true }
    );
    expect(summary(items)).toEqual(['seek', 'shuffle', 'prev', 'main:pause', 'next']);
    const shuffle = items.find(item => item.id === 'shuffle');
    expect(shuffle.active).toBe(true);
    expect(shuffle.params).toEqual({ shuffle: false });
  });

  it('music library: a saved queue resumes; its stop is not a second main button', () => {
    const items = controlsOf(['resume', 'play_index', 'stop'], null, { kind: 'music_library', shuffle: false });
    expect(summary(items)).toEqual(['main:resume']);
  });

  it('spotify: shuffle and repeat, each in the state details.spotify publishes', () => {
    const items = controlsOf(
      ['resume', 'seek', 'skip', 'next', 'prev', 'set_shuffle', 'set_repeat'],
      'paused',
      { kind: 'spotify', shuffle: false, repeat: 'track' }
    );
    expect(summary(items)).toEqual(['seek', 'shuffle', 'prev', 'main:resume', 'next', 'repeat']);
    const repeat = items.find(item => item.id === 'repeat');
    expect(repeat.icon).toBe('repeatOnce');
    expect(repeat.active).toBe(true);
    // The press asks for the mode after this one, as the Spotify app's button does.
    expect(repeat.params.mode).not.toBe('track');
    expect(items.find(item => item.id === 'shuffle').params).toEqual({ shuffle: true });
  });

  it('music library: repeat joins the plate once the source lists it', () => {
    const items = controlsOf(
      ['pause', 'seek', 'next', 'prev', 'set_shuffle', 'set_repeat'],
      'playing',
      { kind: 'music_library', shuffle: false, repeat: 'context' }
    );
    expect(summary(items)).toEqual(['seek', 'shuffle', 'prev', 'main:pause', 'next', 'repeat']);
    expect(items.find(item => item.id === 'repeat').active).toBe(true);
  });

  it('spotify: the toggles sit at the two ends of the plate', () => {
    const items = controlsOf(['pause', 'next', 'prev', 'set_shuffle', 'set_repeat'], 'playing',
      { kind: 'spotify', shuffle: false, repeat: 'off' });
    const transport = items.filter(item => item.row === 'transport').map(item => item.id);
    expect(transport[0]).toBe('shuffle');
    expect(transport.at(-1)).toBe('repeat');
  });

  it('spotify: repeat off reads as an inactive toggle', () => {
    const items = controlsOf(['pause', 'next', 'prev', 'set_shuffle', 'set_repeat'], 'loading',
      { kind: 'spotify', shuffle: false, repeat: 'off' });
    const repeat = items.find(item => item.id === 'repeat');
    expect(repeat.icon).toBe('repeat');
    expect(repeat.active).toBe(false);
  });
});

describe('the sources with nothing to browse draw what they drew before', () => {
  it.each([
    ['CD playing', ['pause', 'seek', 'skip', 'next', 'prev', 'play_track', 'eject'], 'playing',
      ['seek', 'prev', 'main:pause', 'next']],
    ['CD paused on its last track', ['resume', 'seek', 'skip', 'prev', 'play_track', 'eject'], 'paused',
      ['seek', 'prev', 'main:resume', 'next(disabled)']],
    ['CD loading a track', ['pause', 'next', 'prev', 'play_track', 'eject'], 'loading',
      ['prev', 'main:pause', 'next']],
    ['CD with a disc it cannot play', ['eject'], null, []],
    ['TIDAL playing', ['pause', 'next', 'prev'], 'playing', ['prev', 'main:pause', 'next']],
    ['TIDAL paused', ['resume', 'next', 'prev'], 'paused', ['prev', 'main:resume', 'next']],
    ['Bluetooth with a player', ['pause', 'next', 'prev', 'disconnect'], 'playing',
      ['prev', 'main:pause', 'next']],
    ['Bluetooth connected, no player', ['disconnect'], 'connected', []],
    ['AirPlay', [], 'playing', []],
    ['Qobuz', [], 'playing', []],
  ])('%s', (_name, controls, phase, expected) => {
    expect(summary(controlsOf(controls, phase))).toEqual(expected);
  });
});

describe('a command not listed draws nothing', () => {
  it.each([
    ['set_shuffle', 'shuffle'],
    ['set_repeat', 'repeat'],
    ['seek', 'seek'],
  ])('without %s there is no %s control', (command, id) => {
    const all = ['pause', 'seek', 'next', 'prev', 'set_shuffle', 'set_repeat'];
    const details = { kind: 'spotify', shuffle: true, repeat: 'context' };
    const without = all.filter(entry => entry !== command);
    expect(controlsOf(all, 'playing', details).some(item => item.id === id)).toBe(true);
    expect(controlsOf(without, 'playing', details).some(item => item.id === id)).toBe(false);
  });

  it('without skip or a step, the main button stands alone', () => {
    expect(summary(controlsOf(['pause'], 'playing'))).toEqual(['main:pause']);
  });

  it('steps, skip or toggles with no main button are no transport at all', () => {
    expect(controlsOf(['next', 'prev'], null)).toEqual([]);
    expect(controlsOf(['skip'], 'playing')).toEqual([]);
    expect(controlsOf(['set_shuffle', 'set_repeat'], null, { kind: 'spotify', shuffle: true, repeat: 'off' })).toEqual([]);
  });

  it('nothing listed, nothing drawn', () => {
    expect(controlsOf([], 'playing', { kind: 'spotify', shuffle: true, repeat: 'track' })).toEqual([]);
  });
});

/**
 * The phone mini-bar's swipe, from the same lists. Goes red if a track change
 * turns the gesture off under a queue source again (each loading track then
 * unmounts the carousel mid-slide and drops a second quick swipe), if a live
 * stream becomes swipeable, or if a step back counts from anything but the
 * entry the bar shows.
 */
describe('the mini-bar swipe', () => {
  // Music Library and Spotify while the track they stepped to loads: `seek`
  // and `skip` are gone, the steps are not.
  const LIBRARY_LOADING = ['pause', 'next', 'prev', 'set_shuffle', 'set_repeat', 'play_index', 'stop'];
  const SPOTIFY_LOADING = ['pause', 'next', 'prev', 'set_shuffle', 'set_repeat'];

  it('holds through the loading of the track a swipe stepped to', () => {
    expect(swipeable(LIBRARY_LOADING)).toBe(true);
    expect(swipeable(SPOTIFY_LOADING)).toBe(true);
    expect(swipeMove(SPOTIFY_LOADING, 'next')).toEqual({ command: 'next' });
  });

  it('steps back from the entry shown, so two quick swipes reach two entries', () => {
    const first = swipeMove(LIBRARY_LOADING, 'prev', 5);
    const second = swipeMove(LIBRARY_LOADING, 'prev', first.params.index);
    expect([first.params.index, second.params.index]).toEqual([4, 3]);
    expect(first.command).toBe('play_index');
    // At the head of the queue, or with no queue at all, it is the plain step.
    expect(swipeMove(LIBRARY_LOADING, 'prev', 0)).toEqual({ command: 'prev' });
    expect(swipeMove(SPOTIFY_LOADING, 'prev')).toEqual({ command: 'prev' });
  });

  it('an episode skips −15 / +30, and not before its file is open', () => {
    const playing = ['pause', 'seek', 'skip'];
    expect(swipeMove(playing, 'next').skip).toBeGreaterThan(0);
    expect(swipeMove(playing, 'prev').skip).toBeLessThan(0);
    expect(swipeable(['pause'])).toBe(false);
  });

  it('past the last entry of a repeating queue it lands on the first, as next does', () => {
    // Music Library on its last entry of three: `next` is listed only while
    // the queue repeats, and the backend then goes back to entry 0.
    const repeating = { controls: LIBRARY_LOADING, details: { kind: 'music_library', repeat: 'context' } };
    expect(swipeTarget(repeating, 'next', 2, 3)).toBe(0);
    expect(swipeTarget(repeating, 'next', 1, 3)).toBe(2);
    // Not repeating, the backend lists no `next` there and nothing follows.
    const ending = { controls: LIBRARY_LOADING.filter(c => c !== 'next'), details: { kind: 'music_library', repeat: 'off' } };
    expect(swipeTarget(ending, 'next', 2, 3)).toBe(-1);
    // Backward never wraps: on the first entry `prev` restarts the track.
    expect(swipeTarget(repeating, 'prev', 0, 3)).toBe(-1);
    expect(swipeTarget(repeating, 'prev', 2, 3)).toBe(1);
  });

  it('a live stream is never swiped, though it steps through the favorites', () => {
    expect(swipeable(['stop', 'next', 'prev'])).toBe(false);
    expect(swipeable(['resume_playback', 'next', 'prev'])).toBe(false);
  });
});
