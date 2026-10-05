// frontend/src/utils/playerControls.js
// Which controls the full-screen player draws, and in which state — read from
// the wire alone: a control exists iff the source lists its command in
// `controls` now, and a toggle's state is the one `details` publishes. No
// source is named here, so a source earns a button by listing a command, never
// by being on a list.
import { pausesOnPress } from '@/utils/transport';
import { nextRepeatMode } from '@/utils/repeatMode';

/** The relative moves a `skip` pair offers, in seconds (the −15 / +30 glyphs). */
export const SKIP_BACK_SECONDS = -15;
export const SKIP_FORWARD_SECONDS = 30;

/**
 * The main button: the pause/resume pair, else a live stream's stop/resume_playback.
 * The pause pair follows the phase rather than which half is listed, as the
 * glyph always has (utils/transport) — a press that would be refused shows
 * disabled instead of sending.
 */
function mainControl(listed, phase) {
  if (listed('pause') || listed('resume')) {
    const pauses = pausesOnPress(phase);
    const command = pauses ? 'pause' : 'resume';
    return { id: 'main', row: 'transport', command, icon: pauses ? 'pause' : 'play', enabled: listed(command) };
  }
  // Another device of the account plays: the one button brings it here, and
  // says so in words — a bare play glyph would read as playing it there.
  if (listed('take_over')) {
    return { id: 'main', row: 'transport', command: 'take_over', icon: 'play', labelled: true, enabled: true };
  }
  // A live stream has no pause to stand beside: the button says what it does,
  // in words where there is room for them and by its glyph alone elsewhere.
  if (listed('stop') || listed('resume_playback')) {
    const stops = listed('stop');
    return {
      id: 'main', row: 'transport',
      command: stops ? 'stop' : 'resume_playback',
      icon: stops ? 'stop' : 'play',
      labelled: true, glyphSuffices: true,
      enabled: true
    };
  }
  return null;
}

/**
 * The two buttons around the main one. Track steps win over the relative skip:
 * a source that has a previous or a next item steps through them (a disc, a
 * queue), and only one with neither (an episode) offers
 * −15 / +30 in their place. The pair is drawn whole once either step is listed,
 * the missing half disabled — `next` is absent on the last track.
 *
 * `opening`: a player's session that loads before it can move — an episode
 * opening its file lists neither steps nor `skip` until it plays. Its relative
 * pair is drawn disabled meanwhile, so the row has its shape from the first
 * frame rather than growing two buttons when the file opens. A source that can
 * step while it loads lists its steps, which win as above.
 */
function flankControls(listed, opening) {
  if (listed('prev') || listed('next')) {
    return [
      { id: 'prev', row: 'transport', command: 'prev', icon: 'previous', enabled: listed('prev') },
      { id: 'next', row: 'transport', command: 'next', icon: 'next', enabled: listed('next') }
    ];
  }
  if (listed('skip') || opening) {
    const enabled = listed('skip');
    return [
      { id: 'skip-back', row: 'transport', command: 'skip', icon: 'rewind15', seconds: SKIP_BACK_SECONDS, enabled },
      { id: 'skip-forward', row: 'transport', command: 'skip', icon: 'forward30', seconds: SKIP_FORWARD_SECONDS, enabled }
    ];
  }
  return [];
}

/**
 * The two toggles at the ends of the plate: shuffle before the transport,
 * repeat after it, each in the state `details` publishes.
 */
function toggleControls(listed, details) {
  const shuffle = listed('set_shuffle')
    ? {
      id: 'shuffle', row: 'transport', command: 'set_shuffle', icon: 'shuffle',
      active: !!details?.shuffle, params: { shuffle: !details?.shuffle }, enabled: true
    }
    : null;
  let repeat = null;
  if (listed('set_repeat')) {
    const mode = details?.repeat ?? 'off';
    repeat = {
      id: 'repeat', row: 'transport', command: 'set_repeat',
      icon: mode === 'track' ? 'repeatOnce' : 'repeat',
      mode, active: mode !== 'off', params: { mode: nextRepeatMode(mode) }, enabled: true
    };
  }
  return [shuffle, repeat];
}

/**
 * The controls the player draws, in drawing order.
 *
 * `row` says where each goes: `progress` (the bar is interactive iff a `seek`
 * control is present) and `transport` (the plate: shuffle, flank, main, flank,
 * repeat). Every control carries the command it sends; `params` is the payload
 * a press sends when the control knows it alone.
 *
 * Flanks and toggles without a main button are dropped: a transport is a main
 * button first, and a row of arrows around nothing is not one.
 *
 * @param {object} state
 * @param {string[]} state.controls - the commands the source takes now
 * @param {object|null} state.details - the source's details (`kind` and its fields)
 * @param {string|null} state.phase - the session's phase, null with no session
 * @returns {Array<{id: string, row: string, command: string, enabled: boolean}>}
 */
export function playerControls({ controls, details, phase }) {
  const listed = (command) => controls.includes(command);
  const items = [];

  if (listed('seek')) items.push({ id: 'seek', row: 'progress', command: 'seek', enabled: true });

  const main = mainControl(listed, phase);
  if (main) {
    const pauses = listed('pause') || listed('resume');
    const [before, after] = flankControls(listed, pauses && phase === 'loading');
    const [shuffle, repeat] = toggleControls(listed, details);
    items.push(...[shuffle, before, main, after, repeat].filter(Boolean));
  }

  return items;
}

/**
 * What a horizontal swipe on the phone's mini-bar sends, or null when the
 * source takes no such move now: a step where the source takes one, else the
 * relative skip (−15 / +30). Backwards over a queue it steps to the entry
 * before the one the bar shows (`play_index`): `prev` restarts the current
 * track past a few seconds, which would contradict the carousel already sliding
 * the previous title in — and counting from the entry shown rather than the one
 * playing is what lets two quick swipes reach two different entries.
 *
 * Read from what the source lists, never from the phase: a queue source keeps
 * its steps listed while the track it stepped to loads (only `skip` drops), so
 * a swipe that changed the track leaves the next one possible.
 *
 * @param {string[]} controls - the commands the source takes now
 * @param {'next'|'prev'} direction
 * @param {number} [shownIndex] - the queue entry the bar shows, -1 without a queue
 * @returns {{command: string, params?: object}|{skip: number}|null}
 */
export function swipeMove(controls, direction, shownIndex = -1) {
  const listed = (command) => controls.includes(command);
  if (direction === 'next') {
    if (listed('next')) return { command: 'next' };
    return listed('skip') ? { skip: SKIP_FORWARD_SECONDS } : null;
  }
  if (shownIndex > 0 && listed('play_index')) return { command: 'play_index', params: { index: shownIndex - 1 } };
  if (listed('prev')) return { command: 'prev' };
  return listed('skip') ? { skip: SKIP_BACK_SECONDS } : null;
}

/**
 * The queue entry a swipe lands on from the entry the bar shows, or -1 where
 * there is none — what the carousel slides in, and whether it slides at all.
 * Forward it is the next entry, and past the last one the first again where the
 * source takes `next` there while the queue repeats: that press wraps (Music
 * Library goes back to entry 0, as the full player's next button does), so the
 * swipe must too. Backward it never wraps: on the first entry `prev` restarts
 * the track rather than reaching the last one.
 *
 * @param {object} state
 * @param {string[]} state.controls - the commands the source takes now
 * @param {object|null} state.details - the source's details (`repeat`)
 * @param {'next'|'prev'} direction
 * @param {number} shownIndex - the queue entry the bar shows, -1 without one
 * @param {number} length - the queue's length
 * @returns {number}
 */
export function swipeTarget({ controls, details }, direction, shownIndex, length) {
  if (shownIndex < 0 || shownIndex >= length) return -1;
  if (direction === 'prev') return shownIndex - 1;
  if (shownIndex + 1 < length) return shownIndex + 1;
  const repeats = (details?.repeat ?? 'off') !== 'off';
  return repeats && controls.includes('next') ? 0 : -1;
}

/**
 * Whether the mini-bar takes a swipe at all: a move in either direction. A
 * live stream lists none, so it is never swiped.
 *
 * @param {string[]} controls - the commands the source takes now
 */
export function swipeable(controls) {
  return !!(swipeMove(controls, 'next') || swipeMove(controls, 'prev'));
}
